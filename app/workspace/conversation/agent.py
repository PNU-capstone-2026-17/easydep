"""Workspace 메시지를 해석하는 LLM 기반 대화 경계다.

모델은 발화를 분류하고 유한한 ref 중 하나를 고른다. 실행 단계와 영향 범위를 결정하거나 전문
서비스를 호출하지 않으며, 그 결정은 action registry와 project tool이 맡는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Literal, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.requirements.runtime.structured_llm import invoke_structured

from .cloud_guidance import CloudTopic, cloud_guidance_evidence
from .context import ConversationContext
from .contracts import (
    Clarification,
    CommandIntent,
    ConversationIntent,
    Reply,
    RevisionInterpretation,
)
from .project_tools import ProjectTools

T = TypeVar("T", bound=BaseModel)
ProposalCall = Callable[[type[T], list], T]
CloudGuidanceCall = Callable[[str, CloudTopic, str], dict[str, object]]
ConversationResult = Reply | Clarification | CommandIntent


class _ConversationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["reply", "project_question", "command", "clarification"]
    intent: Literal[
        "",
        "advance",
        "answer",
        "revise",
        "delegate_repair",
        "branch",
        "rerun",
        "confirm_revision",
        "dismiss_revision",
    ] = ""
    query: str = ""
    reply: str = ""
    question: str = ""
    stage: Literal["", "requirements", "design", "implementation", "testing"] = ""
    cloud_topic: Literal["", "provider_region", "sku", "free_tier", "topology"] = ""

    @model_validator(mode="after")
    def validate_kind_fields(self) -> _ConversationPlan:
        if self.kind == "command" and not self.intent:
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


class _GroundedReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


_PLAN_SYSTEM = """You are the conversational boundary of a software delivery workspace.
Classify the user's utterance without inventing state or artifact references.
- reply: ordinary social conversation that needs no project data or workflow execution.
- project_question: a question about this project's current state or artifacts. Supply a concise
  search query, not an answer from memory. Set cloud_topic only when the question concerns the
  current cloud provider or region, VM SKU, cost or performance, VM Free Tier, or deployment resource topology;
  otherwise leave it empty.
- command: an explicit request to advance, answer a pending question, revise project content,
  delegate an offered repair, approve or dismiss a pending revision plan, create a checkpoint
  branch, or rerun a delivery stage. Select confirm_revision or dismiss_revision only when the
  corresponding pending-plan action is present in the supplied workspace context. Branch supports
  requirements, design, and implementation; rerun also supports testing. Choose only one stage.
- clarification: the utterance is ambiguous between those categories.
An artifact selection in workspace context identifies what the user is viewing; it is not itself an
instruction to revise it. Classify the user's utterance first.
Never infer a file, impact scope, or target reference. A stage may be selected only for an explicit
branch or rerun request. Buttons and explicit action payloads do not pass through this classifier."""


class ConversationAgent:
    def __init__(
        self,
        proposal_call: ProposalCall | None = None,
        cloud_guidance_call: CloudGuidanceCall | None = None,
    ) -> None:
        self._propose = proposal_call or invoke_structured
        self._cloud_guidance = cloud_guidance_call or cloud_guidance_evidence

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
            "recentTargetRemap": context.target_remap,
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
            return self._answer_project_question(
                utterance,
                plan.query,
                project_tools,
                app_id=app_id,
                cloud_topic=plan.cloud_topic,
                context=context,
            )

        assert plan.intent
        if plan.intent == ConversationIntent.REVISE:
            return self._resolve_revision(
                utterance,
                plan.query or utterance,
                project_tools,
                recent_refs=list(dict.fromkeys(context.target_remap.values())),
                context=context,
            )
        return CommandIntent(
            intent=plan.intent,
            instruction=utterance,
            stage=plan.stage,
        )

    def _resolve_revision(
        self,
        text: str,
        query: str,
        tools: ProjectTools,
        *,
        recent_refs: list[str] | None = None,
        context: ConversationContext | None = None,
    ) -> CommandIntent | Clarification:
        selected_candidates = self._selected_artifact_candidates(context, tools)
        candidates = _merge_candidates(
            self._exact_catalog_candidates(query, tools),
            tools.search_elements(query),
            selected_candidates,
        )
        if not candidates and query.strip() != text.strip():
            candidates = _merge_candidates(candidates, tools.search_elements(text))
        if recent_refs:
            validation = tools.validate_revision_selections(recent_refs[:12])
            known_refs = {str(item.get("ref") or "") for item in candidates}
            for ref in validation.get("valid_refs") or []:
                normalized_ref = str(ref)
                if normalized_ref in known_refs:
                    continue
                try:
                    candidates.append(tools.read_element(normalized_ref))
                    known_refs.add(normalized_ref)
                except KeyError:
                    continue
        return self._select_revision(
            text,
            candidates,
            tools,
            context=context,
        )

    def interpret_revision(
        self,
        text: str,
        target_refs: list[str],
        *,
        tools: ProjectTools,
        context: ConversationContext | None = None,
    ) -> CommandIntent | Clarification:
        """Interpret semantics for UI-selected targets without reclassifying the command."""

        validation = tools.validate_revision_selections(target_refs)
        exact_candidates: list[dict] = []
        describe = getattr(tools, "describe_element", None)
        for ref in validation.get("valid_refs") or []:
            try:
                exact_candidates.append(
                    describe(str(ref)) if callable(describe) else tools.read_element(str(ref))
                )
            except KeyError:
                continue
        # A selected card is locality, not necessarily the leaf that owns the
        # requested change. Include finite current-project matches so a named
        # operation inside a selected sequence can become the exact authority.
        candidates = _merge_candidates(
            self._exact_catalog_candidates(text, tools),
            exact_candidates,
            tools.search_elements(text),
            self._selected_artifact_candidates(context, tools),
        )[:12]
        return self._select_revision(
            text,
            candidates,
            tools,
            context=context,
        )

    def _select_revision(
        self,
        text: str,
        candidates: list[dict],
        tools: ProjectTools,
        *,
        context: ConversationContext | None = None,
    ) -> CommandIntent | Clarification:
        """Use one structured call for target selection and revision semantics."""

        if not candidates:
            return Clarification(
                question="I could not find the artifact element to revise. Please specify the target.",
                candidates=[],
            )
        selection = self._propose(
            RevisionInterpretation,
            [
                SystemMessage(
                    content=(
                        "Select only refs from the supplied finite candidate list that are directly "
                        "targeted by the revision. If the target is ambiguous, return no targets and "
                        "ask one concise clarification question. Never invent or rewrite a ref. "
                        "Classify only the user's semantic scope as presentation, contract, behavior, "
                        "implementation, test_expectation, or unknown. Use implementation for a "
                        "testing finding that asks to repair trace-linked production code; use "
                        "test_expectation only when the expected external behavior itself changes. "
                        "Use presentation only for visual formatting or labels that do not change "
                        "a described system response. Scenario steps, emitted messages, conditions, "
                        "and outcomes are behavior. Use contract for designed interfaces such as "
                        "operation names, parameters, return types, API shapes, and schema fields. "
                        "Use implementation only for source files, implementation tasks, or code "
                        "details behind those contracts. "
                        "requested_effect is a short "
                        "resolved description of what the user asked for. When the supplied "
                        "conversation contains an original request and a follow-up, preserve the "
                        "specific details from both; do not invent details. Never name an executable stage, file, "
                        "owner, impact list, or inferred upstream target. Also classify change_type "
                        "as modify, add, rename, remove, or unknown. A selected artifact is context, "
                        "not a requested mutation. Use unknown when the wording "
                        "does not distinguish those meanings."
                    )
                ),
                HumanMessage(
                    content=(
                        f"Revision conversation:\n{self._recent_turns_text(context)}\n\n"
                        f"Revision request:\n{text}\n\nCandidates:\n"
                        + json.dumps(candidates, ensure_ascii=False, default=str)
                    )
                ),
            ],
        )
        available = {str(item.get("ref") or "") for item in candidates}
        selected = list(dict.fromkeys(ref for ref in selection.targets if ref in available))
        exact_refs = _exact_candidate_refs(text, candidates)
        selected_exact = [ref for ref in selected if ref in exact_refs]
        if len(selected_exact) == 1:
            # Exact identity narrows an already model-selected target; merely
            # mentioning another artifact is not authority to edit it.
            selected = selected_exact
        validation = tools.validate_revision_selections(selected)
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
        # A follow-up after a clarification needs the original request as well
        # as the new constraint. The structured interpreter receives that small
        # dialogue and resolves the user-authored request without guessing refs.
        requested_effect = (
            selection.requested_effect.strip()
            if context is not None and context.turns and selection.requested_effect.strip()
            else text.strip()
        )
        interpretation = selection.model_copy(
            update={
                "targets": valid,
                "requested_effect": requested_effect,
            }
        )
        return CommandIntent(
            intent=ConversationIntent.REVISE,
            targets=valid,
            instruction=interpretation.requested_effect,
            revision=interpretation,
        )

    def _answer_project_question(
        self,
        text: str,
        query: str,
        tools: ProjectTools,
        *,
        app_id: str,
        cloud_topic: CloudTopic | Literal[""],
        context: ConversationContext | None = None,
    ) -> Reply | Clarification:
        workspace = tools.read_workspace()
        selected_candidates = self._selected_artifact_candidates(context, tools)
        candidates = _merge_candidates(
            self._explicit_selection_candidates(context, selected_candidates),
            tools.search_elements(query),
            selected_candidates,
        )
        if not candidates and query.strip() != text.strip():
            candidates = _merge_candidates(candidates, tools.search_elements(text))
        evidence = {
            "workspace": workspace,
            "selection": self._selection(context),
            "matches": candidates,
        }
        if cloud_topic:
            evidence["cloud"] = self._cloud_guidance(
                app_id,
                cloud_topic,
                f"{query}\n{text}",
            )
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
                        "workflow action ran and do not recommend values or artifact refs absent "
                        "from the evidence. Treat cloud catalog data as guidance, not a selection "
                        "or a guarantee of availability, performance, Free Tier eligibility, or "
                        "zero cost. The user chooses provider, region, and SKU. Answer in English."
                    )
                ),
                HumanMessage(
                    content=(
                        f"Conversation:\n{self._recent_turns_text(context)}\n\n"
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

    @staticmethod
    def _selection(context: ConversationContext | None) -> dict:
        if context is None:
            return {}
        selection = context.workspace.get("selection")
        return dict(selection) if isinstance(selection, dict) else {}

    def _selected_artifact_candidates(
        self, context: ConversationContext | None, tools: ProjectTools
    ) -> list[dict]:
        """Read the finite catalog behind a UI artifact selection when present."""

        stage = str(self._selection(context).get("artifact_stage") or "").strip()
        artifact_candidates = getattr(tools, "artifact_candidates", None)
        if not stage or not callable(artifact_candidates):
            return []
        return list(artifact_candidates(stage))

    @staticmethod
    def _exact_catalog_candidates(text: str, tools: ProjectTools) -> list[dict]:
        resolver = getattr(tools, "resolve_exact_elements", None)
        return list(resolver(text)) if callable(resolver) else []

    def _explicit_selection_candidates(
        self, context: ConversationContext | None, candidates: list[dict]
    ) -> list[dict]:
        """Place the exact UI element first so a grounded answer reads it."""

        selected_ref = str(self._selection(context).get("element_ref") or "").strip()
        if not selected_ref:
            return []
        return [item for item in candidates if str(item.get("ref") or "") == selected_ref]

    @staticmethod
    def _recent_turns_text(context: ConversationContext | None) -> str:
        if context is None:
            return ""
        return _bounded_text(
            "\n".join(
                f"{turn.role}: {turn.text}"
                for turn in context.turns[-4:]
            ),
            16_000,
        )

def _bounded_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n...[content truncated]...\n"
    available = limit - len(marker)
    if available <= 1:
        return text[:limit]
    tail = max(1, available // 3)
    return f"{text[: available - tail]}{marker}{text[-tail:]}"


def _merge_candidates(*groups: list[dict]) -> list[dict]:
    """Preserve the first catalog candidate for every finite public ref."""

    merged: list[dict] = []
    refs: set[str] = set()
    for group in groups:
        for item in group:
            ref = str(item.get("ref") or "")
            if not ref or ref in refs:
                continue
            refs.add(ref)
            merged.append(item)
    return merged


def _exact_candidate_refs(text: str, candidates: list[dict]) -> list[str]:
    """Resolve finite public candidate identifiers without ref-kind semantics."""

    normalized_text = " ".join(text.split()).casefold()
    text_tokens = re.findall(r"[A-Za-z0-9_]+", text.casefold())
    refs: list[str] = []
    for item in candidates:
        ref = str(item.get("ref") or "").strip()
        if not ref:
            continue
        forms = [
            str(item.get(key) or "").strip()
            for key in ("ref", "name", "label", "canonical_ref", "requested_ref")
        ]
        summary = str(item.get("summary") or "").strip()
        if summary and normalized_text == " ".join(summary.split()).casefold():
            refs.append(ref)
            continue
        for form in forms:
            if not form:
                continue
            literal = re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(form)}(?![A-Za-z0-9_])",
                text,
                re.IGNORECASE,
            )
            # Separator-insensitive comparison supports catalog identifiers in
            # ordinary prose (for example, ``Owner.method``). Compare whole
            # token sequences so a shorter catalog name cannot match a word
            # fragment such as ``Incident`` inside ``Incidental``.
            form_tokens = re.findall(r"[A-Za-z0-9_]+", form.casefold())
            token_match = bool(form_tokens) and any(
                text_tokens[index : index + len(form_tokens)] == form_tokens
                for index in range(len(text_tokens) - len(form_tokens) + 1)
            )
            if literal or token_match:
                refs.append(ref)
                break
    return list(dict.fromkeys(refs))


conversation_agent = ConversationAgent()


__all__ = ["ConversationAgent", "ConversationResult", "conversation_agent"]
