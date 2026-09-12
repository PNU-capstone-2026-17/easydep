import json
from pathlib import Path
from types import SimpleNamespace

import app.implementation.agents.admission as admission
from app.implementation.agents.admission import admit_behavior_capsule
from app.implementation.agents.upstream_gap_tool import UpstreamGap


def test_implement_admission_returns_no_gap_and_uses_low_budget() -> None:
    calls = []

    def propose(messages, schema, **kwargs):
        calls.append((messages, schema, kwargs))
        return {"decision": "IMPLEMENT", "summary": "connected", "source_ref": ""}

    assert admit_behavior_capsule(
        {"behaviorCapsule": {"directMethods": []}}, ["UC-1"], proposal_call=propose
    ) is None
    assert calls[0][2] == {
        "reasoning_effort": "low",
        "max_completion_tokens": 2048,
        "operation": "implementation-admission",
    }
    payload = json.loads(calls[0][0][1]["content"])
    assert payload == {"behaviorCapsule": {"directMethods": []}, "sourceRefs": ["UC-1"]}


def test_needs_input_admission_returns_one_upstream_gap() -> None:
    calls = []

    def propose(_messages, _schema, **_kwargs):
        calls.append(_messages)
        return {
            "decision": "NEEDS_INPUT",
            "summary": "  The retry policy is not specified.  ",
            "source_ref": "use_case_spec:UC-1",
        }

    assert admit_behavior_capsule(
        {},
        ["use_case:UC-1", "api:retry", "use_case_spec:UC-1"],
        proposal_call=propose,
    ) == UpstreamGap(
        summary="The retry policy is not specified.", source_ref="use_case_spec:UC-1"
    )
    assert json.loads(calls[0][1]["content"])["sourceRefs"] == [
        "api:retry",
        "use_case_spec:UC-1",
    ]


def test_preflight_reuses_exact_checkpoint_and_invalidates_changed_input(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []
    monkeypatch.setattr(
        admission, "build_llm_connection", lambda: SimpleNamespace(model="glm")
    )

    expected_gap = UpstreamGap(
        summary="The operation has no carrier for the required actor.",
        source_ref="operation:Note::create()",
    )

    def propose(context, source_refs):
        calls.append((context, source_refs))
        return expected_gap if not context["behaviorCapsule"].get("directMethods") else None

    monkeypatch.setattr(admission, "admit_behavior_capsule", propose)
    task = {"task_id": "behavior-1"}
    context = {"behaviorCapsule": {"directMethods": []}}
    refs = ["use_case_spec:UC-1", "operation:Note::create()"]

    assert admission.preflight_semantic_behavior(tmp_path, task, context, refs) == expected_gap
    assert admission.preflight_semantic_behavior(tmp_path, task, context, refs) == expected_gap
    assert len(calls) == 1

    result_path = tmp_path / "reports/agent-executions/behavior-1.result.json"
    result_path.write_text(json.dumps({"status": "NEEDS_INPUT"}), encoding="utf-8")
    changed_context = {"behaviorCapsule": {"directMethods": [{"method": {}}]}}
    assert admission.preflight_semantic_behavior(tmp_path, task, changed_context, refs) is None
    assert admission.preflight_semantic_behavior(tmp_path, task, changed_context, refs) is None
    assert len(calls) == 2
    assert not result_path.exists()

    checkpoint = json.loads(
        (tmp_path / "reports/agent-executions/behavior-1.admission.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["decision"] == "IMPLEMENT"
