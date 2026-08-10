from pathlib import Path

from evaluation.research_protocol.commands.provider_schema_audit import (
    _alternative_exists,
    _provider_config,
    audit_schema,
)
from evaluation.research_protocol.core.provider_tools import (
    PLUGIN_CACHE,
    audit_provider_cache,
    provider_cache_environment,
    run_provider_command,
)


def _schema():
    return {
        "provider_schemas": {
            "registry.opentofu.org/hashicorp/azurerm": {
                "resource_schemas": {
                    "azurerm_application_gateway": {
                        "block": {
                            "block_types": {
                                "http_listener": {"block": {"block_types": {}}},
                                "ssl_certificate": {"block": {"block_types": {}}},
                            }
                        }
                    },
                    "azurerm_managed_disk": {"block": {}},
                    "azurerm_virtual_machine_data_disk_attachment": {"block": {}},
                },
                "data_source_schemas": {},
            }
        }
    }


def test_nested_and_top_level_schema_alternatives_are_distinguished():
    provider = _schema()["provider_schemas"]["registry.opentofu.org/hashicorp/azurerm"]

    assert _alternative_exists(provider, "azurerm_managed_disk")
    assert _alternative_exists(provider, "azurerm_application_gateway.http_listener")
    assert not _alternative_exists(provider, "azurerm_application_gateway.missing")


def test_generated_provider_configuration_uses_valid_multiline_blocks():
    azure = _provider_config("azure")

    assert 'source  = "hashicorp/azurerm"' in azure
    assert 'version = "=5.0.1"' in azure
    assert 'provider "azurerm" {\n  features {}\n}' in azure


def test_command_result_records_elapsed_time(tmp_path):
    result = run_provider_command(["tofu", "version"], tmp_path)

    assert result["status"] == "passed"
    assert result["elapsedSeconds"] >= 0


def test_provider_cache_is_dedicated_to_research_versions():
    environment = provider_cache_environment()

    assert Path(environment["TF_PLUGIN_CACHE_DIR"]) == PLUGIN_CACHE
    assert PLUGIN_CACHE.name == "provider-plugin-cache"


def test_provider_cache_rejects_versions_outside_allowlist(tmp_path):
    allowed = tmp_path / "registry.opentofu.org/hashicorp/aws/5.100.0/windows_amd64"
    unexpected = tmp_path / "registry.opentofu.org/hashicorp/aws/6.0.0/windows_amd64"
    allowed.mkdir(parents=True)
    unexpected.mkdir(parents=True)

    result = audit_provider_cache(tmp_path)

    assert result["status"] == "failed"
    assert result["unexpected"] == [{"provider": "aws", "version": "6.0.0"}]


def test_schema_audit_reports_missing_component_without_guessing():
    projections = {
        "deltas": [{
            "id": "storage",
            "realizations": {
                "azure": {
                    "components": [
                        {"id": "disk", "terraformKind": "resource", "terraformType": "azurerm_managed_disk"},
                        {"id": "missing", "terraformKind": "nestedBlock", "terraformType": "azurerm_application_gateway.probe"},
                        {"id": "mount", "terraformKind": "guestConfiguration"},
                    ]
                }
            },
        }]
    }

    result = audit_schema(projections, "azure", _schema())

    assert result["status"] == "failed"
    assert [item["status"] for item in result["checks"]] == [
        "passed",
        "failed",
        "outside-provider-schema",
    ]
