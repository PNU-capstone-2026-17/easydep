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

from typing import Any

from app.db.models import ORIGIN_FEEDBACK_REVISED
from app.design.graphs.subgraphs import DESIGN_SPECS, DESIGN_STAGES
from app.design.nodes.artifact import (
    CHECKED_ONLY,
    CLEAN,
    DesignArtifactSpec,
    merge_model,
    render_and_validate,
)
from app.design.rtm import affected_by_element, build_design_rtm
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
    revised = spec.revise(original, feedback, state, targets)
    merged = merge_model(spec, original, revised, targets)

    # 지목 수정은 비대상 보존이 계약이므로 전체 흐름 재추출을 포함하는 reconcile은
    # 실행하지 않는다. 대신 최종 불변식은 반드시 적용해, 새 시퀀스 호출의 메서드는
    # 수신 클래스에 결정론적으로 보강한 뒤에만 렌더한다.
    working: ArchitectureState = {**state, spec.model_key: merged}
    patch: dict[str, Any] = {spec.model_key: merged}
    if spec.finalize:
        finalized = spec.finalize(working)
        patch.update(finalized)
        working.update(finalized)
        merged = working.get(spec.model_key) or merged

    patch.update(render_and_validate(spec, merged, working))
    if spec.check_key:
        patch[spec.check_key] = _check_report(spec, merged, working)
    return patch


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
    """`{stage}:{element}` 를 고치고, 영향받는 하류 항목만 따라 고친다.

    반환 {"state": 바뀐 상태, "changed": [스테이지...], "touched": {스테이지: [항목...]}}
    — 화면이 "무엇을 고쳤는지" 보여줄 재료다.

    무관한 스테이지는 리바이저를 **부르지도 않는다.** LLM 호출이 곧 변경 위험이므로,
    안 부르는 것이 가장 확실한 보존이다.
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

    # ① 지목한 항목을 고친다.
    working.update(_apply(DESIGN_SPECS[stage], working, feedback, {element}))
    changed = [stage]
    touched: dict[str, list[str]] = {stage: [element]}

    # ② 추적표가 알려준 하류 항목을 스테이지별로 모은다. 추적표는 **수정 전** 상태에서
    #    읽는다 — 수정 후에는 방금 지운 링크가 사라져서 하류를 놓칠 수 있다.
    downstream: dict[str, list[str]] = {}
    for affected in affected_by_element(rtm, stage, element):
        affected_stage, _, affected_element = affected.partition(":")
        downstream.setdefault(affected_stage, []).append(affected_element)

    # ③ 파이프라인 순서로 따라간다 — 앞 스테이지의 결과가 뒤의 맥락이 되기 때문이다.
    upstream_note = (
        f"An upstream change was applied to {stage}:{element} — \"{feedback}\". "
        "Update the listed elements so they agree with it. Change nothing else."
    )
    for next_stage in DESIGN_STAGES[DESIGN_STAGES.index(stage) + 1 :]:
        if next_stage == "erd":
            # ERD 는 투영이다. 영향 여부와 무관하게 클래스가 바뀌었으면 다시 그린다.
            #
            # **다만 이미 있는 ERD 만 다시 그린다.** 지목 수정은 설계가 끝나기 전에도
            # 부를 수 있고(화면이 파이프라인 진행 중에도 대상 고르기를 연다), 그때
            # 없던 ERD 를 여기서 만들어 저장하면 파이프라인이 아직 도달하지 않은
            # 단계가 완료된 것처럼 보인다. 하류를 맞추는 일이지 앞질러 만드는 일이 아니다.
            if "class_diagram" in changed and working.get(DESIGN_SPECS["erd"].model_key):
                working.update(_reproject_erd(working))
                changed.append("erd")
                touched["erd"] = ["(클래스 BCE 에서 재투영)"]
            continue

        elements = downstream.get(next_stage)
        if not elements:
            continue        # 영향 없음 — 리바이저를 부르지 않는다
        working.update(
            _apply(DESIGN_SPECS[next_stage], working, upstream_note, set(elements))
        )
        changed.append(next_stage)
        touched[next_stage] = sorted(set(elements))

    return {"state": working, "changed": changed, "touched": touched}


def persist_cascade(app_id: str, result: dict[str, Any]) -> None:
    """고친 스테이지만 새 버전으로 남긴다. 안 고친 것은 저장하지 않는다."""
    for stage in result["changed"]:
        artifact_repository.save_stage(
            app_id, stage, result["state"], origin=ORIGIN_FEEDBACK_REVISED
        )
