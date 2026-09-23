from __future__ import annotations

import pytest

import app.design.service as design_service
from app.design.service import (
    _deployment_endpoint_question,
    _parse_public_endpoint,
)
from app.design.services.deployment_diagram.digest import workload_graph_structure_digest
from app.design.services.deployment_diagram.normalization import validate_workload_graph


def _state() -> dict:
    graph = {
        "workloads": [
            {
                "id": "api",
                "configuration": [
                    {
                        "id": "external-url",
                        "kind": "endpointBinding",
                        "connectionRef": "api-to-payments",
                        "projection": "url",
                        "value": "",
                    },
                    {
                        "id": "api-to-payments-endpoint-url",
                        "name": "EXISTING_VALUE",
                        "kind": "value",
                        "value": "preserve",
                    },
                ],
            }
        ],
        "externalDependencies": [{"id": "payments"}],
        "connections": [
            {
                "id": "api-to-payments",
                "sourceRef": "api",
                "targetRef": "payments",
                "sourceRefs": ["req:payment-provider"],
            }
        ],
        "issues": [
            {
                "field": "connections.api-to-payments.endpoint",
                "reason": "An external dependency endpoint must be supplied.",
                "classification": "needsInput",
            }
        ],
    }
    return {"deployment_diagram_bundle": {"workloadGraph": graph}}


def test_deployment_endpoint_question_is_pinned_to_one_connection() -> None:
    question = _deployment_endpoint_question("app-1", _state())

    assert question is not None
    resource_question = question["resource_question"]
    assert resource_question["field"] == "connectionEndpoint:api-to-payments"
    assert resource_question["kind"] == "text"
    assert resource_question["sourceRefs"] == ["req:payment-provider"]
    assert resource_question["context"] == {
        "connectionId": "api-to-payments",
        "sourceRef": "api",
        "workloadGraphStructureDigest": workload_graph_structure_digest(
            _state()["deployment_diagram_bundle"]["workloadGraph"]
        ),
    }


def test_initial_deployment_gate_result_carries_endpoint_question(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    monkeypatch.setattr(design_service, "_validate_app_id", lambda _app: None)
    monkeypatch.setattr(design_service, "_require_app_exists", lambda _app: None)
    monkeypatch.setattr(design_service, "_require_active_session", lambda _app: None)
    monkeypatch.setattr(design_service, "session_status", lambda _app: {"stage": "erd"})
    monkeypatch.setattr(design_service, "_load_app", lambda _app: state)
    monkeypatch.setattr(design_service, "design_readiness_report", lambda *_a, **_kw: {"findings": []})
    monkeypatch.setattr(
        design_service,
        "data_execution_mode_decision",
        lambda *_a, **_kw: {"status": "completed"},
    )
    monkeypatch.setattr(
        design_service,
        "resume_design",
        lambda *_a, **_kw: {
            "status": "need_feedback",
            "stage": "deployment_diagram",
            "feedback_prompt": "Review the deployment design.",
        },
    )
    monkeypatch.setattr(design_service, "to_web_response", lambda _state: {})

    result = design_service.resume_design_session("app-1")

    assert result["resource_question"]["field"] == "connectionEndpoint:api-to-payments"
    assert result["stage"] == "deployment_diagram"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://payments.example", ("url", "https://payments.example")),
        ("https://payments.example/v1/api", ("url", "https://payments.example/v1/api")),
        ("payments.example:443", ("hostport", "payments.example")),
        ("[2001:db8::1]:443", ("hostport", "[2001:db8::1]")),
    ],
)
def test_public_endpoint_parser_accepts_plain_endpoints(value: str, expected: tuple[str, str]) -> None:
    assert _parse_public_endpoint(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@payments.example",
        "https://payments.example?token=secret",
        "https://payments.example#secret",
        "payments.example",
        "payments.example:70000",
    ],
)
def test_public_endpoint_parser_rejects_credentials_and_non_endpoint_values(value: str) -> None:
    with pytest.raises(ValueError):
        _parse_public_endpoint(value)


@pytest.mark.parametrize("introduce_invalid_issue", [False, True])
def test_endpoint_answer_updates_only_pinned_source_and_resumes(
    monkeypatch: pytest.MonkeyPatch, introduce_invalid_issue: bool
) -> None:
    state = _state()
    graph = state["deployment_diagram_bundle"]["workloadGraph"]
    graph["workloads"].append(
        {
            "id": "worker",
            "configuration": [
                {
                    "id": "worker-endpoint",
                    "name": "WORKER_ENDPOINT",
                    "kind": "endpointBinding",
                    "connectionRef": "api-to-payments",
                    "projection": "url",
                    "value": "untouched",
                }
            ],
        }
    )
    question = _deployment_endpoint_question("app-1", state)["resource_question"]
    captured: dict = {}
    rebuilt_bundle = {
        "workloadGraph": {
            "issues": [
                {
                    "field": "workloads.api.configuration",
                    "classification": "invalid",
                    "reason": "Configuration ids must be unique.",
                }
            ]
            if introduce_invalid_issue
            else []
        },
        "planningFacts": {},
    }
    saved: list[bool] = []

    monkeypatch.setattr(design_service, "_validate_app_id", lambda _app: None)
    monkeypatch.setattr(design_service, "_require_app_exists", lambda _app: None)
    monkeypatch.setattr(design_service, "_require_active_session", lambda _app: None)
    monkeypatch.setattr(
        design_service, "session_status", lambda _app: {"active": True, "stage": "deployment_diagram"}
    )
    monkeypatch.setattr(design_service, "_load_app", lambda _app: state)

    def build(candidate: dict, *_args, **_kwargs) -> dict:
        captured["graph"] = candidate
        return rebuilt_bundle

    monkeypatch.setattr(design_service, "build_deployment_diagram_bundle", build)
    monkeypatch.setattr(
        design_service,
        "hydrate_deployment_diagram_bundle",
        lambda bundle: {"deployment_diagram_bundle": bundle},
    )
    monkeypatch.setattr(design_service, "deployment_bundle_runtime_puml", lambda _bundle: "runtime")
    monkeypatch.setattr(design_service, "deployment_bundle_provisioning_puml", lambda _bundle: "provisioning")
    monkeypatch.setattr(
        design_service.artifact_repository,
        "save_stage",
        lambda *_a, **_kw: saved.append(True),
    )
    monkeypatch.setattr(design_service, "sync_design_state", lambda *_a, **_kw: None)
    monkeypatch.setattr(design_service, "resume_design_session", lambda _app: {"status": "need_feedback"})

    if introduce_invalid_issue:
        with pytest.raises(ValueError, match="introduced a deployment graph validation issue"):
            design_service.apply_deployment_endpoint_answer_session(
                "app-1", question, "https://payments.example/v1"
            )
        assert saved == []
        return

    result = design_service.apply_deployment_endpoint_answer_session(
        "app-1", question, "https://payments.example/v1"
    )

    assert result == {"status": "need_feedback"}
    assert saved == [True]
    configurations = {
        item["id"]: item
        for workload in captured["graph"]["workloads"]
        for item in workload.get("configuration") or []
    }
    assert configurations["api-to-payments-endpoint-url-2"]["value"] == "https://payments.example/v1"
    assert configurations["api-to-payments-endpoint-url"]["value"] == "preserve"
    assert configurations["worker-endpoint"]["value"] == "untouched"
    endpoint_configuration = configurations["api-to-payments-endpoint-url-2"]
    assert endpoint_configuration["name"] == "PAYMENTS_ENDPOINT"
    normalized = {
        **captured["graph"],
        "schemaVersion": "easydep-workload-graph",
        "workloads": [
            {
                **captured["graph"]["workloads"][0],
                "artifact": {"kind": "generatedApplication"},
                "sourceRefs": ["req:payment-provider"],
            }
        ],
        "externalDependencies": [{"id": "payments", "sourceRefs": ["req:payment-provider"]}],
    }
    name_findings = [
        issue
        for issue in validate_workload_graph(normalized)
        if str(issue.get("field") or "").endswith(".name")
    ]
    assert name_findings == []
    id_findings = [
        issue
        for issue in validate_workload_graph(normalized)
        if issue.get("classification") == "invalid"
        and str(issue.get("field") or "").endswith(".configuration")
    ]
    assert id_findings == []
