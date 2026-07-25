"""다이어그램 주장 대조 — **우리가 낸 그림을 우리가 검사한다.**

`claim_check` 계보다. 저쪽은 답변의 구체값이 도구 출력에 있는지 봤고, 여기는
**그림의 모든 노드·선이 계획에 있는지, 계획의 모든 것이 그림에 있는지** 본다.

## 왜 필요한가

다이어그램은 이 저장소가 처음으로 만드는 **생성물**이다. 조립하다 노드를 흘리면
그림이 조용히 작아지고, 아무도 모른다 — 답변이 아니라 그림이라 눈으로도 안 걸린다.
(bundlekb에서 VM의 유일한 필수 동반자를 흘려 "VM은 아무것도 필요 없다"가 나온 적이
있다. 그때는 테스트가 잡았다.)

KB를 import하지 않는다 — 이름 제약 검증처럼 KB가 필요한 검사는 도구 계층이 한다.
"""

from __future__ import annotations

import re

from appkb.plan import DeploymentPlan, needs_hedge
from appkb.diagram import parse_back

#: 계획의 노드 id가 지켜야 하는 모양. 계약의 `components[].id`와 같은 규칙이라
#: 여기서 어긋나면 우리가 조립 중에 망가뜨린 것이다.
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def verify_plan(plan: DeploymentPlan) -> list[str]:
    """계획 자체의 정합성. 빈 목록이면 통과."""
    problems: list[str] = []
    ids = [n.id for n in plan.nodes]
    seen = set()
    for node_id in ids:
        if node_id in seen:
            problems.append(f"[plan] duplicate node id: {node_id}")
        seen.add(node_id)
        if not _ID.match(node_id):
            problems.append(f"[plan] node id breaks the resource name rule: {node_id}")

    for edge in plan.edges:
        for end in (edge.from_id, edge.to_id):
            if end not in seen:
                problems.append(f"[plan] a connection points at a missing node: {end}")

    for node in plan.nodes:
        if node.role == "managed" and not node.type_id and not node.candidates:
            # **"관리형 서비스가 필요하다"까지만 알고 무엇인지 모르는 상태**를
            # 조용히 두면 그림에 빈 상자가 남는다. 미결로 올려야 한다.
            if not any(node.id in item for item in plan.unresolved):
                problems.append(
                    f"[plan] {node.id}: managed, but it has no type, no candidates,"
                    " and is not in the 'could not answer' list either"
                )
    return problems


def verify_diagram(plan: DeploymentPlan, uml: str) -> list[str]:
    """그림이 계획을 **빠짐없이, 더하지 않고** 옮겼는가."""
    problems: list[str] = []
    aliases, edges = parse_back(uml)

    plan_ids = {n.id for n in plan.nodes}
    missing = plan_ids - aliases
    extra = aliases - plan_ids
    if missing:
        problems.append(
            f"[diagram] nodes in the plan but not in the diagram: {sorted(missing)}"
        )
    if extra:
        # 지어낸 노드다 — 답변에서 없는 값을 만드는 것과 같은 실패의 그림판.
        problems.append(
            f"[diagram] nodes in the diagram but not in the plan: {sorted(extra)}"
        )

    plan_edges = {(e.from_id, e.to_id) for e in plan.edges}
    if plan_edges - edges:
        problems.append(
            f"[diagram] connections missing from the diagram: {sorted(plan_edges - edges)}"
        )
    if edges - plan_edges:
        problems.append(
            f"[diagram] connections not in the plan: {sorted(edges - plan_edges)}"
        )

    # 유보가 그림에 살아 있는가 — 그림은 잘려 돌아다닌다.
    if plan.hedged_count and "inferred" not in uml \
            and "specified by the designer" not in uml:
        problems.append(
            "[diagram] there are inferred / designer-specified items but the diagram"
            " carries no mark"
        )
    return problems


#: 청구 대상이 될 수 있는 역할 — 값이 안 붙었으면 "미가격"으로 센다.
#: actor(사람)·external(남의 시스템)은 우리 청구서가 아니다.
_BILLABLE_ROLES = ("compute", "managed", "shared", "ingress")


def verify_against_requirements(
    plan: DeploymentPlan, requirements: dict | None, hours_per_month: float
) -> list[str]:
    """계획 ↔ 요구 제약 대조 — **판정문 목록**을 돌려준다 (문제 목록이 아니다).

    research.md 목표 1의 "산출물이 요구사항에 부합하는지 측정"이 이 함수다.
    잴 수 없는 것을 잴 수 없다고 말하는 것까지가 측정이다 — 침묵을 안전 신호로
    오독하게 두지 않는다.

    `hours_per_month`는 호출자가 준다(costkb의 730). appkb는 KB를 import하지
    않으므로 그 상수를 여기 복제하면 두 벌이 되어 드리프트한다.

    ## 예산 판정은 비대칭이다

    이 계획은 합계를 내지 않는다(미가격 구성원이 있어 더하면 실제보다 낮아진다).
    그래도 판정은 반쪽이 가능하다: **값이 붙은 부분만의 월합은 실제 청구의
    하한**이므로, 하한이 이미 예산을 넘으면 초과는 확정이다. 반대로 하한이 예산
    아래인 것은 부합의 근거가 못 된다 — 모르는 것을 0으로 치지 않는다.
    """
    req = requirements or {}
    out: list[str] = []

    budget = req.get("monthlyBudgetUSD")
    priced = [n for n in plan.nodes if n.hourly_usd]
    unpriced = sorted(
        n.id for n in plan.nodes if n.role in _BILLABLE_ROLES and not n.hourly_usd
    )
    if budget is None:
        out.append(
            "Budget: no yardstick — without monthlyBudgetUSD we do not judge cost fit"
        )
    elif not priced:
        out.append(
            f"Budget (${budget:,.2f}/month): no verdict — no node carries a value"
            " (provider/region unspecified, or a layout with no price axis)"
        )
    else:
        floor = sum(n.hourly_usd for n in priced) * hours_per_month
        # "1대 기준"을 판정문에 박는다 — 하한은 컴퓨트가 각 1대일 때의 값이고,
        # 수평 확장(대수)은 이 지식베이스가 정하지 못하는 사이징이다. 이 명시가
        # 없으면 하한이 "스케일아웃해도 이 값"으로 읽힌다.
        if floor > budget:
            out.append(
                f"Budget (${budget:,.2f}/month): **over, confirmed** — the monthly sum"
                f" of the priced part alone (a floor, one instance per compute) is "
                f"${floor:,.2f}, already past the budget. "
                f"{len(unpriced)} unpriced members are not even added in"
            )
        else:
            out.append(
                f"Budget (${budget:,.2f}/month): **cannot be asserted to fit** — the "
                f"floor of the priced part (one instance per compute) is ${floor:,.2f}, "
                f"but we do not know the value of {len(unpriced)} unpriced members "
                f"({', '.join(unpriced[:5])}), and the number of horizontally scaled "
                "instances is not fixed either. We do not treat the unknown as zero"
            )

    users = req.get("expectedConcurrentUsers")
    rps = req.get("approxRequestsPerSecond")
    if users is not None or rps is not None:
        scale = (
            f"{users} concurrent users" if users is not None else f"about {rps} RPS"
        )
        out.append(
            f"Scale ({scale}): this knowledge base cannot judge whether the spec is "
            "sufficient — the plan's sizing is marked as an estimate with no backing, "
            "and must be confirmed with a load test or a sizing reference point"
        )

    if req.get("multiZone"):
        if not any(n.role == "shared" for n in plan.nodes):
            out.append(
                "multiZone: nothing to check — this plan has no VM network (serverless)."
                " Spreading across availability zones is decided in a layer this plan"
                " does not cover"
            )
        else:
            subnet = plan.node("subnet")
            if subnet and any("availability zone" in n.text for n in subnet.notes):
                out.append(
                    "multiZone: reflected — the subnet carries the availability-zone"
                    " spread requirement"
                )
            else:
                out.append(
                    "multiZone: **not reflected** — the requirement is multiZone but "
                    "the plan shows no trace of an availability-zone spread"
                )

    pattern = req.get("trafficPattern")
    if pattern:
        # 버스트 경고는 도구 계층이 perfkb에서 붙인 노트로 판정한다. 경고의
        # **부재**를 "버스트 아님"으로 읽는 건 침묵 오독이라, 값이 붙은 노드가
        # 있을 때만(=성능 조인이 실제로 돌았을 때만) 그렇게 말한다 — partial·
        # untracked는 노트가 붙으므로 부재와 구분된다.
        # 노트 원문은 perfkb 데이터셋에서 온다. 데이터셋을 영어로 다시 빌드했으므로
        # 한국어 후보는 뺐다 — 두 언어를 다 받으면 "어느 쪽이 진짜인지"가 흐려지고,
        # 다음 사람이 데이터셋을 되돌려도 검사가 통과해 버린다.
        burst = sorted(
            n.id for n in plan.nodes
            if any(
                x.source == "perfkb" and "burst" in x.text.lower()
                for x in n.notes
            )
        )
        if pattern == "steady" and burst:
            out.append(
                f"trafficPattern(steady): **conflict** — the load is sustained but "
                f"burst instances ({', '.join(burst)}) are in the plan. When CPU "
                "credits run out, performance drops to baseline — review this with a "
                "fixed-performance spec"
            )
        elif pattern == "spiky" and burst:
            out.append(
                f"trafficPattern(spiky): no known conflict with the burst instances "
                f"({', '.join(burst)}) — a credit model can fit intermittent spikes"
            )
        elif any(n.hourly_usd for n in plan.nodes):
            out.append(
                f"trafficPattern({pattern}): no burst warning on the plan's specs"
                " (as checked on the performance axis)"
            )
        else:
            out.append(
                f"trafficPattern({pattern}): no verdict — this plan has no value or "
                "performance join"
            )

    stateless = req.get("stateless")
    serverless = sorted(
        n.id for n in plan.nodes if n.archetype == "app::serverlessFunction"
    )
    if stateless is False and serverless:
        out.append(
            f"stateless(false) × serverless ({', '.join(serverless)}): **possible "
            "conflict (we inferred)** — a serverless function is an execution model "
            "that keeps no state on the instance, so moving the state to managed "
            "storage is a redesign you will need"
        )
    elif stateless is False:
        out.append(
            "stateless(false): state kept on compute disappears on re-creation and "
            "scaling — the plan's storage node is where state belongs (we inferred)"
        )
    elif stateless is None and serverless:
        # 서버리스가 계획에 있는데 상태성 주장을 못 받았다 — 침묵을 적합으로
        # 읽지 않도록 미확인을 명시한다.
        out.append(
            f"Statefulness unconfirmed: serverless ({', '.join(serverless)}) is in the "
            "plan but we were not told whether it is stateless"
        )
    elif stateless is True:
        out.append(
            "stateless(true): no known conflict with the plan — recorded as a claim"
        )

    residency = req.get("dataResidency")
    if residency:
        # **판정 불가를 판정으로 낸다.** 리전의 국가를 기계 판정할 소스가 없다 —
        # 표시 이름이 프로바이더마다 자유 서식이라(실측), 산문에서 국가를 추출해
        # 판정하면 확신에 찬 오답이 된다. 대조 자료(원본 표시 이름)는 도구 계층이
        # 계획 노트로 싣는다.
        out.append(
            f"dataResidency({residency}): **no verdict** — we have no source that "
            "decides a region's country by machine. Check it yourself against the "
            "region's original display name in the plan notes"
        )

    provider = (req.get("provider") or "").strip().lower()
    if provider:
        mismatched = sorted(
            n.id for n in plan.nodes
            if n.type_id and "::" in n.type_id
            and n.type_id.split("::")[0] not in ("core", "app", provider)
        )
        if mismatched:
            out.append(
                f"Provider ({provider}): **mismatch** — nodes carrying another "
                f"provider's type: {', '.join(mismatched)}"
            )
        else:
            out.append(
                f"Provider ({provider}): every vendor type in the plan matches"
            )
    return out


def unhedged_claims(plan: DeploymentPlan) -> list[str]:
    """유보가 필요한데 근거 줄이 하나도 없는 요소.

    `needs_hedge`가 참인 노드는 **왜 그렇게 봤는지**가 노트에 있어야 한다 —
    없으면 그림에 근거 없는 상자가 하나 늘어난 것이다.
    """
    return [
        node.id
        for node in plan.nodes
        if needs_hedge(node.origin) and not node.notes
    ]
