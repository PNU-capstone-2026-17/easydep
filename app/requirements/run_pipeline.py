"""inputs/*.json을 처리하고 artifacts/runs/<run-id>/에 저장한다.

사용 예:
  python -m app.requirements.run_pipeline shopping_mall           # 이름 하나
  python -m app.requirements.run_pipeline shopping_mall note_taking
  python -m app.requirements.run_pipeline --all                   # inputs/*.json 전부
  python -m app.requirements.run_pipeline --input path/to/custom.json
  python -m app.requirements.run_pipeline shopping_mall --out /tmp/artifacts

입력 JSON은 {"name","description","classified":[{id,text,type}, ...]} 형식(inputs/README.md).
LLM(NIM)을 실제 호출하므로 .env(API_KEY)가 필요하다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from app.orchestration.run_identity import identity_manifest, make_run_id
from app.requirements.common.console import use_utf8_stdout
from app.requirements.config import settings
from app.requirements.contracts.state import RequirementItem
from app.requirements.orchestration.runner import (
    ARTIFACTS_DIR,
    INPUTS_DIR,
    load_input,
    persist_run,
    run_pipeline,
)
from app.requirements.runtime import telemetry


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    telemetry.configure_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.requirements.run_pipeline",
        description="요구사항 → 액터/유스케이스/명세/다이어그램 파이프라인 실행 + 아티팩트 저장",
    )
    parser.add_argument("datasets", nargs="*", help="inputs/ 의 데이터셋 이름")
    parser.add_argument("--all", action="store_true", help="inputs/*.json 전부 실행")
    parser.add_argument("--input", help="임의 경로의 입력 JSON 하나")
    parser.add_argument("--out", default=str(ARTIFACTS_DIR), help="아티팩트 루트 디렉토리")
    args = parser.parse_args(argv)

    # 실행 대상 수집: (표시이름, 경로|None)
    targets: list[tuple[str, str | None]] = []
    if args.input:
        targets.append(("(custom)", args.input))
    names = list(args.datasets)
    if args.all:
        names += sorted(p.stem for p in INPUTS_DIR.glob("*.json"))
    seen: set[str] = set()
    for n in names:
        if n not in seen:
            seen.add(n)
            targets.append((n, None))

    if not targets:
        print("실행할 데이터셋이 없습니다. 이름을 주거나 --all / --input 을 사용하세요.", file=sys.stderr)
        return 1

    print(f"모델: {settings.model} | 아티팩트: {args.out}")
    for name, path in targets:
        obj = load_input(path or name)
        classified = cast(list[RequirementItem], obj.get("classified") or [])
        resource_answers = cast(
            dict[str, str], obj.get("resource_answers") or {}
        )
        print(f"\n[run] {name}: 요구사항 {len(classified)}개 실행 중...")
        # 데이터셋마다 스코프를 연다 — 합계가 "무엇 하나에 대한 것"인지 분명해야
        # 실행끼리 비교할 수 있다.
        with telemetry.run_scope(f"batch:{name}") as stats:
            state = run_pipeline(
                classified,
                resource_constraints_text=str(obj.get("resource_constraints_text") or ""),
                resource_answers=resource_answers,
            )
        run_dir = persist_run(
            obj,
            state,
            dataset_name=str(obj.get("name") or name),
            artifact_root=Path(args.out),
            run_metrics=stats.as_dict(),
            run_id_factory=make_run_id,
            identity_manifest_factory=identity_manifest,
        )
        cov = state.get("coverage", {})
        print(f"  → {run_dir}")
        print(
            f"    actors={len(state.get('actors', []))} "
            f"use_cases={len(state.get('use_cases', []))} "
            f"coverage={cov.get('coverage_ratio')} "
            f"specs={len(state.get('use_case_specs', []))}"
        )
        print(
            f"    deployment_needs={len(state.get('deployment_needs', {}))} "
            f"resource_spec={'valid' if state.get('resource_spec') else 'missing'}"
        )
        summary = stats.as_dict()
        print(
            f"    llm_calls={summary['llm_calls']} "
            f"tokens={summary['prompt_tokens']}+{summary['completion_tokens']} "
            f"fallbacks={summary['structured_fallbacks']} "
            f"llm_seconds={summary['llm_seconds']}"
        )
        # 실행끼리 산출물을 비교할 때 지문이 다르면 그 차이를 코드 탓으로 돌릴 수 없다.
        print(f"    backend={','.join(summary['model_fingerprints']) or '(미제공)'}")
        missing_goals = cov.get("missing_goal_requirement_ids")
        if missing_goals:
            print(f"    [WARN] 미실현 사용자 목표: {missing_goals}")
        # 저하가 있으면 위 숫자들을 액면가로 읽으면 안 된다. 예를 들어 의미 검증이
        # 죽은 채 돈 실행은 "결함 0"이 아니라 "결함을 안 본 것"이다.
        for entry in summary["degradations"]:
            subject = f" ({entry['subject']})" if entry["subject"] else ""
            print(f"    [DEGRADED] {entry['component']}{subject}: {entry['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
