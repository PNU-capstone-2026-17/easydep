"""End-to-end deterministic patching against the report's course case."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from app.design.cascade import revise_and_cascade

_REPORT_STATE = (
    Path(__file__).parents[1]
    / "artifacts"
    / "checkpoint-e2e"
    / "current"
    / "e1-aws"
    / "chain"
    / "snapshots"
    / "class_diagram"
    / "state.json"
)


def _by_name(values: list[dict], key: str, expected: str) -> dict:
    return next(value for value in values if value[key] == expected)


def _assert_existing_calls_are_preserved(
    old_calls: list[dict], new_calls: list[dict], inserted_at: int,
) -> None:
    """Compare all existing call data after its position-based ID shifts once."""

    old_to_new = {
        old["callId"]: new_calls[index + (index > inserted_at)]["callId"]
        for index, old in enumerate(old_calls)
    }
    for index, old in enumerate(old_calls):
        actual = new_calls[index + (index > inserted_at)]
        expected = copy.deepcopy(old)
        expected["callId"] = old_to_new[old["callId"]]
        if expected.get("parentCallId") in old_to_new:
            expected["parentCallId"] = old_to_new[expected["parentCallId"]]
        for binding in expected.get("argumentBindings") or []:
            source, separator, suffix = binding["sourceRef"].partition("#")
            if separator and source in old_to_new:
                binding["sourceRef"] = f"{old_to_new[source]}#{suffix}"
        assert actual == expected


def test_report_case_patch_preserves_existing_class_and_call_design() -> None:
    state = json.loads(_REPORT_STATE.read_text(encoding="utf-8"))
    before = copy.deepcopy(state["extracted_bce_classes"])
    revisions = [
        (
            "class_diagram:CourseOffering",
            [{
                "operation": "add_operation",
                "target": "class_diagram:CourseOffering",
                "name": "decrementEnrolledCount",
                "parameters": [],
                "returnType": "void",
                "stepRefs": ["UC3:main:4", "UC5:main:3"],
            }],
        ),
        (
            "class_diagram:UC3:main:1",
            [{
                "operation": "insert_call_after",
                "target": "class_diagram:UC3:main:1",
                # The LLM may use canonical UUID while the legacy checkpoint
                # still spells the persisted receiver parameter as ``uuid``.
                "anchor": "Registration::deleteById(id:UUID)",
                "receiverOperationId": "CourseOffering::decrementEnrolledCount()",
                "stepRefs": ["UC3:main:4"],
                "argumentBindings": [],
            }],
        ),
        (
            "class_diagram:UC5:main:1",
            [{
                "operation": "insert_call_after",
                "target": "class_diagram:UC5:main:1",
                "anchor": "Registration::deleteById(id:UUID)",
                "receiverOperationId": "CourseOffering::decrementEnrolledCount()",
                "stepRefs": ["UC5:main:3"],
                "argumentBindings": [],
            }],
        ),
    ]

    working = state
    for target, patch_intents in revisions:
        result = revise_and_cascade(
            working,
            target,
            "Add and invoke CourseOffering.decrementEnrolledCount after deletion.",
            patch_intents=patch_intents,
        )
        working = result["state"]

    after = working["extracted_bce_classes"]
    before_course = _by_name(before["Classes"], "className", "CourseOffering")
    after_course = _by_name(after["Classes"], "className", "CourseOffering")
    expected_operation = {
        "operationId": "CourseOffering::decrementEnrolledCount()",
        "name": "decrementEnrolledCount",
        "parameters": [],
        "returnType": "void",
        "stepRefs": ["UC3:main:4", "UC5:main:3"],
    }
    assert after_course == {
        **before_course,
        "operations": [*before_course["operations"], expected_operation],
    }
    assert [item for item in after["Classes"] if item["className"] != "CourseOffering"] == [
        item for item in before["Classes"] if item["className"] != "CourseOffering"
    ]

    for collaboration_id in ("UC3:main:1", "UC5:main:1"):
        old_calls = _by_name(
            before["Collaborations"], "collaborationId", collaboration_id
        )["calls"]
        new_calls = _by_name(
            after["Collaborations"], "collaborationId", collaboration_id
        )["calls"]
        old_receivers = [call["receiverOperationId"] for call in old_calls]
        new_receivers = [call["receiverOperationId"] for call in new_calls]
        delete_index = old_receivers.index("Registration::deleteById(id:uuid)")
        assert new_receivers == [
            *old_receivers[: delete_index + 1],
            "CourseOffering::decrementEnrolledCount()",
            *old_receivers[delete_index + 1 :],
        ]
        inserted = new_calls[delete_index + 1]
        assert inserted["parentCallId"] == new_calls[delete_index]["parentCallId"]
        assert inserted["stepRefs"] == [
            "UC3:main:4" if collaboration_id.startswith("UC3") else "UC5:main:3"
        ]
        _assert_existing_calls_are_preserved(old_calls, new_calls, delete_index)

    assert [
        item
        for item in after["Collaborations"]
        if item["collaborationId"] not in {"UC3:main:1", "UC5:main:1"}
    ] == [
        item
        for item in before["Collaborations"]
        if item["collaborationId"] not in {"UC3:main:1", "UC5:main:1"}
    ]
