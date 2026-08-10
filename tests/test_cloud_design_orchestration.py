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
    assert result["dependency_coverage"] == {
        "modeledInputs": [{
            "source": "system_scope",
            "field": "docker_on_vm",
            "outcome": "vm",
        }],
        "unmodeledAcceptedNeeds": [],
    }
    assert result["kb_used"] == ["depkb"]
    assert result["deferred"] == ["capacity", "performance", "price", "vm_selection"]
    assert "Application container" in result["deployment_diagram_puml"]
    assert "vm" in result["deployment_diagram_puml"]
    assert "?" not in result["deployment_diagram_puml"]
    assert result["logical_deployment_diagram_puml"].startswith("@startuml")


def test_logical_database_does_not_imply_cloud_block_storage():
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

    assert result["anchors"] == ["vm", "loadBalancer"]


def test_accepted_persistent_storage_adds_disk_anchor():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "aws",
                "region": "ap-northeast-2",
            },
            "deployment_needs": {
                "persistent_storage": {
                    "required": True,
                    "decision": "accepted",
                }
            },
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [{"name": "Application", "kind": "executionEnvironment"}]
            }
        },
    )

    assert result["anchors"] == ["vm", "disk"]
    assert result["dependency_coverage"]["modeledInputs"][-1]["outcome"] == "disk"


def test_stable_capability_id_does_not_depend_on_dynamic_need_key():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "gcp",
                "region": "asia-northeast3",
            },
            "deployment_needs": {
                "durable_storage_across_restarts": {
                    "required": True,
                    "decision": "accepted",
                    "dependencyCapabilityIds": ["persistent-block-storage"],
                }
            },
        },
        design_result={},
    )

    assert result["anchors"] == ["vm", "disk"]
    assert result["dependency_coverage"]["unmodeledAcceptedNeeds"] == []


def test_https_load_balanced_capability_selects_https_projection():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "aws",
                "region": "ap-northeast-2",
            },
            "deployment_needs": {
                "secure_public_entry": {
                    "required": True,
                    "decision": "accepted",
                    "dependencyCapabilityIds": ["https-load-balanced-ingress"],
                }
            },
        },
        design_result={},
    )

    assert result["anchors"] == ["vm", "loadBalancer"]
    realizations = result["infra_intent"]["capabilityRealizations"]
    assert [item["id"] for item in realizations] == ["https-alb"]
    assert any(
        component["id"] == "certificate"
        for component in realizations[0]["components"]
    )


def test_mandatory_prohibition_does_not_add_disk_anchor():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "azure",
                "region": "koreacentral",
            },
            "deployment_needs": {
                "persistent_storage": {
                    "role": "Do not allocate a persistent application disk",
                    "required": True,
                    "decision": "accepted",
                    "metadata": {"persistent_application_disk": False},
                }
            },
        },
        design_result={},
    )

    assert result["anchors"] == ["vm"]
    assert result["dependency_coverage"]["modeledInputs"][-1]["outcome"] == (
        "no_disk"
    )


def test_cloud_design_reports_accepted_but_unmodeled_capabilities():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "gcp",
                "region": "asia-northeast3",
                "multiZone": True,
            },
            "deployment_needs": {
                "https_ingress": {
                    "required": True,
                    "decision": "accepted",
                },
                "availability_requirement": {
                    "required": True,
                    "decision": "accepted",
                    "metadata": {"high_availability": True},
                },
                "unconfirmed_observability": {
                    "required": True,
                    "decision": "needsQuestion",
                },
            },
        },
        design_result={},
    )

    assert result["anchors"] == ["vm", "loadBalancer"]
    assert result["dependency_coverage"]["unmodeledAcceptedNeeds"] == [
        "availability_requirement",
        "https_ingress",
    ]
    assert result["dependency_coverage"]["modeledInputs"][-1] == {
        "source": "resource_spec",
        "field": "multiZone",
        "outcome": "loadBalancer",
    }


def test_cloud_design_does_not_block_without_cloud_coordinates():
    logical = "@startuml\nnode app\n@enduml"
    result = CloudDesignAdapter().finalize(
        requirements_result={"resource_spec": {}},
        design_result={"deployment_diagram_puml": logical},
    )

    assert result["status"] == "skipped"
    assert result["deployment_diagram_puml"] == logical
    assert result["kb_used"] == []
