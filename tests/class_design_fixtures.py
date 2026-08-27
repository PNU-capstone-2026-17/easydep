"""Small synthetic class-design inputs shared by focused tests.

The fixtures intentionally describe the contracts at the service boundary. A
test should be able to exercise class inventory, operation, collaboration, and
sequence projection behavior without reaching into orchestration internals.
"""
from __future__ import annotations


def scenario() -> dict:
    return {
        "use_cases": [
            {"id": "UC1", "name": "Manage order", "primary_actor": "Customer"},
            {"id": "UC2", "name": "Validate order", "primary_actor": ""},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "subject_ref": "Customer", "sentence": "Customer submits an order."},
                    {"step_number": 2, "subject_ref": "System", "sentence": "System validates the order."},
                    {"step_number": 3, "subject_ref": "System", "sentence": "System returns the result."},
                    {"step_number": 4, "subject_ref": "Customer", "sentence": "Customer requests the receipt."},
                    {"step_number": 5, "subject_ref": "System", "sentence": "System returns the receipt."},
                ],
                "extensions": [{
                    "label": "2a",
                    "branch_step": 2,
                    "condition": "The order is invalid",
                    "handling_steps": [{
                        "sub_step": "2a1",
                        "subject_ref": "System",
                        "sentence": "System returns the rejection.",
                    }],
                }],
            },
            {
                "use_case_id": "UC2",
                "main_scenario": [{
                    "step_number": 1,
                    "subject_ref": "System",
                    "sentence": "System checks order constraints.",
                }],
                "extensions": [],
            },
        ],
        "relationships": {
            "includes": [{
                "base_use_case_id": "UC1",
                "included_use_case_id": "UC2",
                "step_refs": [{"use_case_id": "UC1", "step_ref": "main:2"}],
            }],
            "extends": [],
        },
    }


def single_use_case() -> dict:
    return {
        "use_cases": [{
            "id": "UC1", "name": "Submit request", "primary_actor": "Member",
        }],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "subject_ref": "Member", "sentence": "Member submits a request."},
                {"step_number": 2, "subject_ref": "System", "sentence": "System returns the result."},
            ],
            "extensions": [],
        }],
        "relationships": {"includes": [], "extends": []},
    }


def inventory_proposal() -> dict:
    return {
        "items": [
            {"name": "RequestBoundary", "kind": "Boundary", "description": "Member interface", "fields": [], "identifier": [], "values": [], "useCaseIds": ["UC1"]},
            {"name": "RequestControl", "kind": "Control", "description": "Request coordination", "fields": [], "identifier": [], "values": [], "useCaseIds": ["UC1"]},
        ],
        "Relationships": [],
    }


def operation_fragment(*, unsourceable: bool = False) -> dict:
    control_parameter = (
        {"name": "other", "type": "Boolean"}
        if unsourceable
        else {"name": "request", "type": "RequestData"}
    )
    return {
        "DataTypes": [
            {
                "name": "RequestData",
                "kind": "valueObject",
                "fields": [{"name": "value", "type": "String"}],
                "values": [],
            },
            {
                "name": "RequestResult",
                "kind": "valueObject",
                "fields": [{"name": "accepted", "type": "Boolean"}],
                "values": [],
            },
        ],
        "Classes": [
            {"className": "RequestBoundary", "operations": [{
                "name": "submit",
                "parameters": [{"name": "request", "type": "RequestData"}],
                "returnType": "RequestResult",
                "stepRefs": ["UC1:main:1", "UC1:main:2"],
            }]},
            {"className": "RequestControl", "operations": [{
                "name": "process",
                "parameters": [control_parameter],
                "returnType": "RequestResult",
                "stepRefs": ["UC1:main:2"],
            }]},
        ],
    }


def call_plan() -> dict:
    return {
        "calls": [
            {
                "receiverOperationId": "RequestBoundary::submit(request:RequestData)",
                "parentCallIndex": None,
            },
            {
                "receiverOperationId": "RequestControl::process(request:RequestData)",
                "parentCallIndex": 1,
            },
        ],
    }


def valid_parse_response(_messages, schema, **_kwargs):
    """Return the standard synthetic response for a structured parser stub."""

    from app.design.services.class_diagram.proposals import (
        CallPlanProposal,
        InventoryProposal,
        OperationFragment,
    )

    if schema is InventoryProposal:
        return inventory_proposal()
    if schema is OperationFragment:
        return operation_fragment()
    if issubclass(schema, CallPlanProposal):
        return call_plan()
    raise AssertionError(schema)


def patch_class_design_parser(monkeypatch, parser):
    """Inject one deterministic parser at every class-design stage."""

    from app.design.services.class_diagram import (
        collaboration,
        feedback,
        inventory,
        operations,
    )

    for module in (collaboration, feedback, inventory, operations):
        monkeypatch.setattr(module, "parse_structured", parser)


def typed_class_model_payload() -> dict:
    """A small accepted BCE model with deliberately stale generated IDs."""

    return {
        "Classes": [
            {
                "className": "OrderBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "stale-boundary-id",
                    "name": "submit",
                    "parameters": [{"name": "request", "type": "OrderRequest"}],
                    "returnType": "Receipt",
                    "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "OrderControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "stale-control-id",
                    "name": "place",
                    "parameters": [{"name": "request", "type": "OrderRequest"}],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [{
            "name": "OrderRequest",
            "kind": "valueObject",
            "fields": ["sku : String"],
        }, {
            "name": "Receipt",
            "kind": "enumeration",
            "values": ["ACCEPTED", "REJECTED"],
        }],
        "Relationships": [],
        "Collaborations": [{
            "collaborationId": "place-order",
            "useCaseIds": ["UC1"],
            "entryActor": "Buyer",
            "calls": [{
                "callId": "stale-call-id",
                "receiverOperationId": "OrderBoundary::submit(request:OrderRequest)",
                "stepRefs": ["UC1:main:1"],
                "argumentBindings": [{
                    "parameter": "request", "sourceRef": "UC1:main:1#request",
                }],
            }, {
                "callId": "another-stale-call-id",
                "parentCallId": "place-order::call:1",
                "receiverOperationId": "OrderControl::place(request:OrderRequest)",
                "stepRefs": ["UC1:main:2"],
                "argumentBindings": [{
                    "parameter": "request", "sourceRef": "place-order::call:1#request",
                }],
            }],
        }],
    }


def typed_sequence_model_payload() -> dict:
    """A valid persisted sequence collection for checkpoint round-trips."""

    return {
        "Diagrams": [{
            "use_case_id": "UC1",
            "use_case_name": "Place order",
            "Participants": [{
                "name": "Buyer",
                "alias": "Buyer",
                "kind": "actor",
                "source_class": "",
            }, {
                "name": "OrderBoundary",
                "alias": "OrderBoundary",
                "kind": "boundary",
                "source_class": "OrderBoundary",
            }],
            "Messages": [{
                "source": "Buyer",
                "target": "OrderBoundary",
                "label": "submit(request:OrderRequest)",
                "type": "sync",
                "use_case_ids": ["UC1"],
                "step_ids": ["UC1:main:1"],
                "call_id": "place-order::call:1",
                "arguments": [{
                    "parameter": "request",
                    "type": "OrderRequest",
                    "source_kind": "input",
                    "source_ref": "UC1:main:1#request",
                }],
            }, {
                "source": "OrderBoundary",
                "target": "Buyer",
                "label": "Receipt",
                "type": "return",
                "use_case_ids": ["UC1"],
                "step_ids": ["UC1:main:1"],
                "reply_to": "place-order::call:1",
            }],
            "UnresolvedSteps": [],
            "NarrativeSteps": [],
        }],
        "class_diagram_hash": "class-hash",
        "MethodProposals": [],
    }
