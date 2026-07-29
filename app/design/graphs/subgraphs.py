"""5개 설계 스테이지의 스펙과 서브그래프 — 골격은 하나, 다른 것은 스펙뿐.

상위 그래프(`design_graph.py`)의 노드명은 산출물 이름이고, 세부 작업은 서브그래프
내부 노드로 캡슐화된다. 다섯 산출물이 모두 같은 골격을 따른다:

  생성:   extract_{stage} → convert_{stage} → validate_{stage} → END
  피드백: revise_{stage}  → convert_{stage} → validate_{stage} → END

예전에는 여기가 둘로 갈려 있었다. 클래스·ERD는 구조화된 모델에서 결정론적으로 렌더됐고,
시퀀스·API·배포는 LLM이 PlantUML/JSON을 직접 써서 validate → repair 루프를 달고 있었다.
지금은 다섯 모두 LLM에게 **구조화 모델만** 받고 산출물은 결정론적으로 렌더한다. 그래서
수리 루프가 사라졌고(무한 루프 위험도 함께), 피드백은 언제나 모델을 편집하며, 모델과
산출물이 어긋날 수 없다. 골격의 근거는 `app/design/nodes/artifact.py` 참조.

서브그래프는 체크포인터 없이 컴파일된다 — 상위 그래프의 세이버가 트리 전체를 관장한다.
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.design.nodes.artifact import (
    DesignArtifactSpec,
    convert_node,
    extract_node,
    revise_node,
    validate_node,
)
from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.services.api_spec.extractor import extract_api_spec_model
from app.design.services.api_spec.openapi import build_openapi_from_model
from app.design.services.api_spec.reviser import revise_api_spec_model
from app.design.services.class_diagram.extractor import extract_bce_classes_from_scenario
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.reviser import revise_bce_classes
from app.design.services.common.validation import validate_api_spec, validate_puml_artifact
from app.design.services.deployment_diagram.extractor import extract_deployment_model
from app.design.services.deployment_diagram.plantuml import generate_deployment_from_model
from app.design.services.deployment_diagram.reviser import revise_deployment_model
from app.design.services.erd.plantuml import generate_erd_from_bce_json
from app.design.services.erd.reviser import revise_erd_classes
from app.design.services.sequence_diagram.extractor import extract_sequence_model
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.services.sequence_diagram.reviser import revise_sequence_model

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
    """
    return state.get("extracted_bce_classes") or state.get("erd_bce_classes") or {}


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
            "[Sequence Diagram]\n" + state.get("sequence_diagram_puml", ""),
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
        usecase_spec_text(state),
        state.get("class_diagram_puml", ""),
    ),
    revise=lambda current, feedback, state, targets: revise_sequence_model(
        current, feedback, _design_context(state), targets
    ),
    render=generate_sequence_from_model,
    validate=validate_puml_artifact,
    elements={
        "Participants": lambda p: p.get("name", ""),
        # 메시지에는 id 가 없다 — 추적표가 쓰는 것과 같은 조합으로 가리킨다.
        "Messages": _message_key,
    },
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
)

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
    ),
    revise=lambda current, feedback, state, targets: revise_deployment_model(
        current, feedback, _design_context(state), targets
    ),
    render=generate_deployment_from_model,
    validate=validate_puml_artifact,
    elements={
        "Nodes": lambda n: n.get("name", ""),
        "Artifacts": lambda a: a.get("name", ""),
    },
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


def _add_convert_and_validate(
    builder: StateGraph, spec: DesignArtifactSpec, entry_node: str
) -> None:
    """생성과 피드백이 공유하는 꼬리: 모델 → 결정론적 변환 → (트립와이어) 검증 → END.

    변환이 유효성을 보장하므로 문법 수리 루프는 없다.
    """
    convert = f"convert_{spec.stage}"
    validate = f"validate_{spec.stage}"
    builder.add_node(convert, convert_node(spec))
    builder.add_node(validate, validate_node(spec))
    builder.add_edge(entry_node, convert)
    builder.add_edge(convert, validate)
    builder.add_edge(validate, END)


def build_generation_graph(spec: DesignArtifactSpec):
    """생성: 앞선 산출물 → 구조화 모델 추출 → 변환 → 검증."""
    builder = StateGraph(ArchitectureState)
    entry = f"extract_{spec.stage}"
    builder.add_node(entry, extract_node(spec))
    builder.add_edge(START, entry)
    _add_convert_and_validate(builder, spec, entry)
    return builder.compile()


def build_feedback_graph(spec: DesignArtifactSpec):
    """피드백: 사용자 피드백을 모델에 적용 → 같은 변환 → 검증.

    LLM은 구조화 모델만 편집하고 렌더된 텍스트는 만지지 않으므로, 모델과 산출물이
    어긋나지 않는다. 생성 그래프와 convert/validate 노드를 공유한다.
    """
    builder = StateGraph(ArchitectureState)
    entry = f"revise_{spec.stage}"
    builder.add_node(entry, revise_node(spec))
    builder.add_edge(START, entry)
    _add_convert_and_validate(builder, spec, entry)
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
