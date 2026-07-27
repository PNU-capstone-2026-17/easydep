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


def _balanced(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """규칙을 **전부** 잰 도메인만 남긴다. 돌려주는 둘째 값은 버린 도메인.

    캠페인은 예산이 떨어지면 도메인 중간에서 멈춘다. 그 도메인은 앞쪽 규칙만 측정돼 있어서,
    합계에 넣으면 앞쪽 규칙이 표본을 한 도메인 더 갖는다 — 순위가 규칙의 성질이 아니라
    **측정이 어디서 끊겼는지**를 반영하게 된다.

    잘라낸 것을 부르는 쪽이 반드시 말하게 하려고 목록으로 돌려준다. 조용히 버리면 이 함수가
    막으려는 것과 같은 종류의 사고가 된다.
    """
    all_rules = {r["rule_id"] for r in rows}
    by_domain: dict[str, set[str]] = {}
    for row in rows:
        by_domain.setdefault(row["domain"], set()).add(row["rule_id"])
    complete = {d for d, seen in by_domain.items() if seen == all_rules}
    dropped = sorted(set(by_domain) - complete)
    return [r for r in rows if r["domain"] in complete], dropped


def _domains_per_rule(rows: list[dict]) -> dict[str, int]:
    """규칙마다 **몇 개 도메인에서** 쟀는가."""
    seen: dict[str, set[str]] = {}
    for row in rows:
        seen.setdefault(row["rule_id"], set()).add(row["domain"])
    return {rule_id: len(domains) for rule_id, domains in seen.items()}


def _ranking_warnings(rows: list[dict]) -> list[str]:
    """이 표를 순위로 읽으면 안 되는 이유들. 비어 있으면 읽어도 된다.

    이 저장소에서 결론이 뒤집힌 원인은 언제나 표본 쪽이었지 계산 쪽이 아니었다(§8은 도메인
    하나에 과적합, §9는 조건이 섞임). 그래서 표보다 **이 목록을 먼저** 찍는다.
    """
    warnings = []
    if len({(r["repeats"], r["n_specs"]) for r in rows}) > 1:
        warnings.append("조건이 섞여 있다 — 같은 조건으로 다시 모아야 한다.")

    # 캠페인은 몇 시간을 돈다. 그동안 프롬프트가 바뀌면 앞뒤 행이 다른 것을 잰 것이 되는데,
    # 반복·명세수만 봐서는 안 보인다.
    fingerprints = {json.dumps(r.get("prompts"), sort_keys=True) for r in rows if r.get("prompts")}
    if len(fingerprints) > 1:
        warnings.append(f"프롬프트 판이 {len(fingerprints)}가지 섞여 있다 — 측정 도중 코드가 바뀌었다.")

    coverage = set(_domains_per_rule(rows).values())
    if len(coverage) > 1:
        warnings.append(f"규칙마다 잰 도메인 수가 다르다 {sorted(coverage)} — "
                        "적게 잰 규칙은 빼고 읽어야 한다.")
    elif len({r["domain"] for r in rows}) < 2:
        warnings.append("도메인이 하나다 — 한 데이터셋에서 나온 수로 규칙을 바꾸지 않는다(§9).")
    return warnings


def _rank(rows: list[dict]) -> None:
    """(도메인, 규칙) 행들을 규칙별로 합쳐 순위를 찍는다."""
    totals: dict[str, list[int]] = {}
    for row in rows:
        acc = totals.setdefault(row["rule_id"], [0, 0, 0])
        acc[0] += row["always"]
        acc[1] += row["sometimes"]
        acc[2] += row["never"]

    domains = sorted({r["domain"] for r in rows})
    print(f"\n[합계 — 단독 프로브] 도메인 {len(domains)}종 {domains}")
    print(f"조건(반복, 명세수): {sorted({(r['repeats'], r['n_specs']) for r in rows})}")
    if not any(r.get("prompts") for r in rows):
        print("· 프롬프트 판 기록 없음(fingerprint 도입 전 데이터다)")

    warnings = _ranking_warnings(rows)
    for warning in warnings:
        print(f"⚠ {warning}")
    print("→ 이 표는 **아직 순위가 아니다**" if warnings else "→ 순위로 읽어도 되는 표")

    coverage = _domains_per_rule(rows)
    print(f"\n{'규칙':46} {'항상':>5} {'때때로':>7} {'없음':>5} {'흔들림':>8} {'도메인':>7}")
    ranked = sorted(
        totals.items(),
        key=lambda kv: (kv[1][1] / (kv[1][0] + kv[1][1])) if (kv[1][0] + kv[1][1]) else 1.0,
    )
    for rule_id, (always, sometimes, never) in ranked:
        fired = always + sometimes
        share = f"{sometimes / fired:.0%}" if fired else "안 걸림"
        print(f"{rule_id:46} {always:>5} {sometimes:>7} {never:>5} {share:>8} "
              f"{coverage.get(rule_id, 0):>7}")


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
        # 같은 (도메인, 규칙, 명세)를 여러 번 재면 마지막 것을 쓴다. 명세 단위 행은
        # `_rank`가 규칙별로 합치므로 그대로 넘긴다.
        latest = {(r["domain"], r["rule_id"], r.get("spec_id")): r for r in rows}
        rows = list(latest.values())
        if args.balanced:
            rows, dropped = _balanced(rows)
            # 무엇을 버렸는지 **먼저** 말한다. 표를 보고 나서 알면 이미 읽은 뒤다.
            print(f"[균형 표본] 규칙을 다 재지 못한 도메인 {len(dropped)}종을 뺐다: {dropped}"
                  if dropped else "[균형 표본] 뺄 도메인이 없다 — 전부 규칙을 다 쟀다")
            if not rows:
                print("남는 행이 없다. 완주한 도메인이 아직 하나도 없다.")
                return 1
        _rank(rows)
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


def _cmd_campaign(args) -> int:
    """무인 캠페인 — 몇 시간 동안 혼자 돌면서 쌓는다. **실제 LLM 호출.**"""
    from app.requirements.evaluation import campaign

    return campaign.run(
        out_dir=Path(args.out_dir),
        hours=args.hours,
        run_dirs=args.run_dirs or [],
        labels_path=Path(args.labels) if args.labels else None,
        pure_docs=args.pure_docs or [],
        repeats=args.repeats,
        limit=args.limit,
        concurrency=args.concurrency,
        phases=args.phases,
        pure_limit=args.pure_limit,
    )


def _cmd_playbook(args) -> int:
    """실행에서 배운 것을 쌓고 보여 준다. **LLM 없음** — 아티팩트를 읽어 세는 것뿐이다.

    쌓는 것과 쓰는 것을 갈라 둔다. 여기서 파일을 만들어도 파이프라인은 `PLAYBOOK_ENABLED=1`
    이어야 읽는다 — 배운 것이 조용히 프롬프트에 들어가지 않도록.
    """
    from app.requirements.agent import playbook
    from app.requirements.knowledge import rules

    entries = playbook.load(args.path)
    for run_dir in args.runs or []:
        entries = playbook.curate(entries, playbook.harvest(run_dir))
    if args.runs:
        playbook.save(args.path, entries)
        print(f"{len(args.runs)}개 실행을 반영했다 → {args.path}")

    for entry in sorted(entries, key=lambda e: (-e.runs, e.rule_id)):
        mark = "실린다" if entry.qualifies else "문턱 미달"
        print(f"[{mark}] {entry.rule_id}  실행 {entry.runs}종"
              f"  (검출기 {len(set(entry.detector_runs))} · 검증자 {len(set(entry.validator_runs))})")
    if not entries:
        print("아직 배운 것이 없다.")

    for stage in (rules.WRITE_SPECIFICATIONS, rules.DRAW_DIAGRAM):
        block = playbook.render(entries, stage)
        if block:
            print(f"\n--- {stage} 생성 프롬프트에 실릴 절 ---\n{block}")
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
    _print(dataset.score(
        labelled,
        repeats=args.repeats,
        verdict_file=Path(args.verdicts) if args.verdicts else None,
        budget=args.budget or None,
    ))
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
    p_probe.add_argument("--balanced", action="store_true",
                         help="규칙을 다 재지 못한 도메인을 빼고 합친다. 중단된 캠페인의 "
                              "마지막 도메인은 앞쪽 규칙만 재고 끝나므로, 그대로 합치면 "
                              "순위가 '측정이 어디서 끊겼는지'를 반영한다")
    p_probe.add_argument("--repeats", type=int, default=5)
    p_probe.add_argument("--limit", type=int, default=0)
    p_probe.set_defaults(fn=_cmd_probe)

    p_camp = sub.add_parser(
        "campaign", help="무인 측정 캠페인 — 중단돼도 이어서 쌓는다 (실제 LLM)"
    )
    p_camp.add_argument("--out-dir", required=True, dest="out_dir")
    p_camp.add_argument("--hours", type=float, default=8.0)
    p_camp.add_argument("--run-dirs", nargs="+", dest="run_dirs")
    p_camp.add_argument("--labels")
    p_camp.add_argument("--pure-docs", nargs="+", dest="pure_docs")
    p_camp.add_argument("--repeats", type=int, default=5)
    p_camp.add_argument("--limit", type=int, default=5,
                        help="프로브가 잴 명세 수(실행 하나당)")
    p_camp.add_argument("--pure-limit", type=int, default=30, dest="pure_limit",
                        help="PURE 문서 하나에서 뽑을 요구사항 수. 크면 실행 하나가 길어져 "
                             "중단됐을 때 잃는 것이 많다")
    p_camp.add_argument("--phases", nargs="+", default=["stability", "score", "pure"],
                        choices=["stability", "score", "pure"],
                        help="단계 순서. 앞의 것이 예산을 먼저 쓴다")
    p_camp.add_argument("--concurrency", type=int, default=2,
                        help="동시 호출 수. 높이면 스로틀에 걸려 오히려 느려진다(실측)")
    p_camp.set_defaults(fn=_cmd_campaign)

    p_pb = sub.add_parser(
        "playbook", help="실행에서 배운 것을 쌓고 보여 준다 (LLM 없음)"
    )
    p_pb.add_argument("--path", default="artifacts/playbook.json",
                      help="플레이북 파일. 파이프라인이 읽는 것과 같은 경로여야 한다")
    p_pb.add_argument("--runs", nargs="+", help="반영할 실행 디렉터리. 없으면 보여 주기만 한다")
    p_pb.set_defaults(fn=_cmd_playbook)

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
    p_dscore.add_argument("--verdicts", help="모델 판정을 즉시 덧붙일 JSONL — 중단돼도 이어서 채운다")
    p_dscore.add_argument("--budget", type=int, default=0,
                          help="이번 호출에서 새로 물어볼 항목 수 상한(0=제한 없음)")
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
