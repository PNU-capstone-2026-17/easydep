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

from app.core.cloudkb.appkb.plan import DeploymentPlan, PlanNode

_STEREOTYPE = {
    "designer": "specified by the designer",
    "inferred": "inferred",
}

#: 역할별 PlantUML 요소. `rectangle`로 통일하지 않는 이유 — 모양이 다르면
#: 사람이 한눈에 컴퓨트와 관리형 서비스를 가른다.
#: **설계 개념별 도형.** `role`보다 먼저 본다.
#:
#: 한동안 `role == "managed"`면 무조건 `database`였다 — **SQS 큐도 Secrets Manager도
#: 원통으로 그려졌다**(2026-07-28 지적). 아키타입을 계획이 이미 들고 있는데
#: (`app::messageQueue`·`app::secretStore`) 그림이 안 쓴 것이다. 데이터를 더 모을
#: 필요 없이 **이미 아는 것을 쓰기만** 하면 되는 자리였다.
#:
#: 전부 PlantUML이 배포 다이어그램에서 받는 키워드다(`queue`·`storage`·`database`·
#: `folder`·`component`·`node`). 없는 개념은 매핑하지 않는다 — 억지로 붙이면
#: 도형이 뜻을 잃는다.
_ARCHETYPE_SHAPE = {
    "app::relationalDatabase": "database",
    "app::nosqlDatabase": "database",
    "app::keyValueCache": "database",
    "app::searchIndex": "database",
    "app::messageQueue": "queue",
    "app::eventStream": "queue",
    "app::objectStorage": "storage",
    # 시크릿 저장소는 **데이터 저장소가 아니다** — 값을 조회하는 곳이지 앱의 데이터가
    # 사는 곳이 아니다. 원통으로 그리면 DB와 같은 것으로 읽힌다.
    "app::secretStore": "folder",
    "app::serverlessFunction": "component",
    "app::containerService": "node",
    "app::apiGateway": "hexagon",
    "app::cdn": "cloud",
    "app::dnsZone": "cloud",
}

#: 아키타입이 없을 때의 역할별 도형.
_SHAPE = {
    # 컴퓨트는 **아티팩트**다 — 실행 환경(node)은 `host`가 따로 감싼다.
    "compute": "artifact",
    "managed": "database",
    # 네트워크 경계는 실행 환경이다.
    "shared": "rectangle",
    # 진입점(로드밸런서) — 트래픽이 갈라지는 지점이라 모양을 달리한다.
    "ingress": "hexagon",
    "external": "cloud",
    "actor": "actor",
}

#: **노드가 아니라 배포되는 산출물**인 공유 리소스. 키·정책은 실행 환경이 아니다.
#:
#: SSH 키를 `node`로 그리면 "여기서 무언가 돈다"는 뜻이 된다 — 자격증명은 그런 것이
#: 아니다. 보안 그룹도 정책이지 실행 환경이 아니다. 둘 다 **만들어지는 리소스**라
#: 그림에서 빼지는 않고, 아티팩트로 그린다.
_ARTIFACT_IDS = {"sshkey", "securitygroup"}


def _quote(text: str) -> str:
    """PlantUML 라벨 안의 큰따옴표를 없앤다 — 넣으면 구문이 깨진다."""
    return text.replace('"', "'")


#: 실행 환경 상자의 별칭 접미. 되파싱이 이걸 떼어 계획 id로 되돌린다.
_HOST_SUFFIX = "@host"


def _shape_of(node: PlanNode) -> str:
    """이 노드를 무슨 도형으로 그리나 — **아키타입이 역할보다 먼저다.**"""
    if node.id in _ARTIFACT_IDS:
        return "artifact"
    if node.archetype in _ARCHETYPE_SHAPE:
        return _ARCHETYPE_SHAPE[node.archetype]
    return _SHAPE.get(node.role, "rectangle")


def _node_line(node: PlanNode) -> str:
    shape = _shape_of(node)
    label = _quote(node.label)
    if node.type_id:
        label += f"\\n{_quote(node.type_id)}"
    elif node.candidates:
        label += f"\\n{len(node.candidates)} candidates"
    stereotype = _STEREOTYPE.get(node.origin, "")
    tail = f" <<{stereotype}>>" if stereotype else ""
    # **별칭을 따옴표로 감싼다.** 계약이 컴포넌트 id에 하이픈을 허용하는데
    # PlantUML에서 `-`는 화살표 문자다 — 맨 별칭으로 쓰면 `order-api`가 조용히
    # 쪼개진다(되파싱 검증이 실제로 잡았다). 따옴표 별칭은 PlantUML 표준이다.
    return f'{shape} "{label}" as "{node.id}"{tail}'


def _node_block(node: PlanNode, pad: str) -> list[str]:
    """노드 하나를 그리는 줄들. **실행 환경이 있으면 감싼다.**

    UML 배포 다이어그램의 뼈대가 `Node ← «deploy» ← Artifact`다. 컴포넌트는 노드가
    아니라 노드 위에 배포되는 아티팩트이므로, 실행 환경(`host`)이 있으면 그것이
    바깥 상자가 되고 컴포넌트가 안에 들어간다.

        node "VM · t3a.medium ×?" as "order-api@host" {
          artifact "OrderService" as "order-api" <<inferred>>
        }

    **별칭은 아티팩트가 계획 id를 갖는다.** 왕복 검증이 계획 노드와 그림 별칭을
    1:1로 대조하므로(`verify_diagram`), 바깥 상자는 접미를 달고 되파싱에서 벗겨진다.
    """
    inner = _node_line(node)
    if not node.host:
        return [pad + inner]
    # 대수는 **실행 환경**에 붙는다 — 몇 대인가는 컴포넌트가 아니라 그것이 도는
    # 노드의 성질이다. 여기 없으면 그림이 1대처럼 읽힌다(노트는 그림과 안 다닌다).
    count = node.replicas if node.replicas is not None else "?"
    host_label = f"{_quote(node.host)}\\n×{count}"
    return [
        f'{pad}node "{host_label}" as "{node.id}{_HOST_SUFFIX}" {{',
        f"{pad}  {inner}",
        pad + "}",
    ]


#: 다른 노드를 **담는** 공유 인프라. 바깥부터 안쪽 순서다.
#:
#: 컴퓨트마다 공유 자원으로 선을 그으면 컴포넌트 5개짜리 앱에 선이 20개 늘어
#: 그림이 못 쓰게 된다(실측: 2개에 이미 15개). 배포 다이어그램은 그 관계를
#: **중첩**으로 표현하는 것이 정석이고, tumblebug이 "연결당 공유"라 말한 것과도 맞는다.
#:
#: **바깥→안 순서만 여기서 정한다.** 무엇이 무엇에 담기는지는 이제 계획이 들고
#: 온다(`PlanNode.placement`) — 구성기가 그래프 축에 물어 채운 값이다. 이 상수가
#: 남은 이유는 렌더링 순서(vnet을 먼저 열고 subnet을 그 안에)뿐이고, 담김 여부의
#: 근거가 아니다.
_CONTAINERS = ("vnet", "subnet")


def render(plan: DeploymentPlan) -> str:
    """계획 하나를 PlantUML 텍스트로."""
    lines = [
        "@startuml",
        f"title {_quote(plan.name)} — deployment plan",
        "skinparam shadowing false",
        "",
    ]
    by_id = {n.id: n for n in plan.nodes}
    nesting = [cid for cid in _CONTAINERS if cid in by_id]
    # **담기는 것은 계획이 말한다.** 예전에는 `role == "compute"`로 정했는데, 그건
    # "컴퓨트는 서브넷 안"이라는 우리 가정을 렌더러에 박아 둔 것이었다. 지금은
    # 구성기가 `placement`에 근거와 함께 담고 여기서는 그 값을 읽기만 한다.
    innermost = nesting[-1] if nesting else ""
    inside = {n.id for n in plan.nodes if n.placement == innermost and innermost}
    # 배치를 **모르는** 노드 — 밖에 그리되 그 사실을 범례가 말한다. 부재를 "밖"으로
    # 승격하지 않는다(`PlanNode.placement`).
    unplaced = [
        n for n in plan.nodes
        if n.placement == "unknown" and n.id not in nesting and n.role != "actor"
    ]

    depth = 0
    for cid in nesting:
        lines.append("  " * depth + _node_line(by_id[cid]) + " {")
        depth += 1
    for node in plan.nodes:
        if node.id in nesting:
            continue
        pad = "  " * depth if node.id in inside else ""
        if node.id not in inside and depth:
            continue  # 컨테이너 밖의 노드는 닫은 뒤에 그린다
        lines.extend(_node_block(node, pad))
    for _ in range(depth):
        depth -= 1
        lines.append("  " * depth + "}")
    if depth == 0 and nesting:
        for node in plan.nodes:
            if node.id in nesting or node.id in inside:
                continue
            lines.extend(_node_block(node, ""))
    if plan.nodes and plan.edges:
        lines.append("")
    for edge in plan.edges:
        arrow = "-->" if edge.async_ else "->"
        label = f" : {_quote(edge.label)}" if edge.label else ""
        lines.append(f'"{edge.from_id}" {arrow} "{edge.to_id}"{label}')

    hedged = plan.hedged_count
    lines.append("")
    lines.append("legend right")
    lines.append(
        "  Evidence: design artifact / specified by the designer / knowledge base"
        " / we inferred"
    )
    if hedged:
        # **그림 안에 유보를 남긴다.** 범례만으로는 부족하다 — 그림은 잘려 돌아다닌다.
        lines.append(
            f"  The {hedged} items marked <<inferred>>·<<specified by the designer>>"
            " are not verified facts"
        )
    undecided = [n for n in plan.nodes if n.role == "compute" and n.replicas is None]
    if undecided:
        lines.append(
            f"  ×? on {len(undecided)} compute node(s): **how many instances is not "
            "decided** — this plan shows one box per component, not one instance"
        )
    if unplaced:
        # **밖에 그린 것이 "밖에 있다"로 읽히면 안 된다.** 관리형 서비스가 어느
        # 네트워크에 놓이는지는 이 저장소가 모른다 — `contained_in` 축은 네트워크
        # 배치가 아니라 이름 계층·프로젝트 소속이고, AWS는 그마저 비어 있다.
        # 그리지 않는 것보다 나쁜 것은 모른다는 말 없이 밖에 그리는 것이다.
        names = ", ".join(sorted(n.id for n in unplaced)[:6])
        more = f" and {len(unplaced) - 6} more" if len(unplaced) > 6 else ""
        lines.append(
            f"  Drawn outside the network because **their placement is not known**"
            f" ({len(unplaced)}): {names}{more} — not a claim that they sit outside"
        )
    if plan.unresolved:
        lines.append(
            f"  {len(plan.unresolved)} items we could not answer — see the plan body"
        )
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

    # 중첩이 들어오면서 줄 앞에 들여쓰기가, 줄 끝에 `{`가 붙는다 — 둘 다 허용한다.
    found = re.findall(r'^\s*\w+\s+"[^"]*"\s+as\s+"([^"]+)"', uml, re.MULTILINE)
    # 실행 환경 상자는 아티팩트와 **같은 계획 노드**다 — 접미를 벗겨 하나로 센다.
    # 안 벗기면 왕복 검증이 "계획에 없는 상자"라고 잡는다(그건 지어낸 노드를 잡는
    # 장치라 껍데기 하나로 무뎌지면 안 된다).
    aliases = {a[: -len(_HOST_SUFFIX)] if a.endswith(_HOST_SUFFIX) else a for a in found}
    # `-{1,2}>`다. `-->?`로 쓰면 `--`가 필수라 **동기 화살표 `->`가 통째로 빠진다**
    # (되파싱 검증이 잡았다 — 5개 선 중 4개가 조용히 사라졌다).
    edges = set(re.findall(r'^"([^"]+)"\s+-{1,2}>\s+"([^"]+)"', uml, re.MULTILINE))
    return aliases, edges
