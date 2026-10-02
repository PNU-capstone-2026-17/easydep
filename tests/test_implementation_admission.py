from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.implementation.agents.admission as admission
from app.implementation.agents.upstream_gap_tool import UpstreamGap


def test_integration_prompt_accepts_only_explicit_integrated_same_origin_delivery() -> None:
    prompt = admission._INTEGRATION_SYSTEM_PROMPT

    assert "payload.deliveryContract" in prompt
    assert "supplier=browserDocumentOrigin" in prompt
    assert "portBinding=runtime" in prompt
    assert "Do not extend this rule to a separate frontend" in prompt


def test_integration_needs_input_admission_returns_one_upstream_gap() -> None:
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

    assert admission.admit_integration_evidence(
        {}, ["use_case_spec:UC-1"], proposal_call=propose
    ) == UpstreamGap(
        summary="The retry policy is not specified.",
        source_ref="use_case_spec:UC-1",
    )


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


def _write_frontend_routing_evidence(root: Path, api_base: str) -> list[str]:
    files = {
        "application/frontend/package.json": '{"name":"frontend"}\n',
        "application/frontend/.env.example": f"VITE_API_BASE_URL={api_base}\n",
        "application/frontend/src/config.ts": (
            'export const API_BASE_URL=(import.meta.env.VITE_API_BASE_URL??"")'
            ".replace(/\\/$/,'');\n"
        ),
        "application/frontend/src/api.ts": (
            "const defaultApi = new DefaultApi("
            "new Configuration({ basePath: API_BASE_URL }));\n"
        ),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(files)


def _single_generated_http_deployment(count: int = 1) -> dict[str, object]:
    return {
        "generatedApplicationCount": count,
        "workloads": [
            {
                "id": "application",
                "artifact": {"kind": "generatedApplication"},
                "interfaces": [
                    {
                        "id": "http",
                        "protocol": "http",
                        "exposure": "public",
                        "port": None,
                    }
                ],
            }
        ],
        "connections": [],
    }


def test_integration_payload_declares_integrated_relative_same_origin_supplier(
    tmp_path: Path,
) -> None:
    paths = _write_frontend_routing_evidence(tmp_path, "")
    context = {
        "readSourcePaths": paths,
        "deployment": _single_generated_http_deployment(),
    }

    payload = admission.prepare_integration_admission_payload(
        tmp_path,
        {"task_id": "integration", "depends_on": []},
        context,
        ["workload:application"],
    )

    assert payload["deliveryContract"] == {
        "frontendMode": "integrated",
        "apiBaseMode": "sameOriginRelative",
        "supplier": "browserDocumentOrigin",
        "workloadRef": "workload:application",
        "httpInterfaceId": "http",
        "portBinding": "runtime",
    }


@pytest.mark.parametrize(
    ("generated_count", "api_base"),
    [(2, ""), (1, "https://api.example.test"), (1, "//api.example.test")],
)
def test_integration_payload_does_not_auto_admit_ambiguous_or_cross_origin_delivery(
    tmp_path: Path,
    generated_count: int,
    api_base: str,
) -> None:
    paths = _write_frontend_routing_evidence(tmp_path, api_base)
    context = {
        "readSourcePaths": paths,
        "deployment": _single_generated_http_deployment(generated_count),
    }

    payload = admission.prepare_integration_admission_payload(
        tmp_path,
        {"task_id": "integration", "depends_on": []},
        context,
        ["workload:application"],
    )

    assert "deliveryContract" not in payload


def test_integration_payload_rejects_token_only_absolute_api_base_export(
    tmp_path: Path,
) -> None:
    paths = _write_frontend_routing_evidence(tmp_path, "")
    (tmp_path / "application/frontend/src/config.ts").write_text(
        "const hint=import.meta.env.VITE_API_BASE_URL;\n"
        'export const API_BASE_URL="https://api.example.test";\n',
        encoding="utf-8",
    )

    payload = admission.prepare_integration_admission_payload(
        tmp_path,
        {"task_id": "integration", "depends_on": []},
        {
            "readSourcePaths": paths,
            "deployment": _single_generated_http_deployment(),
        },
        ["workload:application"],
    )

    assert "deliveryContract" not in payload


def test_integration_payload_rejects_token_only_absolute_configuration_binding(
    tmp_path: Path,
) -> None:
    paths = _write_frontend_routing_evidence(tmp_path, "")
    (tmp_path / "application/frontend/src/api.ts").write_text(
        "const hint = API_BASE_URL;\n"
        'const defaultApi = new DefaultApi(new Configuration({'
        ' basePath: "https://api.example.test" }));\n',
        encoding="utf-8",
    )

    payload = admission.prepare_integration_admission_payload(
        tmp_path,
        {"task_id": "integration", "depends_on": []},
        {
            "readSourcePaths": paths,
            "deployment": _single_generated_http_deployment(),
        },
        ["workload:application"],
    )

    assert "deliveryContract" not in payload


def test_missing_integration_evidence_error_is_preserved(tmp_path: Path) -> None:
    context = {"readSourcePaths": ["application/missing-source.java"]}
    with (
        patch.object(admission, "_preflight_admission") as preflight,
        pytest.raises(ValueError, match="Missing integration admission evidence"),
    ):
        admission.preflight_semantic_integration(
            tmp_path,
            {"task_id": "integration"},
            context,
            ["use_case_spec:UC-1"],
        )

    preflight.assert_not_called()
