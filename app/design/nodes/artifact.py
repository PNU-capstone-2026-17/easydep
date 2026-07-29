"""다섯 설계 산출물이 공유하는 스테이지 골격 — 스펙 하나로 노드 네 개를 찍어낸다.

**하나의 골격.** 예전에는 두 계열이 있었다. 클래스·ERD는 구조화된 모델을 받아 결정론적
으로 렌더했고(수리 루프 없음), 시퀀스·API·배포는 LLM이 PlantUML/JSON을 직접 써서
validate → repair 루프가 필요했다. 지금은 다섯 모두 이 골격을 따른다:

    생성:   extract_{stage} → convert_{stage} → validate_{stage} → END
    피드백: revise_{stage}  → convert_{stage} → validate_{stage} → END

  extract  — LLM이 **구조화 모델**을 내놓는다(자유 텍스트 아님). ERD만 예외로 LLM 대신
             클래스 다이어그램의 BCE에서 시드한다.
  revise   — 사용자 피드백을 **모델에** 적용한다. 렌더된 텍스트는 절대 편집하지 않는다.
  convert  — 모델 → 산출물. 결정론적이고, 입력을 중화하므로 구성에 의해 유효하다.
  validate — 유효성을 상태에 기록한다. **트립와이어이지 수리 트리거가 아니다** —
             convert가 유효성을 보장하므로 여기서 실패했다면 변환기의 결함이다.

이 골격의 값어치는 세 가지다. (1) 모델과 산출물이 어긋날 수 없다 — 산출물은 언제나
모델의 순수한 투영이다. (2) 문법 오류가 원천적으로 없으므로 수리 루프와 그 무한 루프
위험이 사라진다. (3) 피드백이 무엇을 고쳤는지 구조화된 diff로 볼 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.design.schemas.architecture_state import ArchitectureState


@dataclass(frozen=True)
class DesignArtifactSpec:
    """한 산출물의 스테이지를 만드는 데 필요한 전부.

    산출물마다 다른 것은 (상태 키 넷, 함수 넷)뿐이고 그래프 골격은 같다. 그래서 새
    산출물을 추가하는 일은 스펙 하나를 적는 일이 된다.
    """

    stage: str
    #: 진실의 원천 — LLM이 편집하고 저장소가 저장하는 구조화 모델.
    model_key: str
    #: 모델에서 렌더된 산출물(PlantUML 문자열 또는 OpenAPI dict).
    content_key: str
    valid_key: str
    errors_key: str
    #: 사용자 피드백을 이 스테이지로 실어 나르는 전이(transient) 상태 키.
    feedback_key: str
    #: 렌더 결과의 "아직 없음" 값 — PlantUML은 "", JSON은 {}.
    empty: Any
    #: 상태 → 모델. 앞선 산출물을 재료로 쓰므로 개별 인자가 아니라 상태를 통째로 받는다.
    extract: Callable[[ArchitectureState], Any]
    #: (현재 모델, 피드백, 상태, 대상) → 수정된 모델.
    #: 대상이 비면 전체 수정, 있으면 그 항목만 고치라고 프롬프트에 적는다. 다만 지시는
    #: 지시일 뿐이라 **믿지 않는다** — 실제 보장은 merge_model 이 한다.
    revise: Callable[[Any, str, ArchitectureState, set[str]], Any]
    #: 모델 → 산출물. 결정론적이어야 한다.
    render: Callable[[Any], Any]
    #: 산출물 → {syntax_valid, syntax_errors}.
    validate: Callable[[Any], dict[str, Any]]
    #: 모델 안에서 개별 항목을 가리키는 법: {목록 필드 이름: 그 항목의 이름을 뽑는 함수}.
    #: 여기서 나오는 이름은 **추적표의 element 와 같아야 한다** — 그래야 "api_spec:Order 를
    #: 고쳐줘"가 모델의 어느 항목인지 통한다. 비어 있으면 지목 수정을 지원하지 않는다.
    elements: dict[str, Callable[[dict], str]] = field(default_factory=dict)


def merge_targeted(
    original: list[dict],
    revised: list[dict],
    key_of: Callable[[dict], str],
    targets: set[str],
) -> list[dict]:
    """대상 항목만 revised 를 쓰고, 나머지는 original 을 **그대로** 둔다.

    바뀔 수 있는 것은 딱 하나다: **대상 항목의 내용.** 목록의 길이도, 순서도, 다른
    항목의 글자 하나도 안 바뀐다. 비대상에 대해서는 LLM 출력을 아예 읽지 않으므로
    "지시를 어기고 다른 데를 고쳤다"가 **구성에 의해 불가능**하다 — 검사해서 막는 게
    아니라 손댈 수 없게 만든다. sanitize(services/*/plantuml.py)와 같은 철학이다.

    **추가를 안 받는 이유.** 이름 변경은 (삭제 + 추가)처럼 보인다. 삭제 쪽은 보호되니
    추가만 통과하면 결국 **원본과 개명본이 둘 다 남는다.** "Order 에 배송지를 넣어줘"가
    Address 스키마를 부르는 정당한 경우도 있지만, 그건 그 스키마를 지목해 따로 요청하면
    된다 — 조용히 늘어나는 것보다 낫다.

    **빠뜨린 것을 삭제로 안 보는 이유.** 대상이 revised 에 없을 때 그것이 "지우라는 뜻"
    인지 "LLM 이 빠뜨린 것"인지 구별할 수 없다. 빠뜨림이 훨씬 흔하므로 원본을 지킨다.
    삭제가 필요하면 그건 다른 조작이어야 한다.
    """
    revised_by_key = {key_of(item): item for item in revised}
    return [
        revised_by_key[key_of(item)]
        if key_of(item) in targets and key_of(item) in revised_by_key
        else item
        for item in original
    ]


def merge_model(
    spec: DesignArtifactSpec,
    original: dict,
    revised: dict,
    targets: set[str],
) -> dict:
    """스펙이 아는 목록마다 merge_targeted 를 적용한다.

    스펙이 `elements` 를 안 갖고 있거나 대상이 비면 revised 를 그대로 쓴다 — 전체 수정
    (기존 피드백 게이트 경로)이라 좁힐 것이 없다는 뜻이다.

    **바탕은 original 이다.** revised 에서 가져오는 것은 스펙이 아는 목록의, 대상 항목뿐.
    api_spec 의 title/version 처럼 스펙이 모르는 필드는 원본을 지킨다 — 지목 대상이
    아닌데 LLM 이 바꿔놓는 자리이기 때문이다(실제로 그랬다).
    """
    if not spec.elements or not targets:
        return revised

    merged = dict(original)
    for list_field, key_of in spec.elements.items():
        merged[list_field] = merge_targeted(
            original.get(list_field) or [],
            revised.get(list_field) or [],
            key_of,
            targets,
        )
    return merged


def model_elements(spec: DesignArtifactSpec, model: dict) -> list[str]:
    """이 모델 안에서 지목할 수 있는 항목 이름들."""
    return [
        key_of(item)
        for list_field, key_of in spec.elements.items()
        for item in (model.get(list_field) or [])
        if key_of(item)
    ]


def extract_node(spec: DesignArtifactSpec) -> Callable[[ArchitectureState], dict]:
    """앞선 산출물에서 이 산출물의 구조화 모델을 도출한다."""

    def node(state: ArchitectureState) -> dict:
        return {spec.model_key: spec.extract(state)}

    return node


def revise_node(spec: DesignArtifactSpec) -> Callable[[ArchitectureState], dict]:
    """사용자 피드백을 모델(진실의 원천)에 적용한다.

    산출물은 convert 노드가 같은 변환으로 재렌더하므로, 피드백이 렌더된 텍스트를 직접
    건드리는 일이 없고 모델과 산출물이 어긋나지 않는다.
    """

    def node(state: ArchitectureState) -> dict:
        # 게이트 피드백은 산출물 전체를 대상으로 한다 — 사용자가 그 산출물을 보면서
        # 말하는 자리라 항목을 좁힐 근거가 없다. 항목을 지목하는 수정은 cascade 가 한다.
        revised = spec.revise(
            state.get(spec.model_key) or {},
            state.get(spec.feedback_key, ""),
            state,
            set(),
        )
        return {spec.model_key: revised}

    return node


def convert_node(spec: DesignArtifactSpec) -> Callable[[ArchitectureState], dict]:
    """모델을 산출물로 렌더한다. 결정론적이고 구성에 의해 유효하다."""

    def node(state: ArchitectureState) -> dict:
        return {spec.content_key: spec.render(state.get(spec.model_key) or {})}

    return node


def validate_node(spec: DesignArtifactSpec) -> Callable[[ArchitectureState], dict]:
    """유효성을 화면에 보이도록 기록한다. 트립와이어이지 수리 트리거가 아니다."""

    def node(state: ArchitectureState) -> dict:
        validation = spec.validate(state.get(spec.content_key, spec.empty))
        return {
            spec.valid_key: validation["syntax_valid"],
            spec.errors_key: validation["syntax_errors"],
        }

    return node
