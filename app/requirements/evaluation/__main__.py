"""평가 CLI — 세 가지 질문에 답한다.

    python -m app.requirements.evaluation seeded              검사기 눈금이 살아 있나
    python -m app.requirements.evaluation score <run_dir>     이 실행은 어떤 결함을 남겼나
    python -m app.requirements.evaluation diff <a.json> <b.json>   무엇이 좋아지고 나빠졌나

전부 LLM을 부르지 않는다. 실행을 만드는 것은 `run_pipeline`의 일이다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ⚠ `scorecard`·`seeded`는 명령 안에서 import한다. `seeded`는 자격증명 없이 돌아야 하고
# (CI 게이트), `score`만 설정·아티팩트 경로가 필요하다. 상단에서 다 끌어오면 그 구분이 사라진다.


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _cmd_seeded(_args) -> int:
    from app.requirements.evaluation import seeded

    report = seeded.detection_report()
    for case in report["cases"]:
        mark = "OK  " if case["detected"] else "MISS"
        extra = f"  (함께 걸림: {case['also_flagged']})" if case["also_flagged"] else ""
        print(f"{mark} {case['rule_id']}  ← {case['seeded']}{extra}")
    print(f"\n검출 {report['detected']}/{report['total']}")
    if report["false_positives"]:
        print(f"⚠ 대조군(결함 없음)에서 나온 오탐 {len(report['false_positives'])}건:")
        for issue in report["false_positives"]:
            print(f"   {issue}")
    if report["unseeded_detector_rules"]:
        print(f"⚠ 심어 두지 않은 검출기 규칙: {report['unseeded_detector_rules']}")
    # 눈금이 하나라도 죽어 있거나 오탐이 있으면 실패로 끝낸다.
    ok = report["detected"] == report["total"] and not report["false_positives"]
    return 0 if ok else 1


def _cmd_semantic(args) -> int:
    """의미 규칙 눈금 — **실제 LLM을 부른다.** CI 게이트가 아니다."""
    from app.requirements.evaluation import semantic

    report = semantic.measure(repeats=args.repeats, stage=args.stage)
    print(f"모델 {report['model']} · 케이스마다 {report['repeats']}회\n")
    for case in report["cases"]:
        mark = "OK  " if case["detected"] else "DEAD"
        line = f"{mark} {case['rate']:>5.0%}  {case['rule_id']}  ← {case['seeded']}"
        print(line)
        if case["also_flagged"]:
            print(f"        함께 걸림: {case['also_flagged']}")
        if case["unexamined"]:
            print(f"        판정 안 한 규칙: {case['unexamined']}")
    print("\n[대조군 — 결함 없는 산출물]")
    for control in report["controls"]:
        print(f"  {control['stage']}: 오탐률 {control['false_positive_rate']:.0%}"
              f"  {control['flagged'] or ''}")
    if report["dead_gauges"]:
        print(f"\n⚠ 한 번도 못 잡은 규칙 {len(report['dead_gauges'])}건: {report['dead_gauges']}")
        print("  이 규칙에 대한 모든 '결함 0건'은 근거가 없다.")
    # 판정이 결정론이 아니라 exit code로 게이트하지 않는다 — 수를 보고 사람이 판단한다.
    return 0


def _cmd_stability(args) -> int:
    """판정 안정성 — 같은 명세를 N번 물어 흔들리는 판정을 센다. **실제 LLM 호출.**"""
    from app.requirements.evaluation import semantic

    payloads = semantic.payloads_from_run(args.run_dir)
    if args.limit:
        # 표본을 줄이는 손잡이. **줄였다는 사실을 출력에 적는다** — 조용히 자르면
        # 비교하는 두 수가 다른 표본에서 나온 것이 된다.
        payloads = payloads[:args.limit]
    report = semantic.measure_stability(payloads, repeats=args.repeats)
    print(f"모델 {report['model']} · 명세 {report['n_specs']}개 × {report['repeats']}회\n")
    print(f"{'규칙':52} {'항상':>5} {'때때로':>7} {'흔들림':>7}")
    for rule_id, row in report["per_rule"].items():
        print(f"{rule_id:52} {row['always']:>5} {row['sometimes']:>7} "
              f"{row['unstable_share']:>7.0%}")
    print(f"\n전체 흔들림 비율: {report['unstable_share']:.0%}")
    return 0


def _rank(rows: list[dict]) -> None:
    """(도메인, 규칙) 행들을 규칙별로 합쳐 순위를 찍는다."""
    totals: dict[str, list[int]] = {}
    for row in rows:
        acc = totals.setdefault(row["rule_id"], [0, 0, 0])
        acc[0] += row["always"]
        acc[1] += row["sometimes"]
        acc[2] += row["never"]

    conditions = sorted({(r["repeats"], r["n_specs"]) for r in rows})
    domains = sorted({r["domain"] for r in rows})
    print(f"\n[합계 — 단독 프로브] 도메인 {len(domains)}종 {domains}")
    print(f"조건(반복, 명세수): {conditions}")
    if len(conditions) > 1:
        # 조건이 섞이면 순위로 쓸 수 없다 — 이 세션에서 결론을 두 번 뒤집은 원인이 그것이다.
        print("⚠ 조건이 섞여 있다. 이 표는 순위가 아니다 — 같은 조건으로 다시 모아야 한다.")
    print(f"{'규칙':46} {'항상':>5} {'때때로':>7} {'없음':>5} {'흔들림':>8}")
    ranked = sorted(
        totals.items(),
        key=lambda kv: (kv[1][1] / (kv[1][0] + kv[1][1])) if (kv[1][0] + kv[1][1]) else 1.0,
    )
    for rule_id, (always, sometimes, never) in ranked:
        fired = always + sometimes
        share = f"{sometimes / fired:.0%}" if fired else "안 걸림"
        print(f"{rule_id:46} {always:>5} {sometimes:>7} {never:>5} {share:>8}")


def _cmd_probe(args) -> int:
    """규칙 하나를 단독으로 물어 안정성을 본다 — 강등/승격 판단용. **실제 LLM 호출.**

    측정은 자주 중단된다(레이트 리밋·시간 상한). 그래서 행이 나올 때마다 `--out` 파일에
    **즉시 덧붙인다** — 다음 시도가 이어서 쌓으면 되고 한 번에 다 돌지 않아도 된다.
    `--summarize`는 쌓인 파일만 읽어 순위를 낸다(LLM 없음).
    """
    # 요약은 LLM도 설정도 필요 없다 — **무거운 import보다 먼저** 처리한다.
    if args.summarize:
        lines = Path(args.summarize).read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
        # 같은 (도메인, 규칙)을 여러 번 재면 마지막 것을 쓴다.
        _rank(list({(r["domain"], r["rule_id"]): r for r in rows}.values()))
        return 0

    from app.requirements.evaluation import dataset, semantic

    print(f"{'도메인':18} {'규칙':46} {'항상':>5} {'때때로':>7} {'없음':>5}", flush=True)
    rows = []
    for run_dir in args.run_dirs or []:
        domain = dataset.domain_of(run_dir)
        payloads = semantic.payloads_from_run(run_dir)
        if args.limit:
            payloads = payloads[:args.limit]
        for rule_id in args.rules or []:
            row = semantic.probe_rule(rule_id, payloads, repeats=args.repeats)
            row["domain"] = domain
            rows.append(row)
            print(f"{domain:18} {rule_id:46} {row['always']:>5} "
                  f"{row['sometimes']:>7} {row['never']:>5}", flush=True)
            if args.out:
                # 즉시 덧붙인다. 중단돼도 여기까지는 남는다.
                with Path(args.out).open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if rows:
        _rank(rows)
    return 0


def _cmd_dataset_build(args) -> int:
    """라벨 붙일 눈가림 파일을 만든다(LLM 호출 없음)."""
    from app.requirements.evaluation import dataset

    out = dataset.build(args.rule, args.run_dirs, per_domain=args.per_domain)
    Path(args.out).write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    domains = sorted({i["domain"] for i in out["items"]})
    print(f"{args.out} — 항목 {len(out['items'])}개 · 도메인 {len(domains)}종 {domains}")
    print(f"규칙: {out['rule_id']}\n{out['instructions']}")
    return 0


def _cmd_dataset_score(args) -> int:
    """라벨 대비 모델 판정을 채점한다. **실제 LLM 호출.**"""
    from app.requirements.evaluation import dataset

    labelled = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    _print(dataset.score(labelled, repeats=args.repeats))
    return 0


def _cmd_score(args) -> int:
    from app.requirements.evaluation import scorecard as sc
    from app.requirements.runner import load_state

    state = load_state(args.run_dir)
    card = sc.scorecard(state)
    if args.out:
        Path(args.out).write_text(
            json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"채점표를 썼다: {args.out}")
    else:
        _print(card)
    return 0


def _cmd_diff(args) -> int:
    from app.requirements.evaluation import scorecard as sc

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    result = sc.diff(before, after)
    for warning in result["warnings"]:
        print(f"⚠ {warning}")
    if result["warnings"]:
        print()
    for key in ("static_now", "as_recorded", "totals"):
        deltas = result[key]
        print(f"[{key}] {'변화 없음' if not deltas else ''}")
        for name, delta in deltas.items():
            print(f"  {delta:+d}  {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.requirements.evaluation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seeded", help="심어 둔 결함으로 검사기 눈금을 확인한다").set_defaults(
        fn=_cmd_seeded
    )

    p_sem = sub.add_parser(
        "semantic", help="의미 규칙 눈금 (실제 LLM 호출 · CI 게이트 아님)"
    )
    p_sem.add_argument("--repeats", type=int, default=3,
                       help="케이스마다 몇 번 판정받을지(기본 3)")
    p_sem.add_argument("--stage", default=None,
                       help="한 단계만: write_specifications | draw_diagram | model_use_cases")
    p_sem.set_defaults(fn=_cmd_semantic)

    p_stab = sub.add_parser(
        "stability", help="실제 명세로 판정 안정성 측정 (실제 LLM 호출 · 게이트 아님)"
    )
    p_stab.add_argument("run_dir")
    p_stab.add_argument("--repeats", type=int, default=3)
    p_stab.add_argument("--limit", type=int, default=0,
                        help="명세 수를 앞에서 N개로 제한(전후 비교 시 같은 값을 쓸 것)")
    p_stab.set_defaults(fn=_cmd_stability)

    p_probe = sub.add_parser(
        "probe", help="규칙 하나를 단독으로 물어 안정성 확인 (강등/승격 판단 · 실제 LLM)"
    )
    p_probe.add_argument("--rules", nargs="+", help="규칙 id들")
    p_probe.add_argument("--run-dirs", nargs="+", dest="run_dirs")
    p_probe.add_argument("--out", help="행을 즉시 덧붙일 JSONL — 중단돼도 진행분이 남는다")
    p_probe.add_argument("--summarize", help="쌓인 JSONL만 읽어 순위를 낸다(LLM 없음)")
    p_probe.add_argument("--repeats", type=int, default=5)
    p_probe.add_argument("--limit", type=int, default=0)
    p_probe.set_defaults(fn=_cmd_probe)

    p_build = sub.add_parser(
        "dataset-build", help="사람이 라벨 붙일 눈가림 파일 생성 (LLM 없음)"
    )
    p_build.add_argument("--rule", required=True)
    p_build.add_argument("--run-dirs", nargs="+", required=True, dest="run_dirs")
    p_build.add_argument("--per-domain", type=int, default=5, dest="per_domain")
    p_build.add_argument("--out", required=True)
    p_build.set_defaults(fn=_cmd_dataset_build)

    p_dscore = sub.add_parser(
        "dataset-score", help="라벨 대비 모델 판정 채점 (실제 LLM 호출)"
    )
    p_dscore.add_argument("--labels", required=True)
    p_dscore.add_argument("--repeats", type=int, default=5)
    p_dscore.set_defaults(fn=_cmd_dataset_score)

    p_score = sub.add_parser("score", help="artifacts/run_*/ 를 채점한다")
    p_score.add_argument("run_dir")
    p_score.add_argument("--out", help="채점표를 이 경로에 JSON으로 쓴다")
    p_score.set_defaults(fn=_cmd_score)

    p_diff = sub.add_parser("diff", help="두 채점표의 규칙별 증감")
    p_diff.add_argument("before")
    p_diff.add_argument("after")
    p_diff.set_defaults(fn=_cmd_diff)

    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
