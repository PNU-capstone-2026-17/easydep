"""Pydantic 스키마 모음.

두 종류가 있다:
  1. HTTP API 계약  — AnalyzeRequest / AnalyzeResponse
  2. LLM 구조화 출력 — Assessment / ClarifyOnlyResult 등
LLM 구조화 출력은 graph.py에서 ChatOpenAI.with_structured_output(...) 에 넘겨,
gpt-oss-120b가 스키마에 맞는 JSON을 반환하도록 강제하는 데 쓴다.
(FR/NFR 분류는 LLM이 아니라 파인튜닝 BERT가 단독 수행한다 → step1 classify.)
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# FR/NFR 라벨 타입 (BERT 매핑과 동일: 0=NFR, 1=FR)
ReqType = Literal["FR", "NFR"]


# ----------------------------------------------------------------------------
# LLM 구조화 출력 스키마
# ----------------------------------------------------------------------------
class ConstraintLink(BaseModel):
    """분리된 품질 제약(NFR)과 그것이 한정하는 기능 요구(FR)의 링크(추적성).

    두 문자열은 refined_requirements에 나온 문장과 (공백 정규화 후) 일치해야 classify가 id로 해소한다.
    """

    constraint: str = Field(
        description="The non-functional / quality constraint sentence — MUST also appear "
        "verbatim in refined_requirements."
    )
    qualifies: str = Field(
        description="The functional requirement sentence this constraint qualifies — MUST also "
        "appear verbatim in refined_requirements."
    )


class ClarifyOnlyResult(BaseModel):
    """요구사항 구체화 결과 출력."""

    refined_requirements: list[str] = Field(
        default_factory=list,
        description="The current best set of concrete, single-need (atomic) requirement "
        "statements in English — each expresses exactly ONE need, with any quality/performance/"
        "security/reliability constraint separated into its own statement (not fused into a "
        "functional sentence). Populated once enough information is available.",
    )
    constraint_links: list[ConstraintLink] = Field(
        default_factory=list,
        description="For EACH quality constraint you split OUT of a compound requirement, one link "
        "mapping the constraint sentence to the functional sentence it qualifies. Both strings MUST "
        "appear verbatim in refined_requirements. Empty if no constraint was separated.",
    )


# ----------------------------------------------------------------------------
# STEP 2 — 액터/유스케이스 구조화 출력
# ----------------------------------------------------------------------------
UseCaseLevel = Literal["summary", "user_goal", "subfunction"]


class Actor(BaseModel):
    """유스케이스와 상호작용하는 액터(역할). FR에서만 도출한다.

    설계 대상 시스템(SuD)은 경계이지 액터가 아니다. Primary/supporting은
    액터의 고정 속성이 아니라 유스케이스별 역할이다.
    """

    name: str = Field(description="Actor role name, e.g. 'Registered User'.")
    description: str = Field(description="One sentence describing the actor's role.")
    parent_actor: str | None = Field(
        default=None,
        description="If this actor specializes another (e.g. Member specializes Guest), the "
        "parent role name; grounds an actor generalization. Null if none.",
    )


class UseCase(BaseModel):
    """user-goal(EBP) 고도의 유스케이스. FR을 묶고 NFR을 제약으로 참조한다."""

    name: str = Field(description="Active-verb goal phrase, e.g. 'Place an order'.")
    primary_actor: str = Field(
        description="Name of the primary actor (must be one of the given actors)."
    )
    supporting_actors: list[str] = Field(
        default_factory=list,
        description="Names of external actors whose services the system calls while carrying "
        "out this use case. A recipient of system output is not supporting merely because it "
        "receives that output. Use only given actor names. An actor may be primary in one use "
        "case and supporting in another.",
    )
    level: UseCaseLevel = Field(
        default="user_goal",
        description="Cockburn goal altitude; prefer user_goal (sea level, an EBP).",
    )
    goal: str = Field(description="One sentence: what the primary actor wants to achieve.")
    requirement_ids: list[str] = Field(
        default_factory=list,
        description="IDs of the FR requirements this use case covers, including "
        "subfunction-level FRs folded into it (e.g. 'R1','R3'). Use only provided ids. "
        "The main scenario and its extensions are produced later (step 3), not here.",
    )
    nfr_ids: list[str] = Field(
        default_factory=list,
        description="IDs of NFR requirements that constrain this use case.",
    )


class RuleVerdict(BaseModel):
    """규칙 하나에 대한 판정. **어느 규칙을 봤는지 반드시 댄다.**

    처음에는 `is_valid` + 자유문 findings였다. 두 가지가 문제였다.

    1. **근거 없는 지적을 구별할 수 없었다.** 검증자가 지식베이스에 없는 기준을 스스로
       만들어 지적해도 근거 있는 지적과 같은 모양이었다. rule_id를 요구하면 대조할 수
       있다(`app/requirements/knowledge/rules.py`의 `known_ids`).
    2. **"봤는데 깨끗하다"와 "안 봤다"가 같은 값이었다.** 규칙 6개 중 2개만 훑고 깨끗하다고
       답해도 결과가 같다. verification subagent의 알려진 실패 모드(early victory)가 이것이다.
       그래서 규칙마다 한 줄씩 판정을 받고, 빠진 규칙은 세어 저하로 남긴다.

    `is_valid`는 없앴다 — `violated`의 합에서 파생되므로 따로 두면 둘이 어긋날 수 있고,
    실제로 어긋난 응답을 방어하는 코드가 있었다.
    """

    rule_id: str = Field(
        description="The rule id, copied exactly from the rule list.",
    )
    violated: bool = Field(
        description="True only if the artifact actually breaks this rule.",
    )
    directive: str = Field(
        default="",
        description=(
            "When violated, one short imperative repair directive (at most two "
            "sentences). Empty when not violated."
        ),
    )


class Critique(BaseModel):
    """의미 검증자의 판정 한 벌 — 단계와 무관하다(어느 규칙을 보는지는 지식베이스가 정한다).

    예전에는 `SpecCritique`·`RelationshipCritique`로 나뉘어 있었지만 모양이 같았고 다른
    점은 어느 규칙을 보느냐뿐이었다. 그건 이제 프롬프트가 지식베이스에서 조립한다.
    """

    verdicts: list[RuleVerdict] = Field(
        default_factory=list,
        description="One verdict per rule in the rule list, in the same order.",
    )


class ConcernLink(BaseModel):
    """관심사 하나에 대한 링크 판정 — **어느 요구가 이것을 다루는가.**

    `RuleVerdict`와 모양은 닮았지만 묻는 것이 반대다(위반이 아니라 다뤄짐). 닮은 부분은
    의도적이다 — 관심사 id를 반드시 대게 해서 **목록에 없는 관심사를 지어낸 응답**과
    **판정을 빠뜨린 응답**을 둘 다 드러낸다.
    """

    concern_id: str = Field(
        description="The concern id, copied exactly from the concern list.",
    )
    requirement_ids: list[str] = Field(
        default_factory=list,
        description=("Ids of the requirements that address this concern. Empty when none do."),
    )


class ConcernLinkage(BaseModel):
    """관심사 링크 한 벌."""

    links: list[ConcernLink] = Field(
        default_factory=list,
        description="One entry per concern in the concern list, in the same order.",
    )


class DeploymentNeed(BaseModel):
    """One generic deployment capability grounded in existing requirement IDs."""

    model_config = ConfigDict(populate_by_name=True)

    role: str = Field(description="What the deployment must provide and why it is needed.")
    required: bool = Field(description="True when mandatory; false when a preference.")
    requirement_ids: list[str] = Field(
        alias="requirementIds",
        min_length=1,
        description="Exact IDs of requirements supporting this need.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Need-specific details; unresolved questions may use an unresolved list.",
    )
    evidence_spans: list[str] = Field(
        alias="evidenceSpans",
        default_factory=list,
        description="Exact requirement substrings supporting this capability proposal.",
    )
    origin: Literal["explicit", "inferred"] = "inferred"
    dependency_capability_ids: list[str] = Field(
        alias="dependencyCapabilityIds",
        default_factory=list,
        description=(
            "Stable dependency-model capability IDs recognized by the supplied registry; "
            "a recognized ID may still be outside the current generation scope."
        ),
    )


class DeploymentNeedsResult(BaseModel):
    """Dynamic deployment needs keyed by LLM-chosen snake_case identifiers."""

    model_config = ConfigDict(populate_by_name=True)

    deployment_needs: dict[str, DeploymentNeed] = Field(
        alias="deploymentNeeds", default_factory=dict
    )


class CapabilityDecision(BaseModel):
    """One auditable CapabilityContract/v1 decision."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    statement: str
    requirement_ids: list[str] = Field(alias="requirementIds", min_length=1)
    evidence_spans: list[str] = Field(alias="evidenceSpans", default_factory=list)
    origin: Literal["explicit", "inferred"]
    necessity: Literal["required", "preferred"]
    decision: Literal["accepted", "needsQuestion", "abstained"]
    decision_reason: str = Field(alias="decisionReason")
    raw_confidence: float = Field(alias="rawConfidence", ge=0, le=1)
    calibrated_confidence: float | None = Field(
        alias="calibratedConfidence", default=None, ge=0, le=1
    )
    threshold_version: str = Field(alias="thresholdVersion")
    confirmation: Literal["notRequired", "pending", "userConfirmed", "reviewerConfirmed"]
    alternatives: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(alias="unresolvedFields", default_factory=list)
    dependency_capability_ids: list[str] = Field(
        alias="dependencyCapabilityIds", default_factory=list
    )


class CapabilityContract(BaseModel):
    """Requirement-to-capability boundary consumed by design agents and evaluators."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["CapabilityContract/v1"] = Field(
        alias="schemaVersion", default="CapabilityContract/v1"
    )
    capabilities: list[CapabilityDecision] = Field(default_factory=list)
    questions: list[dict[str, str]] = Field(default_factory=list)


class ActorResult(BaseModel):
    """identify_actors 노드의 구조화 출력."""

    actors: list[Actor]


class UseCaseResult(BaseModel):
    """identify_use_cases 노드의 구조화 출력."""

    use_cases: list[UseCase]


# ----------------------------------------------------------------------------
# STEP 3 — 유스케이스 명세(Cockburn 풀 템플릿) 구조화 출력
#
# 설계 원칙(문장/분기/종료를 파싱 없이 구조로 표현):
#  - 모든 문장은 plain black-box 서술. 마크다운/프로토콜/내부컴포넌트 금지.
#  - 주 시나리오 스텝은 step_number(int)로 식별하고 스텝별 요구 추적(covered_req_ids).
#  - 확장은 branch_step(int|null=전역)로 분기 스텝을, outcome(enum)+resume_at_step으로
#    종료 방식(주흐름 복귀/대안성공/실패)을 구조적으로 명시한다.
# ----------------------------------------------------------------------------
Outcome = Literal["resume", "alternate_success", "fail"]


class MainScenarioStep(BaseModel):
    """주 성공 시나리오의 한 스텝."""

    step_number: int = Field(description="Sequential step number starting at 1.")
    sentence: str = Field(
        description="One plain black-box business action. Subject is the primary actor "
        "or 'System'. No markdown/bold/asterisks, no UI widgets, no protocols "
        "(HTTP/SQL) or internal components (Server, Database)."
    )
    covered_req_ids: list[str] = Field(
        default_factory=list,
        description="IDs of the FR requirements this step realizes (traceability).",
    )


class ExtensionHandlingStep(BaseModel):
    """확장 흐름의 처리 스텝."""

    sub_step: str = Field(description="Cockburn hierarchical code, e.g. '3a1', '3a2'.")
    sentence: str = Field(
        description="One plain black-box action handling the condition. Same style rules "
        "as a main-scenario step; no 'Success!'/'Fail!' tokens in the prose."
    )


class Extension(BaseModel):
    """확장(예외/대안 흐름) 하나. 분기 스텝과 종료 방식을 구조로 표현한다."""

    label: str = Field(description="Cockburn extension label, e.g. '3a' or '*a' (global).")
    branch_step: int | None = Field(
        default=None,
        description="Main-scenario step_number this extension branches from. Null means it "
        "may occur at ANY step (a global extension, label like '*a').",
    )
    condition: str = Field(
        description="The objective exception/alternate state that triggers this extension. "
        "Plain sentence, no trailing colon."
    )
    handling_steps: list[ExtensionHandlingStep] = Field(default_factory=list)
    outcome: Outcome = Field(
        description="How the extension ends: 'resume' = rejoin the main scenario; "
        "'alternate_success' = the use case still succeeds via a different path; "
        "'fail' = the use case aborts without achieving the goal."
    )
    resume_at_step: int | None = Field(
        default=None,
        description="Required IFF outcome == 'resume': the main-scenario step_number to "
        "resume from. Must be null for 'alternate_success' and 'fail'.",
    )


class UseCaseSpec(BaseModel):
    """단일 유스케이스의 Cockburn 스타일 명세 (generate_specs의 UC당 구조화 출력)."""

    preconditions: list[str] = Field(
        default_factory=list,
        description="Verifiable state guaranteed true before start; never re-checked in "
        "steps. Plain sentences, no markdown.",
    )
    trigger: str = Field(description="The business event that starts the use case.")
    main_scenario: list[MainScenarioStep] = Field(
        default_factory=list,
        description="Ordered steps of the main success scenario (the happy path).",
    )
    extensions: list[Extension] = Field(
        default_factory=list,
        description="Exception/alternate flows, each branching from a main-scenario step.",
    )
    success_guarantee: list[str] = Field(
        default_factory=list,
        description="Postconditions guaranteed when the use case succeeds.",
    )
    minimal_guarantee: list[str] = Field(
        default_factory=list,
        description="What the system guarantees even if the use case fails.",
    )


# ----------------------------------------------------------------------------
# STEP 4 — 액터/유스케이스 관계(다이어그램용) 구조화 출력
# 관계 식별은 LLM(의미 판단), 다이어그램 텍스트 렌더링은 결정론적으로 분리한다.
# 유스케이스/액터는 이름(name)으로 참조한다.
# ----------------------------------------------------------------------------
class Association(BaseModel):
    """액터 ↔ 유스케이스 연결."""

    actor: str = Field(description="Actor name (must match a given actor).")
    use_case: str = Field(description="Use case name (must match a given use case).")


class IncludeRelation(BaseModel):
    """공통 하위 행위를 별도 유스케이스로 추출한 include 관계."""

    base_use_case: str = Field(description="Use case that includes the shared behavior.")
    included_use_case: str = Field(description="Factored-out common sub use case.")
    rationale: str = Field(default="", description="Why this shared behavior was factored out.")


class ExtendRelation(BaseModel):
    """선택/조건부 행위를 나타내는 extend 관계."""

    base_use_case: str = Field(description="Use case being extended.")
    extending_use_case: str = Field(description="Optional/conditional behavior use case.")
    extension_point: str = Field(default="", description="Step/condition where it attaches.")
    rationale: str = Field(default="")


class GeneralizationRelation(BaseModel):
    """액터 또는 유스케이스 일반화(상속)."""

    parent: str = Field(description="General actor or use case name.")
    child: str = Field(description="Specialized actor or use case name.")
    kind: Literal["actor", "use_case"] = Field(
        description="Whether this generalizes actors or use cases."
    )
    rationale: str = Field(default="")


class DerivedUseCase(BaseModel):
    """관계 도출 중 새로 등장한 유스케이스(예: 추출된 include 'Authenticate')."""

    name: str = Field(description="Name of the derived use case.")
    origin: Literal["factored_include", "promoted_extend"] = Field(
        description="factored_include = extracted as a shared include; "
        "promoted_extend = introduced as an extending behavior."
    )
    rationale: str = Field(default="")


class RelationshipModel(BaseModel):
    """identify_relationships 노드의 구조화 출력."""

    associations: list[Association] = Field(default_factory=list)
    includes: list[IncludeRelation] = Field(default_factory=list)
    extends: list[ExtendRelation] = Field(default_factory=list)
    generalizations: list[GeneralizationRelation] = Field(default_factory=list)
    derived_use_cases: list[DerivedUseCase] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# BASELINE — 다단계 파이프라인의 대조군(순진한 2콜: 명세 원샷 + 다이어그램 원샷)
# 우리 시스템의 "단계 분해 + 검증/반성" 가치를 정량 비교하기 위한 최소 프롬프트 구조.
# ----------------------------------------------------------------------------
class BaselineSpec(UseCaseSpec):
    """baseline 원샷 명세: UseCaseSpec 필드 + 어느 유스케이스에 속하는지."""

    use_case_name: str = Field(
        description="Name of the use case this specification belongs to; "
        "must exactly match one of the use case names."
    )


class BaselineModelResult(BaseModel):
    """baseline 1콜: 액터 + 유스케이스 + Cockburn 명세를 한 번에 생성."""

    actors: list[Actor]
    use_cases: list[UseCase]
    specs: list[BaselineSpec]


class CoverageVerdict(BaseModel):
    """한 유스케이스가 특정 요구사항을 '실제로' 실현하는지에 대한 의미론적 판정."""

    requirement_id: str = Field(description="The requirement id being judged.")
    realized: bool = Field(
        description="True only if the use case's goal and main scenario genuinely realize "
        "this requirement's intent. False if the coverage claim is unsupported by the behavior "
        "(the model merely asserted it)."
    )
    reason: str = Field(description="One short sentence justifying the verdict.")


class CoverageJudgment(BaseModel):
    """한 유스케이스가 주장한 requirement_ids 각각에 대한 의미론적 커버리지 판정 묶음."""

    verdicts: list[CoverageVerdict]


# ----------------------------------------------------------------------------
# 피드백 기반 재생성 — 사용자 자연어 피드백의 의도 분류
# ----------------------------------------------------------------------------
class FeedbackIntent(BaseModel):
    """사용자 자연어 피드백을 재생성 의도로 분류한 결과."""

    stage: Literal["actors", "use_cases", "specs", "relationships"] = Field(
        description="Which pipeline artifact the feedback targets. Feedback about the diagram "
        "maps to 'relationships' (the diagram is rendered from it)."
    )
    scope: Literal["local", "broad"] = Field(
        description="local = concerns specific items named in target_ids; broad = re-derive the "
        "whole stage output."
    )
    target_ids: list[str] = Field(
        default_factory=list,
        description="For local scope, the affected item ids (e.g. ['UC3'] for a use case/spec, "
        "or an actor name). Empty for broad scope.",
    )
    instruction: str = Field(
        description="One concise imperative directive restating what to change."
    )


class FeedbackEdit(BaseModel):
    """화면이 이미 아는 것을 추측하지 않고 그대로 보내는 구조화 피드백.

    자연어 피드백은 LLM이 `{stage, scope, target_ids}`를 **추측**해야 한다. 그런데
    화면은 사용자가 어느 단계의 어느 항목을 편집 중인지 이미 알고 있다. 알고 있는 것을
    보내면 그 LLM 호출과 오분류가 통째로 사라진다.

    `instruction`만 자연어로 남는다 — 무엇을 어떻게 바꿀지는 사람이 말해야 하고 그건
    생성 모델의 몫이다. **자연어 경로를 대체하지 않는다**: 사용자가 use_cases 게이트에서
    "액터에서 관리자를 분리해줘"라고 적으면 분류기가 actors로 보내 주는데, 그 기능은
    그대로 둔다. 화면이 확신할 때만 이 형태를 쓴다.
    """

    stage: Literal["actors", "use_cases", "specs", "relationships"]
    scope: Literal["local", "broad"] = "broad"
    #: local일 때 대상 항목 id. broad면 비운다.
    target_ids: list[str] = Field(default_factory=list)
    instruction: str


class ResourceAnswer(BaseModel):
    """되묻기(`RESOURCE_SPEC`의 못 채운 칸)에 대한 답. **요구사항 피드백이 아니다.**

    같은 게이트에서 두 종류의 입력을 받기 때문에 형태로 가른다. 자연어로 받으면
    "aws 서울 월 3000달러"를 요구사항 편집 지시로 알아듣고 분류를 다시 돌린다 —
    사용자는 질문에 답했을 뿐인데 요구사항이 흔들린다.

    값은 **문자열 그대로** 받는다. 해석은 제약 구조화 에이전트가 산문과 같은 규율로
    한다(`steps/step_resource.py`) — 화면이 "서울"을 코드로 바꿔 보내면 그 해석이
    어디서 왔는지 아무도 모르게 된다. 답했다는 사실이 모호함을 없애 주지도 않는다:
    "서울"은 여전히 카탈로그를 거쳐야 하고, 후보가 여럿이면 여전히 되물어야 한다.
    """

    #: 계약 칸 이름 → 사용자가 쓴 답. 모르는 칸은 단계가 버린다.
    answers: dict[str, str] = Field(default_factory=dict)


class CloudConstraintExtraction(BaseModel):
    """One-pass LLM extraction of user-stated cloud constraints.

    Every evidence field must be an exact substring of the input. Missing or ambiguous
    values stay null; validation and normalization happen in code after extraction.
    """

    provider: str | None = None
    provider_evidence: str = ""
    region_as_written: str | None = None
    region_evidence: str = ""
    monthly_budget_amount: float | None = None
    monthly_budget_currency: str | None = None
    monthly_budget_evidence: str = ""
    min_vcpu: int | None = None
    min_vcpu_evidence: str = ""
    min_memory_gib: float | None = None
    min_memory_evidence: str = ""
    traffic_pattern: Literal["steady", "spiky"] | None = None
    traffic_pattern_evidence: str = ""
    scale_value: float | None = None
    scale_unit: Literal["concurrentUsers", "requestsPerSecond"] | None = None
    scale_evidence: str = ""
    data_residency: str | None = None
    data_residency_evidence: str = ""
    ambiguous_fields: list[str] = Field(default_factory=list)
    understanding: str = ""


class InitialCloudConstraints(BaseModel):
    """사용자가 분석 시작 전에 직접 고르는 최소 클라우드 제약.

    소프트웨어 요구사항과 섞지 않는다. 정확한 CSP, 사용자가 쓴 지역 표현, 예산을
    구조화해서 받아 제약 추출 LLM이 이미 알려진 값을 다시 추측하지 않게 한다.
    """

    provider: Literal["aws", "azure", "gcp"]
    region: str = Field(min_length=1)
    monthly_budget_amount: float | None = Field(default=None, gt=0)
    monthly_budget_currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("region")
    @classmethod
    def _non_blank_region(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("region must not be blank")
        return value

    @field_validator("monthly_budget_currency")
    @classmethod
    def _currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not value.isalpha() or len(value) != 3:
            raise ValueError("monthly_budget_currency must be a three-letter code")
        return value


class DeploymentTarget(BaseModel):
    """One provider-specific alternative selected from the deployment map."""

    provider: Literal["aws", "azure", "gcp"]
    region: str = Field(min_length=1, max_length=100)
    zones: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("region")
    @classmethod
    def _target_region(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("region must not be blank")
        return value

    @field_validator("zones")
    @classmethod
    def _target_zones(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values if str(value).strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("zones must not contain duplicates")
        return normalized


class DeploymentPreferences(BaseModel):
    """Cloud alternatives entered independently from application requirements."""

    mode: Literal["alternatives"] = "alternatives"
    targets: list[DeploymentTarget] = Field(min_length=1, max_length=3)
    monthly_budget_amount: float | None = Field(default=None, gt=0)
    monthly_budget_currency: str = Field(default="USD", min_length=3, max_length=3)
    compute_profile: Literal[
        "standaloneOne",
        "managedGroupOne",
        "managedGroupManySingleZone",
        "managedGroupManyMultiZone",
    ] = "standaloneOne"
    replica_count: int = Field(default=1, ge=1)
    public_ingress: Literal["direct", "loadBalanced"] = "direct"
    database_placement: Literal["colocated", "dedicated"] = "dedicated"
    resource_constraints_text: str = Field(default="", max_length=12000)

    @field_validator("monthly_budget_currency")
    @classmethod
    def _preference_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if not value.isalpha() or len(value) != 3:
            raise ValueError("monthly_budget_currency must be a three-letter code")
        return value

    @model_validator(mode="after")
    def _coherent_targets(self) -> "DeploymentPreferences":
        providers = [target.provider for target in self.targets]
        if len(providers) != len(set(providers)):
            raise ValueError("select at most one region for each provider")
        many = self.compute_profile in {
            "managedGroupManySingleZone",
            "managedGroupManyMultiZone",
        }
        spread = self.compute_profile == "managedGroupManyMultiZone"
        grouped = self.compute_profile != "standaloneOne"
        if many and self.replica_count < 2:
            raise ValueError(
                "many-replica compute profiles require replica_count >= 2"
            )
        if not many and self.replica_count != 1:
            raise ValueError("one-replica compute profiles require replica_count = 1")
        if grouped and self.public_ingress == "direct":
            raise ValueError("managed groups require loadBalanced public ingress")
        if spread and any(len(target.zones) < 2 for target in self.targets):
            raise ValueError("multi-zone spread requires at least two zones per target")
        if not spread and any(len(target.zones) > 1 for target in self.targets):
            raise ValueError("single-zone profiles may select at most one zone per target")
        return self

# ----------------------------------------------------------------------------
# HTTP API 스키마
# ----------------------------------------------------------------------------
class RequirementItemOut(BaseModel):
    """최종 FR/NFR 목록의 한 항목 (BERT 단독 분류). id는 FR1/NFR2 형식."""

    id: str
    text: str
    type: ReqType


class AnalyzeRequest(BaseModel):
    """요구사항 분석 세션 시작 또는 진행 요청.

    - 신규 세션: requirements 를 채워 보낸다 (thread_id 없음/무관).
    - 구체화 답변: answer 와 기존 thread_id 를 함께 보낸다.
    """

    requirements: list[str] | None = None
    # 최초 화면에서 받는 최소 클라우드 좌표. 신규 세션에만 사용한다.
    cloud_constraints: InitialCloudConstraints | None = None
    # 대화형 워크스페이스가 요구사항 분석 중 별도로 받은 복수 CSP 배포 대안.
    # 신규 세션의 초기값 또는 요구사항 게이트의 구조화 재개 값으로 사용한다.
    deployment_preferences: DeploymentPreferences | None = None
    # 클라우드 제약 원문(`apps.resource_constraints_text`). 요구사항과 **따로** 받는다 —
    # 여기서 `RESOURCE_SPEC`이 만들어진다(`steps/step_resource.py`). 없으면 필수 칸이
    # 비고, 그 사실이 되묻기 질문으로 나간다.
    resource_constraints_text: str | None = None
    answer: str | None = None
    # 자연어 대신 보내는 구조화 편집(피드백 게이트 전용). answer와 함께 보낼 수 없다 —
    # 둘 다 오면 무엇을 따를지가 모호해지므로 400으로 거절한다.
    edit: FeedbackEdit | None = None
    # 되묻기의 답(칸 이름 → 사용자가 쓴 문자열). answer/edit과 함께 보낼 수 없다 —
    # 재개 값은 하나이고, 섞이면 무엇을 따를지가 모호해진다.
    resource_answers: dict[str, str] | None = None
    thread_id: str | None = None
    # 대화형 게이트(step1 clarify + 각 스텝 피드백) 사용 여부. None이면 서버 기본값(설정)을 따른다.
    # 신규 세션 시작 시에만 의미가 있으며, 이후 재개(answer)는 세션이 시작된 모드를 유지한다.
    feedback_gates: bool | None = None
    # 산출물을 저장할 앱(POST /api/apps 로 발급). 있으면 분석이 완료된 시점에
    # refined_requirements / usecase_spec / usecase_diagram 이 그 앱에 기록되어
    # 설계 에이전트가 이어받는다. 없으면 저장 없이 응답만 돌려준다(단독 실행).
    app_id: str | None = None


class AnalyzeResponse(BaseModel):
    thread_id: str
    phase: str
    status: Literal["need_clarification", "need_feedback", "completed"]
    # status == need_clarification 일 때 채워짐
    questions: list[str] | None = None
    # status == need_feedback 일 때 채워짐(대화형 피드백 게이트)
    feedback_prompt: str | None = None
    feedback_summary: object | None = None
    # 이 게이트에서 화면이 구조화 편집(FeedbackEdit)을 만들 때 쓸 재료.
    # edit_stage는 이 게이트가 재생성할 수 있는 단계, edit_targets는 고를 수 있는 항목 id다.
    # 화면이 이걸 쓰면 의도 분류 LLM 호출이 생략된다.
    edit_stage: str | None = None
    edit_targets: list[str] | None = None
    # 이 게이트가 함께 묻는 `RESOURCE_SPEC` 되묻기. 각 항목은 {field, kind, why,
    # question, seen}이고, 화면은 `field`를 키로 `resource_answers`를 만들어 보낸다.
    resource_questions: list[dict] | None = None
    # status == completed 일 때 채워짐 (step1)
    requirements: list[RequirementItemOut] | None = None
    # step2~4 산출물 — 파이프라인은 항상 실행되지만, 게이트 interrupt로 중간에 멈춘
    # 시점에는 아직 안 만들어진 단계의 필드가 None일 수 있다.
    # 각 항목의 상세 구조는 상단의 Actor/UseCase/UseCaseSpec/RelationshipModel 스키마 및
    # state.py 의 대응 TypedDict 참조. (출력 전용이라 dict 그대로 통과시킨다.)
    # 클라우드 층 산출물 둘. **요구사항과 나란한 별도 산출물이지 명세의 일부가 아니다.**
    # 여기 적지 않으면 조용히 사라진다 — pydantic이 모르는 키를 버리므로, 파이프라인이
    # 만들어도 화면은 받을 수 없으므로 출력 필드로 명시한다.
    deployment_needs: dict | None = None  # 요구사항 ID 기반 제네릭 배포 필요사항
    capability_contract: dict | None = None  # CapabilityContract/v1 selective decisions
    resource_spec: dict | None = None  # RESOURCE_SPEC — 계약을 만족할 때만 있다
    resource_intake: dict | None = None  # 초안·질문·근거·버린 후보(A 트랙)
    actors: list[dict] | None = None  # ActorItem
    use_cases: list[dict] | None = None  # UseCaseItem
    coverage: dict | None = None  # check_coverage 결과
    model_review: dict | None = None  # review_model(독립 의미 검증자)의 판정
    use_case_specs: list[dict] | None = None  # UseCaseSpecItem (Cockburn 명세)
    spec_report: dict | None = None  # check_specs 검증 집계
    relationships: dict | None = None  # associations/includes/extends/generalizations/derived
    relationship_report: dict | None = None  # check_relationships 검증 집계
    diagram: str | None = None  # PlantUML 텍스트
    # 이번 응답에서 산출물 저장소에 새 버전으로 기록된 stage 이름들.
    # app_id를 보냈을 때만 채워지며, 화면이 "무엇이 저장됐는지"를 표시하는 데 쓴다.
    # 내용이 이전과 같으면 저장하지 않으므로 빈 리스트일 수 있다.
    saved_stages: list[str] | None = None
    # 이번 호출에서 실제로 일어난 일: LLM 호출 수·토큰·폴백 횟수와 **저하 목록**.
    # degradations가 비어 있지 않으면 산출물 일부가 검증을 못 거쳤다는 뜻이므로,
    # 화면은 결과를 그대로 신뢰해서는 안 된다. (app/requirements/common/telemetry.py)
    telemetry: dict | None = None


# `ResourceFieldRead`·`ResourceReading`은 없앴다(2026-07-29). 제약 구조화를 **한 번
# 읽고 끝내는 구조화 출력**에서 도구를 쓰는 에이전트 루프로 바꾸면서, 읽기의 결과는
# 스키마가 아니라 도구 호출(`record_field`)로 들어온다 — 인용 대조는 그 문에서 그대로
# 한다(`steps/step_resource._ground`).
