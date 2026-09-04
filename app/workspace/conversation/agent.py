"""Workspace 메시지를 해석하는 LLM 기반 대화 경계다.

모델은 발화를 분류하고 유한한 ref 중 하나를 고른다. 실행 단계와 영향 범위를 결정하거나 전문
서비스를 호출하지 않으며, 그 결정은 action registry와 project tool이 맡는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.requirements.runtime.structured_llm import invoke_structured

from .context import ConversationContext
from .contracts import Clarification, CommandIntent, ConversationIntent, Reply
from .project_tools import ProjectTools

T = TypeVar("T", bound=BaseModel)
ProposalCall = Callable[[type[T], list], T]
ConversationResult = Reply | Clarification | CommandIntent


class _ConversationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["reply", "project_question", "command", "clarification"]
    intent: ConversationIntent | None = None
    query: str = ""
    reply: str = ""
    question: str = ""
    stage: Literal["", "requirements", "design", "implementation", "testing"] = ""

    @model_validator(mode="after")
    def validate_kind_fields(self) -> _ConversationPlan:
        if self.kind == "command" and self.intent is None:
            raise ValueError("command plans require an intent")
        if self.kind == "command" and self.intent in {
            ConversationIntent.BRANCH,
            ConversationIntent.RERUN,
        } and not self.stage:
            raise ValueError("branch and rerun commands require a stage")
        if self.kind == "project_question" and not self.query.strip():
            raise ValueError("project questions require a search query")
        if self.kind == "reply" and not self.reply.strip():
            raise ValueError("replies require text")
        if self.kind == "clarification" and not self.question.strip():
            raise ValueError("clarifications require a question")
        return self


class _TargetSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[str] = Field(default_factory=list, max_length=12)
    clarification: str = ""


class _GroundedReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


_PLAN_SYSTEM = """You are the conversational boundary of a software delivery workspace.
Classify the user's utterance without inventing state or artifact references.
- reply: ordinary social conversation that needs no project data or workflow execution.
- project_question: a question about this project's current state or artifacts. Supply a concise
  search query, not an answer from memory.
- command: an explicit request to advance, answer a pending question, revise project content,
  delegate an offered repair, create a checkpoint branch, or rerun a delivery stage. Select only
  the corresponding allowed intent. Branch supports requirements, design, and implementation;
  rerun also supports testing. Choose only one of those named stages.
- clarification: the utterance is ambiguous between those categories.
Never infer a file, impact scope, or target reference. A stage may be selected only for an explicit
branch or rerun request. Buttons and explicit action payloads do not pass through this classifier."""


class ConversationAgent:
    def __init__(self, proposal_call: ProposalCall | None = None) -> None:
        self._propose = proposal_call or invoke_structured

    def respond(
        self,
        app_id: str,
        text: str,
        context: ConversationContext,
        *,
        tools: ProjectTools | None = None,
    ) -> ConversationResult:
        """발화 한 건을 분류해 아직 실행하지 않은 공개 결과로 반환한다."""

        project_tools = tools or ProjectTools(app_id)
        utterance = _bounded_text(text.strip(), 8_000)
        # 분류기는 앱 ID나 오래된 command ID를 사용하지 않는다. 현재 상태와 대기 질문을
        # 먼저 주고, 지시 대상을 이어 말할 때 필요한 최근 대화만 남긴다. 실제 project
        # 정보와 수정 대상은 분류 뒤 전용 tool이 다시 읽으므로 여기서 산출물을 복사하지 않는다.
        planning_context = {
            "workspace": context.workspace,
            "pendingQuestion": context.pending_question,
            "actions": context.actions,
            "recentTurns": [
                {"role": turn.role, "text": turn.text}
                for turn in context.turns[-4:]
            ],
            "recentDecisions": context.decisions[-3:],
        }
        context_json = json.dumps(
            planning_context,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        context_json = _bounded_text(context_json, 24_000)
        plan = self._propose(
            _ConversationPlan,
            [
                SystemMessage(content=_PLAN_SYSTEM),
                HumanMessage(
                    content=(
                        f"Workspace context:\n{context_json}\n\n"
                        f"User utterance:\n{utterance}"
                    )
                ),
            ],
        )
        if plan.kind == "reply":
            return Reply(text=plan.reply.strip())
        if plan.kind == "clarification":
            return Clarification(question=plan.question.strip(), candidates=[])
        if plan.kind == "project_question":
            return self._answer_project_question(utterance, plan.query, project_tools)

        assert plan.intent is not None
        if plan.intent == ConversationIntent.REVISE:
            return self._resolve_revision(utterance, plan.query or utterance, project_tools)
        return CommandIntent(
            intent=plan.intent,
            instruction=utterance,
            stage=plan.stage,
        )

    def _resolve_revision(
        self, text: str, query: str, tools: ProjectTools
    ) -> CommandIntent | Clarification:
        candidates = tools.search_elements(query)
        if not candidates and query.strip() != text.strip():
            candidates = tools.search_elements(text)
        if not candidates:
            return Clarification(
                question="I could not find the artifact element to revise. Please specify the target.",
                candidates=[],
            )
        selection = self._propose(
            _TargetSelection,
            [
                SystemMessage(
                    content=(
                        "Select only refs from the supplied finite candidate list that are directly "
                        "targeted by the revision. If the target is ambiguous, return no targets and "
                        "ask one concise clarification question. Never invent or rewrite a ref."
                    )
                ),
                HumanMessage(
                    content=(
                        f"Revision request:\n{text}\n\nCandidates:\n"
                        + json.dumps(candidates, ensure_ascii=False, default=str)
                    )
                ),
            ],
        )
        available = {str(item.get("ref") or "") for item in candidates}
        selected = list(dict.fromkeys(ref for ref in selection.targets if ref in available))
        validation = tools.validate_targets(selected)
        valid = list(validation.get("valid_refs") or [])
        if not valid:
            labels = [
                str(item.get("label") or item.get("ref") or "")
                for item in candidates[:5]
            ]
            return Clarification(
                question=(
                    selection.clarification.strip()
                    or "Please select the artifact element to revise."
                ),
                candidates=[item for item in labels if item],
            )
        return CommandIntent(
            intent=ConversationIntent.REVISE,
            targets=valid,
            instruction=text.strip(),
        )

    def _answer_project_question(
        self, text: str, query: str, tools: ProjectTools
    ) -> Reply | Clarification:
        workspace = tools.read_workspace()
        candidates = tools.search_elements(query)
        if not candidates and query.strip() != text.strip():
            candidates = tools.search_elements(text)
        evidence = {
            "workspace": workspace,
            "matches": candidates,
        }
        if candidates:
            refs = [str(item.get("ref") or "") for item in candidates[:5]]
            validation = tools.validate_targets(refs)
            readable = list(validation.get("existing_refs") or refs)
            evidence["elements"] = [
                tools.read_element(ref) for ref in readable[:3]
            ]
        reply = self._propose(
            _GroundedReply,
            [
                SystemMessage(
                    content=(
                        "Answer the project question using only the supplied tool evidence. "
                        "Say explicitly when the evidence is insufficient. Do not claim that a "
                        "workflow action ran and do not recommend unlisted artifact refs."
                    )
                ),
                HumanMessage(
                    content=(
                        f"Question:\n{text}\n\nTool evidence:\n"
                        + json.dumps(evidence, ensure_ascii=False, default=str)
                    )
                ),
            ],
        )
        if not reply.text.strip():
            return Clarification(
                question="Please be more specific about what to inspect in the project.",
                candidates=[],
            )
        return Reply(text=reply.text.strip())


def _bounded_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n...[content truncated]...\n"
    available = limit - len(marker)
    if available <= 1:
        return text[:limit]
    tail = max(1, available // 3)
    return f"{text[: available - tail]}{marker}{text[-tail:]}"


conversation_agent = ConversationAgent()


__all__ = ["ConversationAgent", "ConversationResult", "conversation_agent"]
