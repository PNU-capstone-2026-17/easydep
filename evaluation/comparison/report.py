"""기계 판독 JSON과 사람이 읽는 비율 중심 Markdown 보고서."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

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
        all_usage = [{"totalTokens": run["usage"]["totalTokens"], "wallSeconds": run["wallSeconds"]} for run in runs]
        successful_usage = [{"totalTokens": run["usage"]["totalTokens"], "wallSeconds": run["wallSeconds"]} for run in successful]
        aggregates.append(
            {
                "armId": arm_id,
                "framework": runs[0]["framework"],
                "successfulRuns": fraction(len(successful), len(runs)),
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


def _value(value: Any) -> str:
    return "미수집" if value is None else str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# 비교 결과: {report['experimentId']}",
        "",
        "가중 합산값 없이 각 항목을 `충족 개수 / 전체 개수`로 표시합니다. 실패와 시간 초과도 반복 실행의 전체 개수에 포함됩니다.",
        "",
        "## 반복 실행 요약",
        "",
        "| 대상 | 프롬프트 프로필 | 성공 실행 | 전체 요구사항 구현 실행 | 공통 산출물 완비 실행 | 전체 추적성 확보 실행 | 총 토큰 중앙값 (전체/성공) | 시간 중앙값 초 (전체/성공) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.get("aggregates", []):
        lines.append(
            "| {framework} (`{arm}`) | {prompt_profile} | {success} | {implemented} | {artifacts} | {traceability} | {tokens_all} / {tokens_success} | {wall_all} / {wall_success} |".format(
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
            "| 대상 | 반복 | 상태 | 구현 요구사항 | 필수 게이트 | 제약조건 | 공통 산출물 | 추적성 | 입력/출력/총 토큰 | LLM 호출 | 실행 시간(초) |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in report["runs"]:
        usage = run["usage"]
        lines.append(
            "| {framework} (`{arm}`) | {repetition} | {status} | {requirements} | {gates} | {constraints} | {artifacts} | {traceability} | {input}/{output}/{total} | {calls} | {wall} |".format(
                framework=run["framework"],
                arm=run["armId"],
                repetition=run["repetition"],
                status=(
                    "성공"
                    if run["successful"]
                    else "검증 실패"
                    if run["status"] == "completed"
                    else run["status"]
                ),
                requirements=run["implementedRequirements"]["display"],
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
                    f"### {run['framework']} / 반복 {run['repetition']}",
                    "",
                    f"- 실행 상태: `{run['status']}`",
                    f"- 미통과 요구사항: {', '.join(failed_requirements) or '없음'}",
                    f"- 자동 검증 미연결 요구사항: {', '.join(not_automated) or '없음'}",
                    f"- 미통과 필수 게이트: {', '.join(failed_gates) or '없음'}",
                    f"- 누락 공통 산출물: {', '.join(missing_artifacts) or '없음'}",
                    f"- 결과 로드 오류: {run.get('resultLoadError') or '없음'}",
                    "",
                ]
            )
    lines.extend(
        [
            "## 해석 기준",
            "",
            "- 구현 요구사항은 해당 요구사항에 연결된 검증 게이트가 모두 통과한 경우입니다.",
            "- 공통 산출물은 각 프레임워크의 고유 형식을 허용하며, 어댑터가 신고한 실제 파일의 존재 여부를 별도로 집계합니다.",
            "- 추적성은 요구사항별 필수 단계의 증거 파일이 실제로 존재하는 경우이며, 동작 구현 여부와 별도입니다.",
            "- 토큰과 시간은 원시 효율 지표입니다. 요구사항 충족 결과와 합산하지 않습니다.",
            "- `미수집`은 0이 아니라 제공자 또는 어댑터에서 사용량을 확인하지 못했다는 뜻입니다.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    complete = add_aggregates(report)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "comparison.json"
    markdown_path = directory / "comparison.md"
    json_path.write_text(json.dumps(complete, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(complete), encoding="utf-8")
    return json_path, markdown_path
