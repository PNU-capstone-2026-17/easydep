"""요구사항 분석 에이전트의 그래프 State 정의.

모든 노드가 공유하는 단일 상태. 마일스톤1 필드 + 2~4단계 필드가 함께 있어,
단계가 늘어도 State는 이 파일 한 곳에서 확장한다.
"""
from __future__ import annotations

from typing import Annotated, Literal, NotRequired

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class RequirementItem(TypedDict):
    """분류된 개별 요구사항 (BERT 단독 분류 결과).

    id는 유형+순번(FR1, NFR2 …) 형식. 분류는 파인튜닝 BERT가 단독 수행한다.
    """

    id: str
    text: str
    type: Literal["FR", "NFR"]
    # Phase 2(RTM): 이 NFR이 한정하는(qualify) FR id들. clarify가 복합요구에서 분리해낸
    # 제약이면 classify가 부모 FR id로 채운다(NFR에만, 없으면 부재). 추적성 링크.
    qualifies: NotRequired[list[str]]


class ActorItem(TypedDict):
    """도출된 액터(역할). FR에서만 도출한다. SuD(설계 대상 시스템)는 액터가 아님."""

    name: str
    description: str
    kind: Literal["primary", "supporting"]
    parent_actor: str | None        # 일반화(상속) 부모 액터, 없으면 None


class UseCaseItem(TypedDict):
    """도출된 유스케이스 (user-goal 고도). FR 추적성·NFR 제약을 담는다."""

    id: str
    name: str
    primary_actor: str
    level: Literal["summary", "user_goal", "subfunction"]
    goal: str
    requirement_ids: list[str]      # 커버하는 FR id (서브펑션 흡수 포함, 추적성)
    nfr_ids: list[str]              # 이 UC를 한정하는 NFR id
    # 주 시나리오/확장(예외·대안)은 step3에서 생성한다 (여기서 만들지 않음).


class UseCaseSpecItem(TypedDict):
    """유스케이스 명세 (step3 산출, Cockburn 풀 템플릿)."""

    use_case_id: str
    name: str
    preconditions: list[str]
    trigger: str
    main_scenario: list[dict]       # {step_number, sentence, covered_req_ids}
    extensions: list[dict]          # {label, branch_step, condition, handling_steps, outcome, resume_at_step}
    success_guarantee: list[str]
    minimal_guarantee: list[str]
    issues: list[str]               # 검증 위반(정적+의미). reflection 루프 후 남은 것.
    repair_iters: int               # 반성 루프에서 재생성한 횟수
    # 의미 검증(LLM)을 실제로 거쳤는지.
    # "ok"|"disabled"|"failed"|"ungrounded"(지식베이스에 없는 규칙만 인용해 버렸다)|"pending".
    # issues가 비었다는 것만으로는 "깨끗함"과 "확인 못 함"을 구별할 수 없어서 둔다.
    # 예전 spec item과 섞일 수 있으므로 NotRequired.
    semantic_status: NotRequired[str]
    # 생성이 성공했는지. False면 이 항목은 자리만 지키는 빈 명세다(형제를 살리려고
    # 남긴다 — 목록에서 빼면 산출물에서 조용히 사라진다). 없으면 성공한 것으로 본다.
    generated: NotRequired[bool]
    # 반성 루프가 멈춘 이유.
    #   "clean"          결함이 없어져서
    #   "no_improvement" 재생성이 결함을 줄이지 못해서(개수가 같은 것도 포함)
    #   "budget"         max_repair_iters를 다 써서
    #   "error"          재생성 호출이 실패해서
    #   "not_generated"  최초 생성부터 실패해 루프에 들어가지도 못해서
    # 부분(수술적) 수정으로 바꿀 값어치가 있는지는 이 분포를 봐야 안다.
    repair_stopped: NotRequired[str]


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    raw_requirements: list[str]
    refined_requirements: list[str]
    # Phase 2(RTM): clarify가 분리한 (constraint 문장 → qualify하는 functional 문장) 링크.
    # classify가 이 문장쌍을 id로 해소해 RequirementItem.qualifies를 채운다.
    constraint_links: NotRequired[list[dict]]
    classified: list[RequirementItem]
    phase: str
    # 요구사항에서 도출한 제네릭 배포 필요사항. 구체 클라우드 리소스 선택은 설계 책임이다.
    # 각 need는 기존 요구사항 ID를 참조해 근거를 추적한다.
    deployment_needs: NotRequired[dict]
    # 사용자가 쓴 클라우드 제약 원문(`apps.resource_constraints_text`). 요구사항 문장과
    # **따로** 받는다 — 실측상 provider·region·예산은 요구사항 산문에 아예 없고(0건),
    # 없는 곳을 뒤지면 오탐만 남는다(`steps/step_resource.py`).
    resource_constraints_text: NotRequired[str]
    # 되묻기의 답: 계약 칸 이름 → 사용자가 쓴 문자열. **값이 아니라 답이다** — 제약
    # 구조화 에이전트가 산문과 같은 규율로 해석한다("서울"은 여전히 카탈로그를 거쳐
    # 코드로 풀려야 하고, 후보가 여럿이면 여전히 모호하다).
    resource_answers: NotRequired[dict[str, str]]
    # 제약 구조화의 작업 기록: 초안·질문·근거·버린 후보(`steps/step_resource.py`).
    # 계약을 만족하지 못해도 **여기는 늘 존재한다** — 왜 못 채웠는지가 사라지면 안 된다.
    resource_intake: NotRequired[dict]
    # `RESOURCE_SPEC` 계약 산출물. **계약을 만족할 때만 존재한다.** 반쯤 채운 사양을
    # 내보내면 뒤 단계(배포 구성)가 그것을 사양으로 알고 조인을 돌린다.
    resource_spec: NotRequired[dict]
    # 2단계 — 액터/유스케이스 도출 + FR 커버리지 점검
    actors: list[ActorItem]
    use_cases: list[UseCaseItem]
    coverage: dict                   # check_coverage의 결정론적 커버리지 결과
    # review_model(독립 의미 검증자)의 판정. {issues, semantic_status, unexamined_rules}.
    # 커버리지와 나란히 두는 이유: 하나는 "빠진 게 없나"(결정론), 다른 하나는 "모델이
    # 규칙을 지켰나"(의미)이고 둘은 서로를 대신하지 못한다.
    model_review: NotRequired[dict]
    # 3단계 — 유스케이스별 명세(병렬 생성) + 검증 요약
    use_case_specs: list[UseCaseSpecItem]
    spec_report: dict                # check_specs의 명세 검증 집계
    # 4단계 — 관계 식별(LLM) + 검증 요약 + 다이어그램 렌더(결정론)
    relationships: dict              # {associations, includes, extends, generalizations, derived_use_cases}
    relationship_report: dict        # check_relationships의 관계 검증 집계
    diagram: str                     # PlantUML 유스케이스 다이어그램 텍스트
    # 정적 라우팅 마커 — 피드백 게이트가 "advance"(다음 단계)/"loop"(재생성 후 재질문)를 써 두면
    # 서브그래프의 조건부 엣지(route_gate)가 이를 읽어 분기한다. Command(goto) 동적 라우팅 대체.
    gate_route: NotRequired[str]
    # --- 되돌아가기(supervisor) ---
    # 결함을 낸 단계로 몇 번 되돌렸는지. `settings.max_redo_rounds`로 묶인다.
    redo_rounds: NotRequired[int]
    # 되돌린 기록: {owner, reason, escalated, rule_ids}. 왜 그 단계가 다시 돌았는지가
    # 남아 있지 않으면, 산출물만 보고는 되돌리기가 있었는지조차 알 수 없다.
    redo_history: NotRequired[list[dict]]
    # 되돌릴 단계에 들려 보내는 지시. 단계 함수가 `feedback` 인자 대신 여기서 읽는다 —
    # 그래프 엣지로 되돌릴 때는 인자를 넘길 자리가 없다.
    stage_feedback: NotRequired[dict[str, str]]
    # 정적 라우팅 마커 — 감독 노드가 "advance" 또는 되돌릴 **그룹 이름**을 써 두면
    # 조건부 엣지(route_redo)가 읽어 분기한다. gate_route와 같은 방식이다.
    redo_route: NotRequired[str]
