from app.cloudkb.costkb.resource_pricing import (
    load_resource_pricing_rules,
    no_separate_meter_primitive_kinds,
    resource_pricing_rule_for_primitive,
    resource_pricing_rules,
)
from app.cloudkb.provider_primitives import _PROVIDER_MODELS, _TERRAFORM_TYPES


def test_resource_pricing_rules_cover_provider_plan_vocabulary_once() -> None:
    knowledge = load_resource_pricing_rules()

    for provider, model in _PROVIDER_MODELS.items():
        rules = resource_pricing_rules(provider=provider)
        metered = {
            primitive
            for rule in rules
            for primitive in rule["providerPrimitiveKinds"]
        }
        no_separate_meter = set(
            knowledge["coverage"][provider]["noSeparateMeterPrimitiveKinds"]
        )

        assert metered.isdisjoint(no_separate_meter)
        assert metered | no_separate_meter == set(model["resources"])


def test_resource_pricing_terraform_bindings_match_provider_primitives() -> None:
    for rule in resource_pricing_rules():
        provider_types = _TERRAFORM_TYPES[rule["provider"]]
        expected_types = {
            terraform_type
            for primitive in rule["providerPrimitiveKinds"]
            for terraform_type in provider_types.get(primitive, ())
        }
        assert set(rule["terraformTypes"]) == expected_types


def test_resource_pricing_queries_distinguish_meter__structural_and_flow_rules() -> None:
    assert resource_pricing_rule_for_primitive("AWS", "nat-gateway")["id"] == (
        "aws.network.nat-gateway"
    )
    assert resource_pricing_rule_for_primitive("gcp", "network") is None
    assert "network" in no_separate_meter_primitive_kinds("gcp")
    assert resource_pricing_rules(provider="azure", category="network-egress")[0][
        "providerPrimitiveKinds"
    ] == []
