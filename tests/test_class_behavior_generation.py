"""Offline contracts for structural BCE plus bounded operation enrichment."""
from __future__ import annotations

from copy import deepcopy

import app.design.graphs.subgraphs as subgraphs
from app.design.knowledge.detectors import class_diagram_findings
from app.design.services.class_diagram import behavior


def _scenario() -> dict:
    return {
        "use_cases": [{"id": "UC1", "name": "Place order", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "name": "Place order",
            "primary_actor": "Buyer",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer submits an order request."},
                {"step_number": 2, "sentence": "System records the order."},
            ],
            "extensions": [],
        }],
        "relationships": {"associations": [{"actor": "Buyer", "use_case": "Place order"}]},
    }


def _skeleton(*, connected: bool = True) -> dict:
    relationships = [{"source": "OrderControl", "target": "Order", "type": "Dependency"}]
    if connected:
        relationships.insert(0, {"source": "OrderForm", "target": "OrderControl", "type": "Dependency"})
    return {
        "Classes": [
            {"className": "OrderForm", "stereotype": "Boundary", "fields": ["draft : String"], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "OrderControl", "stereotype": "Control", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "Order", "stereotype": "Entity", "fields": ["number : String"], "methods": [], "use_case_ids": ["UC1"]},
        ],
        "Relationships": relationships,
    }


def _root_proposal() -> dict:
    return {"Classes": [
        {"className": "OrderForm", "operations": [{
            "name": "submitOrder", "parameters": [{"name": "request", "type": "OrderRequest"}],
            "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "OrderControl", "operations": [{
            "name": "placeOrder", "parameters": [{"name": "request", "type": "OrderRequest"}],
            "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False,
        }]},
        {"className": "Order", "operations": [{
            "name": "recordOrder", "parameters": [],
            "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False,
        }]},
    ]}


def test_behavior_slice_receives_only_constraints_for_its_use_case():
    scenario = _scenario()
    scenario["traceability"] = {
        "requirements": {
            "R1": {
                "type": "FR",
                "text": "Concurrent order placement preserves uniqueness.",
                "constrains_use_cases": ["UC1"],
            },
            "R2": {
                "type": "NFR",
                "text": "Reports complete within one second.",
                "constrains_use_cases": ["UC2"],
            },
        }
    }

    group = behavior.execution_groups(scenario)[0]
    payload = behavior._group_payload(_skeleton(), group, scenario)

    assert payload["evidence"]["constraintRequirements"] == [
        {
            "id": "R1",
            "type": "FR",
            "text": "Concurrent order placement preserves uniqueness.",
        }
    ]


def test_class_graph_passes_the_separate_relationship_artifact(monkeypatch):
    captured: dict = {}

    def skeleton(scenario_text):
        captured["skeletonScenario"] = __import__("json").loads(scenario_text)
        return _skeleton()

    monkeypatch.setattr(
        subgraphs,
        "extract_bce_classes_from_scenario",
        skeleton,
    )

    def enrich(scenario, skeleton):
        captured["scenario"] = scenario
        return skeleton

    monkeypatch.setattr(subgraphs, "enrich_bce_behavior", enrich)
    relationships = {"includes": [{"base_use_case": "A", "included_use_case": "B"}]}

    subgraphs._extract_class_model({
        "usecase_spec": {
            "use_cases": [{"id": "UC1", "name": "A"}],
            "use_case_specs": [{"use_case_id": "UC1", "main_scenario": []}],
        },
        "relationships": relationships,
    })

    assert captured["scenario"]["relationships"] == relationships
    assert captured["skeletonScenario"]["relationships"] == relationships


def test_derived_include_builds_one_internal_contract_from_stable_evidence(monkeypatch):
    """A factored include is one callable flow even when several roots invoke it."""
    scenario = {
        "use_cases": [
            {"id": "UC-A", "name": "First flow", "primary_actor": "Requester"},
            {"id": "UC-B", "name": "Second flow", "primary_actor": "Requester"},
        ],
        "use_case_specs": [
            {
                "use_case_id": use_case_id,
                "main_scenario": [
                    {"step_number": 1, "sentence": "Requester starts the flow."},
                    {
                        "step_number": 2,
                        "sentence": "System performs the shared validation.",
                        "covered_req_ids": ["R-SHARED"],
                    },
                ],
                "extensions": [],
            }
            for use_case_id in ("UC-A", "UC-B")
        ],
        "relationships": {
            "includes": [
                {
                    "base_use_case_id": use_case_id,
                    "included_use_case_id": "UC_INC_SHARED",
                    "base_use_case": "ignored legacy base",
                    "included_use_case": "ignored legacy child",
                    "step_refs": [
                        {
                            "use_case_id": source_id,
                            "step_ref": "main:2",
                            "sentence": "System performs the shared validation.",
                            "covered_req_ids": ["R-SHARED"],
                        }
                        for source_id in ("UC-A", "UC-B")
                    ],
                    "requirement_ids": ["R-SHARED"],
                }
                for use_case_id in ("UC-A", "UC-B")
            ],
            "derived_use_cases": [{
                "use_case_id": "UC_INC_SHARED",
                "name": "Shared validation",
                "origin": "factored_include",
            }],
        },
    }
    skeleton = {
        "Classes": [
            {"className": "FirstBoundary", "stereotype": "Boundary", "use_case_ids": ["UC-A"]},
            {"className": "FirstControl", "stereotype": "Control", "use_case_ids": ["UC-A"]},
            {"className": "SecondBoundary", "stereotype": "Boundary", "use_case_ids": ["UC-B"]},
            {"className": "SecondControl", "stereotype": "Control", "use_case_ids": ["UC-B"]},
            {"className": "SharedControl", "stereotype": "Control", "use_case_ids": ["UC_INC_SHARED"]},
        ],
        "Relationships": [
            {"source": "FirstBoundary", "target": "FirstControl", "type": "Dependency"},
            {"source": "SecondBoundary", "target": "SecondControl", "type": "Dependency"},
            {"source": "FirstControl", "target": "SharedControl", "type": "Dependency"},
            {"source": "SecondControl", "target": "SharedControl", "type": "Dependency"},
        ],
    }
    internal_payloads: list[dict] = []

    def proposal(messages, _schema, **_kwargs):
        payload = __import__("json").loads(messages[1]["content"])
        if payload["internalFlow"]:
            internal_payloads.append(payload)
            return {"Classes": [{"className": "SharedControl", "operations": [{
                "name": "validate", "parameters": [{"name": "request", "type": "Request"}],
                "returnType": "void", "stepRefs": ["UC_INC_SHARED:main:1"], "actorEntry": False,
            }]}]}
        boundary = "FirstBoundary" if payload["useCaseId"] == "UC-A" else "SecondBoundary"
        control = "FirstControl" if payload["useCaseId"] == "UC-A" else "SecondControl"
        return {"Classes": [
            {"className": boundary, "operations": [{
                "name": "submit", "parameters": [], "returnType": "void",
                "stepRefs": [f"{payload['useCaseId']}:main:1"], "actorEntry": True,
            }]},
            {"className": control, "operations": [{
                "name": "continueFlow", "parameters": [], "returnType": "void",
                "stepRefs": [f"{payload['useCaseId']}:main:2"], "actorEntry": False,
            }]},
        ]}

    monkeypatch.setattr(behavior, "parse_structured", proposal)

    groups = behavior.execution_groups(scenario)
    assert behavior.relationship_pairs(scenario) == [
        ("include", "UC-A", "UC_INC_SHARED"),
        ("include", "UC-B", "UC_INC_SHARED"),
    ]
    assert [(group.use_case_id, group.internal) for group in groups].count(
        ("UC_INC_SHARED", True)
    ) == 1

    result = behavior.enrich_bce_behavior(scenario, skeleton)

    assert len(internal_payloads) == 1
    assert internal_payloads[0]["evidence"] == {
        "sourceStepRefs": [
            {"use_case_id": "UC-A", "step_ref": "main:2", "covered_req_ids": ["R-SHARED"]},
            {"use_case_id": "UC-B", "step_ref": "main:2", "covered_req_ids": ["R-SHARED"]},
        ],
        "requirementIds": ["R-SHARED"],
    }
    shared = next(item for item in result["Classes"] if item["className"] == "SharedControl")
    assert shared["operations"][0]["inputBindings"] == [{
        "useCaseId": "UC_INC_SHARED",
        "parameter": "request",
        "sourceRef": "callsite:UC_INC_SHARED:internal#request",
    }]
    assert class_diagram_findings(result, {"usecase_spec": scenario}) == []
    persisted_spec = {key: value for key, value in scenario.items() if key != "relationships"}
    assert class_diagram_findings(result, {
        "usecase_spec": persisted_spec,
        "relationships": scenario["relationships"],
    }) == []


def test_behavior_keeps_the_structural_skeleton_and_binds_finite_input(monkeypatch):
    skeleton = _skeleton()
    structural_before = {
        "Classes": [{key: value for key, value in item.items() if key not in {"methods", "operations"}} for item in skeleton["Classes"]],
        "Relationships": deepcopy(skeleton["Relationships"]),
    }
    monkeypatch.setattr(behavior, "parse_structured", lambda _messages, _schema, **_kwargs: _root_proposal())

    result = behavior.enrich_bce_behavior(_scenario(), skeleton)

    structural_after = {
        "Classes": [{key: value for key, value in item.items() if key not in {"methods", "operations"}} for item in result["Classes"]],
        "Relationships": result["Relationships"],
    }
    assert structural_after == structural_before
    control = next(item for item in result["Classes"] if item["className"] == "OrderControl")
    operation = control["operations"][0]
    assert operation["operationId"] == "OrderControl::placeOrder(request:OrderRequest)"
    assert operation["inputBindings"] == [{
        "useCaseId": "UC1", "parameter": "request",
        "sourceRef": "OrderForm::submitOrder(request:OrderRequest)#request",
    }]
    assert control["methods"] == ["placeOrder(request : OrderRequest): void"]
    assert class_diagram_findings(result, {"usecase_spec": _scenario()}) == []


def test_partial_group_keeps_independent_operations_and_records_dependency_closed_failure(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "name": "Process request", "primary_actor": "Requester"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Requester supplies a value."},
                {"step_number": 2, "sentence": "System coordinates the request."},
                {"step_number": 3, "sentence": "System stores the state."},
            ],
            "extensions": [],
        }],
    }
    skeleton = {
        "Classes": [
            {"className": "InputBoundary", "stereotype": "Boundary", "use_case_ids": ["UC1"]},
            {"className": "FlowControl", "stereotype": "Control", "use_case_ids": ["UC1"]},
            {"className": "StateStore", "stereotype": "Entity", "use_case_ids": ["UC1"]},
        ],
        "Relationships": [
            {"source": "InputBoundary", "target": "FlowControl", "type": "Dependency"},
            {"source": "FlowControl", "target": "StateStore", "type": "Dependency"},
        ],
    }
    proposal = {"Classes": [
        {"className": "InputBoundary", "operations": [{
            "name": "receive", "parameters": [{"name": "value", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "FlowControl", "operations": [
            {
                "name": "derive", "parameters": [
                    {"name": "derived", "type": "String"},
                    {"name": "derived", "type": "String"},
                ], "returnType": "String", "stepRefs": ["UC1:main:2"], "actorEntry": False,
            },
            {
                "name": "coordinate", "parameters": [{"name": "value", "type": "String"}],
                "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False,
            },
            {
                "name": "consume", "parameters": [{"name": "derived", "type": "String"}],
                "returnType": "void", "stepRefs": ["UC1:main:3"], "actorEntry": False,
            },
        ]},
        {"className": "StateStore", "operations": [{
            "name": "store", "parameters": [], "returnType": "void",
            "stepRefs": ["UC1:main:3"], "actorEntry": False,
        }]},
    ]}
    repair_payloads: list[dict] = []

    def parsed(messages, _schema, **_kwargs):
        payload = __import__("json").loads(messages[1]["content"])
        if "repairScope" in payload:
            repair_payloads.append(payload)
        return proposal

    monkeypatch.setattr(behavior, "parse_structured", parsed)
    result = behavior.enrich_bce_behavior(scenario, skeleton)

    control = next(item for item in result["Classes"] if item["className"] == "FlowControl")
    assert [operation["name"] for operation in control["operations"]] == ["coordinate"]
    outcome = behavior.group_outcomes(result)[0]
    assert outcome.status == "partial"
    assert "FlowControl::derive(derived:String,derived:String)" in outcome.rejected_operation_ids
    assert "FlowControl::consume(derived:String)" in outcome.rejected_operation_ids
    assert repair_payloads[0]["repairScope"]["preserveOperationIds"]
    assert "behaviorOutcomes" not in result
    assert any("behavior enrichment partial" in finding.message for finding in class_diagram_findings(
        result, {"usecase_spec": scenario}
    ))


def test_compatible_operation_ids_merge_their_step_refs(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "name": "Coordinate request", "primary_actor": "Requester"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Requester supplies a value."},
                {"step_number": 2, "sentence": "System begins coordination."},
                {"step_number": 3, "sentence": "System completes coordination."},
            ],
            "extensions": [],
        }],
    }
    skeleton = {
        "Classes": [
            {"className": "InputBoundary", "stereotype": "Boundary", "use_case_ids": ["UC1"]},
            {"className": "FlowControl", "stereotype": "Control", "use_case_ids": ["UC1"]},
        ],
        "Relationships": [{"source": "InputBoundary", "target": "FlowControl", "type": "Dependency"}],
    }
    proposal = {"Classes": [
        {"className": "InputBoundary", "operations": [{
            "name": "receive", "parameters": [{"name": "value", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "FlowControl", "operations": [
            {
                "name": "coordinate", "parameters": [{"name": "value", "type": "String"}],
                "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False,
            },
            {
                "name": "coordinate", "parameters": [{"name": "value", "type": "String"}],
                "returnType": "void", "stepRefs": ["UC1:main:3"], "actorEntry": False,
            },
        ]},
    ]}
    monkeypatch.setattr(behavior, "parse_structured", lambda _messages, _schema, **_kwargs: proposal)

    result = behavior.enrich_bce_behavior(scenario, skeleton)

    control = next(item for item in result["Classes"] if item["className"] == "FlowControl")
    assert len(control["operations"]) == 1
    operation = control["operations"][0]
    assert operation["operationId"] == "FlowControl::coordinate(value:String)"
    assert operation["stepRefs"] == ["UC1:main:2", "UC1:main:3"]
    assert operation["inputBindings"][0]["sourceRef"] == (
        "InputBoundary::receive(value:String)#value"
    )


def test_multiple_compatible_sources_use_the_narrow_selector(monkeypatch):
    scenario = _scenario()
    scenario["use_case_specs"][0]["main_scenario"].extend([
        {"step_number": 3, "subject_ref": "SYSTEM", "sentence": "System validates the request."},
        {"step_number": 4, "subject_ref": "SYSTEM", "sentence": "System records the order."},
    ])
    seen_choices: list[dict] = []

    proposal = {"Classes": [
        {"className": "OrderForm", "operations": [{
            "name": "submit", "parameters": [{"name": "request", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "OrderControl", "operations": [
            {"name": "validate", "parameters": [{"name": "request", "type": "String"}], "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False},
            {"name": "record", "parameters": [{"name": "request", "type": "String"}], "returnType": "void", "stepRefs": ["UC1:main:3"], "actorEntry": False},
        ]},
        {"className": "Order", "operations": [{
            "name": "store", "parameters": [], "returnType": "void",
            "stepRefs": ["UC1:main:4"], "actorEntry": False,
        }]},
    ]}

    def parsed(messages, schema, **_kwargs):
        if schema is behavior.BindingChoice:
            payload = __import__("json").loads(messages[1]["content"])
            seen_choices.append(payload)
            return {"sourceRef": "OrderControl::validate(request:String)#request"}
        return proposal

    monkeypatch.setattr(behavior, "parse_structured", parsed)
    result = behavior.enrich_bce_behavior(scenario, _skeleton())

    assert seen_choices and len(seen_choices[0]["candidates"]) == 2
    control = next(item for item in result["Classes"] if item["className"] == "OrderControl")
    record = next(item for item in control["operations"] if item["name"] == "record")
    assert record["inputBindings"][0]["sourceRef"] == "OrderControl::validate(request:String)#request"


def test_topology_insufficiency_preserves_behavior_without_adding_dependency(monkeypatch):
    skeleton = _skeleton(connected=False)
    monkeypatch.setattr(behavior, "parse_structured", lambda _messages, _schema, **_kwargs: _root_proposal())

    result = behavior.enrich_bce_behavior(_scenario(), skeleton)

    assert result["Relationships"] == skeleton["Relationships"]
    assert any(item["operations"] for item in result["Classes"])
    assert behavior.group_outcomes(result)[0].status == "partial"
    findings = class_diagram_findings(result, {"usecase_spec": _scenario()})
    assert any("execution root lacks one reachable" in finding.message for finding in findings)


def test_class_check_rejects_a_future_or_cyclic_parameter_producer(monkeypatch):
    monkeypatch.setattr(behavior, "parse_structured", lambda _messages, _schema, **_kwargs: _root_proposal())
    result = behavior.enrich_bce_behavior(_scenario(), _skeleton())
    control = next(item for item in result["Classes"] if item["className"] == "OrderControl")
    operation = control["operations"][0]
    operation["inputBindings"][0]["sourceRef"] = operation["operationId"] + "#request"

    findings = class_diagram_findings(result, {"usecase_spec": _scenario()})

    assert any("future, reverse, cyclic, or unreachable" in finding.message for finding in findings)


def test_two_roots_share_an_internal_control_without_fabricating_child_input(monkeypatch):
    """The child exposes formal parameters; each caller supplies them later."""
    scenario = {
        "use_cases": [
            {"id": "UC1", "name": "Buy first", "primary_actor": "Buyer"},
            {"id": "UC2", "name": "Buy second", "primary_actor": "Buyer"},
            {"id": "UC3", "name": "Validate shared", "primary_actor": "Buyer"},
        ],
        "use_case_specs": [
            {"use_case_id": "UC1", "main_scenario": [{"step_number": 1, "sentence": "Buyer starts first."}, {"step_number": 2, "sentence": "System continues."}]},
            {"use_case_id": "UC2", "main_scenario": [{"step_number": 1, "sentence": "Buyer starts second."}, {"step_number": 2, "sentence": "System continues."}]},
            {"use_case_id": "UC3", "main_scenario": [
                {"step_number": 1, "sentence": "System validates shared request."},
                {"step_number": 2, "sentence": "System records the validation."},
            ]},
        ],
        "relationships": {
            "associations": [{"actor": "Buyer", "use_case": "Buy first"}, {"actor": "Buyer", "use_case": "Buy second"}],
            "includes": [{"base_use_case": "Buy first", "included_use_case": "Validate shared"}, {"base_use_case": "Buy second", "included_use_case": "Validate shared"}],
        },
    }
    skeleton = {
        "Classes": [
            {"className": "FirstForm", "stereotype": "Boundary", "use_case_ids": ["UC1"]},
            {"className": "FirstControl", "stereotype": "Control", "use_case_ids": ["UC1"]},
            {"className": "SecondForm", "stereotype": "Boundary", "use_case_ids": ["UC2"]},
            {"className": "SecondControl", "stereotype": "Control", "use_case_ids": ["UC2"]},
            {"className": "SharedControl", "stereotype": "Control", "use_case_ids": ["UC3"]},
            {"className": "SharedRecord", "stereotype": "Entity", "use_case_ids": ["UC3"]},
        ],
        "Relationships": [
            {"source": "FirstForm", "target": "FirstControl", "type": "Dependency"},
            {"source": "SecondForm", "target": "SecondControl", "type": "Dependency"},
            {"source": "FirstControl", "target": "SharedControl", "type": "Dependency"},
            {"source": "SecondControl", "target": "SharedControl", "type": "Dependency"},
            {"source": "SharedControl", "target": "SharedRecord", "type": "Dependency"},
        ],
    }

    def proposal(messages, _schema, **_kwargs):
        payload = __import__("json").loads(messages[1]["content"])
        if payload["internalFlow"]:
            return {"Classes": [
                {"className": "SharedControl", "operations": [{
                    "name": "validate", "parameters": [{"name": "request", "type": "String"}],
                    "returnType": "void", "stepRefs": ["UC3:main:1"], "actorEntry": False,
                }]},
                {"className": "SharedRecord", "operations": [{
                    "name": "recordValidation", "parameters": [{"name": "request", "type": "String"}],
                    "returnType": "void", "stepRefs": ["UC3:main:2"], "actorEntry": False,
                }]},
            ]}
        form = "FirstForm" if payload["useCaseId"] == "UC1" else "SecondForm"
        control = "FirstControl" if payload["useCaseId"] == "UC1" else "SecondControl"
        return {"Classes": [
            {"className": form, "operations": [{"name": "submit", "parameters": [], "returnType": "void", "stepRefs": [f"{payload['useCaseId']}:main:1"], "actorEntry": True}]},
            {"className": control, "operations": [{"name": "continueRequest", "parameters": [], "returnType": "void", "stepRefs": [f"{payload['useCaseId']}:main:2"], "actorEntry": False}]},
        ]}

    monkeypatch.setattr(behavior, "parse_structured", proposal)
    result = behavior.enrich_bce_behavior(scenario, skeleton)

    shared = next(item for item in result["Classes"] if item["className"] == "SharedControl")
    assert shared["operations"][0]["inputBindings"] == [{
        "useCaseId": "UC3",
        "parameter": "request",
        "sourceRef": "callsite:UC3:internal#request",
    }]
    record = next(item for item in result["Classes"] if item["className"] == "SharedRecord")
    assert record["operations"][0]["inputBindings"] == [{
        "useCaseId": "UC3",
        "parameter": "request",
        "sourceRef": "SharedControl::validate(request:String)#request",
    }]
    assert class_diagram_findings(result, {"usecase_spec": scenario}) == []

    record["operations"][0]["inputBindings"][0]["sourceRef"] = "callsite:UC3:internal#request"
    findings = class_diagram_findings(result, {"usecase_spec": scenario})
    assert any("callsite source is allowed only" in finding.message for finding in findings)


def test_observable_scenario_steps_trigger_one_return_contract_repair(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "name": "Inspect record", "primary_actor": "Requester"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "subject_ref": "Requester", "sentence": "Requester requests record details."},
                {"step_number": 2, "sentence": "System retrieves and presents the record details."},
            ],
            "extensions": [],
        }],
        "relationships": {"associations": [{"actor": "Requester", "use_case": "Inspect record"}]},
    }
    skeleton = {
        "Classes": [
            {"className": "InputBoundary", "stereotype": "Boundary", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "FlowControl", "stereotype": "Control", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "RecordStore", "stereotype": "Entity", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
        ],
        "Relationships": [
            {"source": "InputBoundary", "target": "FlowControl", "type": "Dependency"},
            {"source": "FlowControl", "target": "RecordStore", "type": "Dependency"},
        ],
    }
    void_proposal = {"Classes": [
        {"className": "InputBoundary", "operations": [{
            "name": "begin", "parameters": [{"name": "recordKey", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "FlowControl", "operations": [{
            "name": "coordinate", "parameters": [{"name": "recordKey", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False,
        }]},
        {"className": "RecordStore", "operations": [{
            "name": "load", "parameters": [], "returnType": "void",
            "stepRefs": ["UC1:main:2"], "actorEntry": False,
        }]},
    ]}
    repaired = deepcopy(void_proposal)
    for class_item in repaired["Classes"]:
        class_item["operations"][0]["returnType"] = "RecordDetails"
    payloads: list[dict] = []

    def parsed(messages, schema, **_kwargs):
        assert schema is not behavior.BindingChoice
        payload = __import__("json").loads(messages[1]["content"])
        payloads.append(payload)
        return repaired if "currentProposal" in payload else void_proposal

    monkeypatch.setattr(behavior, "parse_structured", parsed)
    result = behavior.enrich_bce_behavior(scenario, skeleton)

    assert len(payloads) == 2
    assert "currentProposal" in payloads[1]
    assert {
        operation["returnType"]
        for class_item in result["Classes"]
        for operation in class_item["operations"]
    } == {"RecordDetails"}


def test_failed_local_repairs_preserve_valid_partial_result(monkeypatch):
    incomplete = {"Classes": [{
        "className": "OrderForm",
        "operations": [{
            "name": "submitOrder",
            "parameters": [{"name": "request", "type": "OrderRequest"}],
            "returnType": "void",
            "stepRefs": ["UC1:main:1"],
            "actorEntry": True,
        }],
    }]}
    efforts: list[str] = []

    def parsed(_messages, _schema, **kwargs):
        effort = kwargs.get("reasoning_effort")
        efforts.append(effort)
        return incomplete

    monkeypatch.setattr(behavior, "parse_structured", parsed)

    result = behavior.enrich_bce_behavior(_scenario(), _skeleton())

    assert efforts == ["medium", "medium"]
    assert behavior.group_outcomes(result)[0].status == "partial"
    assert result["Classes"][0]["operations"][0]["name"] == "submitOrder"


def test_scalar_results_do_not_bind_unrelated_parameters_but_value_objects_can():
    group = behavior._Group("UC1", "UC1:root", (), None, False)
    target = ("SinkControl", {"actorEntry": False})
    edges = {"SourceControl": {"SinkControl"}}
    scalar_source = ("SourceControl", {
        "operationId": "SourceControl::makeValue()",
        "parameters": [],
        "returnType": "String",
    })

    assert behavior._binding_candidates(
        target, [scalar_source], group, edges, {"name": "targetValue", "type": "String"}
    ) == []

    propagated_scalar = ("SourceControl", {
        "operationId": "SourceControl::passValue(targetValue:String)",
        "parameters": [{"name": "targetValue", "type": "String"}],
        "returnType": "String",
    })
    assert behavior._binding_candidates(
        target, [propagated_scalar], group, edges, {"name": "targetValue", "type": "String"}
    ) == ["SourceControl::passValue(targetValue:String)#targetValue"]

    object_source = ("SourceControl", {
        "operationId": "SourceControl::makeSnapshot()",
        "parameters": [],
        "returnType": "Snapshot",
    })
    assert behavior._binding_candidates(
        target, [object_source], group, edges, {"name": "snapshot", "type": "Snapshot"}
    ) == ["SourceControl::makeSnapshot()"]


def test_validation_rejects_a_persisted_scalar_result_for_a_different_parameter(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "name": "Apply value", "primary_actor": "Requester"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Requester submits values."},
                {"step_number": 2, "sentence": "System derives a value."},
                {"step_number": 3, "sentence": "System applies the value."},
                {"step_number": 4, "sentence": "System stores the state."},
            ],
            "extensions": [],
        }],
        "relationships": {"associations": [{"actor": "Requester", "use_case": "Apply value"}]},
    }
    skeleton = {
        "Classes": [
            {"className": "InputBoundary", "stereotype": "Boundary", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "FlowControl", "stereotype": "Control", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "StateStore", "stereotype": "Entity", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
        ],
        "Relationships": [
            {"source": "InputBoundary", "target": "FlowControl", "type": "Dependency"},
            {"source": "FlowControl", "target": "StateStore", "type": "Dependency"},
        ],
    }
    proposal = {"Classes": [
        {"className": "InputBoundary", "operations": [{
            "name": "receive", "parameters": [
                {"name": "sourceValue", "type": "String"},
                {"name": "targetValue", "type": "String"},
            ], "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "FlowControl", "operations": [
            {"name": "derive", "parameters": [{"name": "sourceValue", "type": "String"}], "returnType": "String", "stepRefs": ["UC1:main:2"], "actorEntry": False},
            {"name": "apply", "parameters": [{"name": "targetValue", "type": "String"}], "returnType": "void", "stepRefs": ["UC1:main:3"], "actorEntry": False},
        ]},
        {"className": "StateStore", "operations": [{
            "name": "store", "parameters": [], "returnType": "void",
            "stepRefs": ["UC1:main:4"], "actorEntry": False,
        }]},
    ]}
    monkeypatch.setattr(behavior, "parse_structured", lambda _messages, _schema, **_kwargs: proposal)
    result = behavior.enrich_bce_behavior(scenario, skeleton)
    control = next(item for item in result["Classes"] if item["className"] == "FlowControl")
    derive = next(item for item in control["operations"] if item["name"] == "derive")
    apply = next(item for item in control["operations"] if item["name"] == "apply")

    assert apply["inputBindings"][0]["sourceRef"] == "InputBoundary::receive(sourceValue:String,targetValue:String)#targetValue"
    apply["inputBindings"][0]["sourceRef"] = derive["operationId"]
    findings = class_diagram_findings(result, {"usecase_spec": scenario})

    assert any("scalar operation result cannot bind" in finding.message for finding in findings)


def test_same_step_entity_result_can_feed_a_control_then_an_entity(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "name": "Transform record", "primary_actor": "Requester"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Requester submits a record key."},
                {"step_number": 2, "sentence": "System obtains a record and creates a summary."},
            ],
            "extensions": [],
        }],
        "relationships": {"associations": [{"actor": "Requester", "use_case": "Transform record"}]},
    }
    skeleton = {
        "Classes": [
            {"className": "InputBoundary", "stereotype": "Boundary", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "FlowControl", "stereotype": "Control", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "RecordStore", "stereotype": "Entity", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
        ],
        "Relationships": [
            {"source": "InputBoundary", "target": "FlowControl", "type": "Dependency"},
            {"source": "FlowControl", "target": "RecordStore", "type": "Dependency"},
        ],
    }
    proposal = {"Classes": [
        {"className": "InputBoundary", "operations": [{
            "name": "begin", "parameters": [{"name": "recordKey", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "FlowControl", "operations": [
            {"name": "request", "parameters": [{"name": "recordKey", "type": "String"}], "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False},
            {"name": "transform", "parameters": [{"name": "record", "type": "Record"}], "returnType": "Summary", "stepRefs": ["UC1:main:2"], "actorEntry": False},
        ]},
        {"className": "RecordStore", "operations": [
            {"name": "read", "parameters": [{"name": "recordKey", "type": "String"}], "returnType": "Record", "stepRefs": ["UC1:main:2"], "actorEntry": False},
            {"name": "store", "parameters": [{"name": "summary", "type": "Summary"}], "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False},
        ]},
    ]}

    def parsed(messages, schema, **_kwargs):
        if schema is behavior.BindingChoice:
            return {"sourceRef": "FlowControl::request(recordKey:String)#recordKey"}
        return proposal

    monkeypatch.setattr(behavior, "parse_structured", parsed)
    result = behavior.enrich_bce_behavior(scenario, skeleton)
    control = next(item for item in result["Classes"] if item["className"] == "FlowControl")
    store = next(item for item in result["Classes"] if item["className"] == "RecordStore")
    transform = next(item for item in control["operations"] if item["name"] == "transform")
    read = next(item for item in store["operations"] if item["name"] == "read")
    write = next(item for item in store["operations"] if item["name"] == "store")

    assert transform["inputBindings"][0]["sourceRef"] == read["operationId"]
    assert write["inputBindings"][0]["sourceRef"] == transform["operationId"]
    assert class_diagram_findings(result, {"usecase_spec": scenario}) == []

    transform["inputBindings"][0]["sourceRef"] = write["operationId"]
    cycle_findings = class_diagram_findings(result, {"usecase_spec": scenario})
    assert any("operation input bindings form a cycle" in finding.message for finding in cycle_findings)

    transform["inputBindings"][0]["sourceRef"] = read["operationId"]
    scenario["use_case_specs"][0]["main_scenario"].append({
        "step_number": 3,
        "sentence": "System retains the summary.",
    })
    read["stepRefs"] = ["UC1:main:3"]
    future_findings = class_diagram_findings(result, {"usecase_spec": scenario})
    assert any("operation-result source is future" in finding.message for finding in future_findings)


def test_same_step_scalar_peers_only_bind_from_the_established_frontier(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "name": "Coordinate values", "primary_actor": "Requester"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Requester submits a shared value."},
                {"step_number": 2, "sentence": "System coordinates the values."},
            ],
            "extensions": [],
        }],
        "relationships": {"associations": [{"actor": "Requester", "use_case": "Coordinate values"}]},
    }
    skeleton = {
        "Classes": [
            {"className": "InputBoundary", "stereotype": "Boundary", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "FlowControl", "stereotype": "Control", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "StateStore", "stereotype": "Entity", "fields": [], "methods": [], "use_case_ids": ["UC1"]},
        ],
        "Relationships": [
            {"source": "InputBoundary", "target": "FlowControl", "type": "Dependency"},
            {"source": "FlowControl", "target": "StateStore", "type": "Dependency"},
        ],
    }
    proposal = {"Classes": [
        {"className": "InputBoundary", "operations": [{
            "name": "begin", "parameters": [{"name": "shared", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "FlowControl", "operations": [
            {"name": "first", "parameters": [{"name": "shared", "type": "String"}], "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False},
            {"name": "second", "parameters": [{"name": "shared", "type": "String"}], "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False},
        ]},
        {"className": "StateStore", "operations": [{
            "name": "store", "parameters": [], "returnType": "void",
            "stepRefs": ["UC1:main:2"], "actorEntry": False,
        }]},
    ]}
    selector_operations: list[str] = []

    def parsed(messages, schema, **_kwargs):
        if schema is behavior.BindingChoice:
            payload = __import__("json").loads(messages[1]["content"])
            selector_operations.append(payload["operationId"])
            return {"sourceRef": "FlowControl::first(shared:String)#shared"}
        return proposal

    monkeypatch.setattr(behavior, "parse_structured", parsed)
    result = behavior.enrich_bce_behavior(scenario, skeleton)
    control = next(item for item in result["Classes"] if item["className"] == "FlowControl")
    first = next(item for item in control["operations"] if item["name"] == "first")
    second = next(item for item in control["operations"] if item["name"] == "second")

    assert first["inputBindings"][0]["sourceRef"] == "InputBoundary::begin(shared:String)#shared"
    assert second["inputBindings"][0]["sourceRef"] == first["operationId"] + "#shared"
    assert selector_operations == [second["operationId"]]
    assert class_diagram_findings(result, {"usecase_spec": scenario}) == []

    first["inputBindings"][0]["sourceRef"] = second["operationId"] + "#shared"
    findings = class_diagram_findings(result, {"usecase_spec": scenario})
    assert any("operation input bindings form a cycle" in finding.message for finding in findings)


def test_relationship_artifact_keeps_unassociated_specs_as_execution_roots():
    scenario = {
        "use_cases": [
            {"id": use_case_id, "name": use_case_id, "primary_actor": "Requester"}
            for use_case_id in ("UC1", "UC2", "UC3", "UC4", "UC5")
        ],
        "use_case_specs": [
            {
                "use_case_id": use_case_id,
                "main_scenario": [{
                    "step_number": 1,
                    "sentence": (
                        "System runs the internal action."
                        if use_case_id == "UC4" else "Requester starts the action."
                    ),
                }],
                "extensions": [],
            }
            for use_case_id in ("UC1", "UC2", "UC3", "UC4", "UC5")
        ],
        "relationships": {
            "associations": [{"actor": "Requester", "use_case": "UC1"}],
            "includes": [{"base_use_case": "UC1", "included_use_case": "UC2"}],
            "extends": [
                {"base_use_case": "UC1", "extending_use_case": "UC3"},
                {"base_use_case": "UC1", "extending_use_case": "UC4"},
            ],
        },
    }

    groups = behavior.execution_groups(scenario)

    assert {group.use_case_id for group in groups if not group.internal} == {"UC1", "UC3", "UC5"}
    assert {group.use_case_id for group in groups if group.internal} == {"UC2", "UC4"}


def test_consecutive_actor_steps_share_one_request_segment_until_a_system_response():
    scenario = {
        "use_cases": [{"id": "UC1", "name": "Adjust request", "primary_actor": "Requester"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Requester provides a code."},
                {"step_number": 2, "sentence": "Requester selects an option."},
                {"step_number": 3, "sentence": "System shows the current result."},
                {"step_number": 4, "sentence": "Requester provides an adjustment."},
                {"step_number": 5, "sentence": "System confirms the update."},
            ],
            "extensions": [],
        }],
    }

    groups = behavior.execution_groups(scenario)

    assert [(group.actor_step, group.step_ids) for group in groups] == [
        ("UC1:main:1", ("UC1:main:1", "UC1:main:2", "UC1:main:3")),
        ("UC1:main:4", ("UC1:main:4", "UC1:main:5")),
    ]


def test_entity_in_use_case_scope_does_not_force_a_segment_entity_call(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "name": "Check request", "primary_actor": "Requester"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Requester supplies a value."},
                {"step_number": 2, "sentence": "System validates the supplied value."},
            ],
            "extensions": [],
        }],
    }
    skeleton = {
        "Classes": [
            {"className": "InputBoundary", "stereotype": "Boundary", "use_case_ids": ["UC1"]},
            {"className": "FlowControl", "stereotype": "Control", "use_case_ids": ["UC1"]},
            {"className": "SharedState", "stereotype": "Entity", "use_case_ids": ["UC1"]},
        ],
        "Relationships": [{"source": "InputBoundary", "target": "FlowControl", "type": "Dependency"}],
    }
    proposal = {"Classes": [
        {"className": "InputBoundary", "operations": [{
            "name": "receive", "parameters": [{"name": "value", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:1"], "actorEntry": True,
        }]},
        {"className": "FlowControl", "operations": [{
            "name": "validate", "parameters": [{"name": "value", "type": "String"}],
            "returnType": "void", "stepRefs": ["UC1:main:2"], "actorEntry": False,
        }]},
    ]}
    monkeypatch.setattr(behavior, "parse_structured", lambda _messages, _schema, **_kwargs: proposal)

    result = behavior.enrich_bce_behavior(scenario, skeleton)

    control = next(item for item in result["Classes"] if item["className"] == "FlowControl")
    entity = next(item for item in result["Classes"] if item["className"] == "SharedState")
    assert control["operations"]
    assert entity["operations"] == []
    assert behavior.group_outcomes(result)[0].status == "accepted"


def test_cross_group_signature_conflict_gets_one_local_repair(monkeypatch):
    scenario = {
        "use_cases": [
            {"id": "UC1", "name": "First request", "primary_actor": "Requester"},
            {"id": "UC2", "name": "Second request", "primary_actor": "Requester"},
        ],
        "use_case_specs": [
            {
                "use_case_id": use_case_id,
                "main_scenario": [
                    {"step_number": 1, "sentence": "Requester starts the request."},
                    {"step_number": 2, "sentence": "System records the state."},
                ],
                "extensions": [],
            }
            for use_case_id in ("UC1", "UC2")
        ],
    }
    skeleton = {
        "Classes": [
            {"className": "FirstBoundary", "stereotype": "Boundary", "use_case_ids": ["UC1"]},
            {"className": "FirstControl", "stereotype": "Control", "use_case_ids": ["UC1"]},
            {"className": "SecondBoundary", "stereotype": "Boundary", "use_case_ids": ["UC2"]},
            {"className": "SecondControl", "stereotype": "Control", "use_case_ids": ["UC2"]},
            {"className": "SharedStore", "stereotype": "Entity", "use_case_ids": ["UC1", "UC2"]},
        ],
        "Relationships": [
            {"source": "FirstBoundary", "target": "FirstControl", "type": "Dependency"},
            {"source": "FirstControl", "target": "SharedStore", "type": "Dependency"},
            {"source": "SecondBoundary", "target": "SecondControl", "type": "Dependency"},
            {"source": "SecondControl", "target": "SharedStore", "type": "Dependency"},
        ],
    }

    def proposal(use_case_id: str, *, repaired: bool = False) -> dict:
        prefix = "First" if use_case_id == "UC1" else "Second"
        return {"Classes": [
            {"className": f"{prefix}Boundary", "operations": [{
                "name": "start", "parameters": [], "returnType": "void",
                "stepRefs": [f"{use_case_id}:main:1"], "actorEntry": True,
            }]},
            {"className": f"{prefix}Control", "operations": [{
                "name": "handle", "parameters": [], "returnType": "void",
                "stepRefs": [f"{use_case_id}:main:2"], "actorEntry": False,
            }]},
            {"className": "SharedStore", "operations": [{
                "name": "inspectForSecond" if repaired else "inspect",
                "parameters": [], "returnType": "void",
                "stepRefs": [f"{use_case_id}:main:2"], "actorEntry": False,
            }]},
        ]}

    repairs: list[dict] = []

    def parsed(messages, _schema, **_kwargs):
        payload = __import__("json").loads(messages[1]["content"])
        if "repairScope" in payload:
            repairs.append(payload)
            return proposal(payload["useCaseId"], repaired=True)
        return proposal(payload["useCaseId"])

    monkeypatch.setattr(behavior.settings, "design_class_behavior_parallelism", 1)
    monkeypatch.setattr(behavior, "parse_structured", parsed)

    result = behavior.enrich_bce_behavior(scenario, skeleton)

    shared = next(item for item in result["Classes"] if item["className"] == "SharedStore")
    assert {operation["name"] for operation in shared["operations"]} == {
        "inspect", "inspectForSecond"
    }
    assert len(repairs) == 1
    assert all(outcome.status == "accepted" for outcome in behavior.group_outcomes(result))


def test_persisted_parameter_sources_must_match_name_scope_and_execution_order(monkeypatch):
    monkeypatch.setattr(behavior, "parse_structured", lambda _messages, _schema, **_kwargs: _root_proposal())
    result = behavior.enrich_bce_behavior(_scenario(), _skeleton())
    boundary = next(item for item in result["Classes"] if item["className"] == "OrderForm")
    control = next(item for item in result["Classes"] if item["className"] == "OrderControl")
    entity = next(item for item in result["Classes"] if item["className"] == "Order")
    control_operation = control["operations"][0]

    control_operation["inputBindings"][0]["sourceRef"] = (
        boundary["operations"][0]["operationId"] + "#different"
    )
    control["use_case_ids"] = ["UC2"]
    findings = class_diagram_findings(result, {"usecase_spec": _scenario()})
    assert any("exact same parameter name" in finding.message for finding in findings)
    assert any("outside its execution group's use_case_ids scope" in finding.message for finding in findings)

    control["use_case_ids"] = ["UC1"]
    control_operation["inputBindings"][0]["sourceRef"] = entity["operations"][0]["operationId"]
    findings = class_diagram_findings(result, {"usecase_spec": _scenario()})
    assert any("execution order must place Control before reachable Entity" in finding.message for finding in findings)


def test_untagged_entities_are_not_in_behavior_scope():
    skeleton = {
        "Classes": [{
            "className": "SharedStore",
            "stereotype": "Entity",
            "use_case_ids": [],
        }],
    }

    assert behavior._scope_classes(skeleton, "UC1") == []
