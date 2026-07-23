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
            problems.append(f"[계획] 노드 id 중복: {node_id}")
        seen.add(node_id)
        if not _ID.match(node_id):
            problems.append(f"[계획] 노드 id가 리소스 이름 규칙과 어긋난다: {node_id}")

    for edge in plan.edges:
        for end in (edge.from_id, edge.to_id):
            if end not in seen:
                problems.append(f"[계획] 선이 없는 노드를 가리킨다: {end}")

    for node in plan.nodes:
        if node.role == "managed" and not node.type_id and not node.candidates:
            # **"관리형 서비스가 필요하다"까지만 알고 무엇인지 모르는 상태**를
            # 조용히 두면 그림에 빈 상자가 남는다. 미결로 올려야 한다.
            if not any(node.id in item for item in plan.unresolved):
                problems.append(
                    f"[계획] {node.id}: 관리형인데 타입도 후보도 없고 미결에도 없다"
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
        problems.append(f"[그림] 계획에 있는데 그림에 없는 노드: {sorted(missing)}")
    if extra:
        # 지어낸 노드다 — 답변에서 없는 값을 만드는 것과 같은 실패의 그림판.
        problems.append(f"[그림] 계획에 없는데 그림에 있는 노드: {sorted(extra)}")

    plan_edges = {(e.from_id, e.to_id) for e in plan.edges}
    if plan_edges - edges:
        problems.append(f"[그림] 그림에 빠진 선: {sorted(plan_edges - edges)}")
    if edges - plan_edges:
        problems.append(f"[그림] 계획에 없는 선: {sorted(edges - plan_edges)}")

    # 유보가 그림에 살아 있는가 — 그림은 잘려 돌아다닌다.
    if plan.hedged_count and "추론" not in uml and "설계자 지정" not in uml:
        problems.append("[그림] 추론·설계자 지정이 있는데 그림에 표시가 없다")
    return problems


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
