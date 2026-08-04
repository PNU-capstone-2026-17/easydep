from __future__ import annotations

import pytest

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter


@pytest.mark.parametrize(
    ("provider", "region"),
    [
        ("aws", "ap-northeast-2"),
        ("azure", "koreacentral"),
        ("gcp", "asia-northeast3"),
    ],
)
def test_cloud_design_uses_only_dependency_kb(provider, region):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": provider,
                "region": region,
                "workloads": ["vm"],
                "monthlyBudgetUSD": 100,
            }
        },
        design_result={
            "deployment_diagram_puml": "@startuml\nnode app\n@enduml",
            "deployment_diagram_model": {
                "Nodes": [{"name": "Application", "kind": "executionEnvironment"}]
            },
        },
    )

    assert result["status"] == "completed"
    assert result["anchors"] == ["vm"]
    assert result["kb_used"] == ["depkb"]
    assert result["deferred"] == ["capacity", "performance", "price", "vm_selection"]
    assert "Docker application container" in result["deployment_diagram_puml"]
    assert "vm" in result["deployment_diagram_puml"]


def test_cloud_design_adds_only_design_supported_optional_anchors():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "aws",
                "region": "ap-northeast-2",
                "multiZone": True,
            }
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [{"name": "Database", "kind": "database"}]
            }
        },
    )

    assert result["anchors"] == ["vm", "disk", "loadBalancer"]


def test_cloud_design_does_not_block_without_cloud_coordinates():
    logical = "@startuml\nnode app\n@enduml"
    result = CloudDesignAdapter().finalize(
        requirements_result={"resource_spec": {}},
        design_result={"deployment_diagram_puml": logical},
    )

    assert result["status"] == "skipped"
    assert result["deployment_diagram_puml"] == logical
    assert result["kb_used"] == []
