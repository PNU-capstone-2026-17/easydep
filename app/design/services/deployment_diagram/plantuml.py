"""배포 토폴로지 모델(JSON)을 배포 다이어그램 PlantUML로 변환한다.

클래스 다이어그램·ERD·시퀀스와 같은 결정론적 변환이다. 식별자는 단어 문자로만 남기고
라벨은 한 줄로 중화하므로 "구성에 의해" 항상 문법적으로 유효한 PlantUML을 낸다.

중첩(parent)은 PlantUML 블록으로 그린다. 순환 참조나 없는 부모를 가리키는 노드는
최상위로 끌어올린다 — 잘못된 중첩으로 블록이 안 닫히면 그림 전체가 깨지기 때문이다.
"""
from __future__ import annotations

import re
from typing import Any

#: 노드 종류 → PlantUML 키워드.
_NODE_KEYWORD = {
    "device": "node",
    "executionenvironment": "node",
    "database": "database",
    "cloud": "cloud",
    "node": "node",
}


def sanitize_identifier(name: str) -> str:
    if not name:
        return "UnknownNode"
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("‑", "-")
    return text.replace('"', "'").replace("{", "(").replace("}", ")")


def _resolve_parents(nodes: list[dict[str, Any]]) -> dict[str, str]:
    """각 노드의 유효한 부모를 정한다(없는 부모·자기 자신·순환은 최상위로).

    순환을 끊지 않으면 렌더가 무한 재귀에 빠지거나 블록이 안 닫힌다.
    """
    aliases = {sanitize_identifier(n.get("name", "")) for n in nodes}
    parent: dict[str, str] = {}
    for node in nodes:
        alias = sanitize_identifier(node.get("name", ""))
        raw_parent = sanitize_identifier(node.get("parent", "")) if node.get("parent") else ""
        parent[alias] = raw_parent if raw_parent in aliases and raw_parent != alias else ""

    for alias in list(parent):
        seen = {alias}
        cursor = parent[alias]
        while cursor:
            if cursor in seen:  # 순환 — 이 노드를 최상위로 끌어올린다.
                parent[alias] = ""
                break
            seen.add(cursor)
            cursor = parent.get(cursor, "")
    return parent


def generate_deployment_from_model(model: dict[str, Any]) -> str:
    """배포 모델을 배포 다이어그램 PlantUML로 변환한다.

    노드도 연결도 없으면 빈 문자열을 반환한다(그릴 대상 없음).
    """
    if not model:
        return ""

    nodes = model.get("Nodes", [])
    artifacts = model.get("Artifacts", [])
    connections = model.get("Connections", [])
    if not nodes and not connections:
        return ""

    parent_of = _resolve_parents(nodes)
    by_alias: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for node in nodes:
        alias = sanitize_identifier(node.get("name", ""))
        if alias in by_alias:
            continue
        by_alias[alias] = node
        order.append(alias)

    children: dict[str, list[str]] = {alias: [] for alias in order}
    roots: list[str] = []
    for alias in order:
        parent = parent_of.get(alias, "")
        if parent and parent in children:
            children[parent].append(alias)
        else:
            roots.append(alias)

    # 아티팩트를 배포 노드별로 모은다. 없는 노드를 가리키면 버린다 — 어디에 그릴지
    # 모르는 아티팩트를 임의의 자리에 두면 그림이 거짓말을 한다.
    artifacts_on: dict[str, list[dict[str, Any]]] = {alias: [] for alias in order}
    for artifact in artifacts:
        host = sanitize_identifier(artifact.get("deployed_on", ""))
        if host in artifacts_on:
            artifacts_on[host].append(artifact)

    lines = ["@startuml", "!theme plain", "skinparam linetype ortho", ""]

    def render(alias: str, depth: int) -> None:
        node = by_alias[alias]
        pad = "  " * depth
        keyword = _NODE_KEYWORD.get(str(node.get("kind", "")).strip().lower(), "node")
        display = sanitize_text(node.get("name", "")) or alias
        stereotype = sanitize_text(node.get("stereotype", ""))
        header = f'{pad}{keyword} "{display}"'
        if stereotype:
            header += f" <<{stereotype}>>"
        header += f" as {alias}"

        # 담을 것이 없으면 블록을 열지 않는다 — 빈 {}는 그림만 어지럽힌다.
        if children[alias] or artifacts_on[alias]:
            lines.append(f"{header} {{")
            for artifact in artifacts_on[alias]:
                art_alias = sanitize_identifier(artifact.get("name", ""))
                art_name = sanitize_text(artifact.get("name", "")) or art_alias
                lines.append(f'{pad}  artifact "{art_name}" as {art_alias}')
            for child in children[alias]:
                render(child, depth + 1)
            lines.append(f"{pad}}}")
        else:
            lines.append(header)

        description = sanitize_text(node.get("description", ""))
        if description:
            lines.append(f"{pad}note right of {alias} : {description}")

    for root in roots:
        render(root, 0)
        lines.append("")

    for connection in connections:
        source = sanitize_identifier(connection.get("source", ""))
        target = sanitize_identifier(connection.get("target", ""))
        if source not in by_alias or target not in by_alias:
            continue
        label = sanitize_text(connection.get("protocol", "")) or sanitize_text(
            connection.get("description", "")
        )
        line = f"{source} --> {target}"
        if label:
            line += f" : {label}"
        lines.append(line)

    lines.append("")
    lines.append("@enduml")

    return "\n".join(lines).replace("\xa0", " ").replace("​", "")
