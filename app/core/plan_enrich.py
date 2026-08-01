"""배포 계획에 3사 실측을 **붙인다** — 대조기가 센 틈을 메우는 쪽.

## 왜 있나

`plan_crosscheck`가 표본 3종 × 3사를 재서 같은 결론을 냈다(2026-08-01):
계획이 실측을 한 번도 만나지 않고, **그 유실은 앱과 무관한 구조적 사실**이다.
빠진 것 절반이 노드·선으로는 담을 수 없는 종류였다 —

    시간축      무엇을 먼저 만들고 무엇을 먼저 지우나 · 언제까지 기다리나
    무방비 지대  컨트롤 플레인이 **막지 않는** 결속(떼면 조용히 깨진다)
    서버의 몫    우리가 만들면 이중이 되는 것, 안 정하면 서버가 채우는 것

그래서 계획에 자리를 만들고(`plan.Measured`) 여기서 채운다.

## 만들지 않고 **붙이기만** 한다

노드를 더하거나 지우지 않는다. 계획의 모양은 설계 산출물이 정하고(그쪽이 앱을
안다), 우리는 그 위에 *"이 구성에 대해 실측이 아는 것"*을 얹을 뿐이다. 계획을
고치는 것은 사람의 판단이고, 그 판단에 필요한 재료를 대는 것이 여기까지다.

**검사 규칙도 판정하지 않는다** — 규칙과 계획을 나란히 놓는 데까지다. 이유가
둘인데 섞어 적지 않는다(`plan_crosscheck` 모듈 문서 참고): 규율(규칙을 코드로
다시 적으면 사본이 둘이 된다)과 **정보 부재**다. 여덟 중 일곱이 뒤쪽이고,
계획에 가용영역·존·시점 칸이 없어서다. 그래서 규칙마다 **무엇이 있으면
판정되는지**를 함께 낸다 — 그것이 계획 형식에 대한 요구 목록이 된다.

## 앵커는 계획 자신이 정한다

폐포의 입력은 *"우리가 놓기로 한 워크로드"*다. 계획이 그린 것을 전부 앵커로
삼으면 폐포가 그것들을 "사용자가 고른 것"으로 받아들여 **서버가 대신 만든다는
주장이 통째로 사라진다.** 무엇이 워크로드인지는 계획의 `role`이 이미 안다 —
`plan_crosscheck.read_plan`과 같은 판단을 쓴다(둘이 갈리면 배선한 것과 대조한
것이 달라진다).
"""

from __future__ import annotations

from app.core.cloudkb.appkb.plan import DeploymentPlan, Measured
from app.core.plan_crosscheck import WORKLOAD_ROLES, read_plan
from app.core.infra_planning import plan_for_anchors


def enrich(plan: DeploymentPlan, csp: str, region: str = "-") -> DeploymentPlan:
    """계획에 실측 사영을 붙인다. **계획 자체는 안 바꾼다.**

    앵커를 하나도 못 읽으면 `measured`를 비운 채 두지 않고 **빈 묶음으로** 채운다
    — `None`("안 붙였다")과 "붙였는데 아는 게 없다"는 다른 사실이라서다.

    Args:
        plan: `design_tools.compose`가 낸 계획. 제자리에서 바뀐다.
        csp: 주장이 CSP로 색인돼 있어 필수다.
        region: 계획에 실릴 리전(판정에는 안 쓰인다).
    """
    mapped, unmapped, _weak, roles = read_plan(plan, csp)
    drawn = set(mapped.values())
    anchors = tuple(sorted({
        res for node, res in mapped.items()
        if roles.get(node) in WORKLOAD_ROLES}))
    # 계획에 있는데 어휘 밖인 것 — 관리형 서비스가 대부분이다(실측 0건).
    unmeasured = tuple(sorted(unmapped))

    if not anchors:
        plan.measured = Measured(csp=csp, unmeasured=unmeasured)
        return plan

    provision = plan_for_anchors(list(anchors), csp, region).provision

    # 순서에는 **그린 것 + 필수인 것**을 담는다. 선택 자원까지 다 실으면 계획이
    # 아니라 폐포를 옮겨 적는 것이 되지만, **필수는 다르다** — 없으면 apply가
    # 거부하므로 그리지 않았어도 만들어야 한다.
    #
    # aws의 `image`가 정확히 그 자리다: 계획은 이미지를 자원이 아니라 **값**으로
    # 다뤄 컴퓨트 노드의 노트로 붙인다(`design_tools._add_image_note` — 그 판단은
    # 맞다). 그래서 노드 집합만 보면 "필수가 빠졌다"로 읽히는데, 순서에 실으면
    # 그 요건이 실행하는 사람에게 그대로 간다.
    create_order = tuple(c["id"] for c in provision["createOrder"]
                         if c["id"] in drawn or c["required"])
    delete_before = tuple((a, b) for a, b in
                          (tuple(p) for p in provision["deleteBefore"])
                          if a in drawn and b in drawn)
    wait_for = tuple(
        (w["id"], w["op"], w["doneSignal"], w["confidence"])
        for w in provision["waitFor"] if w["id"] in drawn)
    # `doNotCreate`는 **그리지 않은 것도 싣는다** — "안 그려도 된다"가 정보다.
    do_not_create = tuple(
        (d["id"], d["why"], d.get("kind", "")) for d in provision["doNotCreate"])
    # 같은 쌍이 두 번 실측된 경우가 있다(aws `subnet→internetGateway`는 인바운드·
    # 아웃바운드 신호로 따로 쟀다). **주장은 둘이지만 사람에게 할 말은 하나**라
    # 여기서 합친다 — 뷰가 낸 문장이 같으면 같은 경고다.
    warnings = tuple(dict.fromkeys(
        (w["subject"], w["object"], w["warning"])
        for w in provision["operationalWarnings"]
        if w["subject"] in drawn or w["object"] in drawn))
    checks = tuple(
        (c["subject"], c["object"], c["rule"]) for c in provision["checks"]
        if c["subject"] in drawn or c["object"] in drawn)

    plan.measured = Measured(
        csp=csp, anchors=anchors,
        create_order=create_order, delete_before=delete_before,
        wait_for=wait_for, do_not_create=do_not_create,
        operational_warnings=warnings, checks=checks, unmeasured=unmeasured)
    return plan


#: 검사 규칙의 부류 표식 → **판정에 필요한데 계획에 없는 것.**
#:
#: 규칙 문자열에서 부류를 읽는 이유: `Measured.checks`는 (주체, 대상, 규칙)
#: 셋만 나르고 부류를 안 나른다. 부류까지 실으면 계획 자료 모델이 depkb의
#: 분류 어휘를 알아야 하는데, `plan.py`는 **KB를 import하지 않는다**는 규율이
#: 있다. 그래서 여기서 읽는다 — 규칙 원문이 부류 접두사를 달고 오기 때문에
#: 가능하다(`closure.PREDICATE_CLASSES`).
_NEEDS: tuple[tuple[str, str], ...] = (
    ("배치 조건", "availability zone of each resource"),
    ("ALB는", "availability zone of each subnet"),
    ("쌍 호환", "the zone/SKU attributes of both resources"),
    ("수명 조건", "a time axis (create-time versus afterwards)"),
)


def _judging_needs(rule: str) -> str:
    """그 규칙을 기계로 보려면 계획에 무엇이 더 있어야 하는가. 없으면 빈 문자열."""
    for mark, needs in _NEEDS:
        if mark in rule:
            return needs
    return ""


def render(measured: Measured | None) -> str:
    """사람이 읽는 실측 절. 계획 본문 뒤에 붙는다.

    `None`이면 **침묵하지 않는다** — 안 붙였다는 사실 자체를 낸다.
    """
    if measured is None:
        return ("Measured cloud knowledge: **not attached** — this is not a claim "
                "that nothing applies.")
    if not measured.anchors:
        return (f"Measured cloud knowledge ({measured.csp}): no workload in this "
                "plan maps onto a resource we have measured, so ordering, "
                "cleanup and operational warnings are all absent — **not empty, "
                "unknown**."
                + (f" Outside our measurements: {', '.join(measured.unmeasured)}."
                   if measured.unmeasured else ""))

    lines = [f"Measured cloud knowledge ({measured.csp}) — from applying against "
             f"the real control plane, anchored on {', '.join(measured.anchors)}:"]
    if measured.create_order:
        lines.append("  Create in this order: "
                     + " → ".join(measured.create_order))
    if measured.delete_before:
        lines.append("  Delete in this order (otherwise the API refuses): "
                     + " · ".join(f"{a} before {b}"
                                  for a, b in measured.delete_before))
    for rid, op, signal, confidence in measured.wait_for:
        note = ("an intermediate state was observed"
                if confidence == "async-confirmed"
                else "we waited — not proof that waiting is required")
        lines.append(f"  Wait after {rid}.{op} until `{signal}` ({note})")
    for rid, why, kind in measured.do_not_create:
        mark = "creating it as well is a duplicate" if kind == "server-implicit" \
            else "you may still set it yourself"
        lines.append(f"  {why} — {mark}")
    for subject, obj, rule in measured.checks:
        missing = _judging_needs(rule)
        why = (f"we cannot judge it here — the plan carries no {missing}"
               if missing else "we do not judge it here — check it yourself")
        lines.append(f"  Rule on {subject}→{obj}: {rule} ({why})")
    for subject, obj, breaks in measured.operational_warnings:
        lines.append(f"  {breaks}")
    if measured.unmeasured:
        lines.append("  We have measured nothing about: "
                     + ", ".join(measured.unmeasured)
                     + " — **silence here is not a pass**")
    return "\n".join(lines)
