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


def test_explicit_evidence_is_accepted_and_sample_seeds_are_distinct(monkeypatch):
    seeds = []

    def sample(*_args, **kwargs):
        seeds.append(kwargs["seed_override"])
        return _result({
            "https_ingress": DeploymentNeed(
                role="Provide external HTTPS access",
                required=True,
                requirementIds=["NFR1"],
                evidenceSpans=["External clients use HTTPS."],
                origin="explicit",
            )
        })

    monkeypatch.setattr(step_cloud, "invoke_structured", sample)
    result = step_cloud.derive_deployment_needs({"classified": CLASSIFIED})

    capability = result["capability_contract"]["capabilities"][0]
    assert capability["decision"] == "accepted"
    assert capability["rawConfidence"] == 1
    assert len(seeds) == step_cloud.settings.capability_samples
    assert len(set(seeds)) == len(seeds)


def test_supported_dependency_capability_id_is_preserved(monkeypatch):
    classified = [{
        "id": "NFR1",
        "text": "External clients use HTTPS through a load balancer.",
        "type": "NFR",
    }]
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "secure_ingress": DeploymentNeed(
            role="Provide load-balanced HTTPS access",
            required=True,
            requirementIds=["NFR1"],
            evidenceSpans=["External clients use HTTPS through a load balancer."],
            origin="explicit",
            dependencyCapabilityIds=[
                "https-load-balanced-ingress",
                "unsupported-invented-id",
            ],
        )
    }))

    result = step_cloud.derive_deployment_needs({"classified": classified})

    assert result["deployment_needs"]["secure_ingress"][
        "dependencyCapabilityIds"
    ] == ["https-load-balanced-ingress"]
    assert result["capability_contract"]["capabilities"][0][
        "dependencyCapabilityIds"
    ] == ["https-load-balanced-ingress"]


def test_dependency_capability_id_requires_sample_agreement(monkeypatch):
    def sample(*_args, **kwargs):
        agrees = kwargs["seed_override"] % 2 == 0
        capability_ids = ["https-load-balanced-ingress"] if agrees else []
        return _result({
            "secure_ingress": DeploymentNeed(
                role=(
                    "Provide load-balanced HTTPS access"
                    if agrees else "Provide HTTPS access"
                ),
                required=True,
                requirementIds=["NFR1"],
                evidenceSpans=["External clients use HTTPS."],
                origin="explicit",
                dependencyCapabilityIds=capability_ids,
            )
        })

    monkeypatch.setattr(step_cloud, "invoke_structured", sample)
    result = step_cloud.derive_deployment_needs({"classified": CLASSIFIED})

    assert result["deployment_needs"]["secure_ingress"][
        "dependencyCapabilityIds"
    ] == []


def test_canonical_dynamic_key_becomes_stable_id_after_sample_agreement(monkeypatch):
    classified = [{
        "id": "NFR1",
        "text": "Application data must survive VM replacement.",
        "type": "NFR",
    }]
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "persistent_block_storage": DeploymentNeed(
            role="Keep application data across VM replacement",
            required=True,
            requirementIds=["NFR1"],
            evidenceSpans=["Application data must survive VM replacement."],
            origin="explicit",
        )
    }))

    result = step_cloud.derive_deployment_needs({"classified": classified})

    assert result["deployment_needs"]["persistent_block_storage"][
        "dependencyCapabilityIds"
    ] == ["persistent-block-storage"]


def test_semantically_same_samples_are_clustered_by_requirement_evidence(monkeypatch):
    variants = [
        ("secure_ingress", "External clients use HTTPS."),
        ("external_https", "clients use HTTPS"),
        ("https_access", "HTTPS"),
    ]

    def sample(*_args, **kwargs):
        key, span = variants[kwargs["seed_override"] % len(variants)]
        return _result({key: DeploymentNeed(
            role="Provide external HTTPS access",
            required=True,
            requirementIds=["NFR1"],
            evidenceSpans=[span],
            origin="explicit",
        )})

    monkeypatch.setattr(step_cloud, "invoke_structured", sample)

    result = step_cloud.derive_deployment_needs({"classified": CLASSIFIED})

    capabilities = result["capability_contract"]["capabilities"]
    assert len(capabilities) == 1
    assert capabilities[0]["rawConfidence"] == 1


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


def test_consumed_state_metadata_requires_value_level_evidence(monkeypatch):
    classified = [{
        "id": "NFR1",
        "text": "Mutable state shall persist across restarts.",
        "type": "NFR",
    }]
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "open_state": DeploymentNeed(
            role="Persist mutable application state",
            required=True,
            requirementIds=["NFR1"],
            evidenceSpans=["state shall persist across restarts"],
            origin="explicit",
            metadata={
                "applicationState": {
                    "durability": "persistent",
                    "accessScope": "node-filesystem",
                    "accessPath": "/invented/path",
                }
            },
        )
    }))

    need = step_cloud.derive_deployment_needs({"classified": classified})[
        "deployment_needs"
    ]["open_state"]

    assert need["metadata"]["applicationState"] == {
        "durability": "persistent"
    }
    assert {item["path"] for item in need["rejectedMetadata"]} == {
        "applicationState.accessScope",
        "applicationState.accessPath",
    }


def test_explicit_node_scope_metadata_is_preserved(monkeypatch):
    classified = [{
        "id": "NFR1",
        "text": "Persist state on the VM filesystem at /srv/state.",
        "type": "NFR",
    }]
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "open_state": DeploymentNeed(
            role="Persist mutable application state",
            required=True,
            requirementIds=["NFR1"],
            evidenceSpans=["Persist state on the VM filesystem at /srv/state."],
            origin="explicit",
            metadata={
                "applicationState": {
                    "durability": "persistent",
                    "accessScope": "node-filesystem",
                    "accessPath": "/srv/state",
                }
            },
        )
    }))

    need = step_cloud.derive_deployment_needs({"classified": classified})[
        "deployment_needs"
    ]["open_state"]

    assert need["metadata"]["applicationState"] == {
        "durability": "persistent",
        "accessScope": "node-filesystem",
        "accessPath": "/srv/state",
    }
    assert "rejectedMetadata" not in need


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
