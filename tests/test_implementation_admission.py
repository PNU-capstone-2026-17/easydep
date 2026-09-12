import json
from pathlib import Path
from types import SimpleNamespace

import app.implementation.agents.admission as admission
from app.implementation.agents.admission import admit_behavior_capsule
from app.implementation.agents.upstream_gap_tool import UpstreamGap, UpstreamGapOption


def test_prompt_treats_runtime_transport_as_implementation_only_after_semantics() -> None:
    prompt = admission._SYSTEM_PROMPT

    assert "semantic source and trust boundary" in prompt
    assert "exact framework/runtime transport is not\nspecified" in prompt
    assert "Never infer a semantic source" in prompt
    assert "does not also need a method parameter or context accessor" in prompt
    assert "caller-controlled input" in prompt
    assert "designEvidence is natural-language evidence" in prompt
    assert "identifier" in prompt
    assert "mapping" in prompt
    assert "Do not require an ontology" in prompt
    assert "exact class reference" in prompt
    assert "does not by itself\ndeclare how domain records" in prompt
    assert "relation, match key, or semantic source" in prompt
    assert "treat\nthe capsule as a legacy context" in prompt
    assert "preflightFindings are deterministic evidence" in prompt
    assert "Every option must be resolvable by revising that same source reference" in prompt
    assert "Do not make an option depend on an undeclared class" in prompt
    assert "domain matching rule itself is not specified" in prompt


def test_implement_admission_uses_admission_connection_and_low_budget(monkeypatch) -> None:
    calls = []
    connection = SimpleNamespace(model="@cf/zai-org/glm-5.3-flash")
    monkeypatch.setattr(admission, "build_admission_llm_connection", lambda: connection)

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
        "connection": connection,
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
            "options": [
                {
                    "id": "retry-immediate",
                    "label": "Retry immediately",
                    "description": "Retry the operation without waiting.",
                    "requested_effect": "Declare immediate retry as the policy.",
                },
                {
                    "id": "retry-backoff",
                    "label": "Retry with backoff",
                    "description": "Wait between retry attempts.",
                    "requested_effect": "Declare bounded backoff before retry.",
                },
            ],
        }

    assert admit_behavior_capsule(
        {},
        ["use_case:UC-1", "api:retry", "use_case_spec:UC-1"],
        proposal_call=propose,
    ) == UpstreamGap(
        summary="The retry policy is not specified.",
        source_ref="use_case_spec:UC-1",
        options=(
            UpstreamGapOption(
                id="retry-immediate",
                label="Retry immediately",
                description="Retry the operation without waiting.",
                requested_effect="Declare immediate retry as the policy.",
            ),
            UpstreamGapOption(
                id="retry-backoff",
                label="Retry with backoff",
                description="Wait between retry attempts.",
                requested_effect="Declare bounded backoff before retry.",
            ),
        ),
    )
    assert json.loads(calls[0][1]["content"])["sourceRefs"] == [
        "api:retry",
        "use_case_spec:UC-1",
    ]
    assert admission.admit_integration_evidence(
        {}, ["use_case_spec:UC-1"], proposal_call=propose
    ) == UpstreamGap(
        summary="The retry policy is not specified.",
        source_ref="use_case_spec:UC-1",
    )


def test_preflight_reuses_exact_checkpoint_and_invalidates_changed_input(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []
    monkeypatch.setattr(
        admission,
        "build_admission_llm_connection",
        lambda: SimpleNamespace(model="glm"),
    )

    expected_gap = UpstreamGap(
        summary="The operation has no carrier for the required actor.",
        source_ref="operation:Note::create()",
        options=(
            UpstreamGapOption(
                id="trusted-context",
                label="Use trusted context",
                description="Read the actor from the authenticated platform context.",
                requested_effect="Declare the actor as trusted platform context.",
            ),
            UpstreamGapOption(
                id="caller-input",
                label="Accept caller input",
                description="Add the actor as a caller-provided value.",
                requested_effect="Declare the actor as an explicit caller input.",
            ),
        ),
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
    checkpoint = json.loads(
        (tmp_path / "reports/agent-executions/behavior-1.admission.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["upstreamGap"]["options"][0]["requestedEffect"] == (
        "Declare the actor as trusted platform context."
    )

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


def test_integration_preflight_hashes_exact_file_evidence_without_persisting_it(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        admission,
        "build_admission_llm_connection",
        lambda: SimpleNamespace(model="glm"),
    )
    first = tmp_path / "application/frontend/src/api.ts"
    second = tmp_path / "application/src/main/resources/application.yml"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"const token = 'raw-secret';\r\n")
    second.write_bytes(b"role: USER\n")
    context = {
        "readSourcePaths": [
            "application/frontend/src/api.ts",
            "application/src/main/resources/application.yml",
        ],
        "traceEvidence": {"ownerTaskIds": ["backend", "frontend"]},
        "deployment": {"provider": "local"},
    }
    refs = ["use_case_spec:UC-1", "operation:Course::enroll()"]
    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(
        admission,
        "admit_integration_evidence",
        lambda payload, _refs: payloads.append(payload) or None,
    )
    task = {"task_id": "implement-vertical-integration"}

    assert admission.preflight_semantic_integration(tmp_path, task, context, refs) is None
    assert admission.preflight_semantic_integration(tmp_path, task, context, refs) is None
    assert len(payloads) == 1
    assert payloads[0] == {
        "traceEvidence": context["traceEvidence"],
        "deployment": context["deployment"],
        "evidenceFiles": [
            {
                "path": "application/frontend/src/api.ts",
                "content": "const token = 'raw-secret';\r\n",
            },
            {
                "path": "application/src/main/resources/application.yml",
                "content": "role: USER\n",
            },
        ],
        "sourceRefs": refs,
    }
    checkpoint_path = (
        tmp_path
        / "reports/agent-executions/implement-vertical-integration.admission.json"
    )
    first_checkpoint = checkpoint_path.read_text(encoding="utf-8")
    assert "raw-secret" not in first_checkpoint
    assert "role: USER" not in first_checkpoint

    second.write_bytes(b"role: PROFESSOR\n")
    assert admission.preflight_semantic_integration(tmp_path, task, context, refs) is None
    assert len(payloads) == 2
    assert checkpoint_path.read_text(encoding="utf-8") != first_checkpoint
