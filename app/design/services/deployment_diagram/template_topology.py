"""승인된 입력을 미리 정한 Docker-on-VM 배포 구조에 넣는다.

구조는 기존 WorkloadGraph 정규화기가 만들고 검사한다. 이 모듈은 일반 프로젝트의 기본
애플리케이션 슬롯과 capability가 선택하는 두 가지 경우만 보탠다. LLM은 호출하지 않는다.
"""

from __future__ import annotations

from typing import Any

from app.design.contracts.application_runtime import application_security_source_refs
from app.design.services.deployment_diagram.models import WorkloadGraph
from app.design.services.deployment_diagram.normalization import normalize_workload_graph
from app.design.services.deployment_diagram.planning_facts import extract_planning_facts
from app.design.services.deployment_diagram.workload_contracts import (
    postgresql_runtime_contracts,
)


def _has_explicit_workloads(structured_inputs: dict[str, Any]) -> bool:
    """승인된 workload 계약이 하나라도 있는지 확인한다."""

    return any(
        isinstance(item, dict)
        and item.get("kind") == "workloadContract"
        and item.get("authority") == "explicit"
        and item.get("status") == "accepted"
        and item.get("derivationRule") != "explicit-postgresql-runtime-topology"
        and isinstance(item.get("value"), dict)
        and item["value"].get("workloadId")
        for item in structured_inputs.get("deploymentPlanningFacts") or []
    )


def _default_application(structured_inputs: dict[str, Any]) -> dict[str, Any]:
    """별도 workload 계약이 없을 때 쓰는 단일 애플리케이션 템플릿이다."""

    api_spec = structured_inputs.get("apiSpec")
    paths = api_spec.get("paths") if isinstance(api_spec, dict) else None
    has_http_api = isinstance(paths, dict) and bool(paths)
    resource_spec = structured_inputs.get("resourceSpec")
    spec = resource_spec if isinstance(resource_spec, dict) else {}
    requirements = {
        field: spec[field]
        for field in ("minVCpu", "minMemoryGiB")
        if spec.get(field) is not None
    }
    sources = ["apiSpec:paths"] if has_http_api else ["useCaseSpecification"]
    return {
        "id": "application",
        "name": "Application",
        "artifact": {"kind": "generatedApplication"},
        "interfaces": (
            [
                {
                    "id": "http",
                    "name": "HTTP",
                    "protocol": "http",
                    # 현재 제품의 기본 웹 애플리케이션 경우는 단일 공개 진입점이다.
                    # 내부 전용 구조는 명시적인 workload 계약으로 선택한다.
                    "exposure": "public",
                    "port": None,
                    "healthPath": (
                        "/health"
                        if isinstance(paths, dict) and "/health" in paths
                        else None
                    ),
                    "sourceRefs": sources,
                }
            ]
            if has_http_api
            else []
        ),
        "storage": [],
        "configuration": [],
        "resourceRequirements": requirements,
        "replicationSafety": "unknown",
        "sourceRefs": sources,
    }


def _planning_facts(structured_inputs: dict[str, Any]) -> dict[str, Any]:
    """서비스 입력을 기존 PlanningFact 생성 함수에 이름 그대로 연결한다."""

    return extract_planning_facts(
        refined_requirements=structured_inputs.get("refinedRequirements") or [],
        capability_contract=structured_inputs.get("capabilityContract") or {},
        resource_intake=structured_inputs.get("resourceIntake") or {},
        resource_spec=structured_inputs.get("resourceSpec") or {},
        usecase_spec=structured_inputs.get("useCaseSpecification") or {},
        class_model=structured_inputs.get("classModel") or {},
        sequence_model=structured_inputs.get("sequenceModel") or {},
        api_spec=structured_inputs.get("apiSpec") or {},
        erd_model=structured_inputs.get("erdModel") or {},
        additional_planning_facts=structured_inputs.get("deploymentPlanningFacts") or [],
    )


def _apply_capability_cases(
    graph: dict[str, Any], structured_inputs: dict[str, Any]
) -> None:
    """승인 capability를 기존 storage·managed-group 경우에 대응시킨다."""

    generated = next(
        (
            item
            for item in graph.get("workloads") or []
            if (item.get("artifact") or {}).get("kind") == "generatedApplication"
        ),
        None,
    )
    if generated is None:
        return
    contract = structured_inputs.get("capabilityContract")
    capabilities = contract.get("capabilities") if isinstance(contract, dict) else []
    for capability in capabilities or []:
        if not isinstance(capability, dict) or capability.get("decision") != "accepted":
            continue
        dependencies = set(capability.get("dependencyCapabilityIds") or [])
        source_refs = [
            f"requirement:{item}"
            for item in capability.get("requirementIds") or []
            if str(item)
        ] or [f"capability:{capability.get('id') or 'deployment'}"]
        if "persistent-block-storage" in dependencies and not generated.get("storage"):
            generated["storage"] = [
                {
                    "id": "workload-data",
                    "persistence": "persistent",
                    "capacityGiB": 10,
                    "mountPath": "/var/lib/easydep/data",
                    "deletionPolicy": "retain",
                    "replicaSemantics": "singleAttachment",
                    "sourceRefs": source_refs,
                }
            ]
        managed_id = f"{generated['id']}-managed-replacement"
        needs_load_balancer = (
            "load-balanced-ingress" in dependencies
            and any(
                item.get("exposure") == "public"
                for item in generated.get("interfaces") or []
            )
        )
        if needs_load_balancer and not any(
            item.get("id") == managed_id for item in graph.get("constraints") or []
        ):
            graph.setdefault("constraints", []).append(
                {
                    "id": managed_id,
                    "kind": "managedReplacement",
                    "workloadRefs": [generated["id"]],
                    "value": True,
                    "required": True,
                    "sourceRefs": source_refs,
                }
            )


def _apply_application_security(
    graph: dict[str, Any], structured_inputs: dict[str, Any]
) -> None:
    """인증이 필요한 단일 생성 앱에 실행 계정과 password 입력을 선언한다."""

    source_refs = application_security_source_refs(
        structured_inputs.get("apiSpec"),
        structured_inputs.get("refinedRequirements"),
    )
    generated = [
        item
        for item in graph.get("workloads") or []
        if isinstance(item, dict)
        and (item.get("artifact") or {}).get("kind") == "generatedApplication"
    ]
    if not source_refs or len(generated) != 1:
        return
    configurations = generated[0].setdefault("configuration", [])
    existing_names = {
        str(item.get("name") or "")
        for item in configurations
        if isinstance(item, dict)
    }
    for configuration in (
        {
            "id": "security-username",
            "name": "SPRING_SECURITY_USER_NAME",
            "kind": "value",
            "value": "easydep",
            "sourceRefs": source_refs,
        },
        {
            "id": "security-password",
            "name": "SPRING_SECURITY_USER_PASSWORD",
            "kind": "secretBinding",
            "sensitive": True,
            "sourceRefs": source_refs,
        },
    ):
        if configuration["name"] not in existing_names:
            configurations.append(configuration)


def build_template_workload_graph(structured_inputs: dict[str, Any]) -> WorkloadGraph:
    """코드 템플릿과 승인 계약으로 WorkloadGraph 구조를 완성한다."""

    caller_facts = list(structured_inputs.get("deploymentPlanningFacts") or [])
    generated_contracts = postgresql_runtime_contracts(
        structured_inputs.get("refinedRequirements"),
        capability_contract=structured_inputs.get("capabilityContract"),
        existing_facts=caller_facts,
        resource_spec=structured_inputs.get("resourceSpec"),
    )
    effective_inputs = {
        **structured_inputs,
        "deploymentPlanningFacts": [*caller_facts, *generated_contracts],
    }
    seed = {
        "schemaVersion": "easydep-workload-graph",
        "workloads": (
            []
            if _has_explicit_workloads(structured_inputs)
            else [_default_application(structured_inputs)]
        ),
        "externalDependencies": [],
        "connections": [],
        "constraints": [],
        "derivations": [],
    }
    facts = _planning_facts(effective_inputs)
    graph = normalize_workload_graph(seed, planning_facts=facts)
    _apply_capability_cases(graph, effective_inputs)
    _apply_application_security(graph, effective_inputs)
    # capability가 storage나 managed group을 더했으므로 같은 검증기를 한 번 더 적용한다.
    graph = normalize_workload_graph(graph, planning_facts=facts)
    return WorkloadGraph.model_validate(graph)
