"""근거 계약에 따라 Terraform의 벤더별 구성 요소와 연결 관계를 관측한다."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CONTRACT = (
    Path(__file__).with_name("research_protocol")
    / "definitions"
    / "component-projections.json"
)


@lru_cache(maxsize=1)
def load_component_contract() -> dict[str, Any]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def derive_component_dependency_expectations(
    provider: str, delta_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """선택한 근거 projection에서 평가 관계를 기계적으로 파생한다."""
    contract = load_component_contract()
    by_id = {item["id"]: item for item in contract.get("deltas") or []}
    structural: list[dict[str, Any]] = []
    cardinalities: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    for delta_id in delta_ids:
        delta = by_id.get(delta_id)
        if delta is None:
            raise KeyError(f"Unknown component projection delta: {delta_id}")
        realization = (delta.get("realizations") or {}).get(provider)
        if realization is None:
            raise KeyError(f"Provider realization is absent: {delta_id}/{provider}")
        evidence = list(realization.get("evidence") or [])
        component_kinds = {
            item["id"]: item.get("terraformKind") for item in realization.get("components") or []
        }
        for relation in realization.get("relations") or []:
            base = {
                "delta": delta_id,
                "from": relation["from"],
                "to": relation["to"],
                "evidence": evidence,
            }
            # guest 설정 및 제약만으로 표현된 관계는 Terraform reference가 아니다.
            if relation.get("cardinality") and not any(
                component_kinds.get(endpoint) == "guestConfiguration"
                for endpoint in (relation["from"], relation["to"])
            ):
                structural.append(dict(base))
                cardinalities.append({**base, "cardinality": relation["cardinality"]})
            if relation.get("constraint"):
                constraints.append({**base, "constraint": relation["constraint"]})
    return {
        "structuralReferences": structural,
        "cardinalities": cardinalities,
        "constraints": constraints,
    }


def _blocks(attributes: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = attributes.get(name, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _references(value: Any) -> set[str]:
    text = json.dumps(value, ensure_ascii=False)
    pattern = re.compile(
        r"(?P<data>data\.)?(?P<type>(?:aws|azurerm|google)_[A-Za-z0-9_]+)\."
        r"(?P<name>[A-Za-z0-9_-]+)"
    )
    return {
        f"{'data.' if match.group('data') else ''}{match.group('type')}.{match.group('name')}"
        for match in pattern.finditer(text)
    }


def _provider(resources: list[dict[str, Any]]) -> str | None:
    prefixes = {item["providerType"].split("_", 1)[0] for item in resources}
    found = prefixes & {"aws", "azurerm", "google"}
    if len(found) != 1:
        return None
    return {"aws": "aws", "azurerm": "azure", "google": "gcp"}[found.pop()]


def _native_alternatives(terraform_type: str) -> list[str]:
    return [item.strip() for item in terraform_type.split("|") if item.strip()]


def _guest_mounts(deployment_text: str) -> list[str]:
    """셸 설정에서 mount 대상 후보를 찾되 앱별 경로는 가정하지 않는다."""
    paths: set[str] = set()
    template_values = {
        match.group("name"): match.group("path")
        for match in re.finditer(
            r'^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"(?P<path>/[^"\r\n]+)"\s*,?\s*$',
            deployment_text,
            re.MULTILINE,
        )
    }
    patterns = (
        r"\bmount(?:\s+--?\S+)*\s+\S+\s+(?P<path>/\S+)",
        r"\b(?:echo|printf)\b.*\s(?P<path>/\S+)\s+\S+.*(?:fstab|/etc/fstab)",
    )
    for line in deployment_text.splitlines():
        line = re.sub(
            r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}",
            lambda match: template_values.get(match.group("name"), match.group(0)),
            line,
        )
        for pattern in patterns:
            match = re.search(pattern, line.strip())
            if match:
                paths.add(match.group("path").rstrip("'\";,)"))
    return sorted(paths)


def _component_instances(
    component: dict[str, Any],
    resources: list[dict[str, Any]],
    deployment_text: str,
    expected_mount_path: str | None = None,
) -> list[dict[str, Any]]:
    kind = component["terraformKind"]
    if kind == "guestConfiguration":
        mounts = _guest_mounts(deployment_text)
        if expected_mount_path is not None:
            mounts = [path for path in mounts if path == expected_mount_path]
        return [
            {"id": f"guest:mount:{path}", "kind": kind, "attributes": {"path": path}}
            for path in mounts
        ]

    instances: list[dict[str, Any]] = []
    alternatives = _native_alternatives(component["terraformType"])
    accepts_resource = kind in {"resource", "resourceOrDataSource", "resourceOrNestedBlock"}
    accepts_data = kind == "resourceOrDataSource"
    if accepts_resource:
        plain_types = {item for item in alternatives if "." not in item}
        for resource in resources:
            if resource["providerType"] not in plain_types:
                continue
            if resource["declarationKind"] == "data" and not accepts_data:
                continue
            instances.append(
                {
                    "id": resource["address"],
                    "kind": resource["declarationKind"],
                    "attributes": resource["attributes"],
                    "owner": resource["address"],
                }
            )

    if kind in {"nestedBlock", "resourceOrNestedBlock"}:
        for alternative in alternatives:
            if "." not in alternative:
                continue
            owner_type, block_name = alternative.rsplit(".", 1)
            for resource in resources:
                if resource["providerType"] != owner_type:
                    continue
                for index, attributes in enumerate(_blocks(resource["attributes"], block_name)):
                    instances.append(
                        {
                            "id": f"{resource['address']}#{block_name}[{index}]",
                            "kind": "nestedBlock",
                            "block": block_name,
                            "attributes": attributes,
                            "owner": resource["address"],
                        }
                    )
    return instances


def _external_instances(
    component_id: str, resources: list[dict[str, Any]], observed: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    if component_id in observed:
        return observed[component_id]
    concept_by_id = {
        "vm": "vm",
        "subnet": "subnet",
        "nic": "nic",
        "loadBalancer": "loadBalancer",
        "gateway": "loadBalancer",
        "forwardingRule": "loadBalancer",
        "urlMap": "listenerRule",
    }
    concept = concept_by_id.get(component_id)
    return (
        [
            {
                "id": item["address"],
                "kind": item["declarationKind"],
                "attributes": item["attributes"],
                "owner": item["address"],
            }
            for item in resources
            if item["concept"] == concept
        ]
        if concept
        else []
    )


def _linked(source: dict[str, Any], target: dict[str, Any]) -> bool:
    if source["id"].startswith("guest:mount:"):
        return False
    if source.get("owner") == target["id"] and source["kind"] == "nestedBlock":
        return True
    references = _references(source["attributes"])
    if target["id"] in references or target.get("owner") in references:
        return True
    if source.get("owner") == target.get("owner") and source["kind"] == "nestedBlock":
        target_name = str(target["attributes"].get("name", "")).strip('"')
        return bool(
            target_name and target_name in json.dumps(source["attributes"], ensure_ascii=False)
        )
    return False


def analyze_component_projections(
    resources: list[dict[str, Any]],
    deployment_text: str,
    *,
    expected_mount_path: str | None = None,
) -> dict[str, Any]:
    provider = _provider(resources)
    if provider is None:
        return {"status": "not-applicable", "provider": None, "deltas": {}}
    deltas: dict[str, Any] = {}
    for delta in load_component_contract()["deltas"]:
        realization = delta["realizations"][provider]
        observed = {
            item["id"]: _component_instances(
                item, resources, deployment_text, expected_mount_path
            )
            for item in realization["components"]
        }
        component_checks = [
            {
                "componentId": item["id"],
                "terraformKind": item["terraformKind"],
                "terraformType": item.get("terraformType"),
                "instances": [instance["id"] for instance in observed[item["id"]]],
                "status": (
                    "observed-unverified"
                    if item["terraformKind"] == "guestConfiguration"
                    and observed[item["id"]]
                    else "passed"
                    if observed[item["id"]]
                    else "failed"
                ),
            }
            for item in realization["components"]
        ]
        relation_checks = []
        constraint_checks = []
        for relation in realization["relations"]:
            sources = _external_instances(relation["from"], resources, observed)
            targets = _external_instances(relation["to"], resources, observed)
            pairs = [
                [source["id"], target["id"]]
                for source in sources
                for target in targets
                if _linked(source, target)
            ]
            relation_checks.append(
                {
                    "from": relation["from"],
                    "to": relation["to"],
                    "cardinality": relation.get("cardinality"),
                    "observedPairs": pairs,
                    "status": "observed-unverified" if pairs else "failed",
                }
            )
            if relation.get("constraint"):
                constraint_checks.append(
                    {
                        "from": relation["from"],
                        "to": relation["to"],
                        "constraint": relation["constraint"],
                        "status": "requires-separate-gate",
                    }
                )
        deltas[delta["id"]] = {
            "components": component_checks,
            "relations": relation_checks,
            "constraints": constraint_checks,
        }
    return {
        "status": "available",
        "provider": provider,
        "deltas": deltas,
        "guestMountPaths": _guest_mounts(deployment_text),
    }
