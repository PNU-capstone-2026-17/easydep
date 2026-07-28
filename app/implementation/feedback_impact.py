"""Deterministic eligibility checks for implementation-feedback revisions."""
from __future__ import annotations

import re
from typing import Any


SCHEMA_VERSION = "implementation-feedback-eligibility/v1alpha1"

# These are explicit contract-edit requests. The implementation feedback loop
# never changes a design artifact or regenerates a contract, so it rejects them
# before any OpenHands task, approval request, or run is created.
_UNSUITABLE_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "CLASS_CONTRACT_CHANGE",
        "BCE/class-diagram class, field, or method contract change is outside implementation feedback.",
        (
            r"\b(?:bce|class\s+diagram|interface|field|attribute|method\s+signature|return\s+type)\b",
            r"(?:클래스\s*다이어그램|클래스명|인터페이스|필드(?:명|\s*타입)?|속성(?:명|\s*타입)?|메서드\s*(?:명|시그니처|반환\s*타입)|반환\s*타입|파라미터\s*타입)",
        ),
    ),
    (
        "OPENAPI_CONTRACT_CHANGE",
        "OpenAPI endpoint, request/response, or schema change is outside implementation feedback.",
        (
            r"\b(?:openapi|api\s+(?:spec|contract)|endpoint|request\s+body|response\s+(?:body|schema)|http\s+(?:method|status)|dto|schema)\b",
            r"(?:api\s*명세|엔드포인트|요청\s*(?:본문|바디|형식)|응답\s*(?:본문|바디|형식)|상태\s*코드|http\s*메서드|dto|스키마)",
            r"(?:추가|삭제|변경|수정|rename|remove|add)\s*(?:get|post|put|patch|delete)\b",
        ),
    ),
    (
        "SEQUENCE_FLOW_CHANGE",
        "Sequence diagram message/order change is outside implementation feedback.",
        (
            r"\b(?:sequence\s+diagram|call\s+order|message\s+flow)\b",
            r"(?:시퀀스\s*다이어그램|호출\s*순서|메시지\s*흐름|흐름을?\s*(?:변경|수정|추가|삭제))",
        ),
    ),
    (
        "DATA_MODEL_CHANGE",
        "ERD/database entity, table, column, or relation change is outside implementation feedback.",
        (
            r"\b(?:erd|database\s+schema|table|column|entity\s+relationship)\b",
            r"(?:erd|데이터베이스\s*스키마|테이블|컬럼|엔티티\s*(?:관계|추가|삭제|변경))",
        ),
    ),
)


def assess_feedback_eligibility(feedback: str, design: dict[str, Any] | None = None) -> dict[str, object]:
    """Decide eligibility without an LLM and without routing to another agent."""
    del design  # Reserved for future deterministic artifact-aware checks.
    text = " ".join(feedback.strip().split())
    matches: list[dict[str, str]] = []
    for code, message, patterns in _UNSUITABLE_RULES:
        for pattern in patterns:
            found = re.search(pattern, text, flags=re.IGNORECASE)
            if found:
                matches.append({"code": code, "match": found.group(0), "message": message})
                break
    eligible = not matches
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "ELIGIBLE" if eligible else "UNSUITABLE",
        "feedback": text,
        "matches": matches,
        "nextAction": (
            "Create a constrained implementation feedback revision and run all verification gates."
            if eligible
            else "Do not create or execute an implementation feedback revision for this request."
        ),
    }
