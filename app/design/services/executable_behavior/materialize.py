"""Pure projection from an accepted behavior transaction to the legacy BCE schema."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.design.schemas.class_model import BCEModel
from app.design.services.executable_behavior.contracts import AcceptedBehaviorModel


class MaterializationError(ValueError):
    """The accepted wrapper was changed or cannot project to the legacy schema."""


def materialize_bce_model(accepted: AcceptedBehaviorModel) -> BCEModel:
    """Project calls/bindings without LLM decisions or repair behavior."""
    try:
        accepted = AcceptedBehaviorModel.model_validate(
            accepted.model_dump(by_alias=True)
        )
    except ValueError as error:
        raise MaterializationError(
            "accepted behavior failed integrity validation"
        ) from error
    payload: dict[str, Any] = deepcopy(accepted.catalog.payload)
    payload.setdefault("Classes", [])
    payload.setdefault("DataTypes", [])
    payload.setdefault("Relationships", [])
    collaborations: list[dict[str, Any]] = []
    for witness in accepted.witnesses:
        binding_by_call: dict[str, list[dict[str, Any]]] = {}
        for binding in witness.bindings:
            call_id = str(binding.get("callId") or "")
            binding_by_call.setdefault(call_id, []).append(
                {"parameter": binding.get("parameter"), "sourceRef": binding.get("sourceRef")}
            )
        multiple_groups = len(witness.actor_entries) > 1
        for entry in witness.actor_entries:
            group_key = str(entry.get("groupKey") or "")
            group_calls = [
                call
                for call in witness.calls
                if str(call.get("groupKey") or "") == group_key
            ]
            collaboration_id = (
                f"{witness.use_case_id}::{group_key}"
                if multiple_groups
                else witness.use_case_id
            )
            old_to_new = {
                str(call["callId"]): f"{collaboration_id}::call:{position}"
                for position, call in enumerate(group_calls, start=1)
            }
            calls: list[dict[str, Any]] = []
            for position, call in enumerate(group_calls, start=1):
                raw_id = str(call["callId"])
                parent = call.get("parentCallId")
                calls.append({
                    "callId": f"{collaboration_id}::call:{position}",
                    "stableId": f"{witness.use_case_id}::{raw_id}",
                    "parentCallId": (
                        old_to_new.get(str(parent)) if parent is not None else None
                    ),
                    "receiverOperationId": call["receiverOperationId"],
                    "stepRefs": list(call.get("stepRefs") or []),
                    "argumentBindings": binding_by_call.get(raw_id, []),
                })
            collaborations.append({
                "collaborationId": collaboration_id,
                "useCaseIds": [witness.use_case_id],
                "entryActor": str(entry.get("actor") or "") or None,
                "calls": calls,
            })
    payload["Collaborations"] = collaborations
    try:
        return BCEModel.model_validate(payload)
    except ValueError as error:
        raise MaterializationError("accepted behavior cannot be materialized") from error


__all__ = ["MaterializationError", "materialize_bce_model"]
