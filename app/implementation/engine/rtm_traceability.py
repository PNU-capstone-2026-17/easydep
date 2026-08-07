"""Requirements and Design Traceability Matrix (RTM) for EasyDep Implementation Agent."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "implementation-rtm-traceability/v1alpha1"


def build_rtm_traceability_map(spec: Any, run_root: Path) -> dict[str, Any]:
    """Build and persist an RTM Traceability Map linking implementation files to design artifacts."""
    from .implementation_ir import build_implementation_ir

    ir = build_implementation_ir(spec, run_root)
    package_path = spec.base_package.replace(".", "/")
    package_root = run_root / "application" / "src" / "main" / "java" / package_path
    test_root = run_root / "application" / "src" / "test" / "java" / package_path

    mappings: list[dict[str, Any]] = []

    # 1. Control Services & Contracts (BCE)
    for control in ir.controls:
        contract_file = package_root / "bce" / f"{control}.java"
        impl_file = package_root / "application" / "impl" / f"{control}Service.java"
        test_file = test_root / "application" / "impl" / f"{control}ServiceTest.java"

        mappings.append({
            "target_file": _posix(contract_file, run_root),
            "element_name": control,
            "origin_artifact": "bceClass",
            "origin_element": f"component {control} <<control>>",
            "contract_level": "IMMUTABLE_CONTRACT",
            "allowed_edits": [],
            "description": f"BCE Control contract interface for {control}",
        })
        mappings.append({
            "target_file": _posix(impl_file, run_root),
            "element_name": f"{control}Service",
            "origin_artifact": "bceClass",
            "origin_element": f"component {control} <<control>>",
            "contract_level": "IMPLEMENTATION_INTERNAL",
            "allowed_edits": ["METHOD_BODY_ONLY", "PRIVATE_HELPERS"],
            "description": f"Spring Service implementation of Control {control}",
        })
        mappings.append({
            "target_file": _posix(test_file, run_root),
            "element_name": f"{control}ServiceTest",
            "origin_artifact": "bceClass",
            "origin_element": f"component {control} <<control>>",
            "contract_level": "IMPLEMENTATION_INTERNAL",
            "allowed_edits": ["TEST_CASES"],
            "description": f"Unit test suite for Control service {control}",
        })

    # 2. API Adapters & Schemas (OpenAPI)
    for api_port in ir.api_ports:
        api_name = api_port.name
        interface_file = package_root / "api" / f"{api_name}Api.java"
        controller_file = package_root / "adapter" / "in" / "web" / f"{api_name}ApiController.java"

        mappings.append({
            "target_file": _posix(interface_file, run_root),
            "element_name": f"{api_name}Api",
            "origin_artifact": "openapi",
            "origin_element": f"API port {api_name} ({len(api_port.operations)} operations)",
            "contract_level": "IMMUTABLE_CONTRACT",
            "allowed_edits": [],
            "description": f"OpenAPI generated Spring interface for {api_name}",
        })
        mappings.append({
            "target_file": _posix(controller_file, run_root),
            "element_name": f"{api_name}ApiController",
            "origin_artifact": "openapi",
            "origin_element": f"API port {api_name}",
            "contract_level": "IMPLEMENTATION_INTERNAL",
            "allowed_edits": ["METHOD_BODY_ONLY"],
            "description": f"Spring REST Controller implementation for {api_name}Api",
        })

    # 3. Persistence Entities & Repositories (ERD / BCE)
    for entity in ir.entities:
        entity_file = package_root / "persistence" / "entity" / f"{entity}Entity.java"
        repo_file = package_root / "persistence" / "repository" / f"{entity}Repository.java"

        mappings.append({
            "target_file": _posix(entity_file, run_root),
            "element_name": f"{entity}Entity",
            "origin_artifact": "erd",
            "origin_element": f"entity {entity}",
            "contract_level": "DATABASE_SCHEMA_BOUND",
            "allowed_edits": ["GETTERS_SETTERS", "CONSTRUCTORS"],
            "description": f"JPA Entity mapped from ERD entity {entity}",
        })
        mappings.append({
            "target_file": _posix(repo_file, run_root),
            "element_name": f"{entity}Repository",
            "origin_artifact": "erd",
            "origin_element": f"entity {entity}",
            "contract_level": "IMMUTABLE_CONTRACT",
            "allowed_edits": ["CUSTOM_QUERY_METHODS"],
            "description": f"Spring Data JPA Repository for {entity}",
        })

    # 4. Outbound Gateway Adapters (Sequence / BCE)
    for gateway in ir.gateways:
        gw_name = gateway.name
        gw_contract = package_root / "bce" / f"{gw_name}.java"
        mappings.append({
            "target_file": _posix(gw_contract, run_root),
            "element_name": gw_name,
            "origin_artifact": "sequence",
            "origin_element": f"gateway {gw_name} ({gateway.kind})",
            "contract_level": "IMMUTABLE_CONTRACT",
            "allowed_edits": [],
            "description": f"Outbound Gateway contract interface for {gw_name}",
        })

    # 5. Infrastructure & IaC (Deployment Diagram / Cloud Spec)
    mappings.append({
        "target_file": "application/terraform/main.tf",
        "element_name": "TerraformMain",
        "origin_artifact": "cloud",
        "origin_element": "cloud resource specification",
        "contract_level": "INFRASTRUCTURE_SPEC_BOUND",
        "allowed_edits": [],
        "description": "Main Terraform HCL infrastructure declaration",
    })
    mappings.append({
        "target_file": "application/Dockerfile",
        "element_name": "Dockerfile",
        "origin_artifact": "deployment",
        "origin_element": "deployment topology",
        "contract_level": "INFRASTRUCTURE_SPEC_BOUND",
        "allowed_edits": ["ENVIRONMENT_VARIABLES"],
        "description": "Container image build spec",
    })

    rtm_map = {
        "schemaVersion": SCHEMA_VERSION,
        "applicationName": ir.application_name,
        "basePackage": spec.base_package,
        "mappings": mappings,
    }

    reports = run_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "rtm-traceability-map.json").write_text(
        json.dumps(rtm_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return rtm_map


def evaluate_feedback_rtm_traceability(
    feedback: str,
    design: dict[str, Any] | None = None,
    rtm_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate implementation feedback using RTM traceability mapping and contract constraints."""
    text = " ".join(feedback.strip().split())
    design = design or {}
    matches: list[dict[str, str]] = []

    # 1. Rule-based Contract Violation checks
    contract_rules: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
        (
            "CLASS_CONTRACT_CHANGE",
            "bceClass",
            "BCE/class-diagram contract change requested.",
            (
                r"\b(?:bce|class\s+diagram|interface|field|attribute|method\s+signature|return\s+type)\b",
                r"(?:클래스\s*다이어그램|클래스명|인터페이스|필드(?:명|\s*타입)?|속성(?:명|\s*타입)?|메서드\s*(?:명|시그니처|반환\s*타입)|반환\s*타입|파라미터\s*타입)",
            ),
        ),
        (
            "OPENAPI_CONTRACT_CHANGE",
            "openapi",
            "OpenAPI endpoint or schema contract change requested.",
            (
                r"\b(?:openapi|api\s+(?:spec|contract)|endpoint|request\s+body|response\s+(?:body|schema)|http\s+(?:method|status)|dto|schema)\b",
                r"(?:api\s*명세|엔드포인트|요청\s*(?:본문|바디|형식)|응답\s*(?:본문|바디|형식)|상태\s*코드|http\s*메서드|dto|스키마)",
                r"(?:추가|삭제|변경|수정|rename|remove|add)\s*(?:get|post|put|patch|delete)\b",
            ),
        ),
        (
            "SEQUENCE_FLOW_CHANGE",
            "sequence",
            "Sequence diagram message flow change requested.",
            (
                r"\b(?:sequence\s+diagram|call\s+order|message\s+flow)\b",
                r"(?:시퀀스\s*다이어그램|호출\s*순서|메시지\s*흐름|흐름을?\s*(?:변경|수정|추가|삭제))",
            ),
        ),
        (
            "DATA_MODEL_CHANGE",
            "erd",
            "ERD/database schema change requested.",
            (
                r"\b(?:erd|database\s+schema|table|column|entity\s+relationship)\b",
                r"(?:erd|데이터베이스\s*스키마|테이블|컬럼|엔티티\s*(?:관계|추가|삭제|변경))",
            ),
        ),
    )

    for code, artifact, message, patterns in contract_rules:
        for pattern in patterns:
            found = re.search(pattern, text, flags=re.IGNORECASE)
            if found:
                matches.append({
                    "code": code,
                    "match": found.group(0),
                    "originArtifact": artifact,
                    "message": message,
                })
                break

    # 2. RTM Traceability Map & Design Element Name Cross-Validation
    referenced_names = _extract_design_elements(design, rtm_map)
    structural_action = re.search(
        r"(?:추가|삭제|변경|수정|개명|rename|remove|add|change)\b",
        text,
        flags=re.IGNORECASE,
    )

    if structural_action:
        for name, origin_artifact in referenced_names.items():
            if re.search(rf"\b{re.escape(name)}\b", text):
                matches.append({
                    "code": "REFERENCED_DESIGN_STRUCTURE_CHANGE",
                    "match": name,
                    "originArtifact": origin_artifact,
                    "message": f"Design element '{name}' ({origin_artifact}) is bound to an immutable design contract.",
                })
                break

    eligible = not matches
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "ELIGIBLE" if eligible else "UNSUITABLE",
        "feedback": text,
        "matches": matches,
        "rtmValidated": True,
        "nextAction": (
            "Create a constrained implementation feedback revision and run all verification gates."
            if eligible
            else "Do not create or execute an implementation feedback revision for this request."
        ),
    }


def _extract_design_elements(
    design: dict[str, Any], rtm_map: dict[str, Any] | None
) -> dict[str, str]:
    elements: dict[str, str] = {}
    if rtm_map:
        for mapping in rtm_map.get("mappings", []):
            if mapping.get("contract_level") in {"IMMUTABLE_CONTRACT", "DATABASE_SCHEMA_BOUND"}:
                elements[str(mapping["element_name"])] = str(mapping["origin_artifact"])

    class_diagram = str(design.get("class_diagram_puml", ""))
    for name in re.findall(r"(?im)^\s*(?:class|interface|entity)\s+(?:\"[^\"]+\"\s+as\s+)?([A-Za-z_]\w*)", class_diagram):
        if len(name) > 2:
            elements[name] = "bceClass"

    erd_puml = str(design.get("erd_puml", ""))
    for name in re.findall(r"(?im)^\s*entity\s+\"[^\"]+\"\s+as\s+([A-Za-z_]\w*)", erd_puml):
        if len(name) > 2:
            elements[name] = "erd"

    api_spec = design.get("api_spec", {})
    if isinstance(api_spec, dict):
        for name in api_spec.get("components", {}).get("schemas", {}):
            if str(name).isidentifier() and len(str(name)) > 2:
                elements[str(name)] = "openapi"

    return elements


def _posix(path: Path, relative_to: Path) -> str:
    try:
        return path.relative_to(relative_to).as_posix()
    except ValueError:
        return path.as_posix()
