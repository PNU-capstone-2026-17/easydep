"""지원하는 모든 배포 템플릿의 실제 생성 패키지를 정적 검사한다.

다이어그램용 예제 그래프에서 별도 시험용 Terraform을 만들지 않는다. 실제 서비스가 쓰는
WorkloadGraph → ResourcePlan → OpenTofu → 배포 패키지 경로를 그대로 호출한 뒤 Trivy와
패키지 검사를 실행한다. 상세 JSON은 실행 산출물에, 사람이 읽는 요약은 문서에 남긴다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.design.services.deployment_diagram.bundle import build_deployment_diagram_bundle
from app.implementation.delivery.iac_renderer import render_open_tofu
from app.implementation.delivery.package import render_deployment_package
from app.testing.nodes.static_verification import static_verification_node
from app.validation import stable_digest
from scripts.generate_deployment_diagram_examples import (
    CASE_EXPECTATIONS,
    DEPLOYMENT_CASES,
    TARGETS,
    deployment_case_graph,
    deployment_resource_spec,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = REPOSITORY_ROOT / "artifacts" / "deployment-template-security"


def expected_matrix() -> set[tuple[str, str]]:
    """현재 production 예제 목록과 지원 provider의 모든 조합을 돌려준다."""

    return {(case_id, provider) for case_id in DEPLOYMENT_CASES for provider in TARGETS}


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_name(case_id: str) -> str:
    """긴 case ID를 문서에서 바로 이해할 수 있는 짧은 구조 설명으로 바꾼다."""

    item = CASE_EXPECTATIONS[case_id]
    compute = (
        "managed VM group"
        if item["computeKind"] == "managedVmGroup"
        else "standalone VM"
    )
    return (
        f"{compute}, {item['computeUnitCount']} compute unit(s), "
        f"{item['workloadCount']} workload(s), {item['replicaCount']} replica(s), "
        f"{item['zoneCount']} zone(s), {item['ingressKind']}"
    )


def _command_summary(report: dict[str, Any]) -> list[dict[str, Any]]:
    """임시 절대 경로를 제외하고 재현에 필요한 명령 결과만 저장한다."""

    return [
        {
            "name": str(item.get("name") or ""),
            "status": str(item.get("status") or ""),
            "exitCode": item.get("exitCode"),
            "output": str(
                item.get("output") or item.get("error") or item.get("reason") or ""
            )[-4000:],
        }
        for item in report.get("commands") or []
        if isinstance(item, dict)
    ]


def verify_case(case_id: str, provider: str) -> dict[str, Any]:
    """한 템플릿을 실제 사용자 배포 패키지로 만든 뒤 같은 도구로 검사한다."""

    with tempfile.TemporaryDirectory(
        prefix=f"easydep-security-{provider}-"
    ) as temporary_name:
        application = Path(temporary_name) / "application"
        application.mkdir()

        bundle = build_deployment_diagram_bundle(
            deployment_case_graph(case_id), deployment_resource_spec(provider)
        )
        projection = bundle["projections"][0]
        if projection.get("status") != "completed":
            raise RuntimeError(
                f"Deployment projection is incomplete: {provider}/{case_id}"
            )
        resource_plan = projection["resourcePlan"]
        rendered_tofu = render_open_tofu(resource_plan)
        package = render_deployment_package(application, resource_plan, rendered_tofu)

        # Testing 단계와 똑같은 node를 호출해야 topology 기반 허용 조건과 도구 실패
        # 분류까지 실제 사용자 실행과 일치한다.
        # 이 스크립트는 LangGraph 전체 state가 아니라 정적 node가 읽는 입력만 만든다.
        # ``Any``로 경계를 명시해 누락된 graph 출력 key를 가짜 값으로 채우지 않는다.
        verification_input: Any = {
            "application_dir": str(application),
            "testing_input": {
                "contract_artifacts": {
                    "deployment": {"content": {"resourcePlan": resource_plan}}
                }
            },
            "deployment_package_expected": True,
        }
        verification = static_verification_node(verification_input)
        static_report = verification["static_report"]
        trivy_report = static_report.get("trivyScan") or {}
        package_report = static_report.get("deploymentPackage") or {}

        files = [
            {
                "path": path.relative_to(application).as_posix(),
                "sha256": _file_digest(path),
            }
            for path in sorted(package.rglob("*"))
            if path.is_file()
        ]
        trivy_status = str(trivy_report.get("gateStatus") or "INCONCLUSIVE")
        package_status = str(package_report.get("gateStatus") or "INCONCLUSIVE")
        passed = trivy_status == "PASS" and package_status == "PASS"
        return {
            "caseId": case_id,
            "caseName": _case_name(case_id),
            "provider": provider,
            "region": deployment_resource_spec(provider)["region"],
            "resourcePlanDigest": stable_digest(resource_plan),
            "files": files,
            "trivy": {
                "status": trivy_status,
                "issues": [str(item) for item in trivy_report.get("issues") or []],
                "findings": [
                    dict(item)
                    for item in trivy_report.get("findings") or []
                    if isinstance(item, dict)
                ],
                "tool": str(trivy_report.get("tool") or "trivy"),
                "commands": _command_summary(trivy_report),
                "targets": [str(item) for item in trivy_report.get("targets") or []],
            },
            "deploymentPackage": {
                "status": package_status,
                "issues": [str(item) for item in package_report.get("issues") or []],
                "commands": _command_summary(package_report),
            },
            # 현재는 검토 없이 경고를 숨기지 않는다. topology상 꼭 필요한 예외가 실제로
            # 발견되면 rule ID와 case 조건을 근거로 이 목록을 좁게 채운다.
            "allowedFindings": list(trivy_report.get("allowedFindings") or []),
            "passed": passed,
        }


def _write_details(root: Path, result: dict[str, Any]) -> None:
    target = root / str(result["provider"]) / f"{result['caseId']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_document(path: Path, summary: dict[str, Any]) -> None:
    results = list(summary["results"])
    provider_rows: list[str] = []
    for provider in TARGETS:
        selected = [item for item in results if item["provider"] == provider]
        trivy_findings = sum(len(item["trivy"]["issues"]) for item in selected)
        allowed_findings = sum(len(item["allowedFindings"]) for item in selected)
        failed = sum(not item["passed"] for item in selected)
        provider_rows.append(
            f"| {provider.upper()} | {len(selected)} | {trivy_findings} | "
            f"{allowed_findings} | {failed} |"
        )

    case_rows = [
        "| Provider | Template case | Structure | Trivy | Package |",
        "|---|---|---|---|---|",
        *[
            "| {provider} | `{case}` | {name} | {trivy} | {package} |".format(
                provider=str(item["provider"]).upper(),
                case=item["caseId"],
                name=item["caseName"],
                trivy=item["trivy"]["status"],
                package=item["deploymentPackage"]["status"],
            )
            for item in results
        ],
    ]
    failures = [item for item in results if not item["passed"]]
    failure_lines = (
        [
            f"- `{item['provider']}/{item['caseId']}`: "
            + "; ".join(
                [
                    *item["trivy"]["issues"],
                    *item["deploymentPackage"]["issues"],
                ][:5]
            )
            for item in failures
        ]
        or ["- 없음"]
    )
    allowed_groups: dict[tuple[str, str, str], int] = {}
    for item in results:
        for finding in item["allowedFindings"]:
            key = (
                str(finding.get("ruleId") or ""),
                str(finding.get("condition") or ""),
                str(finding.get("reason") or ""),
            )
            allowed_groups[key] = allowed_groups.get(key, 0) + 1
    allowed_lines = [
        f"- `{rule_id}` {count}건 — 조건: {condition}; 근거: {reason}"
        for (rule_id, condition, reason), count in sorted(allowed_groups.items())
    ] or ["- 없음"]
    text = "\n".join(
        [
            "# 배포 템플릿 보안 검증 결과",
            "",
            f"- 실행 ID: `{summary['runId']}`",
            f"- 실행 시각(UTC): `{summary['generatedAt']}`",
            f"- 검사 조합: {summary['actualCount']} / {summary['expectedCount']}",
            f"- 최종 결과: **{'PASS' if summary['passed'] else 'FAIL'}**",
            "",
            "이 검사는 문서용 예시가 아니라 사용자가 받는 것과 같은 배포 패키지를 생성한 뒤 ",
            "Trivy, OpenTofu, cloud-init, Compose 및 배포 스크립트 검사를 수행한다. OpenTofu는 ",
            "실제 리소스를 만들지 않는 `init -backend=false`와 `validate`까지만 실행한다.",
            "",
            "## Provider별 요약",
            "",
            "| Provider | 조합 수 | 차단 finding | 검토 후 허용 | 실패 조합 |",
            "|---|---:|---:|---:|---:|",
            *provider_rows,
            "",
            "## 전체 조합",
            "",
            *case_rows,
            "",
            "## 실패 및 검토가 필요한 항목",
            "",
            *failure_lines,
            "",
            "## 배포 구조에 따라 허용한 항목",
            "",
            "아래 항목은 규칙 전체를 무시한 것이 아니다. ResourcePlan에 공개 진입점이나 ",
            "필요한 외부 통신이 명시된 조합에만 같은 조건을 다시 확인한 뒤 허용했다.",
            "",
            *allowed_lines,
            "",
            "상세 명령 결과와 파일 digest는 Git에 넣지 않는 ",
            f"`artifacts/deployment-template-security/{summary['runId']}/` 아래에 있다.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def verify_all(
    *,
    run_id: str,
    output_root: Path,
    document: Path | None,
    workers: int,
    providers: tuple[str, ...],
    cases: tuple[str, ...],
) -> dict[str, Any]:
    """선택한 행렬을 검사하되 결과 순서는 항상 provider/case 순서로 고정한다."""

    unknown_providers = set(providers) - set(TARGETS)
    unknown_cases = set(cases) - set(DEPLOYMENT_CASES)
    if unknown_providers or unknown_cases:
        raise ValueError(
            f"Unknown providers={sorted(unknown_providers)}, cases={sorted(unknown_cases)}"
        )

    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    futures = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for provider in providers:
            for case_id in cases:
                futures[executor.submit(verify_case, case_id, provider)] = (
                    case_id,
                    provider,
                )
        results = [future.result() for future in as_completed(futures)]

    order = {
        (case_id, provider): index
        for index, (case_id, provider) in enumerate(
            (case_id, provider)
            for provider in providers
            for case_id in cases
        )
    }
    results.sort(key=lambda item: order[(item["caseId"], item["provider"])])
    for result in results:
        _write_details(run_root, result)

    selected_matrix = {(case_id, provider) for case_id in cases for provider in providers}
    actual_matrix = {(item["caseId"], item["provider"]) for item in results}
    full_matrix = providers == tuple(TARGETS) and cases == tuple(DEPLOYMENT_CASES)
    coverage_ok = actual_matrix == selected_matrix and (
        not full_matrix or actual_matrix == expected_matrix()
    )
    summary = {
        "schemaVersion": "easydep-deployment-template-security/v1",
        "runId": run_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "expectedCount": len(selected_matrix),
        "actualCount": len(results),
        "coverageComplete": coverage_ok,
        "passed": coverage_ok and all(item["passed"] for item in results),
        "results": results,
    }
    (run_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if document is not None:
        _write_document(document, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-id",
        default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument(
        "--document",
        type=Path,
        default=None,
        help=(
            "검증 요약 문서를 갱신할 때만 경로를 지정합니다. 상세 결과는 항상 "
            "--output-root 아래에 저장됩니다."
        ),
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--provider", action="append", choices=tuple(TARGETS))
    parser.add_argument("--case", action="append", choices=tuple(DEPLOYMENT_CASES))
    arguments = parser.parse_args()

    summary = verify_all(
        run_id=arguments.run_id,
        output_root=arguments.output_root,
        document=arguments.document,
        workers=arguments.workers,
        providers=tuple(arguments.provider or TARGETS),
        cases=tuple(arguments.case or DEPLOYMENT_CASES),
    )
    print(
        f"Checked {summary['actualCount']} deployment template/provider combinations: "
        f"{'PASS' if summary['passed'] else 'FAIL'}"
    )
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
