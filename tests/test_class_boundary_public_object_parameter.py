from __future__ import annotations

from types import SimpleNamespace

from app.design.graphs import subgraphs
from app.design.knowledge.detectors import Finding as ArtifactFinding
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.validation.collaboration import (
    CollaborationContext,
    validate_collaboration,
)
from app.design.services.class_diagram.validation.model import validate_class_model


def _scenario() -> dict:
    return {
        "use_cases": [
            {"id": "UC-CHECKOUT", "name": "Submit checkout", "primary_actor": "Buyer"},
            {"id": "UC-QUOTE", "name": "Request quote", "primary_actor": "Buyer"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC-CHECKOUT",
                "main_scenario": [
                    {"step_number": 1, "subject_ref": "buyer", "sentence": "Buyer submits delivery details."},
                ],
                "extensions": [],
            },
            {
                "use_case_id": "UC-QUOTE",
                "main_scenario": [
                    {"step_number": 1, "subject_ref": "buyer", "sentence": "Buyer requests a quote."},
                ],
                "extensions": [],
            },
        ],
        "relationships": {"includes": [], "extends": []},
    }


def _model(step_refs: list[str]) -> dict:
    return {
        "Classes": [{
            "className": "CheckoutBoundary", "stereotype": "Boundary", "fields": [],
            "operations": [{
                "operationId": "CheckoutBoundary::submit(payload:Object)",
                "name": "submit",
                "parameters": [{"name": "payload", "type": "Object", "requiredValueRef": "value-1"}],
                "returnType": "void", "stepRefs": step_refs,
            }],
        }],
        "DataTypes": [],
        "Collaborations": [],
    }


def test_class_gate_reports_exact_object_boundary_parameter_at_its_unique_step_owner() -> None:
    index = build_scenario_index(_scenario())

    report = validate_class_model(_model(["UC-CHECKOUT:main:1"]), index)

    finding = next(
        item for item in report.findings
        if item.rule_id == "class.boundary-public-object-parameter"
    )
    assert finding.location == "UC-CHECKOUT"
    assert "valueObject" in finding.message
    assert finding.requires_user_input is False


def test_class_gate_leaves_multi_owner_object_parameter_unscoped() -> None:
    index = build_scenario_index(_scenario())

    report = validate_class_model(
        _model(["UC-CHECKOUT:main:1", "UC-QUOTE:main:1"]), index,
    )

    assert not any(
        item.rule_id == "class.boundary-public-object-parameter"
        for item in report.findings
    )


def test_object_parameter_finding_repairs_only_its_operation_fragment(monkeypatch) -> None:
    index = build_scenario_index(_scenario())
    calls: list[set[str]] = []
    monkeypatch.setattr(subgraphs, "_class_index", lambda _state: index)
    monkeypatch.setattr(subgraphs, "_stored_class_model", lambda model: model)
    monkeypatch.setattr(
        subgraphs,
        "revise_class_model",
        lambda _model, _index, _feedback, _targets, **kwargs: (
            calls.append(set(kwargs["operation_use_case_ids"]))
            or SimpleNamespace(model_dump=lambda **_kwargs: _model)
        ),
    )

    result = subgraphs._repair_class_batch(
        _model(["UC-CHECKOUT:main:1"]),
        "Repair the deterministic finding.",
        {"usecase_spec": _scenario()},
        {"UC-CHECKOUT"},
        [ArtifactFinding(
            "class.boundary-public-object-parameter",
            "Boundary public parameter uses opaque Object.",
            "UC-CHECKOUT",
        )],
    )

    assert calls == [{"UC-CHECKOUT"}]
    assert result["Classes"][0]["operations"][0]["parameters"][0]["requiredValueRef"] == "value-1"


def test_ancestor_result_finding_uses_per_use_case_collaboration_repair(monkeypatch) -> None:
    index = build_scenario_index(_scenario())
    calls: list[set[str]] = []
    monkeypatch.setattr(subgraphs, "_class_index", lambda _state: index)
    monkeypatch.setattr(subgraphs, "_stored_class_model", lambda model: model)
    monkeypatch.setattr(
        subgraphs,
        "revise_class_model",
        lambda _model, _index, _feedback, _targets, **kwargs: (
            calls.append(set(kwargs["collaboration_use_case_ids"]))
            or SimpleNamespace(model_dump=lambda **_kwargs: _model)
        ),
    )

    subgraphs._repair_class_batch(
        _model(["UC-CHECKOUT:main:1"]),
        "Repair the causally unavailable binding.",
        {"usecase_spec": _scenario()},
        {"UC-CHECKOUT"},
        [ArtifactFinding(
            "class.collaboration.ancestor-result-binding",
            "A nested call cannot use an ancestor result.",
            "UC-CHECKOUT",
        )],
    )

    assert calls == [{"UC-CHECKOUT"}]


def _binding_model(source_call: str) -> dict:
    step_ref = "UC-CHECKOUT:main:1"
    model = {
        "Classes": [
            {
                "className": "CheckoutBoundary", "stereotype": "Boundary", "fields": [],
                "operations": [{
                    "operationId": "CheckoutBoundary::submit()", "name": "submit",
                    "parameters": [], "returnType": "String", "stepRefs": [step_ref],
                }],
            },
            {
                "className": "CheckoutControl", "stereotype": "Control", "fields": [],
                "operations": [{
                    "operationId": "CheckoutControl::prepare()", "name": "prepare",
                    "parameters": [], "returnType": "String", "stepRefs": [step_ref],
                }],
            },
            {
                "className": "CheckoutEntity", "stereotype": "Entity", "fields": [],
                "operations": [{
                    "operationId": "CheckoutEntity::save(payload:String)", "name": "save",
                    "parameters": [{"name": "payload", "type": "String"}],
                    "returnType": "void", "stepRefs": [step_ref],
                }],
            },
        ],
        "DataTypes": [],
        "Collaborations": [],
    }
    calls = [
        {
            "callId": "call-root", "stableId": "stable-root",
            "receiverOperationId": "CheckoutBoundary::submit()", "stepRefs": [step_ref],
            "argumentBindings": [],
        },
        {
            "callId": "call-control", "stableId": "stable-control",
            "receiverOperationId": "CheckoutControl::prepare()", "parentCallId": "call-root",
            "stepRefs": [step_ref], "argumentBindings": [],
        },
        {
            "callId": "call-entity", "stableId": "stable-entity",
            "receiverOperationId": "CheckoutEntity::save(payload:String)",
            "parentCallId": "call-root", "stepRefs": [step_ref],
            "argumentBindings": [{"parameter": "payload", "sourceRef": source_call}],
        },
    ]
    model["Collaborations"] = [{
        "collaborationId": "UC-CHECKOUT", "useCaseIds": ["UC-CHECKOUT"], "calls": calls,
    }]
    return model


def test_class_binding_rejects_result_from_pending_ancestor_call() -> None:
    index = build_scenario_index(_scenario())
    model = _binding_model("stable-root#result")

    report = validate_class_model(model, index)

    assert any(item.rule_id == "class.collaboration.ancestor-result-binding" for item in report.findings)


def test_local_collaboration_check_rejects_result_from_pending_ancestor_call() -> None:
    index = build_scenario_index(_scenario())
    model = _binding_model("stable-root#result")

    report = validate_collaboration(
        model["Collaborations"][0],
        CollaborationContext(index, model, index.use_case("UC-CHECKOUT")),
    )

    assert any(
        item.rule_id == "class.collaboration.ancestor-result-binding"
        for item in report.findings
    )


def test_class_binding_allows_result_from_completed_non_ancestor_call() -> None:
    index = build_scenario_index(_scenario())
    model = _binding_model("stable-control#result")

    report = validate_class_model(model, index)

    assert not any(item.rule_id == "class.collaboration.ancestor-result-binding" for item in report.findings)
