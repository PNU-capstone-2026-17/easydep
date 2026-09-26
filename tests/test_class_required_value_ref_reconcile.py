from __future__ import annotations

from app.design.graphs.subgraphs import _propagate_bound_required_value_refs


def _bound_model() -> dict:
    return {
        "Classes": [{
            "className": "SubmitBoundary", "stereotype": "Boundary", "fields": [], "operations": [{
                "stableId": "op-boundary", "operationId": "SubmitBoundary::submit(details:RequestData)",
                "name": "submit", "parameters": [{"name": "details", "type": "RequestData", "stableRef": "param-details", "requiredValueRef": "val-1"}],
                "returnType": "void", "stepRefs": ["UC1:main:1"],
            }],
        }, {
            "className": "SubmitControl", "stereotype": "Control", "fields": [], "operations": [{
                "stableId": "op-control", "operationId": "SubmitControl::submit(details:RequestData)",
                "name": "submit", "parameters": [{"name": "details", "type": "RequestData", "stableRef": "param-details-control"}],
                "returnType": "Receipt", "stepRefs": ["UC1:main:1"],
            }],
        }],
        "DataTypes": [
            {"name": "RequestData", "kind": "valueObject", "fields": ["value : String"]},
            {"name": "Receipt", "kind": "valueObject", "fields": ["value : String"]},
        ],
        "Collaborations": [{"collaborationId": "UC1", "useCaseIds": ["UC1"], "calls": [
            {"callId": "UC1-call-1", "stableId": "call-boundary", "receiverOperationId": "SubmitBoundary::submit(details:RequestData)"},
            {"callId": "UC1-call-2", "stableId": "call-control", "receiverOperationId": "SubmitControl::submit(details:RequestData)", "parentCallId": "UC1-call-1", "argumentBindings": [{"parameter": "details", "sourceRef": "call-boundary#param-details"}]},
        ]}],
    }


def test_reconcile_propagates_exact_boundary_binding_ref_to_control_parameter() -> None:
    repaired = _propagate_bound_required_value_refs({"extracted_bce_classes": _bound_model()})

    parameter = repaired["extracted_bce_classes"]["Classes"][1]["operations"][0]["parameters"][0]
    assert parameter["requiredValueRef"] == "val-1"


def test_reconcile_does_not_infer_ref_when_binding_does_not_name_boundary_parameter() -> None:
    model = _bound_model()
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0]["sourceRef"] = (
        "call-boundary#not-the-boundary-parameter"
    )

    assert _propagate_bound_required_value_refs({"extracted_bce_classes": model}) == {}
    assert "requiredValueRef" not in model["Classes"][1]["operations"][0]["parameters"][0]


def test_reconcile_does_not_choose_between_conflicting_exact_binding_refs() -> None:
    model = _bound_model()
    model["Classes"].append({
        "className": "AlternateBoundary", "stereotype": "Boundary", "fields": [], "operations": [{
            "operationId": "AlternateBoundary::submit(details:RequestData)", "name": "submit",
            "parameters": [{"name": "details", "type": "RequestData", "stableRef": "alternate-details", "requiredValueRef": "val-2"}],
            "returnType": "void", "stepRefs": ["UC2:main:1"],
        }],
    })
    model["Collaborations"].append({"collaborationId": "UC2", "useCaseIds": ["UC2"], "calls": [
        {"callId": "UC2-call-1", "stableId": "call-alternate-boundary", "receiverOperationId": "AlternateBoundary::submit(details:RequestData)"},
        {"callId": "UC2-call-2", "stableId": "call-alternate-control", "receiverOperationId": "SubmitControl::submit(details:RequestData)", "argumentBindings": [{"parameter": "details", "sourceRef": "call-alternate-boundary#alternate-details"}]},
    ]})

    assert _propagate_bound_required_value_refs({"extracted_bce_classes": model}) == {}


def test_reconcile_ignores_ambiguous_duplicate_operation_id() -> None:
    model = _bound_model()
    duplicate = dict(model["Classes"][1])
    duplicate["className"] = "DuplicateControl"
    model["Classes"].append(duplicate)

    assert _propagate_bound_required_value_refs({"extracted_bce_classes": model}) == {}
