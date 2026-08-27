"""수락된 상호작용 모델에서 파생되는 표시용 투영을 제공한다."""
from __future__ import annotations

from typing import Any

from app.design.services.interaction_design.scenario import text
from app.design.services.interaction_design.validation.model import operation_catalog


def project_call_dependencies(model: dict[str, Any]) -> list[dict[str, Any]]:
    """영속 호출 트리에서 클래스 다이어그램용 의존선만 결정론적으로 만든다."""

    owners = {
        operation_id: operation["className"]
        for operation_id, operation in operation_catalog(model).items()
    }
    result: dict[tuple[str, str], dict[str, str]] = {}
    for collaboration in model.get("Collaborations") or []:
        if not isinstance(collaboration, dict):
            continue
        calls = {
            text(call.get("callId")): call
            for call in collaboration.get("calls") or []
            if isinstance(call, dict)
        }
        for call in calls.values():
            parent = calls.get(text(call.get("parentCallId")))
            if not parent:
                continue
            source = owners.get(text(parent.get("receiverOperationId")), "")
            target = owners.get(text(call.get("receiverOperationId")), "")
            if source and target and source != target:
                result[(source, target)] = {
                    "source": source,
                    "target": target,
                    "type": "Dependency",
                }
    return [result[key] for key in sorted(result)]
