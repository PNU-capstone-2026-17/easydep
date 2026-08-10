"""고정 애플리케이션 스냅숏의 VM 전달 셀을 실행한다."""

from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.core.orchestration.app_cloud_contracts import (
    cloud_contract_from_legacy,
    derive_deployment_bindings,
    infer_application_contract,
)
from app.core.orchestration.contracts import RunMode, StepContext
from app.core.orchestration.providers import LlmVmDeliveryProvider
from evaluation.implementation import evaluate_repository
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT
from evaluation.research_protocol.core.snapshot_context import source_app_id
from evaluation.research_protocol.core.snapshot_support import (
    copy_source,
    tree_sha256,
)
from evaluation.research_protocol.core.support import canonical_json_sha256, read_json


def read_protocol_json(path: Path) -> dict[str, Any]:
    """연구 프로토콜에서 저장소 기준 상대 JSON을 읽는다."""
    return read_json(REPOSITORY_ROOT / path)


def _requirements(
    base: dict[str, Any], case: dict[str, Any], needs: dict[str, Any]
) -> dict[str, Any]:
    result = deepcopy(base)
    result["deployment_needs"] = deepcopy(needs)
    scope = case.get("scope") or {}
    providers = scope.get("providers") or []
    result["resource_spec"] = {
        "provider": str(providers[0]) if providers else "",
        "region": str(scope.get("region") or ""),
    }
    result["source_requirements"] = list(case["requirements"])
    return result


def run_delivery_cell(
    *,
    source: Path,
    output_root: Path,
    condition: str,
    arm: str,
    case: dict[str, Any],
    cell_name: str,
    base_requirements: dict[str, Any],
    design: dict[str, Any],
    needs: dict[str, Any],
    oracle: dict[str, Any],
) -> dict[str, Any]:
    """호출자가 명시한 사례와 입력만 사용해 전달 셀 하나를 실행한다."""
    cell_root = output_root / cell_name
    application = cell_root / "application"
    application.parent.mkdir(parents=True, exist_ok=False)
    copy_source(source, application)
    shutil.rmtree(application / "infra", ignore_errors=True)
    input_sha = tree_sha256(application)
    requirements = _requirements(base_requirements, case, needs)
    use_cloud_kb = arm == "full"
    cloud_design = CloudDesignAdapter().finalize(
        requirements_result=requirements,
        design_result=design,
        use_cloud_kb=use_cloud_kb,
    )
    app_contract = infer_application_contract(application)
    cloud_contract = cloud_contract_from_legacy(requirements)
    cloud_contract, binding_contract = derive_deployment_bindings(
        app_contract, cloud_contract
    )
    payload = {
        "run_root": str(cell_root),
        "requirements_result": requirements,
        "design_result": design,
        "cloud_design_result": cloud_design,
        "resource_constraints_text": case["cloudConstraints"],
        "application_runtime_contract": app_contract.model_dump(
            mode="json", by_alias=True
        ),
        "cloud_capability_contract": cloud_contract.model_dump(
            mode="json", by_alias=True
        ),
        "deployment_binding_contract": binding_contract.model_dump(
            mode="json", by_alias=True
        ),
        "enable_repair_feedback": True,
        "enable_consistency_validator": True,
    }
    started = perf_counter()
    result = LlmVmDeliveryProvider().run(
        payload,
        StepContext(
            run_id=f"component-snapshot-{condition}-{arm}",
            app_id=source_app_id(source),
            mode=RunMode.BATCH,
        ),
    )
    elapsed = perf_counter() - started
    evaluation = evaluate_repository(
        application,
        oracle=oracle,
        run_tools=False,
        case_id=case["caseId"],
    )
    return {
        "condition": condition,
        "arm": arm,
        "caseId": case["caseId"],
        "inputApplicationSha256": input_sha,
        "fixedDeploymentNeeds": needs,
        "fixedDeploymentNeedsSha256": canonical_json_sha256(needs),
        "cloudKbEnabled": use_cloud_kb,
        "modeledOutcomes": (
            (cloud_design.get("dependency_coverage") or {}).get("modeledInputs") or []
        ),
        "realizationIds": [
            item.get("id")
            for item in (cloud_design.get("infra_intent") or {}).get(
                "capabilityRealizations", []
            )
        ],
        "stepStatus": result.status.value,
        "diagnostics": [item.model_dump(mode="json") for item in result.diagnostics],
        "stepMetrics": result.metrics,
        "elapsedSeconds": round(elapsed, 6),
        "evaluation": evaluation,
    }
