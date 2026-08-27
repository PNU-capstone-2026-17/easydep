"""고정 Terraform 공급자 스키마로 구성요소 투영 후보를 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.research_protocol.core.paths import REPOSITORY_ROOT
from evaluation.research_protocol.core.provider_tools import (
    PINNED_PROVIDERS as PROVIDERS,
)
from evaluation.research_protocol.core.provider_tools import (
    PLUGIN_CACHE,
    audit_provider_cache,
    directory_size,
    provider_cache_environment,
    run_provider_command,
)

ROOT = REPOSITORY_ROOT
DEFAULT_PROJECTIONS = ROOT / "evaluation/research_protocol/definitions/component-projections.json"


def _provider_config(provider: str) -> str:
    contract = PROVIDERS[provider]
    provider_block = (
        'provider "azurerm" {\n  features {}\n}\n' if provider == "azure" else ""
    )
    local_name = "azurerm" if provider == "azure" else ("google" if provider == "gcp" else "aws")
    return (
        "terraform {\n  required_providers {\n"
        f"    {local_name} = {{\n"
        f"      source  = \"{contract['source']}\"\n"
        f"      version = \"={contract['version']}\"\n"
        "    }\n"
        "  }\n}\n"
        + provider_block
    )


def _nested_block_exists(block: dict[str, Any], path: list[str]) -> bool:
    current = block
    for name in path:
        nested = (current.get("block_types") or {}).get(name)
        if not isinstance(nested, dict):
            return False
        current = nested.get("block") or {}
    return True


def _alternative_exists(schema: dict[str, Any], expression: str) -> bool:
    resources = schema.get("resource_schemas") or {}
    data_sources = schema.get("data_source_schemas") or {}
    if expression.startswith("data."):
        return expression.removeprefix("data.") in data_sources
    if "." not in expression:
        return expression in resources or expression in data_sources
    parent, *nested = expression.split(".")
    resource = resources.get(parent)
    return isinstance(resource, dict) and _nested_block_exists(resource.get("block") or {}, nested)


def audit_schema(
    projections: dict[str, Any], provider: str, schema: dict[str, Any]
) -> dict[str, Any]:
    provider_key = PROVIDERS[provider]["source"]
    provider_schema = (schema.get("provider_schemas") or {}).get(
        f"registry.opentofu.org/{provider_key}"
    ) or (schema.get("provider_schemas") or {}).get(
        f"registry.terraform.io/{provider_key}"
    )
    if not isinstance(provider_schema, dict):
        return {"status": "failed", "reason": "provider schema key not found", "checks": []}
    checks: list[dict[str, Any]] = []
    for delta in projections["deltas"]:
        realization = delta["realizations"][provider]
        for component in realization["components"]:
            if component["terraformKind"] == "guestConfiguration":
                checks.append({
                    "delta": delta["id"],
                    "component": component["id"],
                    "status": "outside-provider-schema",
                })
                continue
            alternatives = str(component["terraformType"]).split("|")
            found = [item for item in alternatives if _alternative_exists(provider_schema, item)]
            checks.append({
                "delta": delta["id"],
                "component": component["id"],
                "terraformType": component["terraformType"],
                "status": "passed" if found else "failed",
                "matchedAlternatives": found,
            })
    measured = [item for item in checks if item["status"] != "outside-provider-schema"]
    return {
        "status": "passed" if measured and all(item["status"] == "passed" for item in measured) else "failed",
        "checks": checks,
    }


def run_audit(projections_path: Path = DEFAULT_PROJECTIONS) -> dict[str, Any]:
    audit_started = time.perf_counter()
    projections = json.loads(projections_path.read_text(encoding="utf-8"))
    tofu = shutil.which("tofu")
    if not tofu:
        raise RuntimeError("OpenTofu CLI를 찾을 수 없다")
    result: dict[str, Any] = {
        "schemaVersion": "easydep-provider-schema-audit/v1",
        "startedAt": datetime.now(UTC).isoformat(),
        "projectionSha256": hashlib.sha256(projections_path.read_bytes()).hexdigest(),
        "providers": {},
        "providerCache": {
            "path": ".easydep/provider-plugin-cache",
            "policy": "dedicated-pinned-versions-only-serial",
            "allowed": PROVIDERS,
            "bytesBefore": directory_size(PLUGIN_CACHE),
            "contentsBefore": audit_provider_cache(),
        },
    }
    environment = provider_cache_environment()
    for provider, contract in PROVIDERS.items():
        provider_started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix=f"easydep-schema-{provider}-") as directory:
            root = Path(directory)
            (root / "main.tf").write_text(_provider_config(provider), encoding="utf-8")
            initialize = run_provider_command(
                [tofu, "init", "-backend=false", "-input=false", "-no-color"],
                root,
                environment=environment,
            )
            item: dict[str, Any] = {"requested": contract, "initialize": initialize}
            if initialize["status"] == "passed":
                schema_result = run_provider_command(
                    [tofu, "providers", "schema", "-json"],
                    root,
                    environment=environment,
                )
                item["schemaCommand"] = {
                    key: value for key, value in schema_result.items() if key != "stdout"
                }
                if schema_result["status"] == "passed":
                    schema = json.loads(schema_result["stdout"])
                    item["audit"] = audit_schema(projections, provider, schema)
                else:
                    item["audit"] = {"status": "failed", "checks": []}
            else:
                item["audit"] = {"status": "failed", "checks": []}
            result["providers"][provider] = item
            item["elapsedSeconds"] = round(time.perf_counter() - provider_started, 6)
    result["completedAt"] = datetime.now(UTC).isoformat()
    result["status"] = (
        "passed"
        if all(item["audit"]["status"] == "passed" for item in result["providers"].values())
        else "failed"
    )
    result["elapsedSeconds"] = round(time.perf_counter() - audit_started, 6)
    result["providerCache"]["bytesAfter"] = directory_size(PLUGIN_CACHE)
    result["providerCache"]["contentsAfter"] = audit_provider_cache()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="고정 공급자 스키마로 투영 후보를 검사합니다.")
    parser.add_argument("--projections", type=Path, default=DEFAULT_PROJECTIONS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_audit(args.projections)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
