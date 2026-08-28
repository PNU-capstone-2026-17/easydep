"""typed WorkloadGraph의 LLM 생성·수정 경계를 조율한다.

이 서비스는 WorkloadGraph만 제안·수정하며 placement, VM, provider resource를 만들지 않는다.
graph state·repository·requirements 내부 state를 import하지 않고 전달받은 구조화 산출물만
prompt payload로 조립한다.
"""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from app.design.services.common.structured import parse_structured, revision_messages
from app.design.services.deployment_diagram.models import WorkloadGraph
from app.design.services.deployment_diagram.prompts import (
    DEPLOYMENT_REVISION_SYSTEM_PROMPT,
    generation_messages,
)


class WorkloadGraphProposalCall(Protocol):
    """공통 structured LLM adapter와 테스트 대역의 호출 계약이다."""

    def __call__(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> dict[str, Any]: ...


def _propose_workload_graph(
    structured_inputs: dict[str, Any],
    proposal_call: WorkloadGraphProposalCall | None = None,
) -> WorkloadGraph:
    propose = proposal_call or parse_structured
    return WorkloadGraph.model_validate(
        propose(generation_messages(structured_inputs), WorkloadGraph)
    )


def generate_workload_graph(
    scenario_text: str,
    api_spec: dict[str, Any],
    *,
    refined_requirements: Any = None,
    capability_contract: dict[str, Any] | None = None,
    resource_intake: dict[str, Any] | None = None,
    class_model: Any = None,
    sequence_model: Any = None,
    erd_model: Any = None,
    deployment_planning_facts: list[dict[str, Any]] | None = None,
    proposal_call: WorkloadGraphProposalCall | None = None,
) -> WorkloadGraph:
    """구조화된 설계 산출물에서 typed WorkloadGraph를 한 번 제안한다.

    Args:
        scenario_text: workload 행위 근거인 유스케이스 명세다.
        api_spec: 현재 결정론적으로 렌더된 API 계약이다.
        refined_requirements: 정제된 요구사항의 외부 JSON 값이다.
        capability_contract: 승인 capability 계약이다.
        resource_intake: resource 입력과 provenance 계약이다.
        class_model: 승인된 typed BCE model의 JSON 값이다.
        sequence_model: 결정론적 typed sequence model의 JSON 값이다.
        erd_model: 승인된 ERD BCE model의 JSON 값이다.
        deployment_planning_facts: 승인된 deployment-only fact 목록이다.
        proposal_call: 테스트·adapter가 주입할 선택적 structured proposal 호출이다.

    Returns:
        schema 검증이 끝난 ``WorkloadGraph``다.

    Notes:
        빈 scenario면 LLM을 호출하지 않는다. 그 밖에는 기존 prompt field 순서, schema class
        이름 ``WorkloadGraphProposal``과 공통 schema repair 범위를 그대로 사용한다.
    """

    if not scenario_text:
        return WorkloadGraph()
    structured = {
        "refinedRequirements": refined_requirements or [],
        "capabilityContract": capability_contract or {},
        "resourceIntake": resource_intake or {},
        "useCaseSpecification": scenario_text,
        "apiSpec": api_spec,
        "deploymentPlanningFacts": deployment_planning_facts or [],
    }
    if class_model:
        structured["classModel"] = class_model
    if sequence_model:
        structured["sequenceModel"] = sequence_model
    if erd_model:
        structured["erdModel"] = erd_model
    return _propose_workload_graph(structured, proposal_call)


def revise_workload_graph(
    current_model: WorkloadGraph,
    feedback: str,
    context_text: str = "",
    targets: set[str] | None = None,
    *,
    proposal_call: WorkloadGraphProposalCall | None = None,
) -> WorkloadGraph:
    """현재 typed WorkloadGraph에 피드백을 한 번 적용한다.

    Args:
        current_model: 현재 저장된 WorkloadGraph candidate다.
        feedback: 사용자 또는 기존 검증 경계의 제한된 수정 지시다.
        context_text: 기존 graph adapter가 조립한 설계 artifact 문맥이다.
        targets: graph가 정한 선택적 workload element 대상이다.
        proposal_call: 테스트·adapter가 주입할 선택적 structured proposal 호출이다.

    Returns:
        전체 수정 결과를 담은 schema 검증 WorkloadGraph다.

    Notes:
        빈 feedback이면 같은 객체를 반환한다. 그 밖에는 기존 revision envelope와 schema
        ``WorkloadGraphProposal``을 사용해 한 번 호출하며 별도 repair loop를 추가하지 않는다.
    """

    if not feedback:
        return current_model
    propose = proposal_call or parse_structured
    revised = propose(
        revision_messages(
            DEPLOYMENT_REVISION_SYSTEM_PROMPT,
            "Design Artifacts",
            context_text,
            "Current WorkloadGraph",
            current_model.model_dump(),
            feedback,
            targets,
        ),
        WorkloadGraph,
    )
    return WorkloadGraph.model_validate(revised)
