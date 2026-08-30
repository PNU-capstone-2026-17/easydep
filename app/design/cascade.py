"""지목 수정 — 고를 항목 하나를 고치고, 추적표가 알려준 하류 항목만 따라 고친다.

**왜 되감기로는 안 되나.** 되감기는 그 스테이지를 처음부터 다시 만든다.
시퀀스 추출은 클래스 다이어그램 전체를 프롬프트로 받아 유스케이스별 모델을
새로 생성하므로, 클래스 하나가 바뀌면 **수정과 무관한 다이어그램까지 달라질 수 있다.**
사용자가 승인해둔 내용이 날아간다. "필드 하나 추가"의 대가로 산출물 전체를 잃는 것이다.

**그래서 여기서는 고칠 것만 고친다.**

    "Order 클래스에 주문일시 추가"
       ↓ class 모델의 Order 만 수정
       ↓ 추적표: class:Order → api_spec:Order, erd:Order, deployment:order.jar
    그 항목들만 수정. 나머지는 글자 하나 안 바뀐다.

**보장은 프롬프트가 아니라 코드가 한다.** 리바이저는 여전히 모델 전체를 돌려주고, LLM은
지시를 어길 수 있다. `merge_model`(nodes/artifact.py)이 **비대상 항목에 대해서는 LLM
출력을 아예 읽지 않으므로**, 어겨도 결과에 닿지 못한다. 프롬프트의 범위 지시는 대상이
잘 고쳐지도록 초점을 좁히는 보조 수단일 뿐이다.
"""
from __future__ import annotations

import json
from typing import Any

from app.db.models import ORIGIN_FEEDBACK_REVISED
from app.design.graphs.subgraphs import DESIGN_SPECS
from app.design.nodes.artifact import (
    CHECKED_ONLY,
    CLEAN,
    DesignArtifactSpec,
    assert_untargeted_elements_preserved,
    merge_model,
    render_and_validate,
)
from app.design.rtm import affected_by_element, build_design_rtm, linked_elements
from app.design.schemas.architecture_state import ArchitectureState
from app.repositories import artifact_repository


class UnknownTarget(Exception):
    """지목한 항목이 지금 산출물에 없다."""


def _check_report(
    spec: DesignArtifactSpec, model: dict, state: ArchitectureState
) -> dict[str, Any]:
    """고친 모델을 규칙으로 검사한다 — **재생성은 하지 않는다.**

    **왜 여기서도 검사해야 하나.** 예전에는 이 경로가 규칙 검사를 아예 안 돌렸다. 그래서
    그래프 실행이 남긴 `{findings: [], stopped: "clean"}`이 상태에 그대로 남고, 지목 수정이
    모델을 고친 뒤에도 화면은 **아무도 검사하지 않은 새 모델에 대해 계속 "clean"을**
    보여줬다. 낡은 판정이 새 산출물의 보증으로 둔갑하는 것이고, 이 기능 전체가 막으려던
    실패("위반 없음"과 "검사하지 않았음"을 구별하기)와 정확히 같은 것이다.

    **왜 재생성은 안 하나.** 이 경로의 보장은 "지목한 항목만 바뀐다"이고, 그것은
    `merge_model`이 비대상 항목에 대해 LLM 출력을 아예 안 읽어서 성립한다. 그런데 재생성은
    `targets=set()`(전체 수정)으로 부르므로, 여기서 루프를 돌리면 **그 보장을 스스로 깬다.**
    사용자가 "Order 에 주문일시 추가"를 요청했는데 다른 클래스가 조용히 바뀌는 것이다.
    그래서 드러내기만 하고, 고칠지는 사용자가 정한다.
    """
    findings = spec.check(model, state)
    return {
        "findings": [f.as_issue() for f in findings],
        "repair_iters": 0,
        "stopped": CLEAN if not findings else CHECKED_ONLY,
    }


def _apply(
    spec: DesignArtifactSpec,
    state: ArchitectureState,
    feedback: str,
    targets: set[str],
) -> dict[str, Any]:
    """한 스테이지에서 대상 항목만 고치고, 검사·렌더까지 마친 상태 조각을 돌려준다."""
    original = state.get(spec.model_key) or {}
    delta: dict[str, Any] = {}
    if spec.revise_state is not None:
        # Sequence feedback owns an upstream class-collaboration revision as one
        # atomic state transition.  Calling only ``spec.revise`` would merely
        # re-project the unchanged class model and then make the cascade guess an
        # unrelated reverse class edit from RTM links.
        delta = spec.revise_state(original, feedback, state, targets)
        if spec.model_key not in delta:
            raise ValueError(
                f"{spec.stage} state revision did not return {spec.model_key}"
            )
        revised = delta[spec.model_key]
    else:
        revised = spec.revise(original, feedback, state, targets)

    merge_targets = set(targets)
    if spec.stage == "class_diagram":
        merge_targets.update(
            _class_collaboration_dependency_targets(original, revised, targets)
        )
    merged = merge_model(spec, original, revised, merge_targets)
    # The LLM boundary ends here: merge_model must retain every non-target
    # value from the persisted source before a deterministic finalizer derives
    # its own runtime bundle fields.
    assert_untargeted_elements_preserved(spec, original, merged, merge_targets)

    # 지목 수정은 비대상 보존이 계약이므로 전체 흐름 재추출을 포함하는 reconcile은
    # 실행하지 않는다. 대신 최종 구성 규칙은 반드시 적용해, 새 시퀀스 호출의 메서드는
    # 수신 클래스에 결정론적으로 보강한 뒤에만 렌더한다.
    working: ArchitectureState = {**state, **delta, spec.model_key: merged}
    patch: dict[str, Any] = {**delta, spec.model_key: merged}
    if spec.finalize:
        finalized = spec.finalize(working)
        patch.update(finalized)
        working.update(finalized)
        merged = working.get(spec.model_key) or merged

    patch.update(render_and_validate(spec, merged, working))
    if spec.check_key:
        patch[spec.check_key] = _check_report(spec, merged, working)
    return patch


def _class_collaboration_dependency_targets(
    original: dict[str, Any],
    revised: dict[str, Any],
    targets: set[str],
) -> set[str]:
    """Include collaborations whose operation reference changes with a class.

    Operation IDs contain the parameter signature.  Replacing a targeted class
    can therefore turn ``Boundary::login(request:LoginRequest)`` into a different
    canonical ID.  Keeping the old collaboration byte-for-byte then creates a
    dangling receiverOperationId.  ``revise_class_model`` already returns a fully
    validated model with repaired collaborations, so accept only those exact
    dependency-owned collaboration replacements alongside the selected class.
    """

    def operation_ids(model: dict[str, Any]) -> set[str]:
        return {
            str(operation.get("operationId") or "").strip()
            for class_item in model.get("Classes") or []
            if isinstance(class_item, dict)
            and str(class_item.get("className") or "").strip() in targets
            for operation in class_item.get("operations") or []
            if isinstance(operation, dict)
            and str(operation.get("operationId") or "").strip()
        }

    owned_operations = operation_ids(original) | operation_ids(revised)
    if not owned_operations:
        return set()
    return {
        str(collaboration.get("collaborationId") or "").strip()
        for model in (original, revised)
        for collaboration in model.get("Collaborations") or []
        if isinstance(collaboration, dict)
        and str(collaboration.get("collaborationId") or "").strip()
        and any(
            isinstance(call, dict)
            and str(call.get("receiverOperationId") or "").strip()
            in owned_operations
            for call in collaboration.get("calls") or []
        )
    }


def _refs_by_stage(refs: list[str]) -> dict[str, set[str]]:
    """Split ``stage:element`` references without accepting unknown stages."""
    grouped: dict[str, set[str]] = {}
    for ref in refs:
        stage, separator, element = ref.partition(":")
        if separator and stage in DESIGN_SPECS and element:
            grouped.setdefault(stage, set()).add(element)
    return grouped


def _selected_source_payload(
    state: ArchitectureState, stage: str, elements: set[str]
) -> dict[str, Any]:
    """Return only the source elements that justified a reverse update.

    Passing an entire diagram/API document back to a reviser gives it needless
    opportunities to reinterpret unrelated content.  The feedback already
    carries the user's instruction; this compact payload provides just the
    exact RTM-linked evidence for the other artifact.
    """
    spec = DESIGN_SPECS[stage]
    model = state.get(spec.model_key) or {}
    if not isinstance(model, dict):
        return {}
    selected: dict[str, list[dict[str, Any]]] = {}
    for field, key_of in spec.elements.items():
        matches = [
            item
            for item in model.get(field, []) or []
            if isinstance(item, dict) and key_of(item) in elements
        ]
        if matches:
            selected[field] = matches
    return selected


def _trace_backed_feedback(
    state: ArchitectureState,
    source_stage: str,
    source_elements: set[str],
    feedback: str,
) -> str:
    """Give a related artifact only approved, trace-backed revision evidence."""
    evidence = _selected_source_payload(state, source_stage, source_elements)
    return (
        f'The user explicitly revised {source_stage}:{", ".join(sorted(source_elements))} '
        f'with: "{feedback}". This is an exact RTM contract link, not a name match. '
        "Update only the listed target elements so their existing contract agrees. "
        "Do not add, remove, rename, or alter any other element or any unrelated "
        "field, method, relationship, message, endpoint, or schema. If the evidence "
        "does not determine a safe change, preserve the target unchanged.\n"
        "[Trace-backed source elements]\n"
        + json.dumps(evidence, ensure_ascii=False, indent=2)
    )


def _reproject_erd(state: ArchitectureState) -> dict[str, Any]:
    """ERD 를 다시 만든다 — **LLM 을 부르지 않는다.**

    ERD 는 클래스 BCE 의 <<Entity>> 를 결정론적으로 투영한 것이다. 클래스가 바뀌면
    다시 투영하면 그만이고, 물어볼 것이 없다.

    **재투영한 모델도 검사한다.** 클래스 다이어그램이 통과했다는 것이 ERD 의 보증이
    아니기 때문이다 — 같은 BCE 라도 두 스테이지가 보는 규칙이 다르다(다중도가 없는 관계,
    기본키 없는 테이블, 이름으로 가리킨 참조는 전부 ERD 쪽에서만 결함이다). 검사를
    빼면 클래스 쪽 수정이 ERD 를 조용히 망가뜨려도 화면은 아무 말을 안 한다.

    여기서도 재생성은 안 한다(`checked_only`) — 이 경로의 보장은 "지목한 것만 바뀐다"이고,
    ERD 는 애초에 물어보지 않고 다시 그리는 자리다.
    """
    spec = DESIGN_SPECS["erd"]
    model = spec.extract(state)
    patch: dict[str, Any] = {
        spec.model_key: model,
        **render_and_validate(spec, model, state),
    }
    if spec.check_key:
        patch[spec.check_key] = _check_report(spec, model, state)
    return patch


def revise_and_cascade(
    state: ArchitectureState,
    target: str,
    feedback: str,
) -> dict[str, Any]:
    """`{stage}:{element}` 를 고치고, 증명된 관련 항목만 따라 고친다.

    반환 {"state": 바뀐 상태, "changed": [스테이지...], "touched": {스테이지: [항목...]}}
    — 화면이 "무엇을 고쳤는지" 보여줄 재료다.

    클래스 수정은 기존처럼 provenance RTM을 따라 하류를 고친다. 시퀀스/API 수정은
    ``links``의 *정확한* Control-binding/sequence-call 계약이 있을 때만 관련 클래스를
    역방향으로 고치고, 그 클래스에서 다시 하류를 맞춘다. 링크가 없으면 추측하지 않는다.

    어느 경로든 무관한 스테이지는 리바이저를 **부르지도 않는다**. LLM 출력은
    finalizer보다 먼저 ``assert_untargeted_elements_preserved``를 통과해야 하므로,
    환각한 형제 변경은 저장·전파되기 전에 거절된다. 그 뒤의 finalizer는 별도 LLM
    출력 없이 실행되는 결정론적 번들 투영이다.
    """
    stage, _, element = target.partition(":")
    if stage not in DESIGN_SPECS or stage == "erd":
        raise UnknownTarget(f"{target} is not an editable design element.")

    working: ArchitectureState = dict(state)
    rtm = build_design_rtm(working)
    if not any(
        row["stage"] == stage and row["element"] == element for row in rtm["rows"]
    ):
        raise UnknownTarget(f"{target} is not in the current artifacts.")

    changed: list[str] = []
    touched: dict[str, list[str]] = {}
    processed: dict[str, set[str]] = {}
    revised_upstream_stages: set[str] = set()

    def apply_targets(
        target_stage: str, targets: set[str], revision_feedback: str
    ) -> None:
        """Apply one bounded stage patch and record its immutable scope."""
        pending = targets - processed.get(target_stage, set())
        if not pending:
            return
        patch = _apply(
            DESIGN_SPECS[target_stage], working, revision_feedback, pending
        )
        working.update(patch)
        upstream_stages = {
            str(value)
            for value in patch.get("revised_upstream_stages") or []
            if str(value) in DESIGN_SPECS and str(value) != target_stage
        }
        revised_upstream_stages.update(upstream_stages)
        for upstream in sorted(
            upstream_stages,
            key=lambda value: list(DESIGN_SPECS).index(value),
        ):
            if upstream not in changed:
                changed.append(upstream)
                touched[upstream] = []
        processed.setdefault(target_stage, set()).update(pending)
        if target_stage not in changed:
            changed.append(target_stage)
            touched[target_stage] = []
        touched[target_stage] = sorted(set(touched[target_stage]) | pending)

    # ① The user-selected element is the only unconditionally editable source.
    apply_targets(stage, {element}, feedback)

    # ② Freeze direct links from the *pre-change* RTM.  We must not discover
    # links from an LLM revision itself: that would let a hallucinated reference
    # enlarge the change scope while the cascade is already in progress.
    directly_linked = _refs_by_stage(linked_elements(rtm, stage, element))
    source_elements = {element}
    trace_feedback = _trace_backed_feedback(working, stage, source_elements, feedback)

    # A sequence/API element may change an earlier class contract only when the
    # direct-link builder proved the route.  Class edits stay element-targeted;
    # a guessed class name is never passed to a reviser.
    if (
        stage in {"sequence_diagram", "api_spec"}
        and "class_diagram" not in revised_upstream_stages
    ):
        apply_targets(
            "class_diagram",
            directly_linked.get("class_diagram", set()),
            trace_feedback,
        )

    # ③ A touched class is now the authoritative structural change.  Follow its
    # frozen forward provenance links, but do not re-edit the user-selected
    # source element.  Reprocessing it could overwrite the feedback it just
    # approved and would create a new LLM opportunity for unrelated changes.
    scheduled = {
        target_stage: set(elements)
        for target_stage, elements in directly_linked.items()
        if target_stage in {"sequence_diagram", "api_spec"}
    }
    for class_name in processed.get("class_diagram", set()):
        for affected in affected_by_element(rtm, "class_diagram", class_name):
            affected_stage, _, affected_element = affected.partition(":")
            if affected_stage and affected_element:
                scheduled.setdefault(affected_stage, set()).add(affected_element)

    # Keep the design order after the reverse class patch.  An API feedback can
    # therefore repair its exact sequence card against the bounded class result;
    # a sequence feedback can repair only the exact API operation that binds it.
    for next_stage in ("sequence_diagram", "api_spec"):
        apply_targets(
            next_stage,
            scheduled.get(next_stage, set()),
            trace_feedback,
        )

    # ERD is deterministic, never LLM-revised.  As before, do not materialize a
    # future stage solely because an earlier one was edited.
    if (
        processed.get("class_diagram")
        and working.get(DESIGN_SPECS["erd"].model_key)
    ):
        working.update(_reproject_erd(working))
        if "erd" not in changed:
            changed.append("erd")
        touched["erd"] = ["(클래스 BCE 에서 재투영)"]

    # Deployment has no reverse contract link.  It is updated only when the
    # frozen class provenance explicitly names one of its elements.
    apply_targets(
        "deployment_diagram",
        scheduled.get("deployment_diagram", set()),
        trace_feedback,
    )

    # ``persist_cascade`` saves every stage recorded above itself.  Do not leave
    # the graph-only upstream marker in the synchronized checkpoint, where a
    # later ordinary graph persist could save the same class revision again.
    if revised_upstream_stages:
        working["revised_upstream_stages"] = []

    return {
        "state": working,
        "changed": changed,
        "touched": touched,
        "related": sorted(linked_elements(rtm, stage, element)),
    }


def persist_cascade(app_id: str, result: dict[str, Any]) -> None:
    """고친 스테이지만 새 버전으로 남긴다. 안 고친 것은 저장하지 않는다."""
    for stage in result["changed"]:
        artifact_repository.save_stage(
            app_id, stage, result["state"], origin=ORIGIN_FEEDBACK_REVISED
        )
