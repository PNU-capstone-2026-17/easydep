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
  python -m scripts.compare_baseline_batch                    # <req> 문서 중 상한(기본 120) 이하 전부
  python -m scripts.compare_baseline_batch --max-reqs 60      # 더 작은 것만
  python -m scripts.compare_baseline_batch --datasets keepass peering   # 명시(상한 무시)
"""
from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

from app.agent.compare import compare
from scripts.compare_baseline import build_report
from scripts.pure_extract import doc_name, load_pure, pure_docs

RESULTS_ROOT = Path("docs/research/baseline_vs_pipeline")

# SUMMARY에 실을 핵심 지표 (라벨, 키, 방향) — 방향 up=클수록 좋음/down=작을수록 좋음
_SUMMARY_METRICS = [
    ("명세 위반", "spec_validation_issues", "down"),
    ("거짓 커버리지 주장", "false_coverage_claims", "down"),
    ("의미론 커버리지", "semantic_coverage_ratio", "up"),
    ("유령 참조", "dangling_diagram_refs", "down"),
    ("고아 액터", "orphan_actors", "down"),
]


def _num(v) -> float:
    return float(len(v)) if isinstance(v, list) else float(v if v is not None else 0)


def _fmt(v) -> str:
    if isinstance(v, list):
        return str(len(v))
    if isinstance(v, float):
        return f"{v:.2f}" if v != int(v) else str(int(v))
    return str(v)


def run_one(path: str, sample: int = 0) -> dict:
    """한 데이터셋을 통째로(sample=0) 실행 → 폴더 저장 후 요약 행(dict) 반환."""
    name = doc_name(path)
    reqs = load_pure(path, sample=sample)
    result = compare(reqs, semantic=True)
    b, o = result["baseline"]["score"], result["ours"]["score"]

    d = RESULTS_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "requirements.json").write_text(json.dumps(reqs, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "report.md").write_text(build_report(result), encoding="utf-8")
    (d / "score.json").write_text(
        json.dumps({"requirements": reqs, "baseline": b, "ours": o}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return {"dataset": name, "n": len(reqs), "baseline": b, "ours": o}


def build_summary(rows: list[dict]) -> str:
    L: list[str] = []
    L.append("# PURE 배치 비교 — baseline vs 우리 파이프라인 (집계)")
    L.append("")
    L.append(f"- 데이터셋 {len(rows)}개 (PURE `<req>` 문서를 **통째로** 처리; 상한 초과 문서는 건너뜀)")
    L.append("- 각 셀은 `baseline / ours`. 방향: 위반·거짓주장·유령참조·고아액터는 **낮을수록**, "
             "의미론 커버리지는 **높을수록** 좋음.")
    L.append("- 데이터셋별 상세: `<dataset>/report.md`")
    L.append("")
    header = "| 데이터셋 | 요구수 | " + " | ".join(lbl for lbl, _, _ in _SUMMARY_METRICS) + " |"
    L.append(header)
    L.append("|" + "---|" * (2 + len(_SUMMARY_METRICS)))
    for r in rows:
        cells = [f"{_fmt(r['baseline'].get(k))} / {_fmt(r['ours'].get(k))}" for _, k, _ in _SUMMARY_METRICS]
        L.append(f"| {r['dataset']} | {r['n']} | " + " | ".join(cells) + " |")
    # 평균 행
    if rows:
        avg_cells = []
        for _, k, _dir in _SUMMARY_METRICS:
            ba = sum(_num(r["baseline"].get(k)) for r in rows) / len(rows)
            oa = sum(_num(r["ours"].get(k)) for r in rows) / len(rows)
            avg_cells.append(f"{ba:.2f} / {oa:.2f}")
        L.append(f"| **평균** | — | " + " | ".join(avg_cells) + " |")
    L.append("")
    # 총계 한 줄 해석
    tot_b = sum(_num(r["baseline"].get("spec_validation_issues")) for r in rows)
    tot_o = sum(_num(r["ours"].get("spec_validation_issues")) for r in rows)
    tot_fb = sum(_num(r["baseline"].get("false_coverage_claims")) for r in rows)
    tot_fo = sum(_num(r["ours"].get("false_coverage_claims")) for r in rows)
    L.append(f"> 전 데이터셋 합계 — 명세 정적 위반: baseline **{int(tot_b)}** vs ours **{int(tot_o)}**, "
             f"거짓 커버리지 주장: baseline **{int(tot_fb)}** vs ours **{int(tot_fo)}**.")
    L.append("> 결정론 검증(위반·유령참조·고아)에 더해 **의미론 커버리지**로 'UC가 FR을 실제 실현하는가'까지 "
             "본다 — LLM이 requirement_ids에 거짓으로 id를 넣는 추적성 결함을 잡는다.")
    L.append("")
    return "\n".join(L) + "\n"


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

    # NIM 콜드 스타트(첫 호출 ~5분)를 여기서 1회 지불한다. 이후 모든 호출·데이터셋은 warm(2~3초/콜).
    from app.agent.llm import warmup_llm
    print("[batch] NIM 워밍업(콜드 스타트 1회 지불) 중...")
    print(f"[batch] 워밍업 {warmup_llm():.0f}초 — 이후 호출은 warm 상태로 빠릅니다.")
    mode = "통째" if args.sample == 0 else f"샘플 {args.sample}"
    print(f"[batch] {len(selected)}개 데이터셋({mode}) × (baseline+ours+의미론검증) 실행...")

    rows: list[dict] = []
    for path, n in selected:
        name = doc_name(path)
        try:
            print(f"  - {name} ({n}개 요구, {mode}) 실행 중...")
            rows.append(run_one(path, args.sample))
        except Exception as exc:  # noqa: BLE001 - 한 데이터셋 실패가 배치를 죽이지 않게
            print(f"    ! {name} 실패(건너뜀): {exc}")
            traceback.print_exc()

    if rows:
        (RESULTS_ROOT / "SUMMARY.md").write_text(build_summary(rows), encoding="utf-8")
        (RESULTS_ROOT / "SUMMARY.json").write_text(
            json.dumps([{"dataset": r["dataset"], "n": r["n"],
                         "baseline": r["baseline"], "ours": r["ours"]} for r in rows],
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[batch] 완료 — {len(rows)}개. 집계: {RESULTS_ROOT / 'SUMMARY.md'}")
    else:
        print("[batch] 성공한 데이터셋이 없습니다.")


if __name__ == "__main__":
    main()
