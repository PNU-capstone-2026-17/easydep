import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from evaluation.component_projection import (
    _guest_mounts,
    analyze_component_projections,
    derive_component_dependency_expectations,
)
from evaluation.implementation import resolve_oracle
from evaluation.research_protocol.commands.cna_case_audit import audit
from evaluation.terraform_semantics import analyze_terraform_semantics, score_semantics

ROOT = Path(__file__).resolve().parents[1]
PROJECTIONS = ROOT / "evaluation/research_protocol/definitions/component-projections.json"
CROSSWALK = ROOT / "app/core/cloudkb/depkb/neutral_candidates/crosswalk.json"


def test_component_projection_contract_is_complete_and_grounded():
    value = json.loads(PROJECTIONS.read_text(encoding="utf-8"))
    crosswalk = json.loads(CROSSWALK.read_text(encoding="utf-8"))
    concepts = {item["id"] for item in crosswalk["concepts"]}

    assert value["schemaVersion"] == "easydep-component-projections/v1"
    assert {item["id"] for item in value["deltas"]} == {
        "persistent-storage",
        "load-balanced-multi-vm",
        "https-termination",
    }
    for delta in value["deltas"]:
        assert set(delta["realizations"]) == {"aws", "azure", "gcp"}
        for realization in delta["realizations"].values():
            component_ids = [item["id"] for item in realization["components"]]
            assert len(component_ids) == len(set(component_ids))
            assert realization["relations"]
            assert realization["evidence"]
            for component in realization["components"]:
                concept = component.get("neutralConcept")
                assert concept is None or concept in concepts
            for source in realization["evidence"]:
                parsed = urlparse(source)
                assert parsed.scheme == "https"
                assert parsed.netloc in {
                    "cloud.google.com",
                    "docs.aws.amazon.com",
                    "learn.microsoft.com",
                    "registry.terraform.io",
                }


def test_azure_application_gateway_keeps_nested_components_distinct():
    value = json.loads(PROJECTIONS.read_text(encoding="utf-8"))
    scale = next(item for item in value["deltas"] if item["id"] == "load-balanced-multi-vm")
    azure = scale["realizations"]["azure"]

    nested = {item["id"] for item in azure["components"] if item["terraformKind"] == "nestedBlock"}
    assert {"listener", "backendPool", "backendSettings", "routingRule", "probe"} <= nested


def test_gcp_load_balancer_projection_is_not_collapsed_to_one_resource():
    value = json.loads(PROJECTIONS.read_text(encoding="utf-8"))
    scale = next(item for item in value["deltas"] if item["id"] == "load-balanced-multi-vm")
    resource_types = {
        alternative
        for item in scale["realizations"]["gcp"]["components"]
        for alternative in item["terraformType"].split("|")
    }

    assert {
        "google_compute_global_forwarding_rule",
        "google_compute_target_http_proxy",
        "google_compute_url_map",
        "google_compute_backend_service",
        "google_compute_instance_group",
        "google_compute_health_check",
    } <= resource_types


def test_validated_provider_fixtures_expose_components_and_relations():
    fixture_root = ROOT / "evaluation/research_protocol/provider-fixtures"
    expected = {
        "aws": {
            "persistent-storage": (2, 3),
            "load-balanced-multi-vm": (4, 5),
            "https-termination": (2, 2),
        },
        "azure": {
            "persistent-storage": (2, 2),
            "load-balanced-multi-vm": (8, 7),
            "https-termination": (2, 2),
        },
        "gcp": {
            "persistent-storage": (2, 2),
            "load-balanced-multi-vm": (6, 6),
            "https-termination": (2, 3),
        },
    }
    for provider, deltas in expected.items():
        actual = analyze_terraform_semantics(fixture_root / provider)
        assert actual["componentProjections"]["provider"] == provider
        assert actual["unmappedProviderTypes"] == []
        for delta_id, (component_passes, relation_passes) in deltas.items():
            delta = actual["componentProjections"]["deltas"][delta_id]
            assert (
                sum(item["status"] == "passed" for item in delta["components"]) == component_passes
            )
            assert (
                sum(item["status"] == "observed-unverified" for item in delta["relations"])
                == relation_passes
            )


def test_component_delta_is_part_of_semantic_score():
    actual = analyze_terraform_semantics(
        ROOT / "evaluation/research_protocol/provider-fixtures/azure"
    )
    score = score_semantics(
        actual,
        {
            "provider": "azure",
            "requiredCapabilities": {},
            "requiredDependencies": [],
            "componentDelta": "load-balanced-multi-vm",
        },
    )
    component_checks = [item for item in score["checks"] if item["kind"] == "componentProjection"]
    relation_checks = [item for item in score["checks"] if item["kind"] == "componentRelation"]
    assert len(component_checks) == 8
    assert len(relation_checks) == 7
    assert all(item["status"] == "passed" for item in component_checks)
    assert all(item["status"] == "observed-unverified" for item in relation_checks)
    assert score["passed"] == len(component_checks) + 1  # provider boundary


def test_guest_mount_observation_uses_generated_path_not_one_app_fixture():
    assert _guest_mounts("mount /dev/sdb /srv/catalog-data") == ["/srv/catalog-data"]
    assert _guest_mounts("description = '/var/lib/notes'") == []


def test_guest_mount_resolves_generic_template_variable_and_mount_options():
    text = '''
mount_path = "/srv/catalog-data",
- mount --bind /mnt/data ${mount_path}
'''
    assert _guest_mounts(text) == ["/srv/catalog-data"]


def test_guest_mount_component_uses_contract_path_instead_of_fixed_fixture():
    resources = [
        {
            "providerType": "aws_ebs_volume",
            "declarationKind": "resource",
            "address": "aws_ebs_volume.data",
            "attributes": {},
            "concept": "dataDisk",
        }
    ]
    actual = analyze_component_projections(
        resources,
        "mount /dev/sdb /var/lib/notes\nmount /dev/sdc /srv/catalog-data",
        expected_mount_path="/srv/catalog-data",
    )
    mount = next(
        item
        for item in actual["deltas"]["persistent-storage"]["components"]
        if item["componentId"] == "mount"
    )
    assert mount["instances"] == ["guest:mount:/srv/catalog-data"]
    assert mount["status"] == "observed-unverified"


def test_component_oracle_keeps_static_and_function_gates_separate():
    oracle = json.loads(
        (ROOT / "evaluation/baselines/component-cases/oracle.json").read_text(encoding="utf-8")
    )

    control = resolve_oracle(oracle, "TLS-control-gcp")
    treatment = resolve_oracle(oracle, "TLS-treatment-gcp")
    storage = resolve_oracle(oracle, "PS-treatment-aws")

    assert control["componentDeltas"] == ["load-balanced-multi-vm"]
    assert treatment["componentDeltas"] == ["load-balanced-multi-vm", "https-termination"]
    assert treatment["functionalAcceptance"] == control["functionalAcceptance"]
    assert storage["persistenceAcceptance"]["afterRestart"]
    assert storage["legacyProviderProjection"] is False
    assert storage["requiredDependencies"] == []
    derived = storage["componentDependencyExpectations"]
    assert {(item["from"], item["to"]) for item in derived["structuralReferences"]} == {
        ("attachment", "disk"),
        ("attachment", "vm"),
    }
    assert {item["constraint"] for item in derived["constraints"]} == {
        "same-availability-zone",
        "device-must-be-formatted-and-mounted",
    }


def test_dependency_expectations_are_derived_for_each_provider_without_case_table():
    for provider in ("aws", "azure", "gcp"):
        expected = derive_component_dependency_expectations(
            provider, ["load-balanced-multi-vm", "https-termination"]
        )
        assert expected["structuralReferences"]
        assert expected["cardinalities"]
        assert all(item["evidence"] for item in expected["structuralReferences"])
        assert all(
            item["delta"] in {"load-balanced-multi-vm", "https-termination"}
            for item in expected["structuralReferences"]
        )


def test_derived_structural_dependency_is_scored_separately_from_cardinality():
    actual = analyze_terraform_semantics(
        ROOT / "evaluation/research_protocol/provider-fixtures/azure"
    )
    expected = derive_component_dependency_expectations("azure", ["load-balanced-multi-vm"])
    score = score_semantics(
        actual,
        {
            "requiredCapabilities": {},
            "requiredDependencies": [],
            "componentDeltas": ["load-balanced-multi-vm"],
            "componentDependencyExpectations": expected,
        },
    )
    references = [
        item for item in score["checks"] if item["kind"] == "componentDependencyReference"
    ]
    cardinalities = [item for item in score["checks"] if item["kind"] == "componentCardinality"]
    assert references and all(item["status"] == "passed" for item in references)
    assert cardinalities and all(item["status"] == "not-measured" for item in cardinalities)

    broken = deepcopy(actual)
    relation = broken["componentProjections"]["deltas"]["load-balanced-multi-vm"]["relations"][0]
    relation["observedPairs"] = []
    broken_score = score_semantics(
        broken,
        {
            "requiredCapabilities": {},
            "requiredDependencies": [],
            "componentDeltas": ["load-balanced-multi-vm"],
            "componentDependencyExpectations": expected,
        },
    )
    assert any(
        item["kind"] == "componentDependencyReference"
        and item["from"] == relation["from"]
        and item["to"] == relation["to"]
        and item["status"] == "failed"
        for item in broken_score["checks"]
    )

def test_constraint_without_independent_observer_is_not_counted_as_passed():
    actual = analyze_terraform_semantics(
        ROOT / "evaluation/research_protocol/provider-fixtures/azure"
    )
    expected = derive_component_dependency_expectations("azure", ["load-balanced-multi-vm"])
    score = score_semantics(
        actual,
        {
            "requiredCapabilities": {},
            "requiredDependencies": [],
            "componentDeltas": ["load-balanced-multi-vm"],
            "componentDependencyExpectations": expected,
        },
    )

    dedicated_subnet = next(
        item
        for item in score["checks"]
        if item["kind"] == "componentConstraintRequirement"
        and item["constraint"] == "dedicated-subnet"
    )
    assert dedicated_subnet["status"] == "not-measured"
    assert score["notMeasured"] > 0


def test_cna_cases_are_grounded_but_legacy_synthesis_is_not_reproducible():
    result = audit()

    assert result["projectionAndPairEvidenceComplete"] is True
    assert result["eligibleForDevelopmentPilot"] is True
    assert result["eligibleForDependencyStructureMeasurement"] is True
    assert result["eligibleForCardinalityOrConstraintClaim"] is False
    assert result["synthesisProvenanceComplete"] is False
    assert result["eligibleAsReproducibleSyntheticCorpus"] is False
