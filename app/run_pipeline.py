"""inputs/*.json 을 파이프라인에 태우고 artifacts/run_*/ 에 결과를 저장하는 러너 CLI.

사용 예:
  python -m app.run_pipeline shopping_mall           # 이름 하나
  python -m app.run_pipeline shopping_mall note_taking
  python -m app.run_pipeline --all                   # inputs/*.json 전부
  python -m app.run_pipeline --input path/to/custom.json
  python -m app.run_pipeline shopping_mall --out /tmp/artifacts

입력 JSON은 {"name","description","classified":[{id,text,type}, ...]} 형식(inputs/README.md).
LLM(NIM)을 실제 호출하므로 .env(API_KEY)가 필요하다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.runner import (
    ARTIFACTS_DIR,
    INPUTS_DIR,
    load_input,
    persist_run,
    run_pipeline,
)
from app.config import settings


def _reconfigure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    _reconfigure_utf8()
    parser = argparse.ArgumentParser(
        prog="python -m app.run_pipeline",
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
        classified = obj.get("classified") or []
        print(f"\n[run] {name}: 요구사항 {len(classified)}개 실행 중...")
        state = run_pipeline(classified)
        run_dir = persist_run(
            obj, state, dataset_name=obj.get("name", name), artifact_root=Path(args.out)
        )
        cov = state.get("coverage", {})
        print(f"  → {run_dir}")
        print(
            f"    actors={len(state.get('actors', []))} "
            f"use_cases={len(state.get('use_cases', []))} "
            f"coverage={cov.get('coverage_ratio')} "
            f"specs={len(state.get('use_case_specs', []))}"
        )
        orphans = cov.get("orphan_fr_ids")
        if orphans:
            print(f"    [WARN] 고아 FR: {orphans}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
