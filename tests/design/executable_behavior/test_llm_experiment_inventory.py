from __future__ import annotations

import pytest

import scripts.run_executable_behavior_llm_experiment as experiment
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    Step,
    UseCase,
)
from app.design.services.executable_behavior.contracts import canonical_digest
from app.design.services.executable_behavior.operations import (
    OperationContext,
    validated_operation_fragment,
)
from app.design.services.executable_behavior.patches import OperationPatchError
from scripts.run_executable_behavior_llm_experiment import (
    ExperimentFailure,
    LogicalCallBudget,
    UseCaseInputs,
    _apply_sourceability_patch,
    _compose_inventory,
    _inventory_fragment_findings,
    _merge_inventory_fragments,
    _operation_call_skeleton_findings,
    _operation_fragment,
    _operation_signature_semantic_findings,
    _operation_sourceability_findings,
    _sourceability_patch_input,
    _write_json,
)


def _index() -> ScenarioIndex:
    use_cases = tuple(
        UseCase(
            id=use_case_id,
            name=f"Use case {position}",
            primary_actor="Student",
            specification={},
            steps=(
                Step(
                    id=f"{use_case_id}:main:1",
                    use_case_id=use_case_id,
                    subject="Student",
                    sentence="Submit request",
                    order=0,
                    branch="main",
                ),
            ),
            precondition_refs=(),
        )
        for position, use_case_id in enumerate(("UC1", "UC2"), start=1)
    )
    groups = tuple(
        ExecutionGroup(
            id=f"{item.id}:actor:1",
            use_case_id=item.id,
            step_ids=(item.steps[0].id,),
            actor_step=item.steps[0].id,
            entry_actor="Student",
            trace_use_case_ids=(item.id,),
            required_step_ids=(item.steps[0].id,),
        )
        for item in use_cases
    )
    return ScenarioIndex({}, use_cases, (), groups)


def test_actor_named_entity_is_not_forced_to_own_an_operation() -> None:
    index = _index()
    inventory = {
        "Classes": [
            {
                "className": "StudentBoundary",
                "stereotype": "Boundary",
                "useCaseIds": ["UC1"],
            },
            {
                "className": "EnrollmentControl",
                "stereotype": "Control",
                "useCaseIds": ["UC1"],
            },
            {
                "className": "Student",
                "stereotype": "Entity",
                "useCaseIds": ["UC1"],
            },
            {
                "className": "Enrollment",
                "stereotype": "Entity",
                "useCaseIds": ["UC1"],
            },
        ]
    }

    inputs = experiment._use_case_inputs(index, inventory, index.use_cases[0])

    assert inputs.durable_entities == ("Enrollment",)


def test_entity_query_signature_semantics_are_checked_deterministically() -> None:
    inventory = {
        "Classes": [
            {"className": "CourseOffering", "stereotype": "Entity"},
            {"className": "Student", "stereotype": "Entity"},
        ],
        "DataTypes": [],
    }
    duplicate = {
        "name": "getAssignedOfferings",
        "parameters": [{"name": "professorId", "type": "String"}],
        "returnType": "List<CourseOffering>",
        "stepRefs": ["UC9:main:2"],
    }
    fragment = {
        "DataTypes": [],
        "Classes": [
            {"className": "CourseOffering", "operations": [duplicate]},
            {
                "className": "Student",
                "operations": [
                    duplicate,
                    {
                        "name": "getStudentsByOffering",
                        "parameters": [
                            {"name": "professorId", "type": "String"}
                        ],
                        "returnType": "List<Student>",
                        "stepRefs": ["UC9:main:2"],
                    },
                ],
            },
        ],
    }

    findings = _operation_signature_semantic_findings(
        fragment,
        inventory=inventory,
    )

    assert any("qualifier Offering is absent" in item for item in findings)
    assert any("duplicate Entity query contract" in item for item in findings)


def _fragment(use_case_id: str, *, control: str = "CourseControl") -> dict:
    return {
        "Classes": [
            {
                "className": "StudentBoundary",
                "stereotype": "Boundary",
                "description": "Student channel",
                "fields": [],
                "identifier": [],
                "values": [],
                "useCaseIds": [use_case_id],
            },
            {
                "className": control,
                "stereotype": "Control",
                "description": "Course coordination",
                "fields": [],
                "identifier": [],
                "values": [],
                "useCaseIds": [use_case_id],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
    }


def test_fragment_is_complete_before_checkpoint() -> None:
    index = _index()
    assert not _inventory_fragment_findings(index, index.use_cases[0], _fragment("UC1"))

    missing_boundary = _fragment("UC1")
    missing_boundary["Classes"] = missing_boundary["Classes"][1:]
    findings = _inventory_fragment_findings(
        index,
        index.use_cases[0],
        missing_boundary,
    )
    assert any("Boundary" in finding for finding in findings)

    ambiguous_role_name = _fragment("UC1")
    ambiguous_role_name["Classes"][0]["className"] = "Student"
    findings = _inventory_fragment_findings(
        index,
        index.use_cases[0],
        ambiguous_role_name,
    )
    assert any("must end with Boundary" in finding for finding in findings)


def test_merge_unions_only_identical_class_declarations() -> None:
    index = _index()
    merged = _merge_inventory_fragments(
        index,
        {"UC1": _fragment("UC1"), "UC2": _fragment("UC2")},
    )
    assert len(merged["Classes"]) == 2
    assert all(item["useCaseIds"] == ["UC1", "UC2"] for item in merged["Classes"])


def test_merge_rejects_same_name_with_different_contract() -> None:
    index = _index()
    conflicting = _fragment("UC2")
    conflicting["Classes"][0]["description"] = "Different responsibility"
    with pytest.raises(ExperimentFailure, match="StudentBoundary"):
        _merge_inventory_fragments(
            index,
            {"UC1": _fragment("UC1"), "UC2": conflicting},
        )


def test_compose_inventory_persists_validated_merge_without_llm(tmp_path) -> None:
    index = _index()
    result = _compose_inventory(
        index=index,
        fragments={"UC1": _fragment("UC1"), "UC2": _fragment("UC2")},
        run_dir=tmp_path,
        budget=LogicalCallBudget(1),
        parallelism=1,
    )

    assert len(result["Classes"]) == 2
    assert (tmp_path / "validated" / "inventory.json").exists()
    assert (tmp_path / "validated" / "inventory-merge.json").exists()


def test_call_skeleton_requires_main_flow_control_not_only_extension_handler() -> None:
    steps = (
        Step("UC1:main:1", "UC1", "Student", "Search", 0, "main"),
        Step("UC1:main:2", "UC1", "System", "Load courses", 1, "main"),
        Step(
            "UC1:extension:2a:2a1",
            "UC1",
            "System",
            "Show authorization error",
            2,
            "2a",
        ),
    )
    use_case = UseCase("UC1", "Search", "Student", {}, steps, ())
    group = ExecutionGroup(
        "UC1:main:1",
        "UC1",
        tuple(step.id for step in steps),
        "UC1:main:1",
        "Student",
        ("UC1",),
        tuple(step.id for step in steps),
    )
    inputs = UseCaseInputs(
        use_case,
        (group,),
        tuple(step.id for step in steps),
        ("StudentBoundary", "SearchControl", "Course"),
        ("Course",),
    )
    inventory = {
        "Classes": [
            {"className": "StudentBoundary", "stereotype": "Boundary"},
            {"className": "SearchControl", "stereotype": "Control"},
            {"className": "Course", "stereotype": "Entity"},
        ]
    }
    fragment = {
        "Classes": [
            {
                "className": "StudentBoundary",
                "operations": [{"stepRefs": ["UC1:main:1"]}],
            },
            {
                "className": "SearchControl",
                "operations": [{"stepRefs": ["UC1:extension:2a:2a1"]}],
            },
            {
                "className": "Course",
                "operations": [{"stepRefs": ["UC1:main:2"]}],
            },
        ]
    }

    findings = _operation_call_skeleton_findings(
        fragment,
        inputs=inputs,
        inventory=inventory,
    )

    assert any("no Control operation covers a main-flow step" in item for item in findings)


def test_operation_contract_rejects_child_that_consumes_control_eventual_return() -> None:
    index = _index()
    use_case = index.use_cases[0]
    group = index.groups[0]
    inputs = UseCaseInputs(
        use_case,
        (group,),
        ("UC1:main:1",),
        ("StudentBoundary", "CourseControl", "Enrollment"),
        ("Enrollment",),
    )
    inventory = {
        "Classes": [
            {"className": "StudentBoundary", "stereotype": "Boundary"},
            {"className": "CourseControl", "stereotype": "Control"},
            {"className": "Enrollment", "stereotype": "Entity"},
        ]
    }
    fragment = {
        "DataTypes": [
            {
                "name": "JoinRequest",
                "kind": "valueObject",
                "fields": [{"name": "studentId", "type": "String"}],
                "values": [],
            }
        ],
        "Classes": [
            {
                "className": "StudentBoundary",
                "operations": [
                    {
                        "name": "join",
                        "parameters": [{"name": "request", "type": "JoinRequest"}],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
            {
                "className": "CourseControl",
                "operations": [
                    {
                        "name": "process",
                        "parameters": [{"name": "request", "type": "JoinRequest"}],
                        "returnType": "Enrollment",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
            {
                "className": "Enrollment",
                "operations": [
                    {
                        "name": "record",
                        "parameters": [{"name": "entry", "type": "Enrollment"}],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
        ],
    }

    findings = _operation_sourceability_findings(
        fragment,
        inputs=inputs,
        inventory=inventory,
    )
    assert any("incompatible consumer/producer types" in item for item in findings)
    assert any("Enrollment::record" in item and "entry:Enrollment" in item for item in findings)
    assert any("direct Control's eventual return is unavailable" in item for item in findings)

    fragment["Classes"][2]["operations"][0]["parameters"] = [
        {"name": "studentId", "type": "String"}
    ]
    assert not _operation_sourceability_findings(
        fragment,
        inputs=inputs,
        inventory=inventory,
    )


def test_sourceability_names_same_type_field_that_has_no_semantic_match() -> None:
    """A String field is not evidence that it can supply an unrelated String ID."""
    index = _index()
    use_case = index.use_cases[0]
    inputs = UseCaseInputs(
        use_case,
        (index.groups[0],),
        ("UC1:main:1",),
        ("StudentBoundary", "EnrollmentControl"),
        (),
    )
    inventory = {
        "Classes": [
            {"className": "StudentBoundary", "stereotype": "Boundary"},
            {"className": "EnrollmentControl", "stereotype": "Control"},
        ]
    }
    fragment = {
        "DataTypes": [
            {
                "name": "EnrollmentRequest",
                "kind": "valueObject",
                "fields": [
                    {"name": "studentId", "type": "String"},
                    {"name": "offeringId", "type": "String"},
                ],
                "values": [],
            }
        ],
        "Classes": [
            {
                "className": "StudentBoundary",
                "operations": [
                    {
                        "name": "submit",
                        "parameters": [
                            {"name": "request", "type": "EnrollmentRequest"}
                        ],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
            {
                "className": "EnrollmentControl",
                "operations": [
                    {
                        "name": "enroll",
                        "parameters": [
                            {"name": "entryId", "type": "String"}
                        ],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
        ],
    }

    findings = _operation_sourceability_findings(
        fragment,
        inputs=inputs,
        inventory=inventory,
    )

    assert any("entryId:String" in item for item in findings)


def test_operation_resume_promotes_a_valid_existing_attempt_without_llm(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after an attempt write must not spend another LLM call on resume."""
    index = _index()
    use_case = index.use_cases[0]
    inputs = UseCaseInputs(
        use_case,
        (index.groups[0],),
        ("UC1:main:1",),
        ("StudentBoundary", "EnrollmentControl"),
        (),
    )
    inventory = {
        "Classes": [
            {"className": "StudentBoundary", "stereotype": "Boundary"},
            {"className": "EnrollmentControl", "stereotype": "Control"},
        ],
        "DataTypes": [],
    }
    candidate = {
        "DataTypes": [],
        "Classes": [
            {
                "className": "StudentBoundary",
                "operations": [
                    {
                        "name": "submit",
                        "parameters": [{"name": "studentId", "type": "String"}],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
            {
                "className": "EnrollmentControl",
                "operations": [
                    {
                        "name": "enroll",
                        "parameters": [{"name": "studentId", "type": "String"}],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
        ],
    }
    _write_json(
        tmp_path
        / "attempts"
        / "operations"
        / f"UC1-{experiment.OPERATION_ATTEMPT_VERSION}-2.json",
        candidate,
    )

    def unexpected_llm(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("resume must validate the saved attempt before invoking LLM")

    monkeypatch.setattr(experiment, "_invoke", unexpected_llm)
    result = _operation_fragment(
        inputs=inputs,
        scenario={"useCases": []},
        inventory=inventory,
        run_dir=tmp_path,
        budget=LogicalCallBudget(1),
    )

    assert result.use_case_id == "UC1"
    assert (tmp_path / "validated" / "operations" / "UC1.json").exists()


def test_source_patch_cannot_rename_offering_id_to_student_id() -> None:
    index = _index()
    use_case = index.use_cases[0]
    inputs = UseCaseInputs(
        use_case,
        (index.groups[0],),
        ("UC1:main:1",),
        ("StudentBoundary", "EnrollmentControl", "CourseOffering"),
        ("CourseOffering",),
    )
    inventory = {
        "Classes": [
            {"className": "StudentBoundary", "stereotype": "Boundary"},
            {"className": "EnrollmentControl", "stereotype": "Control"},
            {"className": "CourseOffering", "stereotype": "Entity"},
        ],
        "DataTypes": [],
    }
    fragment = {
        "DataTypes": [
            {
                "name": "StudentRequest",
                "kind": "valueObject",
                "fields": [{"name": "studentId", "type": "String"}],
                "values": [],
            }
        ],
        "Classes": [
            {
                "className": "StudentBoundary",
                "operations": [
                    {
                        "name": "submit",
                        "parameters": [
                            {"name": "request", "type": "StudentRequest"}
                        ],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
            {
                "className": "EnrollmentControl",
                "operations": [
                    {
                        "name": "enroll",
                        "parameters": [
                            {"name": "request", "type": "StudentRequest"}
                        ],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
            {
                "className": "CourseOffering",
                "operations": [
                    {
                        "name": "getOfferingDetails",
                        "parameters": [{"name": "offeringId", "type": "String"}],
                        "returnType": "CourseOffering",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
        ],
    }
    context = experiment.OperationContext.from_payload(
        "UC1",
        inventory,
        scenario={"useCases": []},
        allowed_step_ids=inputs.allowed_steps,
        durable_entity_names=inputs.durable_entities,
        allowed_owner_names=inputs.allowed_owners,
    )
    repair = _sourceability_patch_input(
        fragment,
        context=context,
        inputs=inputs,
        inventory=inventory,
    )
    assert repair is not None
    current, repair_input = repair
    target = repair_input["operationTargets"][0]
    patch = {
        "baseDigest": repair_input["baseDigest"],
        "findingIds": repair_input["findingIds"],
        "edits": [
            {
                "action": "REPLACE",
                "owner": target["owner"],
                "operationRef": target["operationRef"],
                "expectedDigest": target["expectedDigest"],
                "replacement": {
                    "name": "getOfferingDetails",
                    "parameters": [{"name": "studentId", "type": "String"}],
                    "returnType": "CourseOffering",
                    "stepRefs": ["UC1:main:1"],
                },
            }
        ],
    }

    with pytest.raises(OperationPatchError, match=r"cannot discard.*semantic"):
        _apply_sourceability_patch(
            current=current,
            patch_payload=patch,
            repair_input=repair_input,
        )

    patch["edits"][0]["replacement"]["parameters"] = [
        {"name": "request", "type": "StudentRequest"}
    ]
    with pytest.raises(OperationPatchError, match=r"cannot discard.*semantic"):
        _apply_sourceability_patch(
            current=current,
            patch_payload=patch,
            repair_input=repair_input,
        )

    patch["edits"][0]["replacement"]["parameters"] = []
    with pytest.raises(OperationPatchError, match=r"cannot discard.*semantic"):
        _apply_sourceability_patch(
            current=current,
            patch_payload=patch,
            repair_input=repair_input,
        )


def test_sourceability_patch_is_limited_to_blocked_signature_and_step_refs() -> None:
    inventory = {
        "Classes": [
            {"className": "StudentBoundary", "stereotype": "Boundary"},
            {"className": "EnrollmentControl", "stereotype": "Control"},
        ],
        "DataTypes": [],
    }
    payload = {
        "DataTypes": [],
        "Classes": [
            {
                "className": "StudentBoundary",
                "operations": [
                    {
                        "name": "submit",
                        "parameters": [{"name": "studentId", "type": "String"}],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
            {
                "className": "EnrollmentControl",
                "operations": [
                    {
                        "name": "enroll",
                        "parameters": [{"name": "entryId", "type": "String"}],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    }
                ],
            },
        ],
    }
    context = OperationContext.from_payload(
        "UC1",
        inventory,
        scenario={"useCases": []},
        allowed_step_ids=("UC1:main:1",),
    )
    current = validated_operation_fragment(payload, context)
    blocked = current.payload["Classes"][1]["operations"][0]
    target_ref = "EnrollmentControl::enroll(entryId:String)"
    repair_input = {
        "findingIds": ["sourceability-1"],
        "operationTargets": [
            {
                "owner": "EnrollmentControl",
                "operationRef": target_ref,
                "expectedDigest": canonical_digest(blocked),
                "currentOperation": blocked,
            }
        ],
    }

    def patch_for(*, operation_ref: str = target_ref, owner: str = "EnrollmentControl", replacement: dict) -> dict:
        return {
            "baseDigest": canonical_digest(current.payload),
            "findingIds": ["sourceability-1"],
            "edits": [
                {
                    "action": "REPLACE",
                    "owner": owner,
                    "operationRef": operation_ref,
                    "expectedDigest": canonical_digest(blocked),
                    "replacement": replacement,
                }
            ],
        }

    accepted = _apply_sourceability_patch(
        current=current,
        repair_input=repair_input,
        patch_payload=patch_for(
            replacement={
                **blocked,
                "parameters": [
                    {"name": "enrollmentEntryId", "type": "String"}
                ],
            }
        ),
    )
    assert list(accepted["Classes"][1]["operations"][0]["parameters"]) == [
        {"name": "enrollmentEntryId", "type": "String"}
    ]

    with pytest.raises(OperationPatchError, match="preserve exact stepRefs"):
        _apply_sourceability_patch(
            current=current,
            repair_input=repair_input,
            patch_payload=patch_for(
                replacement={
                    **blocked,
                    "parameters": [{"name": "studentId", "type": "String"}],
                    "stepRefs": ["UC1:main:other"],
                }
            ),
        )
    with pytest.raises(OperationPatchError, match="preserve the return type"):
        _apply_sourceability_patch(
            current=current,
            repair_input=repair_input,
            patch_payload=patch_for(
                replacement={
                    **blocked,
                    "parameters": [
                        {"name": "enrollmentEntryId", "type": "String"}
                    ],
                    "returnType": "String",
                }
            ),
        )
    with pytest.raises(OperationPatchError, match="outside the blocked set"):
        _apply_sourceability_patch(
            current=current,
            repair_input=repair_input,
            patch_payload=patch_for(
                owner="StudentBoundary",
                operation_ref="StudentBoundary::submit(studentId:String)",
                replacement={
                    **blocked,
                    "parameters": [{"name": "studentId", "type": "String"}],
                },
            ),
        )
