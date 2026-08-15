"""시퀀스 상호작용 모델(JSON)을 시퀀스 다이어그램 PlantUML로 변환한다.

클래스 다이어그램(class_diagram.plantuml)·ERD와 같은 결정론적 변환이다. 참가자 이름과
라벨은 LLM이 만든 자유 텍스트라 PlantUML 구조 문자를 품을 수 있으므로, 식별자는 단어
문자로만 남기고 라벨은 한 줄로 중화한다. 그래서 이 변환은 "구성에 의해" 항상 문법적으로
유효한 PlantUML을 낸다 — 별도의 문법 수리 루프가 필요 없다.

jar 실행·렌더는 common.plantuml이 맡고, 여기서는 시퀀스 다이어그램 고유의
"무엇을 그릴지"(참가자 종류·화살표 모양·조각 묶기)만 다룬다.
"""
from __future__ import annotations

import re
from typing import Any

#: 참가자 종류 → PlantUML 선언 키워드. BCE 스테레오타입을 그림으로 잇는다.
_PARTICIPANT_KEYWORD = {
    "actor": "actor",
    "boundary": "boundary",
    "control": "control",
    "entity": "entity",
    "database": "database",
}

#: 메시지 종류 → PlantUML 화살표.
_ARROW = {
    "sync": "->",
    "async": "->>",
    "return": "-->",
    "self": "->",
}

#: 조각 종류 → PlantUML 블록 키워드. 모르는 값은 그리지 않고 주 흐름으로 둔다.
_FRAGMENTS = {"alt": "alt", "loop": "loop", "opt": "opt"}


def sanitize_identifier(name: str) -> str:
    """참가자 별칭에 안전한 단어 문자만 남긴다."""
    if not name:
        return "Unknown"
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def sanitize_text(text: str) -> str:
    """라벨/조건 텍스트를 한 줄의 PlantUML-안전 토큰으로 만든다."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("‑", "-")
    # 줄바꿈은 이미 없앴고, 따옴표는 라벨을 깨뜨리므로 바꾼다.
    return text.replace('"', "'")


def generate_sequence_from_model(model: dict[str, Any]) -> str:
    """상호작용 모델을 시퀀스 다이어그램 PlantUML로 변환한다.

    참가자도 메시지도 없으면 빈 문자열을 반환한다(그릴 대상 없음).
    """
    if not model:
        return ""

    diagrams = model.get("Diagrams")
    if isinstance(diagrams, list):
        return "\n\n".join(
            rendered
            for diagram in diagrams
            if isinstance(diagram, dict)
            and (rendered := generate_sequence_from_model(diagram))
        )

    participants = model.get("Participants", [])
    messages = model.get("Messages", [])
    if not participants and not messages:
        return ""

    diagram_id = sanitize_identifier(str(model.get("use_case_id") or ""))
    start = f"@startuml {diagram_id}" if model.get("use_case_id") else "@startuml"
    lines = [
        start,
        "!theme plain",
        "skinparam sequenceMessageAlign center",
    ]
    title = " - ".join(
        value
        for value in (
            sanitize_text(str(model.get("use_case_id") or "")),
            sanitize_text(str(model.get("use_case_name") or "")),
        )
        if value
    )
    if title:
        lines.append(f"title {title}")
    lines.append("")

    # 선언된 참가자만 메시지에 쓸 수 있다. 모델이 어긋나 선언에 없는 이름이 나오면
    # 그 메시지는 버린다 — 미선언 참가자를 그리면 그림이 조용히 거짓말을 한다.
    declared: set[str] = set()
    for participant in participants:
        alias = sanitize_identifier(participant.get("alias") or participant.get("name", ""))
        if alias in declared:
            continue
        declared.add(alias)

        keyword = _PARTICIPANT_KEYWORD.get(
            str(participant.get("kind", "")).strip().lower(), "participant"
        )
        display = sanitize_text(participant.get("name", "")) or alias
        lines.append(f'{keyword} "{display}" as {alias}')

    lines.append("")

    # 현재 열린 fragment 경로. 새 모델은 메시지마다 바깥→안쪽 경로를 들고 있어
    # alt/else와 중첩을 잃지 않는다. 옛 저장본의 group/condition은 아래에서 깊이 1의
    # fragment로 읽어 재생 가능성을 유지한다.
    open_fragments: list[dict[str, str]] = []

    def fragment_path(message: dict[str, Any]) -> list[dict[str, str]]:
        path = message.get("fragments")
        if isinstance(path, list):
            return [item for item in path if isinstance(item, dict)]
        legacy_type = _FRAGMENTS.get(str(message.get("group", "")).strip().lower(), "")
        if not legacy_type:
            return []
        condition = sanitize_text(message.get("condition", ""))
        return [{
            "id": f"legacy:{legacy_type}:{condition}",
            "type": legacy_type,
            "branch": "main",
            "condition": condition,
        }]

    def close_to(depth: int) -> None:
        while len(open_fragments) > depth:
            lines.append("end")
            open_fragments.pop()

    def transition_fragments(wanted: list[dict[str, str]]) -> None:
        common = 0
        limit = min(len(open_fragments), len(wanted))
        while common < limit:
            current = open_fragments[common]
            candidate = wanted[common]
            if current.get("id") != candidate.get("id") or current.get("type") != candidate.get("type"):
                break
            if current.get("branch", "main") != candidate.get("branch", "main"):
                # 같은 alt의 다른 branch는 fragment를 닫지 않고 PlantUML else로 전환한다.
                close_to(common + 1)
                if current.get("type") == "alt" and candidate.get("branch") == "else":
                    lines.append(f"else {sanitize_text(candidate.get('condition', ''))}".rstrip())
                    open_fragments[common] = dict(candidate)
                    common += 1
                break
            common += 1

        close_to(common)
        for fragment in wanted[common:]:
            kind = _FRAGMENTS.get(str(fragment.get("type", "")).lower(), "")
            if not kind:
                continue
            condition = sanitize_text(fragment.get("condition", ""))
            lines.append(f"{kind} {condition}".rstrip())
            open_fragments.append(dict(fragment))

    for message in messages:
        source = sanitize_identifier(message.get("source", ""))
        target = sanitize_identifier(message.get("target", ""))
        if source not in declared or target not in declared:
            continue

        transition_fragments(fragment_path(message))

        message_type = str(message.get("type", "sync")).strip().lower()
        if message_type == "activate":
            lines.append(f"activate {target}")
            continue
        if message_type == "deactivate":
            lines.append(f"deactivate {target}")
            continue

        arrow = _ARROW.get(message_type, "->")
        label = sanitize_text(message.get("label", ""))
        line = f"{source} {arrow} {target}"
        if label:
            line += f" : {label}"
        lines.append(f"  {line}" if open_fragments else line)

    close_to(0)

    lines.append("")
    lines.append("@enduml")

    return "\n".join(lines).replace("\xa0", " ").replace("​", "")
