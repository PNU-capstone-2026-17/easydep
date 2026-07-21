"""PURE 데이터셋 배치 비교 — baseline vs 우리 파이프라인.

PURE 문서마다 요구사항을 샘플로 뽑아 두 그래프를 돌리고(의미론적 커버리지 검증 포함), 결과를
데이터셋별 폴더에 저장한 뒤 집계 SUMMARY를 만든다.

출력 구조:
  docs/research/baseline_vs_pipeline/
    <dataset>/requirements.json   (입력)
    <dataset>/report.md           (지표표+결함+다이어그램)
    <dataset>/score.json          (baseline/ours 점수)
    SUMMARY.md / SUMMARY.json      (전 데이터셋 집계)

문서는 **통째로**(무샘플) 처리해 "요구사항 문서"의 응집성을 지킨다. 요구 수가 상한(--max-reqs)을
넘는 문서는 자르지 않고 **건너뛴다**.

실행:
  python -m scripts.extract_pure_test                    # <req> 문서 중 상한(기본 120) 이하 전부
  python -m scripts.extract_pure_test --max-reqs 60      # 더 작은 것만
  python -m scripts.extract_pure_test --datasets keepass peering   # 명시(상한 무시)
"""
from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

from scripts.pure_extract import doc_name, load_pure, pure_docs

RESULTS_ROOT = Path("docs/research/baseline_vs_pipeline")


def _select_docs(args) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """(실행 대상, 건너뛴 문서) 목록을 반환. 각 원소는 (경로, 요구수).

    샘플링으로 문서 응집성을 깨지 않기 위해 항상 문서를 통째로 쓰고, 요구 수가 상한(max_reqs)을
    넘는 문서는 자르지 않고 **건너뛴다**. 단, --datasets로 명시한 문서는 상한과 무관하게 실행한다.
    """
    docs = [(p, len(load_pure(p))) for p in pure_docs()]  # 전체(무샘플) 개수
    if args.datasets:
        want = set(args.datasets)
        return [(p, n) for p, n in docs if doc_name(p) in want], []
    selected = [(p, n) for p, n in docs if n <= args.max_reqs]
    skipped = [(p, n) for p, n in docs if n > args.max_reqs]
    return selected, skipped


def main() -> None:
    print("추출 시작?")
    parser = argparse.ArgumentParser(description="PURE 배치 비교(baseline vs 우리 파이프라인)")
    parser.add_argument("--max-reqs", type=int, default=120,
                        help="요구 수가 이보다 큰 문서는 (자르지 않고) 건너뜀. 기본 120")
    parser.add_argument("--datasets", nargs="*", help="특정 데이터셋만 실행(상한 무시, 예: keepass peering)")
    parser.add_argument("--sample", type=int, default=0,
                        help="스모크용: 문서당 균등 샘플 N개(기본 0=문서 통째). 상시 사용 비권장")
    args = parser.parse_args()

    selected, skipped = _select_docs(args)
    if not selected:
        parser.error("대상 데이터셋이 없습니다.")
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)

    if skipped:
        print("[batch] 상한 초과로 건너뜀(문서 응집성 보존 — 샘플링/자르기 안 함):")
        for p, n in skipped:
            print(f"    · {doc_name(p)} ({n}개 > 상한 {args.max_reqs})")

    for path, n in selected:
        name = doc_name(path)
        try:
            print(f"  - {name} 추출 중...")
            name = doc_name(path)
            reqs = load_pure(path)
            d = RESULTS_ROOT / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "requirements_extracted.json").write_text(json.dumps(reqs, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - 한 데이터셋 실패가 배치를 죽이지 않게
            print(f"    ! {name} 실패(건너뜀): {exc}")
            traceback.print_exc()



if __name__ == "__main__":
    main()
