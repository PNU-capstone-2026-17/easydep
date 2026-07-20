"""완료된 run에 자연어 피드백을 적용해 재생성하고 새 artifact run으로 저장한다.

정책: 대상 stage를 재생성하면 하위 stage는 fresh 재실행(cascade). 원본 run은 보존된다.

사용:
  python -m app.apply_feedback <run_dir> "피드백 문장"
  python -m app.apply_feedback artifacts/run_2026..._abcd "UC3 명세에 결제 실패 확장을 추가해줘"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.feedback import apply_feedback
from app.runner import ARTIFACTS_DIR, load_state, persist_run


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
    ap = argparse.ArgumentParser(prog="python -m app.apply_feedback")
    ap.add_argument("run_dir", help="피드백을 적용할 기존 artifacts/run_*/ 디렉토리")
    ap.add_argument("feedback", help="자연어 피드백")
    ap.add_argument("--out", default=str(ARTIFACTS_DIR), help="아티팩트 루트")
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    input_obj = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
    state = load_state(run_dir)

    print(f"[feedback] 분류·재생성 중: {args.feedback!r}")
    state, report = apply_feedback(state, args.feedback)

    new_dir = persist_run(
        input_obj, state,
        dataset_name=f"{input_obj.get('name', '')}+feedback",
        artifact_root=Path(args.out),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"→ {new_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
