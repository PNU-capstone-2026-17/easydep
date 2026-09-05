"""Workspace 대화형 에이전트가 반환할 수 있는 최소 결과 계약."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConversationIntent(StrEnum):
    """대화 해석기가 제안할 수 있는 제한된 실행 의도."""

    ADVANCE = "advance"
    ANSWER = "answer"
    REVISE = "revise"
    DELEGATE_REPAIR = "delegate_repair"
    BRANCH = "branch"
    RERUN = "rerun"
    CONFIRM_REVISION = "confirm_revision"
    DISMISS_REVISION = "dismiss_revision"


class RevisionTarget(BaseModel):
    """A catalog-owned, version-pinned revision target.

    Caller-provided owner, label, and version are never trusted. This public
    boundary contains only values rebuilt by ``ProjectTools`` from its catalog.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: Annotated[str, Field(min_length=3)]
    kind: Annotated[str, Field(min_length=1)]
    element_id: Annotated[str, Field(min_length=1)]
    owner: Literal["requirements", "design", "implementation", "testing"]
    artifact_type: Annotated[str, Field(min_length=1)]
    artifact_version_id: int | None = None
    display_label: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def normalize_identity(self) -> RevisionTarget:
        normalized = {
            "ref": self.ref.strip(),
            "kind": self.kind.strip(),
            "element_id": self.element_id.strip(),
            "artifact_type": self.artifact_type.strip(),
            "display_label": self.display_label.strip(),
        }
        if any(not value for value in normalized.values()):
            raise ValueError("revision target fields must not be blank")
        for field, value in normalized.items():
            object.__setattr__(self, field, value)
        return self


class RevisionInterpretation(BaseModel):
    """Small revision intent proposed by the model and checked by the planner."""

    model_config = ConfigDict(extra="forbid")

    targets: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)
    semantic_scope: Literal[
        "presentation",
        "contract",
        "behavior",
        "implementation",
        "test_expectation",
        "unknown",
    ]
    requested_effect: Annotated[str, Field(max_length=8_000)] = ""
    clarification: Annotated[str, Field(max_length=2_000)] = ""
    change_type: Literal["modify", "add", "rename", "remove", "unknown"] = "modify"

    @model_validator(mode="after")
    def normalize_interpretation(self) -> RevisionInterpretation:
        self.targets = [target.strip() for target in self.targets]
        self.requested_effect = self.requested_effect.strip()
        self.clarification = self.clarification.strip()
        if any(not target for target in self.targets):
            raise ValueError("revision targets must not be blank")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("revision targets must be unique")
        return self


class RevisionPlan(BaseModel):
    """Deterministic plan that is never executed before required approval."""

    model_config = ConfigDict(extra="forbid")

    plan_digest: Annotated[str, Field(min_length=64, max_length=64)]
    status: Literal[
        "ready_local",
        "needs_confirmation",
        "needs_clarification",
        "unsupported",
    ]
    requested_targets: list[RevisionTarget] = Field(default_factory=list)
    authority_targets: list[RevisionTarget] = Field(default_factory=list)
    upstream_candidates: list[RevisionTarget] = Field(default_factory=list)
    downstream_targets: list[RevisionTarget] = Field(default_factory=list)
    execution_mode: Literal["targeted_revision", "stage_rewind", "none"]
    reason_codes: list[str] = Field(default_factory=list)
    explanation: Annotated[str, Field(min_length=1, max_length=4_000)]
    artifact_versions: dict[str, int] = Field(default_factory=dict)
    trace_digest: Annotated[str, Field(min_length=64, max_length=64)]

    @model_validator(mode="after")
    def normalize_plan_collections(self) -> RevisionPlan:
        self.reason_codes = sorted({code.strip() for code in self.reason_codes if code.strip()})
        if len({target.ref for target in self.requested_targets}) != len(self.requested_targets):
            raise ValueError("requested targets must be unique")
        for field in ("authority_targets", "upstream_candidates", "downstream_targets"):
            targets = getattr(self, field)
            if len({target.ref for target in targets}) != len(targets):
                raise ValueError(f"{field} must be unique")
        return self


class RevisionExecutionResult(BaseModel):
    """Result returned by an execution adapter, including a target remap."""

    model_config = ConfigDict(extra="forbid")

    changed_stages: list[str] = Field(default_factory=list)
    touched_targets: dict[str, list[str]] = Field(default_factory=dict)
    regenerated_targets: dict[str, list[str]] = Field(default_factory=dict)
    stale_targets: dict[str, list[str]] = Field(default_factory=dict)
    target_remap: dict[str, str] = Field(default_factory=dict)
    artifact_versions: dict[str, int] = Field(default_factory=dict)


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
    # Preserve the legacy command payload; Wave 2 explicitly reads this field
    # while constructing an approval action payload.
    revision: RevisionInterpretation | None = Field(default=None, exclude=True)
    # 분기/재실행 routing에만 쓰며 기존 수정 명령의 저장 형식에는 추가하지 않는다.
    stage: str = Field(default="", exclude=True)

    @model_validator(mode="after")
    def validate_intent_payload(self) -> CommandIntent:
        self.targets = [target.strip() for target in self.targets]
        self.instruction = self.instruction.strip()
        self.stage = self.stage.strip()
        if self.revision is not None:
            if self.targets and self.targets != self.revision.targets:
                raise ValueError("command targets must match revision targets")
            if not self.targets:
                self.targets = list(self.revision.targets)
            if not self.instruction:
                self.instruction = self.revision.requested_effect
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
        allowed_stages = {
            ConversationIntent.BRANCH.value: {"requirements", "design", "implementation"},
            ConversationIntent.RERUN.value: {
                "requirements",
                "design",
                "implementation",
                "testing",
            },
        }
        if self.intent in allowed_stages and self.stage not in allowed_stages[self.intent]:
            raise ValueError(f"{self.intent} intent requires a supported stage")
        return self


ConversationOutcome = Reply | Clarification | CommandIntent


__all__ = [
    "Clarification",
    "CommandIntent",
    "ConversationIntent",
    "ConversationOutcome",
    "Reply",
    "RevisionExecutionResult",
    "RevisionInterpretation",
    "RevisionPlan",
    "RevisionTarget",
]
