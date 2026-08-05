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

    participants = model.get("Participants", [])
    messages = model.get("Messages", [])
    if not participants and not messages:
        return ""

    lines = [
        "@startuml",
        "!theme plain",
        "skinparam sequenceMessageAlign center",
        "autonumber",
        "",
    ]

    # 선언된 참가자만 메시지에 쓸 수 있다. 모델이 어긋나 선언에 없는 이름이 나오면
    # 그 메시지는 버린다 — 미선언 참가자를 그리면 그림이 조용히 거짓말을 한다.
    declared: set[str] = set()
    for participant in participants:
        alias = sanitize_identifier(participant.get("name", ""))
        if alias in declared:
            continue
        declared.add(alias)

        keyword = _PARTICIPANT_KEYWORD.get(
            str(participant.get("kind", "")).strip().lower(), "participant"
        )
        display = sanitize_text(participant.get("name", "")) or alias
        lines.append(f'{keyword} "{display}" as {alias}')

    lines.append("")

    open_fragment: tuple[str, str] | None = None

    def close_fragment() -> None:
        nonlocal open_fragment
        if open_fragment is not None:
            lines.append("end")
            open_fragment = None

    for message in messages:
        source = sanitize_identifier(message.get("source", ""))
        target = sanitize_identifier(message.get("target", ""))
        if source not in declared or target not in declared:
            continue

        group = _FRAGMENTS.get(str(message.get("group", "")).strip().lower(), "")
        condition = sanitize_text(message.get("condition", ""))
        # 같은 (종류, 조건)이 이어지는 동안은 한 조각이다. 달라지면 닫고 새로 연다.
        wanted = (group, condition) if group else None
        if wanted != open_fragment:
            close_fragment()
            if wanted is not None:
                lines.append(f"{group} {condition}".rstrip())
                open_fragment = wanted

        arrow = _ARROW.get(str(message.get("type", "")).strip().lower(), "->")
        label = sanitize_text(message.get("label", ""))
        line = f"{source} {arrow} {target}"
        if label:
            line += f" : {label}"
        lines.append(f"  {line}" if open_fragment else line)

    close_fragment()

    lines.append("")
    lines.append("@enduml")

    return "\n".join(lines).replace("\xa0", " ").replace("​", "")
