"""전체 사슬 데모 — 요구사항에서 배포 다이어그램까지 한 번에.

    요구사항(자연어)
      → 배포 의도(k8s 층 — 하류 에이전트가 내는 것)
      → 번역(앵커와 그 근거)
      → 인프라 계획(3사 각각)
      → 계획 검사(구체 계획이 있으면)
      → 배포 다이어그램(HTML)

실행:

    python -m app.core.cloudkb.depkb.demo            # 전부
    python -m app.core.cloudkb.depkb.demo stateful-store   # 하나만

산출: `deployment-diagram.html`(브라우저로 열면 3사가 나란히 보인다).
"""

from __future__ import annotations

import sys

from app.core.infra_planning import plan_for_anchors, plan_from_deployment_intent

from .examples import EXAMPLES, by_id
from .render_deployment import _OUT, CSPS, render


def _line(char: str = "─", n: int = 78) -> None:
    print(char * n)


def run(example) -> None:
    _line("━")
    print(f"{example.title}")
    _line("━")
    print(f'요구사항: "{example.requirement}"\n')

    workloads = example.deployment_intent["workloads"]
    print("[1] 배포 의도 — 하류(구현 에이전트)가 내는 k8s 층 산출물")
    for w in workloads:
        caps = ", ".join(k for k, v in (w.get("capabilities") or {}).items() if v)
        print(f"    {w['name']}: kind={w['kind']}  capabilities={caps or '-'}")

    plans, unavailable = {}, {}
    for csp in CSPS:
        p = plan_from_deployment_intent(example.deployment_intent, csp, "-")
        if example.given_anchors:
            # 하류 스키마에 자리가 없어 사람이 준 앵커를 합친다. 그 자원을
            # 그 CSP에서 재지 않았으면 **계획을 내지 않는다** — 규율대로 죽는
            # 것을 여기서 잡아 "못 낸다"고 말한다.
            merged = sorted(set(p.intent.anchors) | set(example.given_anchors))
            try:
                p2 = plan_for_anchors(merged, csp, "-")
            except KeyError as e:
                unavailable[csp] = str(e)
                plans[csp] = p
                continue
            p = type(p)(intent=p2.intent, design=p2.design,
                        provision=p2.provision, translation=p.translation,
                        report=None, questions=p.questions + p2.questions,
                        unmeasured=p.unmeasured, notes=p.notes)
        plans[csp] = p
    sample = plans[CSPS[0]]

    print("\n[2] 번역 — 어떤 클라우드 자원을 골랐고 왜")
    for anchor, why in sample.translation.rationale:
        print(f"    {anchor:14} ← {why}")
    for anchor in example.given_anchors:
        print(f"    {anchor:14} ← (사람이 줌) {example.given_anchors_why}")
    for q in sample.translation.open_questions:
        print(f"    (물어야 함) {q}")
    for note in sample.notes:
        print(f"    (안 씀) {note}")
    for u in sample.unmeasured:
        print(f"    (못 잼)  {u}")

    print("\n[3] 인프라 계획 — 같은 요구, 세 답")
    for csp in CSPS:
        p = plans[csp]
        if csp in unavailable:
            print(f"    {csp:6} **계획 없음** — 요구된 자원을 이 클라우드에서 "
                  f"재지 않았다. 추측 대신 비운다")
            print(f"    {'':6} ({unavailable[csp][:96]})")
            continue
        auto = [x["id"] for x in p.provision["doNotCreate"]]
        print(f"    {csp:6} 만들 순서: {' → '.join(p.intent.createOrder)}")
        print(f"    {'':6} 서버가 채움: {', '.join(auto) or '-'}")
        if p.intent.constraints:
            for c in p.intent.constraints:
                print(f"    {'':6} 규칙: {c.subject}→{c.object} — {c.rule}")
        for q in (q for q in p.questions if q not in sample.translation.open_questions):
            print(f"    {'':6} 물어볼 것: {q}")

    if example.concrete_plans:
        print("\n[4] 계획 검사 — 구체 계획이 규칙을 지키나")
        if example.check_anchors:
            print(f"    (검사 앵커: {', '.join(example.check_anchors)} — "
                  f"{example.check_anchors_why})")
        for csp, concrete in example.concrete_plans.items():
            checked = (
                plan_for_anchors(list(example.check_anchors), csp, "-",
                                 concrete_plan=concrete)
                if example.check_anchors else
                plan_from_deployment_intent(
                    example.deployment_intent, csp, "-", concrete_plan=concrete))
            r = checked.report
            verdict = "통과" if r.ok else "위반"
            print(f"    {csp}: {verdict}")
            for v in r.violations:
                print(f"        ✗ {v.detail}")
                print(f"          규칙: {v.rule} ({v.kind})")
            for m in r.missing_required:
                print(f"        ✗ 필수 자원이 계획에 없다: {m}")
            for u in r.unchecked:
                print(f"        · 미검사(통과 아님): {u}")
    print()


def main() -> int:
    targets = ([by_id(a) for a in sys.argv[1:]] if len(sys.argv) > 1
               else list(EXAMPLES))
    for example in targets:
        run(example)
    _OUT.write_text(render(), encoding="utf-8")
    _line()
    print(f"[5] 배포 다이어그램: {_OUT}")
    print("    브라우저로 열면 예제마다 3사가 나란히 보인다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
