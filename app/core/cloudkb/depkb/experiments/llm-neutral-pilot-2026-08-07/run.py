"""Run the preregistered direct/planned/neutral resource-graph pilot."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.json"

SCENARIO = (
    "Create one Linux VM with no public IP. It must initiate IPv4 Internet "
    "connections to fetch bootstrap packages, must accept no inbound Internet "
    "traffic, and should use the minimum provider resources needed."
)

OUTPUT_CONTRACT = {
    "provider": "aws|azure|gcp",
    "resources": [{"localId": "string", "type": "Terraform provider resource type", "role": "string"}],
    "dependencies": [
        {"from": "localId", "to": "localId", "kind": "contains|attaches|routes|references"}
    ],
    "assertions": ["short factual assertion"],
}

NEUTRAL_CONTRACT = {
    "concepts": [
        "computeNode",
        "network",
        "workloadSubnet",
        "securityPolicy",
        "egressTranslation",
        "externalAddress",
    ],
    "relations": [
        "computeNode attaches workloadSubnet",
        "securityPolicy governs computeNode",
        "workloadSubnet routes through egressTranslation",
        "egressTranslation references externalAddress when the provider requires it",
    ],
    "variationPoints": [
        "egressTranslation may be a resource or configuration embedded in another resource",
        "a provider may require a distinct public-edge subnet and Internet gateway",
        "external address allocation may be explicit or automatic",
    ],
}

PROVIDERS: dict[str, dict[str, Any]] = {
    "aws": {
        "evidence": [
            "A public NAT Gateway is a standalone resource in a public subnet.",
            "It references an Elastic IP and needs that subnet to route to an Internet Gateway.",
            "The private workload subnet uses a route table whose default route targets the NAT Gateway.",
            "The VM has no public IP and its security group has no ingress rules.",
        ],
        "allow": {
            "aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_eip",
            "aws_nat_gateway", "aws_route_table", "aws_route_table_association",
            "aws_route", "aws_security_group", "aws_instance",
        },
        "required": {
            "aws_vpc": 1, "aws_subnet": 2, "aws_internet_gateway": 1,
            "aws_eip": 1, "aws_nat_gateway": 1, "aws_route_table": 2,
            "aws_route_table_association": 2, "aws_security_group": 1,
            "aws_instance": 1,
        },
        "invariants": [
            ["public subnet", "nat"], ["private", "default route", "nat"],
            ["internet gateway", "public"], ["elastic ip", "nat"],
            ["no public ip"], ["no ingress"],
        ],
        "contradictions": ["vm has a public ip", "attach public ip to vm", "allow inbound from internet"],
    },
    "azure": {
        "evidence": [
            "A NAT Gateway is a standalone resource referencing a Standard static Public IP.",
            "The NAT Gateway is associated directly with the workload subnet.",
            "The VM NIC has no public IP and its network security group has no inbound Internet allow rule.",
            "No separate Internet Gateway or route table is required for this minimal outbound path.",
        ],
        "allow": {
            "azurerm_resource_group", "azurerm_virtual_network", "azurerm_subnet",
            "azurerm_public_ip", "azurerm_nat_gateway",
            "azurerm_nat_gateway_public_ip_association",
            "azurerm_subnet_nat_gateway_association", "azurerm_network_security_group",
            "azurerm_network_interface", "azurerm_linux_virtual_machine",
        },
        "required": {
            "azurerm_resource_group": 1, "azurerm_virtual_network": 1,
            "azurerm_subnet": 1, "azurerm_public_ip": 1,
            "azurerm_nat_gateway": 1,
            "azurerm_nat_gateway_public_ip_association": 1,
            "azurerm_subnet_nat_gateway_association": 1,
            "azurerm_network_security_group": 1,
            "azurerm_network_interface": 1, "azurerm_linux_virtual_machine": 1,
        },
        "invariants": [
            ["standard", "static", "public ip"], ["nat", "subnet"],
            ["nat", "public ip"], ["no public ip"], ["no inbound"],
            ["no", "internet gateway"],
        ],
        "contradictions": ["create an internet gateway", "vm has a public ip", "allow inbound from internet"],
    },
    "gcp": {
        "evidence": [
            "Cloud NAT is configuration nested in a regional Cloud Router, not an independent top-level resource.",
            "The Router belongs to the VPC network; Cloud NAT selects the workload subnetwork.",
            "This run uses a manually reserved regional external address referenced by the NAT configuration.",
            "The VM has no access_config/public external IP and no ingress firewall rule is needed.",
        ],
        "allow": {
            "google_compute_network", "google_compute_subnetwork", "google_compute_router",
            "google_compute_address", "google_compute_router_nat",
            "google_compute_instance", "google_compute_firewall",
        },
        "required": {
            "google_compute_network": 1, "google_compute_subnetwork": 1,
            "google_compute_router": 1, "google_compute_address": 1,
            "google_compute_router_nat": 1, "google_compute_instance": 1,
        },
        "invariants": [
            ["nat", "router"], ["nat", "subnetwork"], ["regional", "address"],
            ["manual", "address"], ["no external ip"], ["no ingress"],
        ],
        "contradictions": ["standalone cloud nat resource", "vm has a public ip", "access_config enabled"],
    },
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def schema_instruction() -> str:
    return "Return JSON only, matching this shape: " + json.dumps(OUTPUT_CONTRACT)


def score(provider: str, output: Any) -> dict[str, Any]:
    spec = PROVIDERS[provider]
    if not isinstance(output, dict):
        return {"valid": False, "requiredRecall": 0.0, "unsupportedTypes": [],
                "invariantsCovered": 0, "invariantsTotal": len(spec["invariants"]),
                "contradictions": []}
    resources = output.get("resources")
    dependencies = output.get("dependencies")
    assertions = output.get("assertions")
    valid = isinstance(resources, list) and isinstance(dependencies, list) and isinstance(assertions, list)
    types = [str(item.get("type", "")).lower() for item in resources or [] if isinstance(item, dict)]
    required_units = sum(spec["required"].values())
    found_units = sum(min(types.count(kind), count) for kind, count in spec["required"].items())
    unsupported = sorted({kind for kind in types if kind not in spec["allow"]})
    corpus = json.dumps(output, ensure_ascii=False).lower()
    covered = [all(term in corpus for term in terms) for terms in spec["invariants"]]
    contradictions = [phrase for phrase in spec["contradictions"] if phrase in corpus]
    return {
        "valid": valid,
        "requiredRecall": round(found_units / required_units, 3),
        "requiredFound": found_units,
        "requiredTotal": required_units,
        "unsupportedTypes": unsupported,
        "invariantsCovered": sum(covered),
        "invariantsTotal": len(covered),
        "invariantChecks": covered,
        "contradictions": contradictions,
    }


class Runner:
    def __init__(self) -> None:
        load_dotenv()
        self.model = os.getenv("MODEL", "openai/gpt-oss-120b")
        self.temperature = float(os.getenv("TEMPERATURE", "0"))
        self.seed = int(os.getenv("SEED", "42"))
        self.client = OpenAI(
            api_key=os.environ["API_KEY"], base_url=os.getenv("BASE_URL"),
            timeout=600, max_retries=0,
        )
        self.calls: list[dict[str, Any]] = []

    def call(self, label: str, system: str, user: str) -> Any:
        started_at = now()
        started = time.monotonic()
        record: dict[str, Any] = {"label": label, "startedAt": started_at,
                                  "system": system, "user": user}
        try:
            response = self.client.chat.completions.create(
                model=self.model, temperature=self.temperature, seed=self.seed,
                max_tokens=2500, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
            )
            content = response.choices[0].message.content or "{}"
            record.update({
                "ok": True, "response": content,
                "usage": response.usage.model_dump() if response.usage else None,
                "systemFingerprint": response.system_fingerprint,
            })
            parsed = json.loads(content)
        except Exception as exc:
            record.update({"ok": False, "errorType": type(exc).__name__,
                           "error": str(exc)[:500]})
            parsed = None
        record["finishedAt"] = now()
        record["durationSeconds"] = round(time.monotonic() - started, 3)
        self.calls.append(record)
        print(f"{label:28} {'OK' if record['ok'] else record['errorType']}", flush=True)
        return parsed

    @staticmethod
    def common(provider: str) -> str:
        facts = "\n".join(f"- {fact}" for fact in PROVIDERS[provider]["evidence"])
        return (
            f"Requirement:\n{SCENARIO}\n\nProvider-native evidence:\n{facts}\n\n"
            "Use Terraform provider resource type names in the resource graph; do not emit HCL."
        )

    def run_cell(self, provider: str, arm: str) -> dict[str, Any]:
        common = self.common(provider)
        system = "You are a conservative cloud infrastructure planner. Do not invent resource types."
        if arm == "direct":
            output = self.call(
                f"{provider}.direct", system,
                f"{common}\n\nProduce the {provider} native resource graph directly.\n{schema_instruction()}",
            )
            intermediate = None
        elif arm == "planned":
            intermediate = self.call(
                f"{provider}.planned.plan", system,
                f"{common}\n\nReturn JSON with one key, plan, containing an ordered natural-language implementation plan.",
            )
            output = self.call(
                f"{provider}.planned.realize", system,
                f"{common}\n\nPrior plan:\n{json.dumps(intermediate)}\n\nProduce the {provider} native resource graph.\n{schema_instruction()}",
            )
        else:
            intermediate = self.call(
                f"{provider}.neutral.intent", system,
                f"{common}\n\nTyped neutral contract:\n{json.dumps(NEUTRAL_CONTRACT)}\n\n"
                "Instantiate only relevant concepts and relations. Return JSON with concepts, relations, and variationDecisions.",
            )
            output = self.call(
                f"{provider}.neutral.realize", system,
                f"{common}\n\nTyped neutral contract:\n{json.dumps(NEUTRAL_CONTRACT)}\n\n"
                f"Instantiated neutral intent:\n{json.dumps(intermediate)}\n\n"
                f"Realize it as a {provider} native resource graph.\n{schema_instruction()}",
            )
        return {"provider": provider, "arm": arm, "intermediate": intermediate,
                "output": output, "score": score(provider, output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("refusing external LLM calls without --execute")
    if RESULTS.exists():
        parser.error(f"refusing to overwrite existing result: {RESULTS}")
    runner = Runner()
    started = now()
    cells = [runner.run_cell(provider, arm) for provider in PROVIDERS
             for arm in ("direct", "planned", "neutral")]
    document = {
        "schemaVersion": 1, "experiment": "llm-neutral-pilot-2026-08-07",
        "startedAt": started, "finishedAt": now(), "model": runner.model,
        "temperature": runner.temperature, "seed": runner.seed,
        "scenario": SCENARIO, "neutralContract": NEUTRAL_CONTRACT,
        "cells": cells, "calls": runner.calls,
    }
    RESULTS.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(cell["score"]["valid"] for cell in cells) else 2


if __name__ == "__main__":
    raise SystemExit(main())
