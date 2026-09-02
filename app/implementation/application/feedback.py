"""구현 결과에 대한 피드백을 구현 단계에서 처리할 수 있는지 확인한다."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.implementation.agents.provider import configured_api_key


class FeedbackTargetProposal(BaseModel):
    """LLM이 기존 RTM에서 고른 수정 대상 후보만 담는다."""

    model_config = ConfigDict(extra="forbid")

    target_refs: list[str] = Field(default_factory=list, max_length=12)


FeedbackProposalCall = Callable[[str, list[dict[str, object]]], FeedbackTargetProposal]

def assess_feedback_eligibility(
    feedback: str,
    design: dict[str, Any] | None = None,
    rtm_map: dict[str, Any] | None = None,
) -> dict[str, object]:
    """추적표와 설계 계약을 기준으로 피드백을 구현 단계에서 처리할지 판단한다."""
    from app.implementation.workflows.traceability import (
        evaluate_feedback_rtm_traceability,
    )

    return evaluate_feedback_rtm_traceability(feedback, design=design, rtm_map=rtm_map)


def resolve_feedback_targets(
    feedback: str,
    rtm_map: dict[str, Any] | None,
    *,
    proposal_call: FeedbackProposalCall | None = None,
    confirmed_refs: list[str] | None = None,
) -> dict[str, object]:
    """LLM 후보를 RTM의 실제 ref와 파일로 확인한다.

    LLM은 파일 이름을 새로 만들 수 없다. RTM에 존재하는 후보만 고르고, 일반 코드가
    다시 교집합을 계산한다. Testing이 이미 정확한 ``kind:id``를 전달한 경우에는 같은
    판단을 LLM에 반복시키지 않는다.
    """
    candidates = _target_candidates(rtm_map)
    available = {
        ref
        for candidate in candidates
        for ref in candidate["refs"]
        if isinstance(ref, str)
    }
    explicit = sorted(ref for ref in available if ref in feedback)
    if confirmed_refs is not None:
        proposed = confirmed_refs
        source = "confirmed"
    elif explicit:
        proposed = explicit
        source = "explicit"
    elif candidates:
        proposal = (proposal_call or _call_target_llm)(feedback, candidates)
        proposed = proposal.target_refs
        source = "llm"
    else:
        proposed = []
        source = "none"

    confirmed = sorted(set(proposed) & available)
    files = sorted(
        {
            str(candidate["file"])
            for candidate in candidates
            if set(candidate["refs"]) & set(confirmed)
        }
    )
    return {
        "source": source,
        "confirmedTargetRefs": confirmed,
        "relatedFiles": files,
    }


def _target_candidates(rtm_map: dict[str, Any] | None) -> list[dict[str, object]]:
    """implementation RTM 행을 LLM이 고를 수 있는 작은 목록으로 바꾼다."""
    mappings = rtm_map.get("mappings") if isinstance(rtm_map, dict) else None
    if not isinstance(mappings, list):
        return []
    result: list[dict[str, object]] = []
    for item in mappings:
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("target_file") or "").strip()
        if not file_path:
            continue
        refs = {
            str(ref)
            for ref in item.get("sourceRefs") or []
            if isinstance(ref, str) and ref
        }
        task_id = str(item.get("taskId") or "").strip()
        if task_id:
            refs.add(f"task:{task_id}")
        refs.add(f"file:{file_path}")
        result.append(
            {
                "file": file_path,
                "element": str(item.get("element_name") or ""),
                "refs": sorted(refs),
            }
        )
    return result


def _call_target_llm(
    feedback: str,
    candidates: list[dict[str, object]],
) -> FeedbackTargetProposal:
    """자연어 지시와 관계있는 RTM ref 후보만 구조화해 받는다."""
    api_key = configured_api_key()
    if not api_key:
        raise RuntimeError("LLM API key is not configured for feedback target interpretation.")
    client = OpenAI(
        api_key=api_key,
        base_url=settings.base_url or settings.implementation_agent_base_url,
        max_retries=0,
        timeout=settings.llm_timeout_seconds,
    )
    model = settings.model.removeprefix("nvidia_nim/")
    response = client.chat.completions.create(
        model=model,
        temperature=settings.temperature,
        max_tokens=2048,
        messages=[
            {
                "role": "system",
                "content": (
                    "Select only the implementation RTM refs directly affected by the user feedback. "
                    "Return an empty list for broad wording that does not identify a supported target. "
                    "Never invent or rewrite a ref."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Feedback:\n"
                    + feedback
                    + "\n\nAvailable RTM targets:\n"
                    + json.dumps(candidates, ensure_ascii=False)
                ),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "FeedbackTargetProposal",
                "strict": True,
                "schema": FeedbackTargetProposal.model_json_schema(),
            },
        },
    )
    content = response.choices[0].message.content if response.choices else ""
    return FeedbackTargetProposal.model_validate_json(content or "")


__all__ = [
    "FeedbackTargetProposal",
    "assess_feedback_eligibility",
    "resolve_feedback_targets",
]
