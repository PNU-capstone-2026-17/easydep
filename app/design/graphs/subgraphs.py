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
import json
from collections.abc import Callable
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from app.design.contracts.api_spec import ApiSpecModel
from app.design.knowledge.detectors import (
    Finding as ArtifactFinding,
)
from app.design.knowledge.detectors import (
    api_spec_findings,
    erd_findings,
)
from app.design.nodes.artifact import (
    DesignArtifactSpec,
    check_node,
    extract_node,
    render_node,
    revise_node,
)
from app.design.schemas.architecture_state import ArchitectureState, usecase_spec_text
from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.projection import build_openapi_from_model
from app.design.services.api_spec.service import (
    generate_api_spec_model as extract_api_spec_model,
)
from app.design.services.api_spec.service import revise_api_spec_model
from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.service import (
    generate_class_model,
    resume_class_model,
    revise_class_model,
)
from app.design.services.class_diagram.validation.model import validate_class_model
from app.design.services.common.validation import validate_api_spec, validate_puml_artifact
from app.design.services.deployment_diagram.bundle import (
    build_deployment_diagram_bundle,
    hydrate_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.models import WorkloadGraph
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.service import (
    generate_workload_graph as extract_deployment_model,
)
from app.design.services.deployment_diagram.service import (
    revise_workload_graph as revise_deployment_model,
)
from app.design.services.erd.plantuml import render_logical_model
from app.design.services.erd.projection import project_logical_model
from app.design.services.erd.service import revise_erd_model as revise_erd_classes
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.services.sequence_diagram.projection import (
    project_sequence_model,
)
from app.design.services.sequence_diagram.validation import validate_sequence_model

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

# 수락 단위 cache는 process에만 존재한다. graph state와 checkpoint에는 기록하지 않는다.
_CLASS_DESIGN_ACCEPTED_UNIT_CACHE = ProcessLocalAcceptedUnitCache(capacity=256)


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
    source = copy.deepcopy(
        state.get("extracted_bce_classes") or state.get("erd_bce_classes") or {}
    )
    return _stored_class_model(source).model_dump(by_alias=True)


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


def _design_context(state: ArchitectureState, stage: str) -> str:
    """한 설계 산출물을 수정하는 데 필요한 상류 문맥만 직렬화한다.

    현재 산출물은 ``revision_messages``가 별도로 넣는다. 여기에도 포함하면 모든 repair
    요청에서 가장 큰 문서를 중복 전송하고 sequence/API 수정에 무관한 하류 산출물까지
    다시 보내게 된다.
    """
    sections = ["[Use Case Specification]\n" + usecase_spec_text(state)]
    if stage in {"sequence_diagram", "api_spec", "deployment_diagram"}:
        sections.append("[Class Diagram]\n" + state.get("class_diagram_puml", ""))
    if stage in {"api_spec", "deployment_diagram"}:
        sections.append("[Sequence Diagrams]\n" + state.get("sequence_diagram_puml", ""))
    if stage == "deployment_diagram":
        sections.extend(
            [
                "[API Spec]\n" + str(state.get("api_spec", {})),
                "[ERD]\n" + state.get("erd_puml", ""),
            ]
        )
    return "\n\n".join(sections)


def _sequence_revision_context(
    state: ArchitectureState, targets: set[str] | None
) -> str:
    """관련 없는 유스케이스를 재전송하지 않는 sequence repair 문맥을 만든다.

    자동 validation repair에는 구체적인 영향 use-case ID가 있다. 전체 요구사항은 대상
    다이어그램보다 훨씬 클 수 있으므로 actor는 유효 participant 정의를 위해 모두 유지하되
    use case와 상세 scenario는 선택 ID로 좁힌다. target이 없는 사용자 feedback 수정은
    전체 문맥을 유지한다.
    """
    specification = state.get("usecase_spec")
    if not targets or not isinstance(specification, dict):
        return _design_context(state, "sequence_diagram")

    scoped_specification = dict(specification)
    for field, id_field in (("use_cases", "id"), ("use_case_specs", "use_case_id")):
        values = specification.get(field)
        if isinstance(values, list):
            scoped_specification[field] = [
                value
                for value in values
                if isinstance(value, dict)
                and str(value.get(id_field) or "").strip() in targets
            ]
    return "\n\n".join(
        [
            "[Use Case Specification]\n"
            + json.dumps(scoped_specification, ensure_ascii=False, indent=2),
            "[Class Diagram]\n" + state.get("class_diagram_puml", ""),
        ]
    )


def _class_scenario(state: ArchitectureState) -> dict[str, Any]:
    scenario = state.get("usecase_spec") or {}
    if not isinstance(scenario, dict):
        return {}
    return {**scenario, "relationships": state.get("relationships") or scenario.get("relationships") or {}}


def _class_index(state: ArchitectureState):
    """그래프 상태의 유스케이스 JSON을 한 번만 수락된 인덱스로 만든다."""
    return build_scenario_index(_class_scenario(state))


def _stored_class_model(value: object) -> BCEModel:
    """상태에 저장된 JSON을 서비스 경계의 BCE 계약으로 검증한다."""
    if isinstance(value, BCEModel):
        return value
    if not isinstance(value, dict):
        raise TypeError("stored class model must be an object")
    return BCEModel.model_validate(value)


def _stored_workload_graph(value: object) -> WorkloadGraph:
    """상태의 deployment candidate를 typed WorkloadGraph 계약으로 검증한다."""

    if isinstance(value, WorkloadGraph):
        return value
    if not isinstance(value, dict):
        raise TypeError("stored workload graph must be an object")
    return WorkloadGraph.model_validate(value)


def _extract_class_model(state: ArchitectureState) -> dict[str, Any]:
    """raw graph state를 클래스 typed service에 연결하고 기존 JSON shape로 반환한다.

    유효한 기존 model이 있으면 resume, 없으면 generate를 호출한다. raw use-case JSON 해석과
    ``model_dump(by_alias=True)``는 이 adapter에만 있어 service 내부 타입이 체크포인트에
    노출되지 않는다.
    """

    scenario = _class_scenario(state)
    if not scenario.get("use_case_specs"):
        raise ValueError("class design requires structured use-case specifications")
    index = _class_index(state)
    current = state.get("extracted_bce_classes")
    if isinstance(current, dict) and current.get("Classes"):
        return resume_class_model(
            index,
            _stored_class_model(current),
            cache=_CLASS_DESIGN_ACCEPTED_UNIT_CACHE,
        ).model_dump(by_alias=True)
    return generate_class_model(
        index,
        cache=_CLASS_DESIGN_ACCEPTED_UNIT_CACHE,
    ).model_dump(by_alias=True)


def _revise_class_state(
    current: dict[str, Any],
    feedback: str,
    state: ArchitectureState,
    targets: set[str],
) -> dict[str, Any]:
    """그래프의 raw 현재값·피드백·target을 typed revise API에 맞춰 변환한다.

    반환 직전 기존 alias JSON으로 직렬화하므로 UI/API가 보던 key는 변경되지 않는다.
    """
    return revise_class_model(
        _stored_class_model(current),
        _class_index(state),
        feedback,
        targets,
        cache=_CLASS_DESIGN_ACCEPTED_UNIT_CACHE,
    ).model_dump(by_alias=True)


def _class_model_findings(
    model: dict[str, Any], state: ArchitectureState,
) -> list[ArtifactFinding]:
    """typed 클래스 검증 보고서를 graph artifact finding 계약으로 투영한다."""
    index = _class_index(state)
    accepted = _stored_class_model(model)
    report = validate_class_model(accepted, index)
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    return [
        ArtifactFinding(
            rule_id=finding.rule_id,
            message=finding.message,
            location=finding.location,
            requires_user_input=finding.requires_user_input,
            origin=finding.origin,
        )
        for finding in report.findings
    ]


def _sequence_model_findings(
    model: dict[str, Any], state: ArchitectureState,
) -> list[ArtifactFinding]:
    """결정론적 시퀀스 투영의 스키마·참조·클래스 버전만 확인한다."""

    report = validate_sequence_model(model, dict(state))
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    return [
        ArtifactFinding(
            rule_id=finding.rule_id,
            message=finding.message,
            location=finding.location,
            requires_user_input=finding.requires_user_input,
            origin=finding.origin,
        )
        for finding in report.findings
    ]


def _revise_sequence_state(
    _current: dict[str, Any],
    feedback: str,
    state: ArchitectureState,
    targets: set[str],
) -> dict[str, Any]:
    """상호작용 원본인 클래스 모델을 국소 수정한 뒤 시퀀스를 다시 투영한다.

    시퀀스에는 독립 LLM 편집 경로가 없다. feedback은 class inventory/operation/collaboration
    수정 대상에 적용되고 class validation과 PlantUML 검증을 통과한 뒤 코드로 새
    ``SequenceCollection``을 만든다.
    """

    revised_class = revise_class_model(
        _stored_class_model(state.get("extracted_bce_classes") or {}),
        _class_index(state),
        feedback,
        targets,
        cache=_CLASS_DESIGN_ACCEPTED_UNIT_CACHE,
    )
    revised_payload = revised_class.model_dump(by_alias=True)
    class_puml = generate_plantuml_from_bce_json(revised_payload)
    class_validation = validate_puml_artifact(class_puml)
    class_findings = _class_model_findings(revised_payload, state)
    if class_findings:
        raise ValueError(
            "sequence feedback produced an invalid class interaction contract: "
            + "; ".join(finding.message for finding in class_findings)
        )
    return {
        "extracted_bce_classes": revised_payload,
        "class_diagram_puml": class_puml,
        "class_diagram_syntax_valid": class_validation["syntax_valid"],
        "class_diagram_syntax_errors": class_validation["syntax_errors"],
        "class_diagram_check": {
            "findings": [], "repair_iters": 0, "stopped": "clean",
        },
        "sequence_diagram_model": project_sequence_model(
            _class_index(state), revised_class, class_puml,
        ).model_dump(),
        "revised_upstream_stages": ["class_diagram"],
    }


def _project_sequence_state(state: ArchitectureState) -> dict[str, Any]:
    """graph의 use-case/class JSON과 PlantUML 버전을 typed 시퀀스 투영에 연결한다.

    이 adapter는 LLM을 호출하지 않으며 ``SequenceCollection.model_dump`` 결과만 state에
    기록한다.
    """
    return project_sequence_model(
        _class_index(state),
        _stored_class_model(state.get("extracted_bce_classes") or {}),
        state.get("class_diagram_puml", ""),
    ).model_dump()


def _state_check(
    callback: Callable[[dict[str, Any], dict[str, Any]], list[ArtifactFinding]],
) -> Callable[[dict[str, Any], ArchitectureState], list[ArtifactFinding]]:
    """런타임 함수 정체성을 보존하며 dict 검사를 TypedDict 경계에 맞춘다."""
    return cast(
        Callable[[dict[str, Any], ArchitectureState], list[ArtifactFinding]],
        callback,
    )


def _stored_api_model(value: object) -> ApiSpecModel:
    """graph state의 API JSON을 typed service 경계에서 검증한다."""

    if not isinstance(value, (dict, ApiSpecModel)):
        raise TypeError("stored API model must be an object")
    return value if isinstance(value, ApiSpecModel) else ApiSpecModel.model_validate(value)


def _api_class_model(state: ArchitectureState) -> BCEModel:
    """API 생성·수정이 공유하는 승인 클래스 모델을 검증한다."""

    return _stored_class_model(state.get("extracted_bce_classes") or {})


def _extract_api_state(state: ArchitectureState) -> dict[str, Any]:
    """raw graph state를 typed API proposal service에 연결해 저장 JSON으로 반환한다."""

    bce_model = _api_class_model(state)
    proposed = extract_api_spec_model(
        usecase_spec_text(state), bce_model,
    )
    return _stored_api_model(proposed).model_dump()


def _revise_api_state(
    current: dict[str, Any],
    feedback: str,
    state: ArchitectureState,
    targets: set[str],
) -> dict[str, Any]:
    """현재 API JSON과 graph feedback을 typed revision service에 연결한다."""

    bce_model = _api_class_model(state)
    revised = revise_api_spec_model(
        _stored_api_model(current),
        feedback,
        usecase_spec_text(state),
        bce_model,
        targets,
    )
    return _stored_api_model(revised).model_dump()


def _render_api_model(model: dict[str, Any]) -> dict[str, Any]:
    """저장 JSON을 검증한 뒤 결정론적 OpenAPI projection만 호출한다."""

    return build_openapi_from_model(_stored_api_model(model))


def _api_model_findings(
    model: dict[str, Any], state: ArchitectureState,
) -> list[ArtifactFinding]:
    """저장 스키마를 확인한 뒤 실제 차단 검사 결과만 반환한다."""

    payload = _stored_api_model(model).model_dump()
    return list(api_spec_findings(payload, cast(dict[str, Any], state)))


def _revise_erd_state(
    current: dict[str, Any],
    feedback: str,
    state: ArchitectureState,
    targets: set[str],
) -> dict[str, Any]:
    """graph의 ERD BCE JSON을 typed revision service에 연결해 alias JSON으로 반환한다."""

    source = current or state.get("extracted_bce_classes") or {}
    revised = revise_erd_classes(
        _stored_class_model(source),
        feedback,
        usecase_spec_text(state),
        targets,
    )
    return _stored_class_model(revised).model_dump(by_alias=True)


def _render_erd_model(model: dict[str, Any]) -> str:
    """검증된 ERD BCE에서 logical model과 PlantUML을 순수 투영한다."""

    return render_logical_model(project_logical_model(_stored_class_model(model)))


CLASS_DIAGRAM_SPEC = DesignArtifactSpec(
    stage="class_diagram",
    model_key="extracted_bce_classes",
    content_key="class_diagram_puml",
    valid_key="class_diagram_syntax_valid",
    errors_key="class_diagram_syntax_errors",
    feedback_key="class_diagram_feedback",
    empty="",
    extract=_extract_class_model,
    revise=_revise_class_state,
    render=generate_plantuml_from_bce_json,
    validate=validate_puml_artifact,
    elements={
        "Classes": lambda c: c.get("className", ""),
        "DataTypes": lambda item: item.get("name", ""),
        "Collaborations": lambda item: item.get("collaborationId", ""),
    },
    # typed ValidationReport를 artifact finding으로 바꾼 뒤 기존 check node가 소비한다.
    # repair 여부와 예산은 validator가 아니라 graph/service orchestration이 결정한다.
    check=_class_model_findings,
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
    extract=_project_sequence_state,
    revise=lambda _current, _feedback, state, _targets: _project_sequence_state(state),
    render=generate_sequence_from_model,
    validate=validate_puml_artifact,
    elements={
        "Diagrams": lambda d: d.get("use_case_id", ""),
        "Participants": lambda p: p.get("name", ""),
        # 메시지에는 id 가 없다 — 추적표가 쓰는 것과 같은 조합으로 가리킨다.
        "Messages": _message_key,
    },
    check=_sequence_model_findings,
    check_key="sequence_diagram_check",
    revise_state=_revise_sequence_state,
)

API_SPEC_SPEC = DesignArtifactSpec(
    stage="api_spec",
    model_key="api_spec_model",
    content_key="api_spec",
    valid_key="api_spec_syntax_valid",
    errors_key="api_spec_syntax_errors",
    feedback_key="api_spec_feedback",
    empty={},
    extract=_extract_api_state,
    revise=_revise_api_state,
    render=_render_api_model,
    validate=validate_api_spec,
    elements={
        "Endpoints": _endpoint_key,
        "Schemas": lambda s: s.get("name", ""),
    },
    check=_api_model_findings,
    check_key="api_spec_check",
    repair=_revise_api_state,
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
    revise=_revise_erd_state,
    render=_render_erd_model,
    validate=validate_puml_artifact,
    # ERD 는 클래스 BCE 의 투영이라 직접 지목하지 않는다 — 클래스를 고치면 따라온다.
    elements={},
    # ERD 모델은 클래스 BCE 의 **사본**이라 독립적으로 편집된다. 클래스 쪽이 통과했다는
    # 것이 이쪽의 보증이 아니므로 여기서 다시 본다. 그리고 검사 대상이 BCE 만이 아니다 —
    # `erd_findings` 가 사상을 돌려 나온 논리 데이터 모델(테이블·키·외래키)까지 판정한다.
    check=_state_check(erd_findings),
    check_key="erd_check",
    repair=_revise_erd_state,
)


def _finalize_deployment_diagram(state: ArchitectureState) -> dict[str, Any]:
    candidate = _stored_workload_graph(
        state.get("deployment_diagram_model") or {}
    ).model_dump()
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


def _extract_deployment_state(state: ArchitectureState) -> dict[str, Any]:
    """graph 원시 상태를 canonical WorkloadGraph 생성 서비스에 연결한다."""

    generated = extract_deployment_model(
        usecase_spec_text(state),
        dict(state.get("api_spec") or {}),
        refined_requirements=state.get("refined_requirements") or [],
        capability_contract=dict(state.get("capability_contract") or {}),
        resource_intake=dict(state.get("resource_intake") or {}),
        class_model=state.get("extracted_bce_classes") or {},
        sequence_model=state.get("sequence_diagram_model") or {},
        erd_model=state.get("erd_bce_classes") or {},
        deployment_planning_facts=list(
            state.get("deployment_planning_facts") or []
        ),
    )
    return _stored_workload_graph(generated).model_dump()


def _revise_deployment_state(
    current: dict[str, Any],
    feedback: str,
    state: ArchitectureState,
    targets: set[str],
) -> dict[str, Any]:
    """저장 JSON을 검증하고 typed WorkloadGraph 수정 결과만 다시 dump한다."""

    revised = revise_deployment_model(
        _stored_workload_graph(current),
        feedback,
        _design_context(state, "deployment_diagram"),
        targets,
    )
    return _stored_workload_graph(revised).model_dump()

DEPLOYMENT_DIAGRAM_SPEC = DesignArtifactSpec(
    stage="deployment_diagram",
    model_key="deployment_diagram_model",
    content_key="deployment_diagram_puml",
    valid_key="deployment_diagram_syntax_valid",
    errors_key="deployment_diagram_syntax_errors",
    feedback_key="deployment_diagram_feedback",
    empty="",
    extract=_extract_deployment_state,
    revise=_revise_deployment_state,
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


def _state_node(
    callback: Callable[[ArchitectureState], dict[str, Any]],
) -> Callable[[ArchitectureState], ArchitectureState]:
    """LangGraph의 부분 상태 업데이트를 typed 노드 경계에서 명시한다."""
    return cast(Callable[[ArchitectureState], ArchitectureState], callback)


def _add_state_node(
    builder: StateGraph[ArchitectureState, None, ArchitectureState, ArchitectureState],
    name: str,
    callback: Callable[[ArchitectureState], ArchitectureState],
) -> None:
    """LangGraph stubs가 부분 TypedDict 상태 노드를 표현하지 못하는 지점을 격리한다."""
    builder.add_node(name, callback)  # type: ignore[call-overload]


def _add_stage_tail(
    builder: StateGraph[ArchitectureState, None, ArchitectureState, ArchitectureState],
    spec: DesignArtifactSpec,
    entry_node: str,
) -> None:
    """공유 꼬리: 모델 → [대사] → [규칙 검사] → [최종 강제] → 렌더 → END.

    **검사 노드는 `check_key`를 가진 스펙에만 생긴다.** 규칙이 아직 없는 산출물에 빈
    노드를 달면 그래프 그림이 "검사한다"고 거짓말을 한다. 시퀀스는 이 노드 안에서
    결정론적 findings를 만들고, 이력을 누적한 LLM 자동 수리 후 같은 검출기로 다시 검사한다.

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
        _add_state_node(builder, reconcile, _state_node(spec.reconcile))
        builder.add_edge(current_node, reconcile)
        current_node = reconcile

    if spec.check_key:
        check = f"check_{spec.stage}"
        _add_state_node(builder, check, _state_node(check_node(spec)))
        builder.add_edge(current_node, check)
        current_node = check

    if spec.finalize:
        finalize = f"finalize_{spec.stage}"
        _add_state_node(builder, finalize, _state_node(spec.finalize))
        builder.add_edge(current_node, finalize)
        current_node = finalize

    render = f"render_{spec.stage}"
    _add_state_node(builder, render, _state_node(render_node(spec)))
    builder.add_edge(current_node, render)

    builder.add_edge(render, END)


def build_generation_graph(spec: DesignArtifactSpec):
    """생성: 앞선 산출물 → 구조화 모델 추출 → [규칙 검사] → 렌더."""
    builder = StateGraph(ArchitectureState)
    entry = f"extract_{spec.stage}"
    _add_state_node(builder, entry, _state_node(extract_node(spec)))
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
    _add_state_node(builder, entry, _state_node(revise_node(spec)))
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
