"""요구사항에서 도출하는 일반 배포 capability의 공개 계약을 검증한다."""
from __future__ import annotations

from app.requirements.knowledge import concerns, verify_concerns
from app.requirements.resources import capability_extraction as step_cloud
from app.requirements.schemas import DeploymentNeed, DeploymentNeedsResult

CLASSIFIED = [
    {"id": "NFR1", "text": "External clients use HTTPS.", "type": "NFR"},
    {"id": "NFR2", "text": "High availability is not required.", "type": "NFR"},
]


def _result(needs: dict[str, DeploymentNeed]) -> DeploymentNeedsResult:
    return DeploymentNeedsResult(deploymentNeeds=needs)


def test_deployment_needs_is_the_only_non_strict_structured_request(monkeypatch):
    captured = {}

    def fake_invoke(schema, messages, **kwargs):
        captured.update(schema=schema, messages=messages, kwargs=kwargs)
        return _result({})

    monkeypatch.setattr(step_cloud, "invoke_structured", fake_invoke)

    result = step_cloud.propose_deployment_needs(CLASSIFIED, seed=17)

    assert result == _result({})
    assert captured["schema"] is DeploymentNeedsResult
    assert captured["kwargs"] == {"seed_override": 17, "strict": False}


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


def test_explicit_out_of_scope_evidence_abstains_and_sample_seeds_are_distinct(monkeypatch):
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
    assert capability["decision"] == "abstained"
    assert capability["decisionReason"] == "model-out-of-scope"
    assert capability["rawConfidence"] == 1
    assert len(seeds) == step_cloud.settings.capability_samples
    assert len(set(seeds)) == len(seeds)


def test_research_call_can_explicitly_request_multiple_capability_samples(monkeypatch):
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

    step_cloud.derive_deployment_needs(
        {"classified": CLASSIFIED}, sample_count=5
    )

    assert len(seeds) == 5
    assert len(set(seeds)) == 5


def test_ambiguous_multi_zone_metadata_produces_a_purpose_question(monkeypatch):
    classified = [{
        "id": "NFR-ZONE",
        "text": "Deploy the application across multiple availability zones.",
        "type": "NFR",
    }]
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "zone_placement": DeploymentNeed(
            role="Place the application across multiple availability zones",
            required=True,
            requirementIds=["NFR-ZONE"],
            evidenceSpans=[classified[0]["text"]],
            origin="explicit",
            metadata={
                "placementScope": "multiZone",
                "unresolved": ["availability"],
            },
        )
    }))

    result = step_cloud.derive_deployment_needs({"classified": classified})

    assert result["deployment_needs"]["zone_placement"]["decision"] == "needsQuestion"
    question = result["capability_contract"]["questions"][0]
    assert question["capabilityId"] == "zone_placement"
    assert "independent VM replicas" in question["question"]


def test_out_of_scope_https_capability_id_is_preserved_and_abstained(monkeypatch):
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
    assert result["deployment_needs"]["secure_ingress"]["decision"] == "abstained"
    assert result["capability_contract"]["capabilities"][0][
        "decisionReason"
    ] == "model-out-of-scope"


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
    result = step_cloud.derive_deployment_needs(
        {"classified": CLASSIFIED}, sample_count=5
    )

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


def test_llm_cannot_promote_restart_persistence_to_vm_independent_block_storage(
    monkeypatch,
):
    classified = [
        {
            "id": "NFR1",
            "text": "Shared state shall not be lost when the application server restarts.",
            "type": "NFR",
        }
    ]
    monkeypatch.setattr(
        step_cloud,
        "invoke_structured",
        lambda *_args, **_kwargs: _result(
            {
                "shared_state": DeploymentNeed(
                    role="Keep shared state durable across restarts",
                    required=True,
                    requirementIds=["NFR1"],
                    evidenceSpans=[classified[0]["text"]],
                    origin="explicit",
                    dependencyCapabilityIds=["persistent-block-storage"],
                    metadata={
                        "applicationState": {
                            "durability": "durable",
                            "accessScope": "shared-service",
                        }
                    },
                )
            }
        ),
    )

    need = step_cloud.derive_deployment_needs({"classified": classified})[
        "deployment_needs"
    ]["shared_state"]

    assert need["dependencyCapabilityIds"] == []
    assert need["metadata"]["applicationState"] == {
        "durability": "persistent",
        "accessScope": "shared-service",
    }


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


def test_application_behavior_without_deployment_evidence_is_not_accepted(monkeypatch):
    classified = [{
        "id": "NFR1",
        "text": "Concurrent order operations shall preserve order uniqueness.",
        "type": "NFR",
    }]
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "transactional_consistency": DeploymentNeed(
            role="Preserve order uniqueness during concurrent operations",
            required=True,
            requirementIds=["NFR1"],
            evidenceSpans=[classified[0]["text"]],
            origin="explicit",
        )
    }))

    result = step_cloud.derive_deployment_needs({"classified": classified})

    assert result["deployment_needs"]["transactional_consistency"]["decision"] == "abstained"
    capability = result["capability_contract"]["capabilities"][0]
    assert capability["decision"] == "abstained"
    assert capability["decisionReason"] == "not-deployment-boundary"


def test_restart_persistence_has_deployment_boundary_evidence(monkeypatch):
    classified = [{
        "id": "NFR1",
        "text": "Order data shall survive application and server restarts.",
        "type": "NFR",
    }]
    monkeypatch.setattr(step_cloud, "invoke_structured", lambda *_args, **_kwargs: _result({
        "persistent_storage": DeploymentNeed(
            role="Keep order data across restarts",
            required=True,
            requirementIds=["NFR1"],
            evidenceSpans=[classified[0]["text"]],
            origin="explicit",
        )
    }))

    result = step_cloud.derive_deployment_needs({"classified": classified})

    assert result["deployment_needs"]["persistent_storage"]["decision"] == "accepted"


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


def test_persistence_and_shared_scope_accept_plain_requirement_wording(monkeypatch):
    classified = [
        {
            "id": "NFR1",
            "text": (
                "Authoritative state shall be stored in a shared location accessible to all "
                "application instances and shall not be lost when a server is restarted."
            ),
            "type": "NFR",
        }
    ]
    monkeypatch.setattr(
        step_cloud,
        "invoke_structured",
        lambda *_args, **_kwargs: _result(
            {
                "shared_state": DeploymentNeed(
                    role="Keep authoritative state durable and shared",
                    required=True,
                    requirementIds=["NFR1"],
                    evidenceSpans=[classified[0]["text"]],
                    origin="explicit",
                    metadata={
                        "applicationState": {
                            "durability": "durable",
                            "accessScope": "shared-service",
                        }
                    },
                )
            }
        ),
    )

    need = step_cloud.derive_deployment_needs({"classified": classified})[
        "deployment_needs"
    ]["shared_state"]

    assert need["metadata"]["applicationState"] == {
        "durability": "persistent",
        "accessScope": "shared-service",
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
