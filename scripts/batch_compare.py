"""여러 입력(inputs/*.json)에 대해 BASELINE vs 우리 파이프라인 비교를 배치로 돌리고 집계한다.

`compare_baseline.py`는 한 입력을 한 번 돌려 고정 경로에 덮어쓴다. 이 배치 러너는:
  - 여러 데이터셋 × 여러 반복(reps)을 순회하며,
  - 각 실행 점수를 데이터셋별로 분리 저장하고(덮어쓰기 없음),
  - 데이터셋 간 **승패표**와 반복 간 **분산**을 집계해 일반화/신뢰도 근거를 만든다.

일반화(breadth)와 신뢰도(reliability)는 서로 다른 축이다:
  - 데이터셋을 늘리면 "우리 시스템이 특정 도메인에서만 유리한 게 아니다"(외적 타당도)를 보인다.
  - 같은 입력을 반복하면 LLM 확률적 변동에도 지표가 안정적임(재현성)을 보인다.
둘을 교차(inputs × reps)해 돌리는 게 가장 방어력이 강하다.

실행:
  python -m scripts.batch_compare                      # 전체 입력, 1회, 의미검증 on
  python -m scripts.batch_compare --reps 3             # 전체 입력, 각 3회
  python -m scripts.batch_compare --datasets toystore  # 특정 입력만
  python -m scripts.batch_compare --no-semantic        # 결정론 지표만(빠름)
출력:
  docs/research/batch/<stem>/rep-<k>.json   (개별 실행 점수)
  docs/research/batch-summary.md (+ .json)  (승패표·분산 집계)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import traceback
from pathlib import Path

from app.requirements.agent.compare import compare

# 방향성 지표(작을수록/클수록 좋음)만 승패에 쓴다. compare_baseline._METRICS와 방향을 맞춘다.
# 핵심 정당성 근거는 요구 수와 무관한 품질 지표(명세 위반·유령 참조·고아·거짓 커버리지)다.
_DIRECTED = [
    ("spec_validation_issues", "명세 정적 위반", "down"),
    ("false_coverage_claims", "거짓 커버리지 주장", "down"),
    ("compound_fr_issues", "복합 FR", "down"),
    ("orphan_fr_ids", "고아 FR(누락)", "down"),
    ("unknown_requirement_refs", "유령 요구 참조", "down"),
    ("dangling_diagram_refs", "다이어그램 유령 참조", "down"),
    ("orphan_actors", "고아 액터", "down"),
    ("semantic_coverage_ratio", "의미 커버리지", "up"),
    ("coverage_ratio", "커버리지(주장)", "up"),
]

INPUTS_DIR = Path("inputs")
OUT_DIR = Path("docs/research/batch")
OUT_MD = Path("docs/research/batch-summary.md")
OUT_JSON = Path("docs/research/batch-summary.json")


def _val(v):
    """list는 길이로, 나머지는 그대로 수치화(점수 dict가 상세는 list, 요약은 int로 섞여 있음)."""
    return len(v) if isinstance(v, list) else v


def _winner(direction: str, b, o) -> str:
    bv, ov = _val(b), _val(o)
    if bv is None or ov is None:
        return "—"
    if bv == ov:
        return "tie"
    if direction == "up":
        return "ours" if ov > bv else "baseline"
    return "ours" if ov < bv else "baseline"


def _load_requirements(stem: str) -> list[str]:
    data = json.loads((INPUTS_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    return [c["text"] for c in data.get("classified", []) if c.get("text", "").strip()]


def _mean(xs: list[float]):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 4) if xs else None


def _std(xs: list[float]):
    xs = [x for x in xs if x is not None]
    return round(statistics.pstdev(xs), 4) if len(xs) > 1 else 0.0


def run_dataset(stem: str, reps: int, semantic: bool) -> dict:
    """한 데이터셋을 reps번 돌려, 반복별 baseline·ours 점수 리스트를 반환."""
    reqs = _load_requirements(stem)
    runs: list[dict] = []
    ds_dir = OUT_DIR / stem
    ds_dir.mkdir(parents=True, exist_ok=True)
    for k in range(1, reps + 1):
        print(f"[batch] {stem} rep {k}/{reps} 실행 중(요구 {len(reqs)}개)...")
        try:
            result = compare(reqs, semantic=semantic)
        except Exception as exc:  # noqa: BLE001 - 한 실행 실패가 배치 전체를 죽이지 않게
            print(f"[batch]   실패(스킵): {exc}")
            traceback.print_exc()
            continue
        rec = {
            "rep": k,
            "baseline": result["baseline"]["score"],
            "ours": result["ours"]["score"],
        }
        (ds_dir / f"rep-{k}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        runs.append(rec)
    return {"stem": stem, "n_reqs": len(reqs), "runs": runs}


def aggregate(datasets: list[dict]) -> dict:
    """데이터셋별로 반복 평균을 내고, 방향성 지표마다 승자를 판정한다."""
    agg: list[dict] = []
    for ds in datasets:
        runs = ds["runs"]
        if not runs:
            agg.append({"stem": ds["stem"], "n_reqs": ds["n_reqs"], "reps": 0, "metrics": {}})
            continue
        metrics: dict = {}
        for key, _label, direction in _DIRECTED:
            b_mean = _mean([_val(r["baseline"].get(key)) for r in runs])
            o_mean = _mean([_val(r["ours"].get(key)) for r in runs])
            metrics[key] = {
                "baseline_mean": b_mean,
                "ours_mean": o_mean,
                "baseline_std": _std([_val(r["baseline"].get(key)) for r in runs]),
                "ours_std": _std([_val(r["ours"].get(key)) for r in runs]),
                "winner": _winner(direction, b_mean, o_mean),
            }
        agg.append({"stem": ds["stem"], "n_reqs": ds["n_reqs"], "reps": len(runs), "metrics": metrics})
    return {"datasets": agg}


def _tally(agg: dict) -> dict:
    """방향성 지표별로 ours 승/무/패 데이터셋 수를 센다."""
    tally = {key: {"ours": 0, "tie": 0, "baseline": 0, "na": 0} for key, _l, _d in _DIRECTED}
    for ds in agg["datasets"]:
        for key, _l, _d in _DIRECTED:
            m = ds["metrics"].get(key)
            w = m["winner"] if m else "—"
            tally[key][{"ours": "ours", "tie": "tie", "baseline": "baseline"}.get(w, "na")] += 1
    return tally


def build_report(agg: dict, reps: int, semantic: bool) -> str:
    L: list[str] = []
    L.append("# Batch: Baseline vs 우리 파이프라인 — 다중 입력 집계")
    L.append("")
    n_ds = len(agg["datasets"])
    L.append(f"- 데이터셋 {n_ds}개 × 반복 {reps}회 (채점={'결정론+의미검증' if semantic else '결정론만'})")
    L.append("- **breadth**(데이터셋)=일반화 근거, **reliability**(반복 std)=재현성 근거")
    L.append("- 방향: ↓=작을수록 좋음, ↑=클수록 좋음. 값은 반복 평균(±std).")
    L.append("")

    # 지표별 승패 집계(요약)
    L.append("## 지표별 데이터셋 승패 (ours 관점)")
    L.append("")
    L.append("| 지표 | ours 승 | 무 | baseline 승 |")
    L.append("|---|---:|---:|---:|")
    tally = _tally(agg)
    for key, label, direction in _DIRECTED:
        t = tally[key]
        arrow = "↓" if direction == "down" else "↑"
        L.append(f"| {label} {arrow} | {t['ours']} | {t['tie']} | {t['baseline']} |")
    L.append("")

    # 데이터셋 × 핵심 지표 상세(baseline→ours 평균)
    L.append("## 데이터셋별 상세 (baseline → ours, 평균±std)")
    L.append("")
    core = ["spec_validation_issues", "false_coverage_claims", "compound_fr_issues",
            "orphan_fr_ids", "dangling_diagram_refs", "orphan_actors"]
    labels = {k: l for k, l, _ in _DIRECTED}
    header = "| 데이터셋 | reqs | reps | " + " | ".join(labels[k] for k in core) + " |"
    L.append(header)
    L.append("|" + "---|" * (3 + len(core)))
    for ds in agg["datasets"]:
        cells = [ds["stem"], str(ds["n_reqs"]), str(ds["reps"])]
        for k in core:
            m = ds["metrics"].get(k)
            if not m or m["baseline_mean"] is None:
                cells.append("—")
                continue
            bo = f"{m['baseline_mean']:g}→{m['ours_mean']:g}"
            if m["ours_std"] or m["baseline_std"]:
                bo += f" (±{m['ours_std']:g})"
            mark = {"ours": "✓", "baseline": "✗", "tie": "="}.get(m["winner"], "")
            cells.append(f"{bo} {mark}".strip())
        L.append("| " + " | ".join(cells) + " |")
    L.append("")
    L.append("> ✓=ours 우위, =동률, ✗=baseline 우위. 핵심 근거는 **명세 정적 위반**(양쪽 동일 검증기 적용)이 "
             "여러 도메인에서 일관되게 ours→0 으로 수렴하는지다.")
    L.append("")
    return "\n".join(L) + "\n"


def _reconfigure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    _reconfigure_utf8()
    parser = argparse.ArgumentParser(description="다중 입력 Baseline vs 우리 파이프라인 배치 비교")
    parser.add_argument("--datasets", nargs="*",
                        help="inputs/<stem>.json 의 stem들(생략 시 inputs/*.json 전체)")
    parser.add_argument("--reps", type=int, default=1, help="입력당 반복 실행 횟수(재현성용)")
    parser.add_argument("--no-semantic", action="store_true",
                        help="의미론적 커버리지 검증(LLM judge) 생략 — 결정론 지표만, 대폭 단축")
    args = parser.parse_args()

    if args.datasets:
        stems = args.datasets
    else:
        stems = sorted(p.stem for p in INPUTS_DIR.glob("*.json"))
    if not stems:
        parser.error("실행할 데이터셋이 없습니다.")

    semantic = not args.no_semantic
    print(f"[batch] 데이터셋 {len(stems)}개 × 반복 {args.reps}회, 채점="
          f"{'결정론+의미검증(LLM)' if semantic else '결정론만'}")

    datasets = [run_dataset(stem, args.reps, semantic) for stem in stems]
    agg = aggregate(datasets)

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(build_report(agg, args.reps, semantic), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[batch] 집계 리포트: {OUT_MD}")
    print(f"[batch] 개별 실행 점수: {OUT_DIR}/<stem>/rep-*.json")


if __name__ == "__main__":
    main()
