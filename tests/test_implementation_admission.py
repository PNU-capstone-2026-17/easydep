import json

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
