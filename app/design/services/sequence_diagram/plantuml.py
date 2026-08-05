"""추출된 시퀀스 다이어그램 요소(JSON)를 PlantUML 코드로 변환한다.

클래스 다이어그램과 마찬가지로 텍스트(라벨·메서드·조건)는 LLM이 작성한 자유 텍스트이므로
PlantUML 구조 문자를 품을 수 있다. sanitize_text가 이를 중화하여 변환 결과는
구성에 의해 항상 문법적으로 유효한 PlantUML이 된다.
"""
from __future__ import annotations

import re
from typing import Any


_PUML_UNSAFE = str.maketrans(
    {
        "{": "(",
        "}": ")",
        '"': "'",
        "\n": " ",
        "\r": " ",
    }
)


def sanitize_alias(alias: str) -> str:
    if not alias:
        return "Obj"
    return re.sub(r"[^a-zA-Z0-9_]", "_", alias)


def sanitize_text(text: str) -> str:
    """메서드/라벨/조건 텍스트를 한 줄의 PlantUML-안전 토큰으로 만든다."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("\u2011", "-")
    return text.translate(_PUML_UNSAFE)


def generate_plantuml_from_sequence_json(json_data: dict[str, Any]) -> str:
    if not json_data:
        return ""

    participants = json_data.get("participants", [])
    sequence = json_data.get("sequence", [])

    if not participants and not sequence:
        return ""

    lines = [
        "@startuml",
        "skinparam sequenceMessageAlign center",
        "skinparam maxMessageSize 150",
        "",
    ]

    # 1. Participant Placement
    for p in participants:
        p_type = (p.get("type") or "participant").lower().strip()
        label = sanitize_text(p.get("label", ""))
        alias = sanitize_alias(p.get("alias", label))

        if not label:
            label = alias

        if p_type == "actor":
            lines.append(f'actor "{label}" as {alias}')
        elif p_type == "database":
            lines.append(f'database "{label}" as {alias} <<Database>>')
        elif p_type in ("boundary", "control", "entity"):
            lines.append(f'participant "{label}" as {alias} <<{p_type.capitalize()}>>')
        else:
            lines.append(f'participant "{label}" as {alias}')

    lines.append("")

    # 2. Sequence Steps Assembly
    for seq in sequence:
        s_type = seq.get("type", "").lower().strip()
        src = sanitize_alias(seq.get("source", ""))
        tgt = sanitize_alias(seq.get("target", ""))
        text = sanitize_text(seq.get("text", ""))

        if s_type == "message":
            if src and tgt:
                lines.append(f"{src} -> {tgt} : {text}")
        elif s_type == "self_message":
            if src:
                lines.append(f"{src} -> {src} : {text}")
        elif s_type == "return_message":
            if src and tgt:
                lines.append(f"{src} --> {tgt} : {text}")
        elif s_type == "activate":
            if tgt:
                lines.append(f"activate {tgt}")
        elif s_type == "deactivate":
            if tgt:
                lines.append(f"deactivate {tgt}")
        elif s_type == "fragment_start":
            frag_type = sanitize_text(seq.get("fragment_type", "alt")).lower() or "alt"
            condition = sanitize_text(seq.get("condition", ""))
            cond_str = f" {condition}" if condition else ""
            lines.append(f"{frag_type}{cond_str}")
        elif s_type == "fragment_else":
            condition = sanitize_text(seq.get("condition", ""))
            cond_str = f" {condition}" if condition else ""
            lines.append(f"else{cond_str}")
        elif s_type == "fragment_end":
            lines.append("end")

    lines.append("@enduml")

    final_puml = "\n".join(lines)
    return final_puml.replace("\xa0", " ").replace("\u200b", "")
