"""Workspace가 사용하는 설계 애플리케이션 서비스다.

이 모듈은 설계 실행을 시작하거나 재개하고, 이미 만든 설계의 일부를 수정한다.
웹 응답 객체를 만들지 않고 일반 ``dict``를 반환하므로 Workspace뿐 아니라 다른
Python 호출자도 같은 흐름을 그대로 사용할 수 있다.

실제 클래스·시퀀스·API·ERD·배포 설계 생성은 ``app.design.graphs``가 담당한다.
여기서는 앱과 실행 상태를 확인하고, 그래프 호출과 수정 결과 저장을 한곳에서 조정한다.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator

from app.artifacts_api import to_web_response
from app.db.models import ORIGIN_FEEDBACK_REVISED
from app.design.cascade import (
    UnapprovedScopeExpansion,
    UnknownTarget,
    persist_cascade,
    revise_and_cascade,
)
from app.design.graphs.design_graph import (
    StageNotReached,
    has_active_session,
    has_design_run,
    reset_design,
    resume_design,
    retry_design,
    revise_design_stage,
    rewind_design,
    session_status,
    start_design,
    sync_design_state,
)
from app.design.graphs.subgraphs import DESIGN_STAGES
from app.design.schemas.architecture_state import ArchitectureState
from app.design.services.deployment_diagram.bundle import (
    hydrate_deployment_diagram_bundle,
    select_deployment_target,
)
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.sizing import (
    apply_capacity_overrides,
    apply_compute_selections,
    compute_sizing_guidance,
)
from app.design.services.deployment_diagram.workload_contracts import (
    data_execution_mode_decision,
)
from app.design.validation import design_readiness_report
from app.repositories import artifact_repository
from app.repositories.artifact_repository import AppNotFound


class ReviseRequest(BaseModel):
    """추적표에 표시된 설계 요소 하나를 수정하는 명령."""

    # ``{stage}:{element}`` 형식이며, 화면이 선택한 대상을 그대로 전달한다.
    target: str
    feedback: str = ""
    # Planner execution supplies these frozen sets.  ``None`` is meaningful:
    # it is not the old implicit permission to discover an upstream authority.
    approved_authority_targets: list[str] | None = None
    approved_downstream_targets: list[str] | None = None


class BatchReviseRequest(BaseModel):
    """여러 설계 요소를 모두 성공했을 때만 저장하는 수정 명령."""

    revisions: list[ReviseRequest] = Field(min_length=1, max_length=20)

    @field_validator("revisions")
    @classmethod
    def targets_are_unique(cls, revisions: list[ReviseRequest]) -> list[ReviseRequest]:
        """대상이 겹치거나 설명이 비어 있는 수정 묶음을 실행 전에 거절한다."""
        targets = [revision.target.strip() for revision in revisions]
        if any(not target for target in targets):
            raise ValueError("Every targeted revision needs an element reference.")
        if len(targets) != len(set(targets)):
            raise ValueError("A target can appear only once in a revision batch.")
        if any(not revision.feedback.strip() for revision in revisions):
            raise ValueError("Every targeted revision needs feedback text.")
        return revisions


def start_design_session(app_id: str) -> dict[str, Any]:
    """첫 설계 단계부터 실행하고 클래스 다이어그램 검토 지점에서 멈춘다."""
    _validate_app_id(app_id)
    state = _load_app(app_id)
    # 모든 설계 산출물은 유스케이스 명세를 입력으로 사용한다. 나머지 순서는 설계
    # 그래프가 보장하므로 시작할 때에는 이 입력이 있는지만 확인하면 된다.
    if not state.get("usecase_spec"):
        raise ValueError(
            "The use case specification must exist first. "
            "Complete requirements analysis in the Workspace first."
        )

    # 새 시작은 이전 체크포인트만 비운다. 이미 저장한 산출물과 버전 이력은 유지한다.
    reset_design(app_id)
    try:
        return start_design(app_id, state)
    except Exception as error:
        raise RuntimeError(f"Design pipeline failed: {error}") from error


def resume_design_session(app_id: str, feedback: str = "") -> dict[str, Any]:
    """검토 중인 설계에 피드백을 적용하거나 다음 설계 단계로 진행한다."""
    _validate_app_id(app_id)
    _require_app_exists(app_id)
    _require_active_session(app_id)

    # 빈 피드백은 현재 결과를 승인하고 다음 단계로 진행한다는 뜻이다. 결정론적 검사가
    # 문제를 찾은 초안은 다음 단계의 입력으로 쓰지 않고, 먼저 수정하도록 안내한다.
    if not feedback.strip():
        active_stage = session_status(app_id).get("stage")
        if active_stage:
            state = _load_app(app_id)
            readiness = design_readiness_report(state, stages=[str(active_stage)])
            findings = list(readiness.get("findings") or [])
            if findings:
                raise ValueError(
                    "Resolve the active design findings before advancing. "
                    f"Stage: {active_stage}. Findings: {findings}"
                )
            # ERD는 논리 데이터 모델일 뿐 실행 DB 엔진을 뜻하지 않는다. 별도 DB가
            # 명시됐지만 엔진이 빠진 경우에만 배포 산출물을 만들기 직전에 물어본다.
            if active_stage == "erd":
                decision = data_execution_mode_decision(
                    state.get("refined_requirements") or [],
                    capability_contract=dict(state.get("capability_contract") or {}),
                    deployment_planning_facts=(
                        state.get("deployment_planning_facts") or []
                    ),
                )
                if decision.get("status") == "needsInput":
                    question = {
                        "field": "dataExecutionMode",
                        "kind": "choice",
                        "question": "How should the application run its database?",
                        "reason": (
                            "A separate database runtime is required, but no supported "
                            "engine was specified."
                        ),
                        "sourceRefs": list(decision.get("sourceRefs") or []),
                        "choices": [
                            {
                                "value": "postgresql-container",
                                "label": "PostgreSQL container",
                                "description": (
                                    "Run the application and PostgreSQL as separate "
                                    "containers on the selected VM."
                                ),
                            },
                            {
                                "value": "embedded",
                                "label": "Embedded database",
                                "description": (
                                    "Keep the database inside the application runtime."
                                ),
                            },
                        ],
                    }
                    return {
                        "app_id": app_id,
                        **to_web_response(state),
                        "status": "need_feedback",
                        "stage": "deployment_diagram",
                        "resource_question": question,
                    }
    try:
        return resume_design(app_id, feedback)
    except Exception as error:
        raise RuntimeError(f"Design pipeline failed: {error}") from error


def apply_deployment_topology_decision_session(
    app_id: str, data_execution_mode: str
) -> dict[str, Any]:
    """배포 직전 DB 실행 방식 답을 기존 planning fact로 기록하고 계속한다."""

    _validate_app_id(app_id)
    _require_app_exists(app_id)
    _require_active_session(app_id)
    if session_status(app_id).get("stage") != "erd":
        raise ValueError("A database runtime choice is only accepted before deployment design.")
    mode = data_execution_mode.strip()
    if mode not in {"embedded", "postgresql-container"}:
        raise ValueError(f"Unsupported database runtime choice: {mode}")

    state = _load_app(app_id)
    decision = data_execution_mode_decision(
        state.get("refined_requirements") or [],
        capability_contract=dict(state.get("capability_contract") or {}),
        deployment_planning_facts=state.get("deployment_planning_facts") or [],
    )
    if decision.get("status") != "needsInput":
        raise ValueError("The current design does not need a database runtime choice.")
    fact = {
        "id": "user-data-execution-mode",
        "kind": "dataExecutionMode",
        "value": mode,
        "sourceRefs": list(decision.get("sourceRefs") or []),
        "derivationRule": "user-approved-data-execution-mode",
        "authority": "explicit",
        "status": "accepted",
    }
    facts = [
        dict(item)
        for item in state.get("deployment_planning_facts") or []
        if isinstance(item, dict) and item.get("kind") != "dataExecutionMode"
    ]
    sync_design_state(app_id, {"deployment_planning_facts": [*facts, fact]})
    try:
        # 실제 graph는 아직 ERD gate에 멈춰 있다. 답을 일반 피드백으로 보내지 않고
        # 승인으로 재개하면 deployment stage 하나만 정상 경로에서 생성·저장된다.
        return resume_design(app_id, "")
    except Exception as error:
        raise RuntimeError(f"Design pipeline failed: {error}") from error


def select_deployment_target_session(app_id: str, target_id: str) -> dict[str, Any]:
    """복수 배포 후보 중 사용자가 고른 하나를 LLM 호출 없이 확정한다.

    후보 ID는 저장된 bundle이 만든 값만 받는다. 선택 결과를 DB와 LangGraph 체크포인트에
    함께 반영한 뒤, 마지막 deployment gate를 통과시켜 설계 세션을 완료한다.
    """

    _validate_app_id(app_id)
    state = _load_app(app_id)
    bundle = state.get("deployment_diagram_bundle")
    if not isinstance(bundle, dict) or not bundle:
        raise ValueError("A deployment bundle must exist before selecting a target.")
    selected = select_deployment_target(bundle, target_id)
    hydrated = hydrate_deployment_diagram_bundle(selected)
    state.update(hydrated)
    state["deployment_diagram_puml"] = deployment_bundle_runtime_puml(selected)
    state["deployment_diagram_provisioning_puml"] = (
        deployment_bundle_provisioning_puml(selected)
    )
    artifact_repository.save_stage(
        app_id,
        "deployment_diagram",
        state,
        origin=ORIGIN_FEEDBACK_REVISED,
    )

    # load_state가 현재 검증 결과를 다시 계산한다. 저장 모델과 checkpoint를 이 값으로
    # 맞춰야 다음 resume이 선택 전 needsInput 상태로 되돌아가지 않는다.
    current = _load_app(app_id)
    sync_design_state(app_id, dict(current))
    status = session_status(app_id)
    if status.get("active") and status.get("stage") == "deployment_diagram":
        return resume_design_session(app_id)
    return {"app_id": app_id, **to_web_response(current), "status": "completed"}


def deployment_sizing_session(
    app_id: str,
    target_id: str,
    capacity_overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """선택 후보의 compute unit별 VM 크기와 compute-only 비용을 계산한다."""

    _validate_app_id(app_id)
    state = _load_app(app_id)
    bundle = state.get("deployment_diagram_bundle")
    if not isinstance(bundle, dict) or not bundle:
        raise ValueError("A deployment bundle must exist before requesting VM guidance.")
    selected = select_deployment_target(bundle, target_id)
    projection = next(
        item
        for item in selected.get("projections") or []
        if isinstance(item, dict) and item.get("target") == selected.get("selectedTarget")
    )
    stored_capacity_overrides = list(
        (projection.get("sizing") or {}).get("capacityOverrides") or []
    )
    effective_capacity = (
        stored_capacity_overrides if capacity_overrides is None else capacity_overrides
    )
    preview_plan, normalized_capacity = apply_capacity_overrides(
        dict(projection.get("deploymentPlan") or {}), effective_capacity
    )
    guidance = compute_sizing_guidance(
        preview_plan,
        provider=str(projection.get("provider") or ""),
        region=str(projection.get("region") or ""),
        workload_graph=dict(selected.get("workloadGraph") or {}),
    )
    return {
        "target": selected.get("selectedTarget"),
        "structureDigest": projection.get("deploymentPlanStructureDigest", ""),
        "guidance": guidance,
        # Sizing choices belong to a provider/region/zone projection.  The
        # compatibility summary at bundle level describes only the last
        # selection, so never use it to prefill a different target.
        "selected": list((projection.get("sizing") or {}).get("selected") or []),
        "capacityOverrides": [item.model_dump(by_alias=True) for item in normalized_capacity],
    }


def apply_deployment_sizing_session(
    app_id: str,
    target_id: str,
    selections: list[dict[str, Any]],
    expected_structure_digest: str | None = None,
    capacity_overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """VM 선택을 저장하고 같은 ResourcePlan에서 두 그림을 다시 만든다."""

    _validate_app_id(app_id)
    state = _load_app(app_id)
    bundle = state.get("deployment_diagram_bundle")
    if not isinstance(bundle, dict) or not bundle:
        raise ValueError("A deployment bundle must exist before applying VM choices.")
    if expected_structure_digest:
        selected = select_deployment_target(bundle, target_id)
        projection = next(
            item
            for item in selected.get("projections") or []
            if isinstance(item, dict)
            and item.get("target") == selected.get("selectedTarget")
        )
        if projection.get("deploymentPlanStructureDigest") != expected_structure_digest:
            raise ValueError(
                "The deployment preview changed. Reload VM choices before applying."
            )
    updated = apply_compute_selections(
        bundle,
        selections,
        selected_target=target_id,
        capacity_overrides=capacity_overrides,
    )
    if updated.get("status") != "completed":
        return {"app_id": app_id, "status": "needs_input", "sizing": updated.get("sizing")}
    hydrated = hydrate_deployment_diagram_bundle(updated)
    state.update(hydrated)
    state["deployment_diagram_puml"] = deployment_bundle_runtime_puml(updated)
    state["deployment_diagram_provisioning_puml"] = (
        deployment_bundle_provisioning_puml(updated)
    )
    artifact_repository.save_stage(
        app_id,
        "deployment_diagram",
        state,
        origin=ORIGIN_FEEDBACK_REVISED,
    )
    current = _load_app(app_id)
    sync_design_state(app_id, dict(current))
    status = session_status(app_id)
    if status.get("active") and status.get("stage") == "deployment_diagram":
        return resume_design_session(app_id)
    return {
        "app_id": app_id,
        **to_web_response(current),
        "status": "completed",
        "sizing": updated.get("sizing"),
    }


def retry_design_session(app_id: str) -> dict[str, Any]:
    """실패한 설계 노드부터 재시도하거나 현재 검토 결과를 복원한다."""
    _validate_app_id(app_id)
    _require_app_exists(app_id)
    status = session_status(app_id)
    if not status.get("retryable"):
        # 검토 지점은 실패 상태가 아니다. 이때에는 LLM을 다시 호출하지 않고 저장된
        # 결과를 반환하여 새로고침한 Workspace와 실행 상태만 다시 맞춘다.
        if status.get("active") and status.get("stage"):
            state = _load_app(app_id)
            return {
                "app_id": app_id,
                **to_web_response(state),
                "status": "need_feedback",
                "stage": status["stage"],
            }
        raise ValueError(f"No failed design stage is available to retry. Session: {status}")
    try:
        return retry_design(app_id)
    except Exception as error:
        raise RuntimeError(f"Design pipeline failed: {error}") from error


def rewind_design_session(app_id: str, stage: str) -> dict[str, Any]:
    """지정한 단계로 돌아가 해당 산출물부터 다시 만든다."""
    _validate_app_id(app_id)
    _require_app_exists(app_id)
    _require_design_run(app_id)

    if stage not in DESIGN_STAGES:
        raise ValueError(f"Unknown design stage: {stage}")
    if stage == DESIGN_STAGES[0]:
        raise ValueError(f"Rewinding to the first stage is the same as starting again: {stage}")

    try:
        return rewind_design(app_id, stage)
    except StageNotReached as error:
        raise ValueError(str(error)) from error
    except Exception as error:
        raise RuntimeError(f"Design pipeline failed: {error}") from error


def revise_design_stage_session(
    app_id: str,
    stage: str,
    feedback: str,
) -> dict[str, Any]:
    """Apply feedback at an already produced stage without pre-regeneration."""

    _validate_app_id(app_id)
    _require_app_exists(app_id)
    _require_design_run(app_id)
    if not feedback.strip():
        raise ValueError("Design stage revision feedback cannot be empty.")
    try:
        return revise_design_stage(app_id, stage, feedback)
    except StageNotReached as error:
        raise ValueError(str(error)) from error
    except Exception as error:
        raise RuntimeError(f"Design pipeline failed: {error}") from error


def revise_design_element(
    app_id: str,
    request: ReviseRequest,
    *,
    approved_authority_targets: set[str] | None = None,
    approved_downstream_targets: set[str] | None = None,
) -> dict[str, Any]:
    """선택한 설계 요소와 추적 관계로 연결된 부분만 수정한다."""
    return revise_design_elements(
        app_id,
        BatchReviseRequest(revisions=[request]),
        approved_authority_targets=approved_authority_targets,
        approved_downstream_targets=approved_downstream_targets,
    )


def revise_design_elements(
    app_id: str,
    request: BatchReviseRequest,
    *,
    approved_authority_targets: set[str] | None = None,
    approved_downstream_targets: set[str] | None = None,
) -> dict[str, Any]:
    """여러 설계 요소를 메모리에서 차례로 수정하고 모두 성공하면 저장한다."""
    _validate_app_id(app_id)
    original = _load_app(app_id)
    working = original
    changed: list[str] = []
    touched: dict[str, set[str]] = {}
    related: dict[str, list[str]] = {}
    regenerated: dict[str, set[str]] = {}

    try:
        for revision in request.revisions:
            result = revise_and_cascade(
                working,
                revision.target,
                revision.feedback,
                approved_authority_targets=(
                    set(revision.approved_authority_targets)
                    if revision.approved_authority_targets is not None
                    else approved_authority_targets
                ),
                approved_downstream_targets=(
                    set(revision.approved_downstream_targets)
                    if revision.approved_downstream_targets is not None
                    else approved_downstream_targets
                ),
            )
            working = result["state"]
            for stage in result["changed"]:
                if stage not in changed:
                    changed.append(stage)
            for stage, elements in result["touched"].items():
                touched.setdefault(stage, set()).update(elements)
            related[revision.target] = result.get("related", [])
            for regenerated_stage, elements in result.get("regenerated", {}).items():
                regenerated.setdefault(regenerated_stage, set()).update(elements)
    except UnknownTarget as error:
        raise ValueError(str(error)) from error
    except UnapprovedScopeExpansion as error:
        raise ValueError(f"Revision requires an approved frozen scope: {error}") from error
    except Exception as error:
        raise RuntimeError(f"Revision failed; no batch changes were saved: {error}") from error

    combined = {
        "state": working,
        "changed": changed,
        "touched": {stage: sorted(elements) for stage, elements in touched.items()},
    }
    # Update the checkpoint first. If artifact persistence then fails, restore
    # the prior checkpoint so a partial database batch cannot become visible.
    # Artifact versions themselves are committed by one repository transaction.
    sync_design_state(app_id, cast(dict[str, Any], working))
    try:
        persist_cascade(app_id, combined)
    except Exception:
        sync_design_state(app_id, cast(dict[str, Any], original))
        raise

    return {
        "app_id": app_id,
        **to_web_response(working),
        "changed": changed,
        "touched": combined["touched"],
        "related": related,
        "regenerated": {stage: sorted(elements) for stage, elements in regenerated.items()},
    }


def _validate_app_id(app_id: str) -> None:
    """저장소를 조회하기 전에 앱 ID가 UUID 형식인지 확인한다."""
    try:
        uuid.UUID(app_id)
    except ValueError as error:
        raise ValueError("Invalid app id.") from error


def _load_app(app_id: str) -> ArchitectureState:
    """저장된 앱 전체를 읽고, 존재하지 않으면 이해하기 쉬운 오류를 낸다."""
    try:
        return artifact_repository.load_state(app_id)
    except AppNotFound as error:
        raise LookupError("Unknown app id.") from error


def _require_app_exists(app_id: str) -> None:
    """산출물 전체를 읽지 않고 앱이 존재하는지만 확인한다."""
    try:
        artifact_repository.ensure_app_exists(app_id)
    except AppNotFound as error:
        raise LookupError("Unknown app id.") from error


def _require_active_session(app_id: str) -> None:
    """설계가 검토 지점에 멈춰 있는지 확인한다."""
    if not has_active_session(app_id):
        raise ValueError("No design session is in progress. Start the design first.")


def _require_design_run(app_id: str) -> None:
    """완료 여부와 관계없이 되감을 설계 실행 이력이 있는지 확인한다."""
    if not has_design_run(app_id):
        raise ValueError("This app has no design run to rewind. Start the design first.")
