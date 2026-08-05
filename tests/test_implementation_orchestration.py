from __future__ import annotations

import json

from app.core.orchestration.adapters.design import DesignAdapter
from app.core.orchestration.adapters.implementation import ImplementationAdapter
from app.core.orchestration.adapters.infrastructure import (
    InfrastructureRecommendationAdapter,
)


def test_provisional_recommendation_is_explicitly_unmeasured():
    adapter = InfrastructureRecommendationAdapter(
        lambda _prompt: json.dumps(
            {"vmFamily": "general-purpose", "vmCount": 1, "confidence": "low"}
        )
    )

    result = adapter.recommend(
        requirements_result={"resource_spec": {"provider": "aws"}},
        cloud_design_result={"dependency_plan": {}, "deferred": ["price"]},
    )

    assert result["status"] == "provisional"
    assert result["method"] == "llm_prompt_only"
    assert result["measured"] is False


def test_orchestration_skips_and_restores_plantuml_jvm_check():
    from app.design.services.common import validation

    original = validation.check_plantuml_syntax
    with DesignAdapter._without_plantuml_jvm():
        assert validation.check_plantuml_syntax("invalid") == []
    assert validation.check_plantuml_syntax is original


def test_implementation_contract_maps_nested_design_artifacts():
    payload = ImplementationAdapter._design_payload(
        {"resource_spec": {"provider": "aws"}},
        {
            "artifacts": {
                "class_diagram": (
                    "A --> B\nclass User {\n  - email\n"
                    "  + authenticate(email,password)\n}"
                ),
                "sequence_diagram": "sequence puml",
                "api_spec": {"openapi": "3.0.0"},
                "erd": "erd puml",
                "deployment_diagram": "logical puml",
            }
        },
        {"deployment_diagram_puml": "cloud puml"},
        {"status": "provisional"},
    )

    assert payload["class_diagram_puml"] == (
        "' implementation relation: A --> B\nclass User {\n  - email: String\n"
        "  + authenticate(email: String, password: String)\n}"
    )
    assert payload["deployment_diagram_puml"] == "cloud puml"
    assert payload["resource_spec"]["provisionalRecommendation"]["status"] == "provisional"
