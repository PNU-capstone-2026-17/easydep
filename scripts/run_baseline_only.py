"""BASELINE 산출물만 생성해 artifacts/run_*/ 로 저장한다(대조군 단독 실행).

compare_baseline은 baseline+ours+semantic 채점을 함께 돌려 느리다. 이 스크립트는 baseline
그래프(순진한 2콜)만 돌려 동일 포맷으로 저장한다.

사용: python -m scripts.run_baseline_only toystore
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.agent.baseline import build_baseline_graph
from app.runner import ARTIFACTS_DIR, INPUTS_DIR, persist_run


def _utf8():
    for s in (sys.stdout, sys.stderr):
        r = getattr(s, "reconfigure", None)
        if r:
            try:
                r(encoding="utf-8")
            except Exception:
                pass


def main(argv=None) -> int:
    _utf8()
    name = (argv or sys.argv[1:])[0]
    data = json.loads((INPUTS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    reqs = [c["text"] for c in data.get("classified", [])]
    print(f"[baseline] {name}: 요구사항 {len(reqs)}개, 순진한 2콜 baseline 실행 중...")
    state = build_baseline_graph().invoke({"raw_requirements": reqs})
    input_obj = {
        "name": f"{name}-baseline",
        "raw_requirements": reqs,
        "classified": state.get("classified", []),
    }
    run_dir = persist_run(input_obj, state, dataset_name=f"{name}-baseline",
                          artifact_root=ARTIFACTS_DIR)
    print(f"  actors={len(state.get('actors', []))} "
          f"use_cases={len(state.get('use_cases', []))} "
          f"specs={len(state.get('use_case_specs', []))}")
    print(f"→ {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
