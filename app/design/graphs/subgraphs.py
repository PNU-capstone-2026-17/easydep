"""5개 설계 스테이지의 스펙과 서브그래프 — 골격은 하나, 다른 것은 스펙뿐.

상위 그래프(`design_graph.py`)의 노드명은 산출물 이름이고, 세부 작업은 서브그래프
내부 노드로 캡슐화된다. 다섯 산출물이 모두 같은 골격을 따른다:

  생성:   extract_{stage} → [reconcile] → [check] → [finalize] → render → END
  피드백: revise_{stage}  → [reconcile] → [check] → [finalize] → render → END

예전에는 여기가 둘로 갈려 있었다. 클래스·ERD는 구조화된 모델에서 결정론적으로 렌더됐고,
시퀀스·API·배포는 LLM이 PlantUML/JSON을 직접 써서 validate → repair 루프를 달고 있었다.
지금은 다섯 모두 LLM에게 **구조화 모델만** 받고 산출물은 결정론적으로 렌더한다. 그래서
수리 루프가 사라졌고(무한 루프 위험도 함께), 피드백은 언제나 모델을 편집하며, 모델과
산출물이 어긋날 수 없다. 골격의 근거는 `app/design/nodes/artifact.py` 참조.

대괄호가 붙은 `check`는 **규칙 지식베이스를 가진 스테이지에만** 생긴다. `render`는
렌더와 그 자기검사를 함께 한다 — 예전의 `convert` + `validate`를
합친 것이고, 문법 검증이 렌더러의 출력만 보고 원리상 실패할 수 없어서 나눠 둘 값이 없었다.

서브그래프는 체크포인터 없이 컴파일된다 — 상위 그래프의 세이버가 트리 전체를 관장한다.
"""
from __future__ import annotations

import copy
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.design.knowledge.detectors import (
    api_spec_findings,
    class_diagram_findings,
    erd_findings,
    sequence_diagram_findings,
)
from app.design.nodes.artifact import (
    DesignArtifactSpec,
    check_node,
    extract_node,
    render_node,
    revise_node,
)
from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.api_spec.extractor import extract_api_spec_model
from app.design.services.api_spec.openapi import build_openapi_from_model
from app.design.services.api_spec.reviser import revise_api_spec_model
from app.design.services.class_diagram.extractor import extract_bce_classes_from_scenario
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.reviser import revise_bce_classes
from app.design.services.common.validation import validate_api_spec, validate_puml_artifact
from app.design.services.deployment_diagram.bundle import (
    build_deployment_diagram_bundle,
    hydrate_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.extractor import extract_deployment_model
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.reviser import revise_deployment_model
from app.design.services.erd.plantuml import generate_erd_from_bce_json
from app.design.services.erd.reviser import revise_erd_classes
from app.design.services.sequence_diagram.extractor import extract_sequence_diagrams
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.services.sequence_diagram.reconcile import (
    finalize_sequence_class_methods,
    reconcile_class_methods,
)
from app.design.services.sequence_diagram.reviser import revise_sequence_model

# 기존 테스트·확장 코드가 패치하는 이름을 유지하되 실제 동작은 복수 추출이다.
extract_sequence_model = extract_sequence_diagrams

#: 설계 파이프라인의 순서. 상위 그래프의 엣지도, 저장 순회도 여기서만 나온다.
#: 시퀀스·API는 클래스 다이어그램을, 배포는 그 앞의 모두를 재료로 쓴다. ERD는 클래스
#: 다이어그램의 BCE만 있으면 되지만, 배포가 ERD를 재료로 쓰므로 그 앞에 온다.
DESIGN_STAGES: tuple[str, ...] = (
    "class_diagram",
    "sequence_diagram",
    "api_spec",
    "erd",
    "deployment_diagram",
)


def _seed_erd_model(state: ArchitectureState) -> dict[str, Any]:
    """ERD 모델을 클래스 다이어그램의 BCE에서 시드한다 — 유일하게 LLM을 안 부르는 추출.

    ERD는 클래스 다이어그램 엔티티의 투영이므로 새로 도출할 것이 없다. 자기 복사본을
    갖는 이유는 이후 ERD 피드백이 클래스 다이어그램을 건드리지 않게 하기 위해서다.
    생성은 현재 클래스 BCE에서 다시 시드하므로 이전 ERD 편집을 버린다(클래스 다이어그램
    생성이 유스케이스에서 다시 추출하는 것과 같다). 편집을 보존하는 길은 피드백이다.

    **깊은 복사를 하는 것이 그 문장의 전부다.** 한동안 같은 객체를 그대로 돌려주고 있었다.
    지금 리바이저가 새 dict를 돌려주고 사상도 원본을 안 건드려서 사고는 안 났지만,
    격리가 있다고 적혀 있으니 다음 사람은 그것을 믿고 제자리 편집을 넣는다 — 그 순간
    ERD 수정이 클래스 다이어그램을 조용히 오염시킨다. 문서가 앞서 있었으므로 코드를
    문서에 맞춘다.
    """
    return copy.deepcopy(
        state.get("extracted_bce_classes") or state.get("erd_bce_classes") or {}
    )


def _message_key(message: dict) -> str:
    """시퀀스 메시지를 가리키는 이름. **rtm.py 가 만드는 element 와 같아야 한다.**

    메시지에는 id 가 없어서 (보내는 쪽 → 받는 쪽 : 라벨) 조합으로 가리킨다.
    """
    return "{} -> {} : {}".format(
        message.get("source", "?"), message.get("target", "?"), message.get("label", "")
    ).strip()


def _endpoint_key(endpoint: dict) -> str:
    """엔드포인트를 가리키는 이름. rtm.py 와 같은 규칙."""
    return endpoint.get("operation_id") or "{} {}".format(
        str(endpoint.get("method", "")).upper(), endpoint.get("path", "")
    ).strip()


def _design_context(state: ArchitectureState) -> str:
    """피드백 수정 때 LLM에게 주는 맥락 — 이 산출물이 무엇에서 나왔는지."""
    return "\n\n".join(
        [
            "[Use Case Specification]\n" + usecase_spec_text(state),
            "[Class Diagram]\n" + state.get("class_diagram_puml", ""),
            "[Sequence Diagrams]\n" + state.get("sequence_diagram_puml", ""),
            "[API Spec]\n" + str(state.get("api_spec", {})),
            "[ERD]\n" + state.get("erd_puml", ""),
        ]
    )


CLASS_DIAGRAM_SPEC = DesignArtifactSpec(
    stage="class_diagram",
    model_key="extracted_bce_classes",
    content_key="class_diagram_puml",
    valid_key="class_diagram_syntax_valid",
    errors_key="class_diagram_syntax_errors",
    feedback_key="class_diagram_feedback",
    empty="",
    extract=lambda state: extract_bce_classes_from_scenario(usecase_spec_text(state)),
    revise=lambda current, feedback, state, targets: revise_bce_classes(
        current_bce=current,
        feedback=feedback,
        scenario_text=usecase_spec_text(state),
        targets=targets,
    ),
    render=generate_plantuml_from_bce_json,
    validate=validate_puml_artifact,
    elements={"Classes": lambda c: c.get("className", "")},
    # 규칙 지식베이스를 가진 두 스테이지 중 하나다(다른 하나는 ERD). 시퀀스·API·배포는
    # 아직 `check_key`가 없고, 그래서 검사 노드도 생기지 않는다 — 검사하지 않는다는
    # 사실이 그래프에 그대로 보인다.
    check=class_diagram_findings,
    check_key="class_diagram_check",
)

SEQUENCE_DIAGRAM_SPEC = DesignArtifactSpec(
    stage="sequence_diagram",
    model_key="sequence_diagram_model",
    content_key="sequence_diagram_puml",
    valid_key="sequence_diagram_syntax_valid",
    errors_key="sequence_diagram_syntax_errors",
    feedback_key="sequence_diagram_feedback",
    empty="",
    extract=lambda state: extract_sequence_model(
        state.get("usecase_spec"),
        state.get("class_diagram_puml", ""),
    ),
    revise=lambda current, feedback, state, targets: revise_sequence_model(
        current, feedback, _design_context(state), targets
    ),
    render=generate_sequence_from_model,
    validate=validate_puml_artifact,
    elements={
        "Diagrams": lambda d: d.get("use_case_id", ""),
        "Participants": lambda p: p.get("name", ""),
        # 메시지에는 id 가 없다 — 추적표가 쓰는 것과 같은 조합으로 가리킨다.
        "Messages": _message_key,
    },
    check=sequence_diagram_findings,
    check_key="sequence_diagram_check",
    reconcile=reconcile_class_methods,
    finalize=finalize_sequence_class_methods,
)

API_SPEC_SPEC = DesignArtifactSpec(
    stage="api_spec",
    model_key="api_spec_model",
    content_key="api_spec",
    valid_key="api_spec_syntax_valid",
    errors_key="api_spec_syntax_errors",
    feedback_key="api_spec_feedback",
    empty={},
    extract=lambda state: extract_api_spec_model(
        usecase_spec_text(state),
        state.get("class_diagram_puml", ""),
        state.get("sequence_diagram_puml", ""),
    ),
    revise=lambda current, feedback, state, targets: revise_api_spec_model(
        current, feedback, _design_context(state), targets
    ),
    render=build_openapi_from_model,
    validate=validate_api_spec,
    elements={
        "Endpoints": _endpoint_key,
        "Schemas": lambda s: s.get("name", ""),
    },
    check=api_spec_findings,
    check_key="api_spec_check",
)

ERD_SPEC = DesignArtifactSpec(
    stage="erd",
    model_key="erd_bce_classes",
    content_key="erd_puml",
    valid_key="erd_syntax_valid",
    errors_key="erd_syntax_errors",
    feedback_key="erd_feedback",
    empty="",
    extract=_seed_erd_model,
    revise=lambda current, feedback, state, targets: revise_erd_classes(
        current_bce=current or state.get("extracted_bce_classes", {}),
        feedback=feedback,
        scenario_text=usecase_spec_text(state),
        targets=targets,
    ),
    render=generate_erd_from_bce_json,
    validate=validate_puml_artifact,
    # ERD 는 클래스 BCE 의 투영이라 직접 지목하지 않는다 — 클래스를 고치면 따라온다.
    elements={},
    # ERD 모델은 클래스 BCE 의 **사본**이라 독립적으로 편집된다. 클래스 쪽이 통과했다는
    # 것이 이쪽의 보증이 아니므로 여기서 다시 본다. 그리고 검사 대상이 BCE 만이 아니다 —
    # `erd_findings` 가 사상을 돌려 나온 논리 데이터 모델(테이블·키·외래키)까지 판정한다.
    check=erd_findings,
    check_key="erd_check",
)


def _finalize_deployment_diagram(state: ArchitectureState) -> dict[str, Any]:
    candidate = dict(state.get("deployment_diagram_model") or {})
    bundle = build_deployment_diagram_bundle(
        candidate,
        dict(state.get("resource_spec") or {}),
        planning_inputs={
            "refined_requirements": state.get("refined_requirements") or [],
            "capability_contract": dict(state.get("capability_contract") or {}),
            "resource_intake": dict(state.get("resource_intake") or {}),
            "usecase_spec": state.get("usecase_spec") or {},
            "class_model": state.get("extracted_bce_classes") or {},
            "sequence_model": state.get("sequence_diagram_model") or {},
            "api_spec": state.get("api_spec") or {},
            "erd_model": state.get("erd_bce_classes") or state.get("erd_puml") or {},
            "artifact_versions": dict(state.get("artifact_versions") or {}),
            "additional_planning_facts": list(
                state.get("deployment_planning_facts") or []
            ),
        },
    )
    hydrated = hydrate_deployment_diagram_bundle(bundle)
    return {
        **hydrated,
        "deployment_diagram_provisioning_puml": (
            deployment_bundle_provisioning_puml(bundle)
        ),
    }

DEPLOYMENT_DIAGRAM_SPEC = DesignArtifactSpec(
    stage="deployment_diagram",
    model_key="deployment_diagram_model",
    content_key="deployment_diagram_puml",
    valid_key="deployment_diagram_syntax_valid",
    errors_key="deployment_diagram_syntax_errors",
    feedback_key="deployment_diagram_feedback",
    empty="",
    extract=lambda state: extract_deployment_model(
        usecase_spec_text(state),
        state.get("class_diagram_puml", ""),
        state.get("sequence_diagram_puml", ""),
        state.get("api_spec", {}),
        state.get("erd_puml", ""),
        refined_requirements=state.get("refined_requirements") or [],
        capability_contract=state.get("capability_contract") or {},
        resource_intake=state.get("resource_intake") or {},
        class_model=state.get("extracted_bce_classes") or {},
        sequence_model=state.get("sequence_diagram_model") or {},
        erd_model=state.get("erd_bce_classes") or {},
        deployment_planning_facts=list(state.get("deployment_planning_facts") or []),
    ),
    revise=lambda current, feedback, state, targets: revise_deployment_model(
        current, feedback, _design_context(state), targets
    ),
    render=lambda _model: "",
    render_with_state=lambda _model, state: deployment_bundle_runtime_puml(
        dict(state.get("deployment_diagram_bundle") or {})
    ),
    validate=validate_puml_artifact,
    elements={
        "workloads": lambda n: n.get("id", ""),
        "externalDependencies": lambda n: n.get("id", ""),
        "connections": lambda n: n.get("id", ""),
        "constraints": lambda n: n.get("id", ""),
    },
    finalize=_finalize_deployment_diagram,
)

#: 스테이지 순서대로. 스펙 하나가 곧 한 산출물의 전부다.
DESIGN_SPECS: dict[str, DesignArtifactSpec] = {
    spec.stage: spec
    for spec in (
        CLASS_DIAGRAM_SPEC,
        SEQUENCE_DIAGRAM_SPEC,
        API_SPEC_SPEC,
        ERD_SPEC,
        DEPLOYMENT_DIAGRAM_SPEC,
    )
}

#: 스테이지별 사용자 피드백을 실어 나르는 상태 키.
FEEDBACK_KEYS: dict[str, str] = {
    stage: spec.feedback_key for stage, spec in DESIGN_SPECS.items()
}


def _add_stage_tail(
    builder: StateGraph, spec: DesignArtifactSpec, entry_node: str
) -> None:
    """공유 꼬리: 모델 → [대사] → [규칙 검사] → [최종 강제] → 렌더 → END.

    **검사 노드는 `check_key`를 가진 스펙에만 생긴다.** 규칙이 아직 없는 산출물에 빈
    노드를 달면 그래프 그림이 "검사한다"고 거짓말을 한다. 시퀀스는 이 노드 안에서
    결정론적 findings를 만들고, 유계 LLM 수리 후 같은 검출기로 후보를 다시 검사한다.

    **대사 노드는 `reconcile` 후크를 가진 스펙에만 생긴다.** 시퀀스 다이어그램이 이것으로
    클래스 다이어그램에 빠진 메서드를 보강한다.

    **최종 판정 노드는 `finalize` 후크를 가진 스펙에만 생긴다.** 의미 수리에서 모델이
    다시 바뀌어도 시퀀스 호출이 실제 수신 클래스 메서드인지 렌더 직전에 확인한다.
    위반 모델은 버리지 않고 게이트 수리를 위해 보존하되 `renderable=false`로 표시하여
    PlantUML 이미지가 정상 산출물처럼 노출되지 않게 한다.

    렌더가 문법 유효성을 보장하므로 **문법** 수리 루프는 여전히 없다. 검사 노드가 도는
    루프는 문법이 아니라 **의미**를 보고, 텍스트가 아니라 모델을 고치며, 위반 수가 줄지
    않으면 멈춘다(`nodes/artifact.py`의 `check_node` 참조).
    """
    current_node = entry_node

    if spec.reconcile:
        reconcile = f"reconcile_{spec.stage}"
        builder.add_node(reconcile, spec.reconcile)
        builder.add_edge(current_node, reconcile)
        current_node = reconcile

    if spec.check_key:
        check = f"check_{spec.stage}"
        builder.add_node(check, check_node(spec))
        builder.add_edge(current_node, check)
        current_node = check

    if spec.finalize:
        finalize = f"finalize_{spec.stage}"
        builder.add_node(finalize, spec.finalize)
        builder.add_edge(current_node, finalize)
        current_node = finalize

    render = f"render_{spec.stage}"
    builder.add_node(render, render_node(spec))
    builder.add_edge(current_node, render)

    builder.add_edge(render, END)


def build_generation_graph(spec: DesignArtifactSpec):
    """생성: 앞선 산출물 → 구조화 모델 추출 → [규칙 검사] → 렌더."""
    builder = StateGraph(ArchitectureState)
    entry = f"extract_{spec.stage}"
    builder.add_node(entry, extract_node(spec))
    builder.add_edge(START, entry)
    _add_stage_tail(builder, spec, entry)
    return builder.compile()


def build_feedback_graph(spec: DesignArtifactSpec):
    """피드백: 사용자 피드백을 모델에 적용 → [규칙 검사] → 같은 렌더.

    LLM은 구조화 모델만 편집하고 렌더된 텍스트는 만지지 않으므로, 모델과 산출물이
    어긋나지 않는다. 생성 그래프와 꼬리(`_add_stage_tail`)를 그대로 공유한다 — 피드백으로
    만든 판도 생성한 판과 **같은 검사**를 거쳐야 하기 때문이다.
    """
    builder = StateGraph(ArchitectureState)
    entry = f"revise_{spec.stage}"
    builder.add_node(entry, revise_node(spec))
    builder.add_edge(START, entry)
    _add_stage_tail(builder, spec, entry)
    return builder.compile()


def build_design_subgraphs() -> dict[str, dict[str, Any]]:
    """{스테이지: {"generate": 컴파일된 서브그래프, "feedback": 컴파일된 서브그래프}}."""
    return {
        stage: {
            "generate": build_generation_graph(spec),
            "feedback": build_feedback_graph(spec),
        }
        for stage, spec in DESIGN_SPECS.items()
    }


#: 앱 전역에서 공유하는 컴파일된 서브그래프(모듈 로드 시 1회). 파이프라인 그래프가
#: 이것을 배선한다 — 요청마다 다시 컴파일할 이유가 없다.
DESIGN_SUBGRAPHS = build_design_subgraphs()
