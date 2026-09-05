"""Testing에서 실패한 gate를 수리 작업 안에서 다시 실행한다.

이 모듈은 새 검증 framework가 아니다. 최초 Testing이 사용하는 Trivy,
배포 package, OpenTofu, HTTP 실행기를 같은 입력으로 다시 호출하는 작은
adapter다. 따라서 Gradle 통과만으로 Trivy 수리가 완료되는 잘못된 판정을
피할 수 있다.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from app.testing.nodes.static_verification import static_verification_node
from app.testing.runtime.app_container import ApplicationLaunchError, running_application
from app.testing.schemas.functional_plan import (
    FunctionalInputValue,
    FunctionalTestCase,
    FunctionalTestPlan,
)
from app.testing.utils.functional_executor import execute_functional_plan
from app.testing.utils.gates import gate_status


def _profile_openapi(profile: dict[str, Any]) -> dict[str, Any] | None:
    value = profile.get("openapi")
    if isinstance(value, dict):
        return value
    testing_input = profile.get("testing_input") or profile.get("testingInput")
    contracts = (
        testing_input.get("contract_artifacts") or testing_input.get("contractArtifacts")
        if isinstance(testing_input, dict)
        else None
    )
    artifact = contracts.get("openapi") if isinstance(contracts, dict) else None
    content = artifact.get("content") if isinstance(artifact, dict) else None
    return content if isinstance(content, dict) else None


def _profile_case(profile: dict[str, Any]) -> FunctionalTestCase | None:
    raw = profile.get("functional_case") or profile.get("functionalCase")
    if isinstance(raw, dict):
        return FunctionalTestCase.model_validate(raw)
    plan = profile.get("candidate_plan") or profile.get("candidatePlan")
    if not isinstance(plan, dict):
        return None
    # candidatePlan은 당시 LLM이 제안한 leaf 값도 함께 보존한다. 값은 아래
    # _profile_inputs가 읽고, 순수 실행 순서 모델에는 cases만 전달한다.
    plan_value = dict(plan)
    plan_value.pop("inputValues", None)
    candidate = FunctionalTestPlan.model_validate(plan_value)
    case_id = str(profile.get("case_id") or profile.get("caseId") or "")
    return next(
        (case for case in candidate.cases if not case_id or case.case_id == case_id),
        None,
    )


def _profile_inputs(
    profile: dict[str, Any],
    case: FunctionalTestCase,
) -> list[FunctionalInputValue]:
    """최초 실패 때 사용한 leaf 값을 같은 case 재검사에 돌려준다."""

    plan = profile.get("candidate_plan") or profile.get("candidatePlan")
    values = plan.get("inputValues") if isinstance(plan, dict) else None
    items = values.get(case.case_id) if isinstance(values, dict) else None
    if not isinstance(items, list):
        return []
    return [FunctionalInputValue.model_validate(item) for item in items]


def _failed_command(report: dict[str, Any]) -> dict[str, Any]:
    return next(
        (
            dict(item)
            for item in report.get("commands") or []
            if isinstance(item, dict)
            and str(item.get("status") or "").upper() not in {"PASS", "PASSED"}
        ),
        {},
    )


def _command_evidence(gate: str, report: dict[str, Any]) -> dict[str, object]:
    """gate 보고서를 기존 OpenHands 검사 오류 형식으로 바꾼다."""
    failed = _failed_command(report)
    issues = [str(item) for item in report.get("issues") or []]
    status = gate_status(report)
    return {
        "command": failed.get("command") or [f"testing-{gate}"],
        "exitCode": failed.get("exitCode") if failed else (0 if status == "PASS" else 1),
        "durationMs": 0,
        "stdout": "",
        "stderr": "\n".join(issues)
        or str(failed.get("output") or failed.get("error") or failed.get("reason") or ""),
        "testResults": "",
        "gate": gate,
        "gateStatus": status,
        "gateEvidence": report,
    }


def _static_gate(
    sandbox: Path,
    gate: str,
    profile: dict[str, Any],
) -> dict[str, object]:
    state = {
        "application_dir": str(sandbox / "application"),
        "testing_input": profile.get("testing_input") or profile.get("testingInput") or {},
        "deployment_package_expected": profile.get("deployment_package_expected"),
        # 구현 에이전트 안의 수리 확인은 원래 실패한 gate 하나만 실행한다. 결과에서
        # 하나를 골라내기만 하면 Trivy·package·OpenTofu가 모두 실행되어 선택 검사의
        # 시간 절약과 오류 분리가 사라진다.
        "gate_scope": [gate],
    }
    result = static_verification_node(state)  # type: ignore[arg-type]
    static = result.get("static_report") or {}
    selected = {
        # static finding은 Trivy config scan 자체이다. package/OpenTofu는 각자
        # 별도 task type으로 재현해 서로 무관한 도구 오류에 막히지 않게 한다.
        "static": static.get("trivyScan") or static,
        "package": static.get("deploymentPackage") or {},
        "iac": result.get("iac_report") or {},
    }[gate]
    return _command_evidence(gate, selected)


def _dynamic_gate(sandbox: Path, profile: dict[str, Any]) -> dict[str, object]:
    openapi = _profile_openapi(profile)
    case = _profile_case(profile)
    if openapi is None or case is None:
        message = (
            "Dynamic Testing repair is missing the frozen OpenAPI document or the failed "
            "FunctionalTestCase. Recreate the feedback job from its blocking finding."
        )
        return _command_evidence(
            "dynamicFunctional",
            {
                "gateStatus": "INCONCLUSIVE",
                "issues": [message],
                "commands": [],
            },
        )
    app_id = str(profile.get("app_id") or profile.get("appId") or "testing-repair")
    try:
        with running_application(
            app_id,
            str(sandbox / "application"),
            launch_id=f"repair-{uuid.uuid4().hex[:12]}",
        ) as (target_url, _application):
            result = execute_functional_plan(
                case,
                openapi=openapi,
                target_url=target_url,
                preserved_inputs=_profile_inputs(profile, case),
            )
    except ApplicationLaunchError as error:
        return _command_evidence(
            "dynamicFunctional",
            {
                "gateStatus": "INCONCLUSIVE"
                if error.defect_class == "ENVIRONMENT_DEFECT"
                else "FAIL",
                "issues": [str(error)],
                "commands": [],
            },
        )
    commands = [
        {
            "name": f"HTTP {item.get('method', '')} {item.get('path', '')}".strip(),
            "command": [item.get("method"), item.get("path")],
            "status": "PASS"
            if 200 <= int(item.get("statusCode") or 0) < 300
            else "FAIL",
            "exitCode": 0
            if 200 <= int(item.get("statusCode") or 0) < 300
            else 1,
            "output": str((result.get("finding") or {}).get("responseBody") or ""),
        }
        for item in result.get("steps") or []
        if isinstance(item, dict)
    ]
    return _command_evidence(
        "dynamicFunctional",
        {
            **result,
            "issues": [str(result.get("reason"))] if result.get("reason") else [],
            "commands": commands,
        },
    )


def verify_testing_repair_gate(
    sandbox: Path,
    gate: str,
    profile: dict[str, Any] | None = None,
) -> dict[str, object]:
    """수리 작업에 지정된 원래 Testing gate 하나를 다시 실행한다."""
    normalized = gate.removeprefix("testing-")
    values = dict(profile or {})
    if normalized in {"static", "package", "iac"}:
        return _static_gate(sandbox, normalized, values)
    if normalized in {"dynamic", "dynamic-functional", "dynamicFunctional"}:
        return _dynamic_gate(sandbox, values)
    raise ValueError(f"Unknown Testing repair gate: {gate}")


__all__ = ["verify_testing_repair_gate"]
