"""클래스 메서드 시그니처와 시퀀스 호출/반환 라벨의 공통 문법."""
from __future__ import annotations

import re


_METHOD_CALL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\([^()\r\n]*\)$")
_METHOD_NAME = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)")
_RETURN_LABEL = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*"
    r"(?:\s*<\s*[A-Za-z_][A-Za-z0-9_.?]*"
    r"(?:\s*,\s*[A-Za-z_][A-Za-z0-9_.?]*)*\s*>)?"
    r"(?:\[\])?\??$"
)


def is_complete_method_call(label: str) -> bool:
    return bool(_METHOD_CALL.fullmatch(label.strip()))


def method_call_signature(raw: str) -> str:
    """가시성·반환 타입을 제외한 전체 호출 시그니처를 정규화한다."""
    raw = re.sub(r"^[+\-#~]\s*", "", raw.strip())
    match = re.match(r"([A-Za-z_][A-Za-z0-9_]*\([^()\r\n]*\))", raw)
    return re.sub(r"\s+", "", match.group(1)) if match else ""


def method_name(raw: str) -> str:
    raw = re.sub(r"^[+\-#~]\s*", "", raw.strip())
    match = _METHOD_NAME.match(raw)
    return match.group(1).lower() if match else ""


def method_return_type(raw: str) -> str | None:
    """`method(args): Type`에서 선언된 반환 타입을 가져온다."""
    raw = re.sub(r"^[+\-#~]\s*", "", raw.strip())
    match = re.match(r"[A-Za-z_][A-Za-z0-9_]*\([^()]*\)\s*:\s*(.+)$", raw)
    return match.group(1).strip() if match else None


def is_return_value_label(label: str) -> bool:
    """반환 라벨로 사용할 수 있는 UML 타입 표기인가."""
    return bool(_RETURN_LABEL.fullmatch(label.strip()))


def normalize_return_type(raw: str) -> str:
    return re.sub(r"\s+", "", raw).lower()
