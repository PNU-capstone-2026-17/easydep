from __future__ import annotations

from types import SimpleNamespace

from app.design import progress as design_progress
from app.design.graphs import subgraphs
from app.design.services.class_diagram import pipeline


def _scenario():
    return {
        "use_cases": [
            {"id": "UC1", "name": "View course", "primary_actor": "Student"},
            {"id": "UC2", "name": "Review course", "primary_actor": "Administrator"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "description": "Student selects a course."},
                    {"step_number": 2, "description": "System returns course details."},
                ],
                "extensions": [],
            },
            {
                "use_case_id": "UC2",
                "main_scenario": [
                    {"step_number": 1, "description": "Administrator selects a course."},
                    {"step_number": 2, "description": "System returns course details."},
                ],
                "extensions": [],
            },
        ],
    }


def _inventory():
    return {
        "Classes": [
            {"className": "CourseBoundary", "stereotype": "Boundary", "description": "Course interface", "fields": [], "identifier": []},
            {"className": "CourseControl", "stereotype": "Control", "description": "Course coordination", "fields": [], "identifier": []},
            {"className": "Course", "stereotype": "Entity", "description": "Persistent course", "fields": ["courseId : String"], "identifier": ["courseId"]},
        ],
        "DataTypes": [
            {"name": "CourseView", "kind": "valueObject", "fields": ["courseId : String"], "values": []},
        ],
        "Relationships": [],
    }


def _inventory_proposal():
    value = _inventory()
    items = []
    for item in value["Classes"]:
        items.append({
            "name": item["className"],
            "kind": item["stereotype"],
            "description": item.get("description", ""),
            "fields": [
                {
                    "name": field.partition(":")[0].strip(),
                    "type": field.partition(":")[2].strip(),
                }
                for field in item.get("fields") or []
            ],
            "identifier": item.get("identifier") or [],
            "values": [],
        })
    for item in value["DataTypes"]:
        items.append({
            "name": item["name"],
            "kind": item["kind"],
            "description": "",
            "fields": [
                {
                    "name": field.partition(":")[0].strip(),
                    "type": field.partition(":")[2].strip(),
                }
                for field in item.get("fields") or []
            ],
            "identifier": [],
            "values": item.get("values") or [],
        })
    return {"items": items, "Relationships": value["Relationships"]}


def _fragment(use_case_id: str, *, repaired: bool = False):
    prefix = "adminViewCourse" if repaired else "viewCourse"
    parameter = "courseId"
    parameter_type = "UUID" if use_case_id == "UC2" and not repaired else "String"
    refs = [f"{use_case_id}:main:1", f"{use_case_id}:main:2"]
    return {
        "Classes": [
            {"className": "CourseBoundary", "operations": [{"name": prefix, "parameters": [{"name": parameter, "type": parameter_type}], "returnType": "CourseView", "stepRefs": refs}]},
            {"className": "CourseControl", "operations": [{"name": prefix, "parameters": [{"name": parameter, "type": parameter_type}], "returnType": "CourseView", "stepRefs": [f"{use_case_id}:main:2"]}]},
            {"className": "Course", "operations": [{"name": "findCourse", "parameters": [{"name": "courseId", "type": parameter_type}], "returnType": "CourseView", "stepRefs": [f"{use_case_id}:main:2"]}]},
        ]
    }


def test_pipeline_owns_structure_once_and_commits_uc_fragments_canonically(monkeypatch):
    collision_repairs = 0

    def fake_parse(_messages, schema, **kwargs):
        nonlocal collision_repairs
        if schema is pipeline.ClassInventoryProposal:
            return _inventory_proposal()
        use_case_id = kwargs["metadata"]["useCaseId"]
        if kwargs["operation"] == "UseCaseOperationCollisionRepair":
            collision_repairs += 1
        return _fragment(
            use_case_id,
            repaired=collision_repairs >= 2,
        )

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)
    events = []
    with design_progress.progress_scope(
        lambda event, fields: events.append((event, fields))
    ):
        model = pipeline.generate_class_skeleton(_scenario())

    classes = {item["className"]: item for item in model["Classes"]}
    assert classes["CourseBoundary"]["use_case_ids"] == ["UC1", "UC2"]
    assert [item["name"] for item in classes["CourseBoundary"]["operations"]] == [
        "viewCourse", "adminViewCourse",
    ]
    assert all(
        operation["operationId"].startswith(f"{class_name}::")
        for class_name, item in classes.items()
        for operation in item["operations"]
    )
    assert [fields["unit"] for event, fields in events if event == "classDiagramSnapshotAccepted"] == [
        "inventory", "UC1", "UC2", "operations",
    ]
    assert collision_repairs == 2


def test_operation_rules_reject_classes_outside_the_fixed_inventory():
    fragment = _fragment("UC1")
    fragment["Classes"][0]["className"] = "InventedBoundary"
    context = pipeline._operation_context(_scenario(), _inventory(), _scenario()["use_case_specs"][0])
    report = pipeline.run_checks(
        pipeline.CLASS_OPERATION_CHECKS, fragment, context, parallel=True,
    )
    assert any(item.rule_id == "class.operation.references" for item in report.findings)


def test_operation_value_flow_rejects_parameter_without_a_finite_source():
    fragment = _fragment("UC1")
    control = fragment["Classes"][1]["operations"][0]
    control["parameters"] = [{"name": "course", "type": "Course"}]
    context = pipeline._operation_context(
        _scenario(), _inventory(), _scenario()["use_case_specs"][0],
    )

    report = pipeline.run_checks(
        pipeline.CLASS_OPERATION_CHECKS, fragment, context, parallel=True,
    )

    assert any(
        item.rule_id == "class.operation.value-flow"
        and item.location.endswith("CourseControl.viewCourse#course")
        for item in report.findings
    )


def test_operation_value_flow_projects_fields_from_structured_entry_input():
    fragment = _fragment("UC1")
    boundary = fragment["Classes"][0]["operations"][0]
    boundary["parameters"] = [{"name": "request", "type": "CourseView"}]
    context = pipeline._operation_context(
        _scenario(), _inventory(), _scenario()["use_case_specs"][0],
    )

    report = pipeline.run_checks(
        pipeline.CLASS_OPERATION_CHECKS, fragment, context, parallel=True,
    )

    assert not any(
        item.rule_id == "class.operation.value-flow"
        for item in report.findings
    )


def test_inventory_accepts_java_time_and_keeps_enum_values_out_of_class_fields():
    inventory = _inventory()
    inventory["Classes"][2]["fields"].append("startTime : LocalTime")
    inventory["DataTypes"].append({
        "name": "CourseStatus",
        "kind": "enumeration",
        "fields": [],
        "values": ["DRAFT", "PUBLISHED"],
    })

    report = pipeline.run_checks(
        pipeline.CLASS_INVENTORY_CHECKS,
        inventory,
        _scenario(),
        parallel=True,
    )

    assert report.findings == ()
    try:
        pipeline.InventoryItem.model_validate({
            "name": "CourseStatus",
            "kind": "enumeration",
            "fields": [{"name": "DRAFT", "type": "String"}],
            "values": ["DRAFT", "PUBLISHED"],
        })
    except ValueError as error:
        assert "enumeration requires values and cannot declare fields" in str(error)
    else:
        raise AssertionError("enumeration literals must not become typed fields")


def test_operation_fragment_schema_rejects_noncanonical_step_references():
    fragment = _fragment("UC1")
    fragment["Classes"][0]["operations"][0]["stepRefs"] = ["main.1"]

    try:
        pipeline.UseCaseOperationFragment.model_validate(fragment)
    except ValueError as error:
        assert "string_pattern_mismatch" in str(error)
    else:
        raise AssertionError("stepRefs must use canonical use-case step identities")


def test_inventory_repair_is_bounded_and_can_restore_type_closure(monkeypatch):
    candidates = [
        {
            "items": [
                {
                    "name": "User",
                    "kind": "Entity",
                    "fields": [{"name": "role", "type": "UserRole"}],
                    "identifier": [],
                    "values": [],
                },
                {
                    "name": "UserRole",
                    "kind": "enumeration",
                    "fields": [],
                    "identifier": [],
                    "values": ["MEMBER", "ADMINISTRATOR"],
                },
            ],
                "Relationships": [{
                    "source": "User",
                    "target": "UserRole",
                    "type": "Association",
                    "sourceMultiplicity": "1",
                    "targetMultiplicity": "1",
                }],
        },
        {
            "items": [{
                "name": "User",
                "kind": "Entity",
                "fields": [{"name": "role", "type": "UserRole"}],
                "identifier": [],
                "values": [],
            }],
            "Relationships": [],
        },
        {
            "items": [
                {
                    "name": "User",
                    "kind": "Entity",
                    "fields": [{"name": "role", "type": "UserRole"}],
                    "identifier": [],
                    "values": [],
                },
                {
                    "name": "UserRole",
                    "kind": "enumeration",
                    "fields": [],
                    "identifier": [],
                    "values": ["MEMBER", "ADMINISTRATOR"],
                },
            ],
            "Relationships": [],
        },
    ]
    calls = []

    def parse(*_args, **_kwargs):
        calls.append(_kwargs["operation"])
        return candidates[len(calls) - 1]

    monkeypatch.setattr(pipeline, "parse_structured", parse)

    inventory = pipeline._generate_inventory({})

    assert [item["name"] for item in inventory["DataTypes"]] == ["UserRole"]
    assert calls == [
        "ClassInventoryProposal",
        "ClassInventoryRepair",
        "ClassInventoryRepair",
    ]


def test_collaboration_failure_repairs_only_owning_uc_once(monkeypatch):
    scenario = _scenario()
    skeleton = {"revision": "skeleton"}
    first = {"revision": "first"}
    repaired = {"revision": "repaired"}
    final = {"revision": "final"}
    enrich_calls = []

    monkeypatch.setattr(subgraphs, "generate_class_skeleton", lambda value: skeleton)
    monkeypatch.setattr(
        subgraphs,
        "execution_groups",
        lambda value: [
            SimpleNamespace(id="UC1:main:1", use_case_id="UC1"),
            SimpleNamespace(id="UC2:main:1", use_case_id="UC2"),
        ],
    )

    def enrich(value, model, **kwargs):
        enrich_calls.append((model, kwargs))
        return first if not kwargs else final

    monkeypatch.setattr(subgraphs, "enrich_bce_behavior", enrich)
    monkeypatch.setattr(
        subgraphs,
        "group_outcomes",
        lambda model: (
            SimpleNamespace(
                group_id="UC1:main:1",
                status="failed",
                issues=("missing source",),
            ),
            SimpleNamespace(
                group_id="UC2:main:1",
                status="accepted",
                issues=(),
            ),
        ) if model is first else (),
    )
    repair_calls = []

    def repair(model, value, use_case_id, findings):
        repair_calls.append((model, use_case_id, findings))
        return repaired

    monkeypatch.setattr(subgraphs, "repair_operation_fragment_for_finding", repair)
    monkeypatch.setattr(
        subgraphs,
        "affected_group_ids",
        lambda value, use_case_ids: {"UC1:main:1"},
    )

    result = subgraphs._extract_class_model({"usecase_spec": scenario})

    assert result is final
    assert repair_calls == [
        (
            skeleton,
            "UC1",
            ["execution group UC1:main:1: missing source"],
        )
    ]
    assert enrich_calls == [
        (skeleton, {}),
        (
            repaired,
            {"group_ids": {"UC1:main:1"}, "existing": first},
        ),
    ]


def test_collaboration_repair_resolves_a_new_operation_collision_locally(monkeypatch):
    model = pipeline._compose(
        _inventory(),
        [("UC1", _fragment("UC1")), ("UC2", _fragment("UC2", repaired=True))],
        final=True,
    )
    operations = []

    def parse(*_args, operation, **_kwargs):
        operations.append(operation)
        if operation == "CollaborationSignatureRepair":
            return _fragment("UC2")
        return _fragment("UC2", repaired=True)

    monkeypatch.setattr(pipeline, "_parse_fragment", parse)

    repaired = pipeline.repair_operation_fragment_for_finding(
        model,
        _scenario(),
        "UC2",
        ["the delegated parameter has no source"],
    )

    boundary = next(
        item for item in repaired["Classes"]
        if item["className"] == "CourseBoundary"
    )
    assert [item["name"] for item in boundary["operations"]] == [
        "viewCourse", "adminViewCourse",
    ]
    assert operations == [
        "CollaborationSignatureRepair",
        "CollaborationSignatureCollisionRepair",
    ]
