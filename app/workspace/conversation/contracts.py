"""Workspace 대화형 에이전트가 반환할 수 있는 최소 결과 계약."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConversationIntent(StrEnum):
    """대화 해석기가 제안할 수 있는 제한된 실행 의도."""

    ADVANCE = "advance"
    ANSWER = "answer"
    REVISE = "revise"
    DELEGATE_REPAIR = "delegate_repair"


class Reply(BaseModel):
    """상태를 바꾸지 않고 사용자에게 돌려주는 답변."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=8_000)]


class Clarification(BaseModel):
    """서로 다른 변경을 뜻하는 유한한 후보를 사용자에게 되묻는 결과."""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(min_length=1, max_length=2_000)]
    candidates: Annotated[list[str], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_candidates(self) -> Clarification:
        normalized = [candidate.strip() for candidate in self.candidates]
        if any(not candidate for candidate in normalized):
            raise ValueError("clarification candidates must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("clarification candidates must be unique")
        self.candidates = normalized
        return self


class CommandIntent(BaseModel):
    """결정론적 router가 검증한 뒤에만 실행할 사용자 의도."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    intent: ConversationIntent
    targets: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)
    instruction: Annotated[str, Field(max_length=8_000)] = ""

    @model_validator(mode="after")
    def validate_intent_payload(self) -> CommandIntent:
        self.targets = [target.strip() for target in self.targets]
        self.instruction = self.instruction.strip()
        if any(not target for target in self.targets):
            raise ValueError("command targets must not be empty")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("command targets must be unique")
        if self.intent == ConversationIntent.REVISE.value:
            if not self.targets:
                raise ValueError("revise intent requires at least one target")
            if not self.instruction:
                raise ValueError("revise intent requires an instruction")
        if self.intent == ConversationIntent.ANSWER.value and not self.instruction:
            raise ValueError("answer intent requires an instruction")
        return self


ConversationOutcome = Reply | Clarification | CommandIntent


__all__ = [
    "Clarification",
    "CommandIntent",
    "ConversationIntent",
    "ConversationOutcome",
    "Reply",
]
