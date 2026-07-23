"""배포 계획 → PlantUML. **되파싱 가능한 형태로 낸다.**

파이프라인이 PlantUML을 쓰므로 출력도 PlantUML이다(계획에서 Mermaid로 잡았던 것을
상류에 맞춰 바꿨다).

## 되파싱이 요구사항이다

이 저장소는 답변을 기계로 검사해 왔다(`claim_check`). 다이어그램도 답변이므로 같은
대접을 받아야 하는데, 그러려면 **우리가 낸 그림을 우리가 다시 읽을 수 있어야** 한다.
그래서 노드 별칭을 `id`와 같게 두고 자유 서식을 쓰지 않는다 — 예쁜 그림보다
**검증 가능한 그림**이 먼저다(`appkb/verify.py`가 이 형식을 읽는다).

## 유보를 그림에 새긴다

`inferred`·`designer` 노드에는 `<<추론>>`·`<<설계자 지정>>` 스테레오타입이 붙는다.
그림만 떼어 봐도 어디가 우리 추론인지 보여야 한다 — 범례에 적어 두는 것으로는
부족하다. 그림은 잘려서 돌아다닌다.
"""

from __future__ import annotations

from appkb.plan import DeploymentPlan, PlanNode, needs_hedge

_STEREOTYPE = {
    "designer": "설계자 지정",
    "inferred": "추론",
}

#: 역할별 PlantUML 요소. `rectangle`로 통일하지 않는 이유 — 모양이 다르면
#: 사람이 한눈에 컴퓨트와 관리형 서비스를 가른다.
_SHAPE = {
    "compute": "node",
    "managed": "database",
    "external": "cloud",
    "actor": "actor",
}


def _quote(text: str) -> str:
    """PlantUML 라벨 안의 큰따옴표를 없앤다 — 넣으면 구문이 깨진다."""
    return text.replace('"', "'")


def _node_line(node: PlanNode) -> str:
    shape = _SHAPE.get(node.role, "rectangle")
    label = _quote(node.label)
    if node.type_id:
        label += f"\\n{_quote(node.type_id)}"
    elif node.candidates:
        label += f"\\n후보 {len(node.candidates)}개"
    stereotype = _STEREOTYPE.get(node.origin, "")
    tail = f" <<{stereotype}>>" if stereotype else ""
    # **별칭을 따옴표로 감싼다.** 계약이 컴포넌트 id에 하이픈을 허용하는데
    # PlantUML에서 `-`는 화살표 문자다 — 맨 별칭으로 쓰면 `order-api`가 조용히
    # 쪼개진다(되파싱 검증이 실제로 잡았다). 따옴표 별칭은 PlantUML 표준이다.
    return f'{shape} "{label}" as "{node.id}"{tail}'


def render(plan: DeploymentPlan) -> str:
    """계획 하나를 PlantUML 텍스트로."""
    lines = [
        "@startuml",
        f"title {_quote(plan.name)} — 배포 구성",
        "skinparam shadowing false",
        "",
    ]
    for node in plan.nodes:
        lines.append(_node_line(node))
    if plan.nodes and plan.edges:
        lines.append("")
    for edge in plan.edges:
        arrow = "-->" if edge.async_ else "->"
        label = f" : {_quote(edge.label)}" if edge.label else ""
        lines.append(f'"{edge.from_id}" {arrow} "{edge.to_id}"{label}')

    hedged = plan.hedged_count
    lines.append("")
    lines.append("legend right")
    lines.append("  근거: 설계 산출물 / 설계자 지정 / 지식베이스 / 우리 추론")
    if hedged:
        # **그림 안에 유보를 남긴다.** 범례만으로는 부족하다 — 그림은 잘려 돌아다닌다.
        lines.append(
            f"  <<추론>>·<<설계자 지정>> 표시 {hedged}건은 검증된 사실이 아닙니다"
        )
    if plan.unresolved:
        lines.append(f"  답하지 못한 것 {len(plan.unresolved)}건 — 계획 본문 참조")
    lines.append("endlegend")
    lines.append("@enduml")
    return "\n".join(lines)


def parse_back(uml: str) -> tuple[set[str], set[tuple[str, str]]]:
    """우리가 낸 그림에서 **노드 별칭과 선**을 다시 읽는다.

    검증이 쓴다. 임의의 PlantUML을 읽는 파서가 아니다 — `render`가 낸 형식만
    읽으며, 그게 요점이다(우리 형식을 우리가 읽는 것은 `flipped`·`priced_as_free`와
    같은 계보다).
    """
    import re

    aliases = set(re.findall(r'^\w+\s+"[^"]*"\s+as\s+"([^"]+)"', uml, re.M))
    # `-{1,2}>`다. `-->?`로 쓰면 `--`가 필수라 **동기 화살표 `->`가 통째로 빠진다**
    # (되파싱 검증이 잡았다 — 5개 선 중 4개가 조용히 사라졌다).
    edges = set(re.findall(r'^"([^"]+)"\s+-{1,2}>\s+"([^"]+)"', uml, re.M))
    return aliases, edges
