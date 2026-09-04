"""타입이 확정된 설계 입력 위에서 API 제안과 제한된 수정을 수행한다."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from app.design.contracts.api_spec import (
    ApiEndpointProposal,
    ApiSpecModel,
    ApiSpecProposal,
)
from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.normalization import (
    api_spec_proposal_from_model,
    interaction_contracts,
    normalize_api_spec_model,
)
from app.design.services.api_spec.prompts import (
    API_SPEC_REVISION_SYSTEM_PROMPT,
    proposal_messages,
    revision_context,
)
from app.design.services.common.structured import parse_structured, revision_messages

ProposalCall = Callable[[list[dict[str, str]], type[BaseModel]], dict[str, Any]]


def _finite_proposal_schema(bce_model: BCEModel) -> type[ApiSpecProposal]:
    """이번 입력에 실제로 존재하는 상호작용만 고를 수 있는 응답 스키마를 만든다.

    HTTP 메서드와 경로는 여전히 LLM이 판단한다. 코드는 후보 ID와 필요한 endpoint 개수만
    알려 주어, 한 항목을 길게 쓰느라 나머지를 빠뜨리거나 같은 후보를 반복하지 못하게 한다.
    """

    interaction_ids = tuple(item.interaction_id for item in interaction_contracts(bce_model))
    if not interaction_ids:
        return ApiSpecProposal
    finite_endpoint = create_model(
        "FiniteApiEndpointProposal",
        __base__=ApiEndpointProposal,
        interaction_id=(
            Literal.__getitem__(interaction_ids),
            Field(description="One supplied interaction candidate copied exactly."),
        ),
    )
    return create_model(
        "FiniteApiSpecProposal",
        __base__=ApiSpecProposal,
        Endpoints=(
            list[finite_endpoint],  # type: ignore[valid-type]
            Field(min_length=len(interaction_ids), max_length=len(interaction_ids)),
        ),
    )


def generate_api_spec_model(
    scenario_text: str,
    bce_model: BCEModel,
    *,
    proposal_call: ProposalCall | None = None,
) -> ApiSpecModel:
    """승인된 BCE 입력에서 HTTP 계약을 한 번 제안하고 실행 정보를 채운다.

    Args:
        scenario_text: 현재 유스케이스 명세 문자열이다.
        bce_model: 검증이 끝난 클래스·연산·협업 모델이다.
        proposal_call: 테스트·adapter가 주입할 선택적 structured proposal 호출이다.

    Returns:
        기존 ``api_spec_model`` JSON shape로 dump할 수 있는 타입 모델이다.

    Notes:
        LLM 호출은 정확히 한 번의 structured proposal 경계에만 있다. 공통 structured
        adapter의 기존 schema repair 정책은 바꾸지 않는다.
    """

    if not scenario_text:
        return ApiSpecModel()
    proposal_schema = _finite_proposal_schema(bce_model)
    propose = proposal_call or parse_structured
    proposal = ApiSpecProposal.model_validate(
        proposal_schema.model_validate(
            propose(proposal_messages(scenario_text, bce_model), proposal_schema)
        )
    )
    return normalize_api_spec_model(proposal, bce_model)


def revise_api_spec_model(
    current_model: ApiSpecModel,
    feedback: str,
    scenario_text: str,
    bce_model: BCEModel,
    targets: set[str] | None = None,
    *,
    proposal_call: ProposalCall | None = None,
) -> ApiSpecModel:
    """피드백을 타입 API 모델에 한 번 적용하고 BCE 계약으로 재정규화한다.

    Args:
        current_model: 현재 저장된 API endpoint 모델이다.
        feedback: 사용자 또는 semantic gate의 제한된 수정 지시다.
        scenario_text: 현재 유스케이스 명세 문자열이다.
        bce_model: 검증이 끝난 BCE 모델이다.
        targets: graph가 정한 선택적 수정 대상 식별자 집합이다.
        proposal_call: 테스트·adapter가 주입할 선택적 structured proposal 호출이다.

    Returns:
        전체 수정 결과를 담은 타입 API 모델이다.

    Notes:
        빈 피드백은 LLM을 호출하지 않는다. 그 밖에는 기존 공통 revision envelope와
        structured schema repair 횟수를 그대로 사용한다.
    """

    if not feedback:
        return current_model
    current_proposal = api_spec_proposal_from_model(current_model, bce_model)
    proposal_schema = _finite_proposal_schema(bce_model)
    propose = proposal_call or parse_structured
    revised = ApiSpecProposal.model_validate(
        proposal_schema.model_validate(
            propose(
                revision_messages(
                    API_SPEC_REVISION_SYSTEM_PROMPT,
                    "Use Cases and Accepted Interaction Candidates",
                    revision_context(scenario_text, bce_model),
                    "Current HTTP Proposal",
                    current_proposal.model_dump(),
                    feedback,
                    targets,
                ),
                proposal_schema,
            )
        )
    )
    return normalize_api_spec_model(revised, bce_model)
