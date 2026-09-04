"""기계 판독 JSON과 사람이 읽는 비율 중심 Markdown 보고서."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .collect import collect_artifacts
from .evaluate import fraction


def _median(runs: list[dict[str, Any]], field: str) -> float | None:
    values = [run[field] for run in runs if run.get(field) is not None]
    return None if not values else round(float(statistics.median(values)), 3)


def add_aggregates(report: dict[str, Any]) -> dict[str, Any]:
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in report["runs"]:
        by_arm[run["armId"]].append(run)
    aggregates: list[dict[str, Any]] = []
    for arm_id, runs in by_arm.items():
        successful = [run for run in runs if run["successful"]]
        fully_implemented = [
            run
            for run in runs
            if run["implementedRequirements"]["denominator"] > 0
            and run["implementedRequirements"]["numerator"] == run["implementedRequirements"]["denominator"]
        ]
        fully_traceable = [
            run
            for run in runs
            if run["traceability"]["denominator"] > 0
            and run["traceability"]["numerator"] == run["traceability"]["denominator"]
        ]
        fully_covered_artifacts = [
            run
            for run in runs
            if run.get("commonArtifactCoverage", {}).get("denominator", 0) > 0
            and run["commonArtifactCoverage"]["numerator"]
            == run["commonArtifactCoverage"]["denominator"]
        ]
        unautomated = max(
            (len(run["implementedRequirements"].get("notAutomatedIds", [])) for run in runs), default=0
        )
        requirement_total = max(
            (run["implementedRequirements"]["denominator"] for run in runs), default=0
        )
        all_usage = [{"totalTokens": run["usage"]["totalTokens"], "wallSeconds": run["wallSeconds"]} for run in runs]
        successful_usage = [{"totalTokens": run["usage"]["totalTokens"], "wallSeconds": run["wallSeconds"]} for run in successful]
        aggregates.append(
            {
                "armId": arm_id,
                "framework": runs[0]["framework"],
                "successfulRuns": fraction(len(successful), len(runs)),
                "unautomatedRequirements": fraction(unautomated, requirement_total),
                "fullyImplementedRuns": fraction(len(fully_implemented), len(runs)),
                "fullyTraceableRuns": fraction(len(fully_traceable), len(runs)),
                "fullyCoveredArtifactRuns": fraction(
                    len(fully_covered_artifacts), len(runs)
                ),
                "medianTotalTokensAllRuns": _median(all_usage, "totalTokens"),
                "medianTotalTokensSuccessfulRuns": _median(successful_usage, "totalTokens"),
                "medianWallSecondsAllRuns": _median(all_usage, "wallSeconds"),
                "medianWallSecondsSuccessfulRuns": _median(successful_usage, "wallSeconds"),
            }
        )
    return {**report, "aggregates": aggregates}


STATUS_LABELS = {"failed": "실행 실패", "timeout": "시간 초과"}


def _status_label(run: dict[str, Any]) -> str:
    """상태는 실행이 어디까지 갔는지를 말한다. 요구사항 구현 개수와 다른 값이다."""
    if run["successful"]:
        return "게이트 통과"
    if run["status"] == "completed":
        return "게이트 미통과"
    return STATUS_LABELS.get(run["status"], run["status"])


def _value(value: Any) -> str:
    return "미수집" if value is None else str(value)


def _legend() -> list[str]:
    """표의 모든 열이 무엇을 세는지 보고서 안에 남긴다."""
    return [
        "## 열 설명",
        "",
        "### 반복 실행 요약",
        "",
        "| 열 | 세는 것 |",
        "|---|---|",
        "| 필수 게이트 통과 실행 | 프레임워크가 정상 종료하고 필수 게이트를 모두 통과한 반복 수 / 계획한 전체 반복 수. 요구사항을 몇 개 구현했는지와는 다른 값입니다. |",
        "| 전체 요구사항 구현 실행 | 그 반복에서 구현 요구사항이 전부 통과한 반복 수 / 전체 반복 수. |",
        "| 자동 검증 미연결 요구사항 | 검증 오라클이 연결되지 않아 구현 여부를 판정하지 않은 요구사항 수 / 전체 요구사항 수. |",
        "| 공통 산출물 완비 실행 | 계약한 산출물이 모두 확인된 반복 수 / 전체 반복 수. |",
        "| 전체 추적성 확보 실행 | 모든 요구사항이 추적성을 확보한 반복 수 / 전체 반복 수. |",
        "| 총 토큰 중앙값 (전체/성공) | 전체 반복의 중앙값과, 필수 게이트를 통과한 반복만의 중앙값. 실패를 숨기지 않으려고 나눠 적습니다. |",
        "| 시간 중앙값 초 (전체/성공) | 같은 방식으로 나눈 실행 시간 중앙값. |",
        "",
        "### 실행별 결과",
        "",
        "| 열 | 세는 것 |",
        "|---|---|",
        "| 상태 | `게이트 통과` = 정상 종료하고 필수 게이트를 모두 통과. `게이트 미통과` = 정상 종료했지만 필수 게이트 중 하나 이상 실패. `실행 실패`·`시간 초과` = 프레임워크 실행 자체가 끝나지 못함. **요구사항을 몇 개 구현했는지를 뜻하지 않습니다.** |",
        "| 구현 요구사항 | 그 요구사항에 연결된 검증 오라클이 모두 통과한 요구사항 수 / 전체 요구사항 수. 코드 파일이 존재한다는 이유로는 세지 않습니다. 오라클이 연결되지 않은 요구사항은 분자에 들어가지 않습니다. |",
        "| 미연결 | 검증 오라클이 하나도 연결되지 않아 구현 여부를 판정하지 않은 요구사항 수. 실패가 아니라 **판정하지 않음**입니다. |",
        "| 필수 게이트 | 통과한 필수 게이트 수 / 전체 필수 게이트 수. 설계·코드·시험·컨테이너·IaC 산출물의 존재와 클라우드 제약 검사입니다. |",
        "| 제약조건 | 연결된 게이트가 모두 통과한 클라우드 제약 수 / 전체 제약 수. 요구한 리전이 IaC에 반영됐고 금지한 서비스가 없는지 봅니다. |",
        "| 공통 산출물 | 계약한 산출물 중 실제 파일이 확인된 수 / 계약 산출물 수. 표기 형식은 강제하지 않고 같은 의미 범주만 확인합니다. |",
        "| 추적성 | 요구사항 ID가 요구사항·설계·코드·시험 각 단계의 파일에 최소 한 번씩 나타나고 그 파일이 실제로 존재하는 요구사항 수 / 전체 요구사항 수. **문서가 이어져 있는지만 보며 동작 검증과 별개입니다.** |",
        "| 입력/출력/총 토큰, LLM 호출 | 공급자가 보고한 원시 사용량. `미수집`은 0이 아니라 확인하지 못했다는 뜻입니다. |",
        "| 실행 시간(초) | 프레임워크 실행부터 게이트 판정까지의 벽시계 시간. |",
        "",
        "### 함께 읽는 법",
        "",
        "- 구현과 추적성은 다릅니다. 코드를 요구사항에 연결했어도 동작 오라클이 실패하면 구현으로 세지 않고, 동작이 통과해도 문서 연결이 없으면 추적성은 미충족입니다.",
        "- `구현 요구사항`이 0/N일 때 `미연결`이 N이면 실패한 것이 아니라 아직 검증을 붙이지 않은 것입니다.",
        "- 토큰과 시간은 품질 결과와 합산하지 않습니다. 같은 성공 수준끼리만 비교하세요.",
        "",
    ]

def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# 비교 결과: {report['experimentId']}",
        "",
        "가중 합산값 없이 각 항목을 `충족 개수 / 전체 개수`로 표시합니다. 실패와 시간 초과도 반복 실행의 전체 개수에 포함됩니다.",
        "",
        "## 반복 실행 요약",
        "",
        "| 대상 | 프롬프트 프로필 | 필수 게이트 통과 실행 | 전체 요구사항 구현 실행 | 자동 검증 미연결 요구사항 | 공통 산출물 완비 실행 | 전체 추적성 확보 실행 | 총 토큰 중앙값 (전체/성공) | 시간 중앙값 초 (전체/성공) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.get("aggregates", []):
        lines.append(
            "| {framework} (`{arm}`) | {prompt_profile} | {success} | {implemented} | {unautomated} | {artifacts} | {traceability} | {tokens_all} / {tokens_success} | {wall_all} / {wall_success} |".format(
                framework=item["framework"],
                arm=item["armId"],
                prompt_profile=next(
                    (
                        run.get("prompt", {}).get("promptProfile", "requirementsOnly")
                        for run in report["runs"]
                        if run["armId"] == item["armId"]
                    ),
                    "requirementsOnly",
                ),
                success=item["successfulRuns"]["display"],
                implemented=item["fullyImplementedRuns"]["display"],
                unautomated=item["unautomatedRequirements"]["display"],
                artifacts=item["fullyCoveredArtifactRuns"]["display"],
                traceability=item["fullyTraceableRuns"]["display"],
                tokens_all=_value(item["medianTotalTokensAllRuns"]),
                tokens_success=_value(item["medianTotalTokensSuccessfulRuns"]),
                wall_all=_value(item["medianWallSecondsAllRuns"]),
                wall_success=_value(item["medianWallSecondsSuccessfulRuns"]),
            )
        )
    lines.extend(
        [
            "",
            "## 실행별 결과",
            "",
            "| 사례 | 대상 | 반복 | 상태 | 구현 요구사항 | 미연결 | 필수 게이트 | 제약조건 | 공통 산출물 | 추적성 | 입력/출력/총 토큰 | LLM 호출 | 실행 시간(초) |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in report["runs"]:
        usage = run["usage"]
        lines.append(
            "| {case} | {framework} (`{arm}`) | {repetition} | {status} | {requirements} | {unautomated} | {gates} | {constraints} | {artifacts} | {traceability} | {input}/{output}/{total} | {calls} | {wall} |".format(
                case=run.get("caseId", "-"),
                framework=run["framework"],
                arm=run["armId"],
                repetition=run["repetition"],
                status=_status_label(run),
                requirements=run["implementedRequirements"]["display"],
                unautomated=len(run["implementedRequirements"].get("notAutomatedIds", [])),
                gates=run["passedRequiredGates"]["display"],
                constraints=run["satisfiedConstraints"]["display"],
                artifacts=run.get("commonArtifactCoverage", {"display": "0/0"})[
                    "display"
                ],
                traceability=run["traceability"]["display"],
                input=_value(usage["inputTokens"]),
                output=_value(usage["outputTokens"]),
                total=_value(usage["totalTokens"]),
                calls=_value(usage["llmCalls"]),
                wall=run["wallSeconds"],
            )
        )
    failures = [run for run in report["runs"] if not run["successful"]]
    if failures:
        lines.extend(["", "## 실패 및 미충족 상세", ""])
        for run in failures:
            failed_requirements = run["implementedRequirements"]["failedIds"]
            not_automated = run["implementedRequirements"]["notAutomatedIds"]
            failed_gates = run["passedRequiredGates"]["failedIds"]
            missing_artifacts = run.get("commonArtifactCoverage", {}).get(
                "missingIds", []
            )
            lines.extend(
                [
                    f"### {run.get('caseId', '-')} / {run['framework']} / 반복 {run['repetition']}",
                    "",
                    f"- 실행 상태: {_status_label(run)}",
                    f"- 미통과 요구사항: {', '.join(failed_requirements) or '없음'}",
                    f"- 자동 검증 미연결 요구사항: {', '.join(not_automated) or '없음'}",
                    f"- 미통과 필수 게이트: {', '.join(failed_gates) or '없음'}",
                    f"- 누락 공통 산출물: {', '.join(missing_artifacts) or '없음'}",
                    f"- 결과 로드 오류: {run.get('resultLoadError') or '없음'}",
                    "",
                ]
            )
    lines.extend(_legend())
    return "\n".join(lines)


def write_reports(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    complete = add_aggregates(report)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "comparison.json"
    markdown_path = directory / "comparison.md"
    json_path.write_text(json.dumps(complete, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(complete), encoding="utf-8")
    # 수치만으로는 산출물의 질을 볼 수 없다. 실제 파일도 대상별로 모아 둔다.
    collect_artifacts(complete, directory)
    return json_path, markdown_path
