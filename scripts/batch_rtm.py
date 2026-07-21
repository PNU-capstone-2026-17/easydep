"""여러 데이터셋에 대해 ours 파이프라인을 돌려 RTM(요구사항 추적 매트릭스)을 일괄 생성한다.

baseline 비교는 건너뛰고(RTM은 ours 산출물) ours 파이프라인만 실행하므로 compare보다 빠르다.
각 데이터셋마다 artifacts/run_*/ 에 전체 산출물(+rtm.json/rtm.md)을 저장하고, 데이터셋 간
교차 요약을 docs/research/rtm_batch/SUMMARY.md 로 남긴다.

실행:
  python -m scripts.batch_rtm                      # inputs/*.json 전부
  python -m scripts.batch_rtm shopping_mall toystore
  python -m scripts.batch_rtm --no-semantic        # Phase3 realized 생략(빠름)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.requirements.agent.compare import run_ours, semantic_coverage
from app.requirements.agent.llm import warmup_llm
from app.requirements.agent.rtm import build_rtm
from app.requirements.classifier import warmup as bert_warmup
from app.requirements.runner import ARTIFACTS_DIR, INPUTS_DIR, persist_run

OUT_DIR = Path("docs/research/rtm_batch")


def _reconfigure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        rc = getattr(stream, "reconfigure", None)
        if rc:
            try:
                rc(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


def _requirements(stem: str) -> list[str]:
    data = json.loads((INPUTS_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    return [c["text"] for c in data.get("classified", []) if c.get("text", "").strip()]


def process(stem: str, semantic: bool, root: Path) -> dict:
    """한 데이터셋: ours 파이프라인 실행 → (선택)semantic 판정 → 아티팩트+RTM 저장 → 요약 반환."""
    reqs = _requirements(stem)
    state = run_ours(reqs)
    verdicts = semantic_coverage(state).get("coverage_verdicts") if semantic else None
    input_obj = {"name": f"rtm-{stem}", "raw_requirements": reqs,
                 "classified": state.get("classified", [])}
    run_dir = persist_run(input_obj, state, dataset_name=f"rtm-{stem}",
                          artifact_root=root, rtm_verdicts=verdicts)
    s = build_rtm(state, verdicts=verdicts)["summary"]
    return {"dataset": stem, "run_dir": str(run_dir), "n_reqs": len(reqs), **s}


def _summary_md(rows: list[dict], semantic: bool) -> str:
    L = ["# RTM 배치 요약", "",
         f"- 데이터셋 {len(rows)}개 · 채점 {'결정론+의미(judge)' if semantic else '결정론만'}", ""]
    L.append("| 데이터셋 | 요구 | FR | 커버 | orphan | verified | 거짓주장 | 복합FR | NFR | ack | gap | unattach | orphanUC |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        ver = r.get("fr_verified", "—") if semantic else "—"      # 미판정이면 0이 아니라 '—'
        false = r.get("fr_false_claim", "—") if semantic else "—"
        L.append(
            f"| {r['dataset']} | {r['n_reqs']} | {r['fr_total']} | {r['fr_covered']} | {r['fr_orphan']} "
            f"| {ver} | {false} | — "
            f"| {r['nfr_total']} | {r.get('nfr_ack', 0)} | {r.get('nfr_gap', 0)} "
            f"| {r['nfr_unattached']} | {len(r['orphan_use_cases'])} |")
    L.append("")
    L.append("> verified/거짓주장은 semantic(judge)이 켜졌을 때만 채워진다. "
             "ack=NFR이 UC로 라우팅됨(attached/linked), gap=unattached(횡단 제약 후보).")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    _reconfigure_utf8()
    parser = argparse.ArgumentParser(description="여러 데이터셋 RTM 일괄 생성(ours 파이프라인)")
    parser.add_argument("datasets", nargs="*", help="inputs/ 데이터셋 stem (없으면 전부)")
    parser.add_argument("--no-semantic", action="store_true",
                        help="Phase3 realized(judge) 생략 — 커버리지+qualifies만, 대폭 빠름")
    parser.add_argument("--out", default=str(ARTIFACTS_DIR), help="아티팩트 루트(기본 artifacts/)")
    args = parser.parse_args(argv)

    stems = args.datasets or sorted(p.stem for p in INPUTS_DIR.glob("*.json"))
    semantic = not args.no_semantic
    root = Path(args.out)
    print(f"[batch_rtm] {len(stems)}개 데이터셋 · 채점 {'의미포함' if semantic else '결정론만'} · 워밍업...", flush=True)
    bert_warmup()
    warmup_llm()

    rows: list[dict] = []
    for i, stem in enumerate(stems, 1):
        print(f"[{i}/{len(stems)}] {stem} 실행...", flush=True)
        try:
            r = process(stem, semantic, root)
        except Exception as exc:  # noqa: BLE001 - 한 데이터셋 실패가 배치를 죽이지 않게
            print(f"  !! {stem} 실패(건너뜀): {type(exc).__name__}: {exc}", flush=True)
            continue
        rows.append(r)
        print(f"  → FR {r['fr_covered']}/{r['fr_total']} 커버"
              + (f", verified {r.get('fr_verified')}·거짓 {r.get('fr_false_claim')}" if semantic else "")
              + f", NFR ack {r.get('nfr_ack', 0)}/{r['nfr_total']} · {Path(r['run_dir']).name}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "SUMMARY.md").write_text(_summary_md(rows, semantic), encoding="utf-8")
    (OUT_DIR / "SUMMARY.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[batch_rtm] 완료 {len(rows)}/{len(stems)} · 요약 {OUT_DIR/'SUMMARY.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
