"""배포 다이어그램 — `design_view`를 PlantUML로.

설계 에이전트의 산출물 형식이 PlantUML이므로, 우리 사영도 거기에 맞춰 낸다.
그림 파일이 아니라 **텍스트**라서 좋은 점이 있다: 다이어그램이 diff에 남고,
근거(왜 이 노드가 여기 있나)를 note로 함께 실을 수 있다.

인코딩(스테레오타입 + 선 종류 이중):
- `<<선택한 것>>` 앵커 · `<<필수>>` 실선 · `<<선택>>` 파선 ·
  `<<자동>>` 점선(서버가 만든다 — 우리가 만들면 중복)
- 간선은 `A --> B : requires`이고, 방향의 뜻을 다이어그램 머리에 적는다

색은 스테레오타입에 건다(색만으로 구분하지 않는다 — 스테레오타입 문자열이
같은 정보를 나른다).
"""

from __future__ import annotations

import re

from .infra_intent import InfraIntent
from .views import design_view

#: 자원 → PlantUML 요소. **우리 구성**(가독 목적) — 판정에 영향이 없다.
_SHAPE: dict[str, str] = {
    "disk": "database",
    "network": "cloud",
    "subnet": "cloud",
}
_DEFAULT_SHAPE = "node"

_STEREOTYPE = {
    "anchor": "선택한 것",
    "required": "필수",
    "attachable": "선택",
}

_HEADER = """@startuml {slug}
' 자동 생성 — app.core.cloudkb.depkb.plantuml. 손으로 고치지 말 것.
' 판정 근거: depkb/claims.json (3사 컨트롤 플레인 실측)
!theme plain
skinparam shadowing false
skinparam defaultFontName sans-serif
skinparam node {{
  BorderColor #8a8a86
  BackgroundColor #fcfcfb
}}
skinparam node<<선택한 것>> {{ BorderColor #2a78d6 BorderThickness 3 }}
skinparam node<<필수>> {{ BorderColor #1baf7a BorderThickness 2 }}
skinparam node<<선택>> {{ BorderStyle dashed }}
skinparam node<<자동>> {{ BorderStyle dotted BackgroundColor #f4f4f2 }}
skinparam database<<필수>> {{ BorderColor #1baf7a BorderThickness 2 }}
skinparam database<<선택>> {{ BorderStyle dashed }}
skinparam cloud<<필수>> {{ BorderColor #1baf7a }}
skinparam cloud<<선택>> {{ BorderStyle dashed }}
skinparam cloud<<자동>> {{ BorderStyle dotted }}

title {title}
caption 화살표 A --> B 는 "A가 B를 요구한다" — 포함 관계가 아니다
"""


def _alias(resource_id: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", resource_id)


def _stereotype(node: dict) -> str:
    if node["autoFilledNotice"]:
        return "자동"
    return _STEREOTYPE[node["role"]]


def deployment_puml(intent: InfraIntent, title: str | None = None,
                    slug: str | None = None) -> str:
    """인프라 의도 하나를 PlantUML 배포 다이어그램으로."""
    view = design_view(intent)
    nodes = view["nodes"]
    slug = _alias(slug or f"{'-'.join(intent.anchors)}-{intent.csp}")
    head = _HEADER.format(
        slug=slug,
        title=title or f"{', '.join(intent.anchors)} — {intent.csp}")

    body: list[str] = []
    by_group: dict[str, list[dict]] = {}
    for n in nodes:
        by_group.setdefault(n["group"], []).append(n)
    for group in sorted(by_group):
        body.append(f'package "{group}" {{')
        for n in sorted(by_group[group], key=lambda x: x["id"]):
            shape = _SHAPE.get(n["id"], _DEFAULT_SHAPE)
            body.append(f'  {shape} "{n["id"]}" as {_alias(n["id"])} '
                        f'<<{_stereotype(n)}>>')
        body.append("}")

    for e in view["edges"]:
        body.append(f'{_alias(e["from"])} --> {_alias(e["to"])} : requires')

    # 근거·고지는 note로 — 그림이 "왜"를 함께 나른다.
    for n in nodes:
        if n["autoFilledNotice"]:
            body.append(f'note right of {_alias(n["id"])}\n  '
                        f'{n["autoFilledNotice"]}\nend note')
        elif n["because"]:
            body.append(f'note right of {_alias(n["id"])}\n  왜: '
                        f'{", ".join(n["because"])}\nend note')

    asks = [d["question"] for d in view["openDecisions"]]
    rules = view["constraints"]
    if asks or rules:
        legend = ["legend bottom"]
        if asks:
            legend.append("  **물어볼 것**")
            legend.extend(f"  - {a}" for a in asks)
        if rules:
            legend.append("  **지켜야 할 규칙**")
            legend.extend(f"  - {r}" for r in rules)
        legend.append("endlegend")
        body.extend(legend)

    return head + "\n".join(body) + "\n@enduml\n"


def deployment_puml_set(intents: dict[str, InfraIntent], title: str) -> str:
    """CSP별 의도를 한 파일에 — PlantUML은 한 파일에 여러 다이어그램을 담는다.

    나란히 두는 것이 요점이다: 같은 요구인데 노드 수도, 누가 만드는지도 다르다.
    """
    parts = [deployment_puml(intent, title=f"{title} — {csp}",
                             slug=f"{title}-{csp}")
             for csp, intent in intents.items()]
    return "\n".join(parts)
