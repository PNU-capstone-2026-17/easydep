"""P2-Azure의 저장된 세 시스템 산출물을 현재 공통 평가기로 순차 검증한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from evaluation.implementation import evaluate_repository
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT as ROOT

ORACLE = ROOT / "evaluation/baselines/cases/oracle.json"
OUTPUT = ROOT / "evaluation/research_protocol/measurements/2026-08-development/system-comparison-p2-azure-uniform.json"
REPOSITORIES = (
    (
        "cot-standard",
        ROOT / "artifacts/runs/cot-standard-p2-azure-20260805T214305Z-a09252/repo",
    ),
    (
        "metagpt-standard",
        ROOT / "artifacts/runs/metagpt-standard-p2-azure-20260805T214412Z-4855e6/repo",
    ),
    (
        "easydep-full-repair-1",
        ROOT
        / "artifacts/runs/easydep-full-p2-azure-20260808T064324Z-72f07e/repairs/attempt-1/03-implementation/application",
    ),
)


def _status(value: Any) -> str | None:
    return value.get("status") if isinstance(value, dict) else None


def main() -> None:
    oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    started_at = datetime.now(UTC)
    for arm, repository in REPOSITORIES:
        started = perf_counter()
        evaluation = evaluate_repository(
            repository, oracle=oracle, run_tools=True, case_id="P2-azure"
        )
        evidence_path = OUTPUT.with_name(f"system-comparison-p2-azure-{arm}.json")
        evidence_path.write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tools = evaluation.get("externalTools") or {}
        container = tools.get("container") or {}
        score = evaluation.get("score") or {}
        rows.append(
            {
                "arm": arm,
                "repository": repository.relative_to(ROOT).as_posix(),
                "evidence": evidence_path.relative_to(ROOT).as_posix(),
                "elapsedSeconds": round(perf_counter() - started, 6),
                "staticPassRate": score.get("passRate"),
                "iacValidation": _status(tools.get("iacEngine")),
                "containerValidation": _status(container),
                "health": _status(container.get("health")),
                "functionalAcceptance": _status(container.get("acceptance")),
                "persistenceAcceptance": _status(container.get("persistenceAcceptance")),
                "experimentEligible": bool(evaluation.get("experimentEligible")),
            }
        )
        print(json.dumps(rows[-1], ensure_ascii=False))
    OUTPUT.write_text(
        json.dumps(
            {
                "schemaVersion": "easydep-system-comparison-uniform/v1",
                "caseId": "P2-azure",
                "startedAt": started_at.isoformat(),
                "finishedAt": datetime.now(UTC).isoformat(),
                "cloudApplyExecuted": False,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
