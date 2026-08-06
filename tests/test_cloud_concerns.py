"""Current generic deployment-needs extraction contract."""
from __future__ import annotations

import re

from app.requirements.agent.steps import step_cloud
from app.requirements.knowledge import concerns, verify_concerns
from app.requirements.schemas import DeploymentNeed, DeploymentNeedsResult

CLASSIFIED = [
    {"id": "NFR1", "text": "External clients use HTTPS.", "type": "NFR"},
    {"id": "NFR2", "text": "High availability is not required.", "type": "NFR"},
]


def _result(needs: dict[str, DeploymentNeed]) -> DeploymentNeedsResult:
    return DeploymentNeedsResult(deploymentNeeds=needs)


def test_grounded_generic_needs_are_preserved(monkeypatch):
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "https_ingress": DeploymentNeed(
            role="Provide external HTTPS access",
            required=True,
            requirementIds=["NFR1"],
            metadata={"protocol": "HTTPS"},
        ),
        "availability_requirement": DeploymentNeed(
            role="No high-availability guarantee is required",
            required=True,
            requirementIds=["NFR2"],
            metadata={"high_availability": False},
        ),
    }))

    needs = step_cloud.derive_deployment_needs({"classified": CLASSIFIED})[
        "deployment_needs"
    ]

    assert set(needs) == {"https_ingress", "availability_requirement"}
    assert needs["availability_requirement"]["requirementIds"] == ["NFR2"]
    assert needs["availability_requirement"]["metadata"] == {
        "high_availability": False
    }


def test_invalid_keys_and_unknown_requirement_references_are_dropped(monkeypatch):
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "AWS EBS": DeploymentNeed(
            role="Store data", required=True, requirementIds=["NFR1"]
        ),
        "unlinked_need": DeploymentNeed(
            role="Unknown", required=True, requirementIds=["MISSING"]
        ),
    }))

    result = step_cloud.derive_deployment_needs({"classified": CLASSIFIED})

    assert result["deployment_needs"] == {}


def test_duplicate_and_partially_unknown_requirement_ids_are_normalized(monkeypatch):
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "https_ingress": DeploymentNeed(
            role="Provide HTTPS",
            required=True,
            requirementIds=["NFR1", "NFR1", "MISSING"],
        )
    }))

    needs = step_cloud.derive_deployment_needs({"classified": CLASSIFIED})[
        "deployment_needs"
    ]

    assert needs["https_ingress"]["requirementIds"] == ["NFR1"]


def test_llm_failure_is_visible_and_does_not_fabricate_needs(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(step_cloud, "invoke_structured", fail)

    result = step_cloud.derive_deployment_needs({"classified": CLASSIFIED})

    assert result["deployment_needs"] == {}


def test_deployment_need_prompt_is_english_and_rejects_design_inference():
    assert not re.search(r"[가-힣]", step_cloud._SYSTEM)
    assert "not a mandate for one instance or no replication" in step_cloud._SYSTEM
    assert "Do not select or name concrete cloud resources" in step_cloud._SYSTEM


def test_vm_concern_evidence_matches_the_current_dependency_kb():
    assert all(verdict.ok for verdict in verify_concerns.verify())


def test_vm_concerns_do_not_reintroduce_out_of_scope_workloads():
    evidence = "\n".join(
        claim for concern in concerns.CONCERNS for claim in concern.claims
    ).lower()

    assert not any(
        token in evidence
        for token in ("k8s", "kubernetes", "pvc", "ingress", "filesystem", "vpn")
    )
