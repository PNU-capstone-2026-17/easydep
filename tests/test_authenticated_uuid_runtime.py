from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.design.contracts.application_runtime import (
    SYNTHETIC_UUID_BASIC_USERNAME,
    authenticated_uuid_context_required,
)
from app.design.services.deployment_diagram.template_topology import (
    _apply_application_security,
)
from app.implementation.generation.orchestrator import PrototypeOrchestrator


def _bce(parameter_type: str = "UUID", *, stereotype: str = "Control") -> dict:
    return {
        "Classes": [
            {
                "className": "UnrelatedDomainControl",
                "stereotype": stereotype,
                "operations": [
                    {
                        "operationId": "legacy",
                        "name": "loadForCurrentActor",
                        "parameters": [{"name": "actorKey", "type": parameter_type}],
                        "returnType": "void",
                    }
                ],
            }
        ],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    }


def _api_model(source: str = "$context.authenticatedPrincipal") -> dict:
    return {
        "Endpoints": [
            {
                "path": "/records/me",
                "method": "get",
                "control_binding": {
                    "control": "UnrelatedDomainControl",
                    "method": "loadForCurrentActor",
                    "arguments": [{"name": "actorKey", "source": source}],
                },
            }
        ],
        "Schemas": [],
    }


def _openapi(source: str = "$context.authenticatedPrincipal") -> dict:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/records/me": {
                "get": {
                    "x-easydep-control": {
                        "control": "UnrelatedDomainControl",
                        "method": "loadForCurrentActor",
                        "arguments": {"actorKey": source},
                        "outcomes": {},
                    }
                }
            }
        },
    }


def test_uuid_context_detector_uses_exact_typed_binding_not_domain_names() -> None:
    assert authenticated_uuid_context_required(_api_model(), _bce()) is True
    assert authenticated_uuid_context_required(_openapi(), _bce()) is True


@pytest.mark.parametrize(
    ("source", "parameter_type", "stereotype"),
    [
        ("request.actorKey", "UUID", "Control"),
        ("$context.authenticatedPrincipal", "String", "Control"),
        ("$context.authenticatedPrincipal", "UUID", "Entity"),
        ("$context.", "UUID", "Control"),
    ],
)
def test_uuid_context_detector_ignores_nonmatching_binding(
    source: str, parameter_type: str, stereotype: str
) -> None:
    assert (
        authenticated_uuid_context_required(
            _api_model(source), _bce(parameter_type, stereotype=stereotype)
        )
        is False
    )


def test_deployment_username_uses_uuid_only_when_structural_detector_matches() -> None:
    graph = {
        "workloads": [
            {
                "id": "application",
                "artifact": {"kind": "generatedApplication"},
                "configuration": [
                    {
                        "id": "security-username",
                        "name": "SPRING_SECURITY_USER_NAME",
                        "kind": "value",
                        "value": "previous-default",
                        "sourceRefs": [],
                    }
                ],
            }
        ]
    }
    _apply_application_security(
        graph,
        {"apiSpec": _openapi(), "classModel": _bce(), "refinedRequirements": []},
    )
    username = next(
        item
        for item in graph["workloads"][0]["configuration"]
        if item["name"] == "SPRING_SECURITY_USER_NAME"
    )
    assert username["value"] == SYNTHETIC_UUID_BASIC_USERNAME


def test_deployment_username_retains_existing_value_for_other_apps() -> None:
    graph = {
        "workloads": [
            {
                "id": "application",
                "artifact": {"kind": "generatedApplication"},
                "configuration": [],
            }
        ]
    }
    _apply_application_security(
        graph,
        {
            "apiSpec": {},
            "classModel": {},
            "refinedRequirements": [{"id": "R1", "text": "Authentication is required."}],
        },
    )
    username = next(
        item
        for item in graph["workloads"][0]["configuration"]
        if item["name"] == "SPRING_SECURITY_USER_NAME"
    )
    assert username["value"] == "easydep"


def test_generated_test_config_and_actor_provider_use_fixed_uuid() -> None:
    with tempfile.TemporaryDirectory(prefix="easydep-uuid-runtime-") as temp_dir:
        root = Path(temp_dir)
        api_path = root / "api-model.json"
        bce_path = root / "bce-model.json"
        api_path.write_text(json.dumps(_api_model()), encoding="utf-8")
        bce_path.write_text(json.dumps(_bce()), encoding="utf-8")
        spec = SimpleNamespace(
            base_package="example.generated",
            name="sample",
            app_id=None,
            inputs={"apiModel": api_path, "bceModel": bce_path},
        )
        application = root / "application"

        PrototypeOrchestrator(spec)._write_runtime_configuration(application)

        test_config = (application / "src/test/resources/application-test.yml").read_text(
            encoding="utf-8"
        )
        assert f"name: {SYNTHETIC_UUID_BASIC_USERNAME}" in test_config
        provider_path = (
            application / "src/main/java/example/generated/config/AuthenticatedActorIdProvider.java"
        )
        provider = provider_path.read_text(encoding="utf-8")
        assert "public UUID currentActorId()" in provider
        assert "SecurityContextHolder.getContext().getAuthentication()" in provider
        assert "authentication == null || !authentication.isAuthenticated()" in provider
        assert "UUID.fromString(principal)" in provider
        assert "actorId.toString().equalsIgnoreCase(principal)" in provider
        assert "valid UUID" in provider
        assert "@Component" in provider
        assert "request.getParameter" not in provider


def test_other_generated_app_keeps_security_defaults_without_uuid_provider() -> None:
    with tempfile.TemporaryDirectory(prefix="easydep-uuid-runtime-") as temp_dir:
        root = Path(temp_dir)
        openapi_path = root / "openapi.json"
        openapi_path.write_text(
            json.dumps({"components": {"securitySchemes": {"basic": {"type": "http"}}}}),
            encoding="utf-8",
        )
        spec = SimpleNamespace(
            base_package="example.generated",
            name="sample",
            app_id=None,
            inputs={"openapi": openapi_path},
        )
        application = root / "application"

        PrototypeOrchestrator(spec)._write_runtime_configuration(application)

        test_config = (application / "src/test/resources/application-test.yml").read_text(
            encoding="utf-8"
        )
        assert "name: easydep-test" in test_config
        assert not (
            application / "src/main/java/example/generated/config/AuthenticatedActorIdProvider.java"
        ).exists()
