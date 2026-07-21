"""참고 프로젝트의 labeled 요구사항 JSON을 우리 inputs/ 형식으로 변환한다.

입력 포맷(참고 프로젝트 sample_requirements.json류):
    {"name": "...", "functional_requirements": [문장...], "non_functional_requirements": [문장...]}
출력(inputs/<name>.json):
    {"name","description","classified": [{"id":"FR-01","text":..,"type":"FR"}, ...]}

FR/NFR 라벨이 이미 분리돼 있으므로 LLM 없이 결정론적으로 변환한다(id만 부여).
원문/추상 포맷(mixed_demo, abstract_specs)은 라벨이 없어 step1 분류가 필요 → 이 스크립트 대상 아님.

사용:
    python scripts/import_labeled_requirements.py <source.json> [--name NAME]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

INPUTS_DIR = Path(__file__).resolve().parent.parent / "inputs"


def convert(src: dict, name: str | None = None) -> dict:
    name = name or src.get("name") or "imported"
    fr = src.get("functional_requirements") or []
    nfr = src.get("non_functional_requirements") or []
    classified = (
        [{"id": f"FR-{i:02d}", "text": t, "type": "FR"} for i, t in enumerate(fr, 1)]
        + [{"id": f"NFR-{i:02d}", "text": t, "type": "NFR"} for i, t in enumerate(nfr, 1)]
    )
    return {
        "name": name,
        "description": f"참고 프로젝트에서 이관 (FR {len(fr)} + NFR {len(nfr)}).",
        "classified": classified,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="import_labeled_requirements")
    ap.add_argument("source", help="참고 프로젝트 labeled 요구사항 JSON 경로")
    ap.add_argument("--name", help="출력 데이터셋 이름(기본: source의 name)")
    args = ap.parse_args(argv)

    src = json.loads(Path(args.source).read_text(encoding="utf-8"))
    if "functional_requirements" not in src:
        print(
            "labeled FR/NFR 포맷이 아닙니다(mixed/abstract는 step1 분류가 필요).",
            file=sys.stderr,
        )
        return 1

    out = convert(src, args.name)
    dest = INPUTS_DIR / f"{out['name']}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {dest} ({len(out['classified'])} requirements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
