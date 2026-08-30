"""설계 validator 테스트가 공유하는 작은 주문 도메인 입력."""

from __future__ import annotations

import copy
from typing import Any

CLEAN_STATE: dict[str, Any] = {
    "usecase_spec": {
        "use_cases": [
            {"id": "UC1", "name": "Place an order", "primary_actor": "Member"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Member submits the order."},
                    {"step_number": 2, "sentence": "System records the order."},
                ],
                "extensions": [],
            }
        ],
        "relationships": {
            "associations": [{"actor": "Member", "use_case": "Place an order"}],
        },
    }
}

CLEAN: dict[str, Any] = {
    "Classes": [
        {
            "className": "OrderForm",
            "stereotype": "Boundary",
            "description": "Collects the order request from the member.",
            "fields": [],
            "use_case_ids": ["UC1"],
            "operations": [
                {
                    "operationId": "OrderForm::submitOrder(request:String)",
                    "name": "submitOrder",
                    "parameters": [{"name": "request", "type": "String"}],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:1"],
                }
            ],
        },
        {
            "className": "OrderController",
            "stereotype": "Control",
            "description": "Coordinates availability check and order recording.",
            "fields": [],
            "use_case_ids": ["UC1"],
            "operations": [
                {
                    "operationId": "OrderController::placeOrder(request:String)",
                    "name": "placeOrder",
                    "parameters": [{"name": "request", "type": "String"}],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:2"],
                }
            ],
        },
        {
            "className": "Order",
            "stereotype": "Entity",
            "description": "The recorded order.",
            "fields": ["orderedAt : DateTime", "totalAmount : Int"],
            "use_case_ids": ["UC1"],
        },
    ],
    "Relationships": [
        {"source": "OrderForm", "target": "OrderController", "type": "Dependency"},
    ],
    "Collaborations": [
        {
            "collaborationId": "UC1",
            "useCaseIds": ["UC1"],
            "entryActor": "Member",
            "calls": [
                {
                    "callId": "UC1::call:1",
                    "receiverOperationId": "OrderForm::submitOrder(request:String)",
                    "stepRefs": ["UC1:main:1"],
                    "argumentBindings": [
                        {
                            "parameter": "request",
                            "sourceRef": "UC1:main:1#request",
                        }
                    ],
                },
                {
                    "callId": "UC1::call:2",
                    "parentCallId": "UC1::call:1",
                    "receiverOperationId": "OrderController::placeOrder(request:String)",
                    "stepRefs": ["UC1:main:2"],
                    "argumentBindings": [
                        {
                            "parameter": "request",
                            "sourceRef": "UC1::call:1#request",
                        }
                    ],
                },
            ],
        }
    ],
}

ERD_CLEAN: dict[str, Any] = {
    "Classes": [
        {
            "className": "OrderController",
            "stereotype": "Control",
            "description": "Coordinates order recording.",
            "fields": [],
            "use_case_ids": ["UC1"],
        },
        {
            "className": "Member",
            "stereotype": "Entity",
            "description": "The account that places orders.",
            "fields": ["email : String", "displayName : String"],
            "identifier": ["email"],
            "use_case_ids": ["UC1"],
        },
        {
            "className": "Order",
            "stereotype": "Entity",
            "description": "The recorded order.",
            "fields": ["orderedAt : DateTime", "totalAmount : Int"],
            "identifier": [],
            "use_case_ids": ["UC1"],
        },
    ],
    "Relationships": [
        {
            "source": "OrderController",
            "target": "Order",
            "type": "Dependency",
        },
        {
            "source": "Member",
            "target": "Order",
            "type": "Association",
            "sourceMultiplicity": "1",
            "targetMultiplicity": "*",
        },
    ],
}


def unmapped_erd() -> dict[str, Any]:
    """구조 관계의 다중도만 빼서 사상 실패를 만드는 대표 입력을 반환한다."""
    model = copy.deepcopy(ERD_CLEAN)
    relationship = model["Relationships"][1]
    relationship.pop("sourceMultiplicity")
    relationship.pop("targetMultiplicity")
    return model
