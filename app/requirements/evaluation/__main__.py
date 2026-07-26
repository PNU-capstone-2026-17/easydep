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
    report = semantic.measure_stability(payloads, repeats=args.repeats)
    print(f"모델 {report['model']} · 명세 {report['n_specs']}개 × {report['repeats']}회\n")
    print(f"{'규칙':52} {'항상':>5} {'때때로':>7} {'흔들림':>7}")
    for rule_id, row in report["per_rule"].items():
        print(f"{rule_id:52} {row['always']:>5} {row['sometimes']:>7} "
              f"{row['unstable_share']:>7.0%}")
    print(f"\n전체 흔들림 비율: {report['unstable_share']:.0%}")
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
    p_stab.set_defaults(fn=_cmd_stability)

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
