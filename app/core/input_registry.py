"""**사용자에게 받아야 할 것** 한 곳 — 질문·근거·소비자·선행조건.

## 왜 생겼나

"무엇을 받아야 하는가"를 말하는 곳이 셋이었고 **서로를 몰랐다**(실측 2026-08-01):

| 출처 | 개수 | 묻는 것 | 실제로 이어진 것 |
|---|---|---|---|
| `appkb/request.json` + `contract.REQUIRED_WHY` | 15칸 | **값** | 조인 축 + 판정 5개 |
| `requirements/knowledge/concerns.py` | 29건 | **요구사항에 적혔나** | 7건만 계약 칸으로 |
| `depkb`의 `closure().decisions` | csp별 1~3 | **앵커 정한 뒤의 선택** | **아무도 안 물었다** |

그리고 계약 15칸에는 축이 하나뿐이었다 — 전부 *"어떻게 고르나"*이고 *"무엇을
놓나"*가 없었다. 앵커 후보가 CSP당 13~14종인데 받을 칸이 없어서 **의존 주장
118건이 사슬에 진입하지 못했다.**

## 새 규칙이 아니라 승격이다

*"모든 칸에는 소비자(어느 조인·어느 판정이 읽는가)가 있어야 한다"* 는 이미
`request.json`의 설명에 적혀 있었고(multiZone을 받아 놓고 안 읽던 결함의 일반화),
*"필수 칸을 지우면 이름 붙은 판정이 실제로 사라져야 한다"* 는
`tests/test_required_fields.py`가 이미 검사하고 있었다. 둘 다 **산문과 테스트에
흩어진 채로** 있었을 뿐이다. 여기서는 그것을 항목의 **칸**으로 만든다:

    Ask(id, question, opens, basis, tier, …)
                      └ 소비자   └ 근거

`opens`나 `basis`가 비면 항목을 만들 수 없다(`__post_init__`이 죽는다). 그래서
**근거 없는 질문이 생길 수 없다** — 이 저장소가 세 번 물렸던 자리
(`cloudkb/CLAUDE.md` §5: 지어낸 뒤 다음 턴에 근거처럼 인용)에 대한 구조적 방어다.

## 근거의 세 갈래

    concern:<id>   요구사항 축이 이 질문을 이미 정의했다.
                   코퍼스 좌표(probe·citation)가 딸려 있고 CI가 대조한다.
    claim:<좌표>   3사 실측이 이 선택을 열었다(depkb).
    code:<파일>#<이름>
                   우리 코드가 이 값 없이는 판정을 못 낸다. **우리 사정이지
                   외부 사실이 아니다** — 그래서 갈래를 갈라 적는다.

셋 다 실재 검사가 붙는다(`app/core/cloudkb/tests/test_input_registry.py`,
`concern:`은 층이 갈려 `tests/test_input_registry_concerns.py`).

## 정적인 것과 파생되는 것

값 질문(`ASKS`)은 여기 손으로 적는다. **결정 질문은 적지 않는다** — depkb의
`closure().decisions`가 이미 내고 있고, 손으로 옮기면 다음 실측에서 어긋난다
(이 저장소가 반복해서 물린 사본 문제). `asks_for()`가 그때그때 뽑는다.

## 이 모듈이 하지 않는 것

값의 **모양**(타입·enum·범위)은 여기 없다. 그건 `request.json`이 진실이고,
`cloud_contract.field_type()`이 읽는다. 같은 사실을 두 곳에 적지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.core.cloudkb.appkb import contract as _contract
from app.core.cloudkb.depkb.closure import closure

#: 계층 — **없으면 못 재는가(필수) · 판정 하나가 닫히는가(권고) · 계획에 실릴
#: 뿐인가(맥락)**. 셋을 같은 얼굴로 물으면 사용자가 전부 필수로 읽고, 안 물으면
#: 계획을 다 만든 뒤에야 "그걸 줬으면 판정이 섰다"를 알게 된다.
REQUIRED = "required"
SUGGESTED = "suggested"
CONTEXT = "context"
DECISION = "decision"   # depkb에서 파생 — 앵커가 정해진 뒤에만 열린다

_TIERS = (REQUIRED, SUGGESTED, CONTEXT, DECISION)

#: 근거의 갈래. 접두사가 곧 검사 방법이라 문자열로 두지 않고 여기서 못 박는다.
CONCERN = "concern"   # requirements/knowledge/concerns.py의 id
CLAIM = "claim"       # depkb claims.json의 좌표
CODE = "code"         # 우리 코드 — 파일#이름

_BASIS_KINDS = (CONCERN, CLAIM, CODE)


@dataclass(frozen=True)
class Basis:
    """근거 하나. **좌표이지 문장이 아니다** — 문장은 인용한 쪽이 쓴다."""

    kind: str
    ref: str

    def __post_init__(self) -> None:
        if self.kind not in _BASIS_KINDS:
            raise ValueError(f"모르는 근거 갈래: {self.kind!r}")
        if not self.ref.strip():
            raise ValueError("근거에 좌표가 없다")

    def __str__(self) -> str:
        return f"{self.kind}:{self.ref}"


@dataclass(frozen=True)
class Ask:
    """받아야 할 것 하나.

    `question`은 사용자에게 그대로 나가는 말이고, `opens`는 **이 값이 없으면
    무엇이 안 되는가**다. 되묻기에 둘 다 실린다 — 왜 없이 물으면 사용자가 아무
    값이나 채운다는 것이 이 구조의 출발점이다(`REQUIRED_WHY`의 원래 취지).
    """

    id: str
    question: str
    #: 소비자 — 이 값이 여는 판정·조인. **비면 항목을 만들 수 없다.**
    opens: str
    basis: tuple[Basis, ...]
    tier: str
    #: `RESOURCE_SPEC`의 어느 칸으로 가는가. 결정 질문은 칸이 없다.
    spec_field: str = ""
    #: 이 CSP에서만 열린다. 빈 문자열이면 3사 공통.
    csp: str = ""
    #: 폐포에 이 자원이 들어올 때만 열린다(결정 질문). 빈 문자열이면 항상.
    needs_resource: str = ""
    #: 사람이 고를 수 있는 값들 — 계약이 enum으로 못 박지 못하는 것(앵커 목록은
    #: CSP마다 다르고 claims에서 나온다). 비어 있으면 스키마가 모양을 말한다.
    choices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Ask에 id가 없다")
        if not self.question.strip():
            raise ValueError(f"{self.id}: 질문이 없다")
        if not self.opens.strip():
            raise ValueError(
                f"{self.id}: 소비자(`opens`)가 없다 — 무엇을 여는지 말할 수 없는 "
                "칸은 받지 않는다(request.json이 적어 둔 규율)")
        if not self.basis:
            raise ValueError(
                f"{self.id}: 근거가 없다 — 근거 없는 질문은 우리 취향이지 지식이 "
                "아니다(cloudkb/CLAUDE.md §5)")
        if self.tier not in _TIERS:
            raise ValueError(f"{self.id}: 모르는 계층 {self.tier!r}")


#: 값 질문. **결정 질문은 여기 없다** — depkb에서 파생된다(`_decision_asks`).
#:
#: 순서가 되묻기 순서다. 조인 축을 먼저 받는 이유는 그 뒤의 모든 것이 CSP로
#: 갈리기 때문이다 — provider 없이는 앵커 목록조차 못 낸다.
ASKS: tuple[Ask, ...] = (
    Ask(
        id="join.provider",
        spec_field="provider",
        tier=REQUIRED,
        question="어느 클라우드에 배포합니까? (aws · azure · gcp)",
        opens="값 조인의 축 전부(비용·성능·번들·벤더 타입)이자 의존 주장의 색인 — "
              "주장 118건이 CSP별로 갈려 있어 이것 없이는 어떤 계획도 못 낸다",
        basis=(Basis(CODE, "app/core/regions.py#providers"),
               Basis(CODE, "app/core/cloudkb/depkb/closure.py#closure"),
               Basis(CODE, "app/core/cloudkb/nim_agent/design_tools.py#compose"),
               Basis(CODE, "app/core/cloudkb/appkb/verify.py#verify_against_requirements")),
    ),
    Ask(
        id="join.region",
        spec_field="region",
        tier=REQUIRED,
        question="어느 리전입니까? 지명으로 말해도 됩니다 — 코드로 바꿔 드립니다.",
        opens="단가·용량·탄소 데이터가 리전 코드로 색인돼 있다 — 지명이 그대로 "
              "오면 조인이 오류 없이 빈 답이 된다(실측)",
        basis=(Basis(CODE, "app/core/regions.py#resolve"),
               Basis(CODE, "app/core/cloudkb/nim_agent/design_tools.py#_attach_values"),
               Basis(CODE, "app/core/cloudkb/appkb/verify.py#verify_against_requirements")),
    ),
    # ── 위상축(신설) — 이 축이 통째로 비어 있었다 ────────────────────────────
    Ask(
        id="topology.workloads",
        spec_field="workloads",
        tier=REQUIRED,
        question="무엇을 배포합니까? 관리형 쿠버네티스 위에 올립니까, 가상 서버에 "
                 "직접 올립니까, 아니면 다른 형태입니까?",
        opens="폐포 계산의 입력 — 생성 순서·삭제 순서·서버가 대신 만드는 것·운영 "
              "경고가 전부 여기서 나온다. 이것이 없으면 실측 주장 118건이 계획에 "
              "닿지 못한다",
        basis=(Basis(CODE, "app/core/infra_planning.py#plan_for_anchors"),),
    ),
    Ask(
        id="budget.monthly",
        spec_field="monthlyBudgetUSD",
        tier=REQUIRED,
        question="월 예산이 얼마입니까? 다른 통화로 말해도 환산해 드립니다.",
        opens="비용 부합 판정의 기준값 — 없으면 판정문 자체가 안 나온다",
        basis=(Basis(CONCERN, "cn.cost-ceiling"),
               Basis(CODE, "app/core/cloudkb/appkb/verify.py#_CLOSES")),
    ),
    # ── 선택축 — 채우면 판정이 하나 열린다 ───────────────────────────────────
    Ask(
        id="spec.min_vcpu",
        spec_field="minVCpu",
        tier=SUGGESTED,
        question="이 워크로드가 필요로 하는 최소 vCPU가 몇 개입니까?",
        opens="스펙 선택 — 하한이 없으면 **스펙을 고르지 않는다**. 최저가를 고르는 "
              "것은 중립이 아니라 근거 없는 주장이다",
        basis=(Basis(CODE, "app/core/cloudkb/appkb/verify.py#_CLOSES"),
               Basis(CODE, "app/core/cloudkb/nim_agent/sizing_floor.py#resolve"),
               Basis(CODE, "app/core/cloudkb/nim_agent/design_tools.py#_attach_values")),
    ),
    Ask(
        id="spec.min_memory",
        spec_field="minMemoryGiB",
        tier=SUGGESTED,
        question="최소 메모리가 몇 GiB입니까? (vCPU 대신 이것만 줘도 됩니다)",
        opens="스펙 선택의 다른 축 — 둘 중 한 축만 있어도 선택이 열린다",
        basis=(Basis(CODE, "app/core/cloudkb/appkb/verify.py#_CLOSES"),
               Basis(CODE, "app/core/cloudkb/nim_agent/sizing_floor.py#resolve")),
    ),
    Ask(
        id="load.traffic_pattern",
        spec_field="trafficPattern",
        tier=SUGGESTED,
        question="부하가 상시로 깔립니까(steady), 간헐적으로 치솟습니까(spiky)?",
        opens="버스트 적합 판정 — 없으면 버스트 경고가 경고로만 남고 이 앱에 "
              "문제인지는 판정하지 않는다",
        basis=(Basis(CONCERN, "cn.traffic-shape"),
               Basis(CODE, "app/core/cloudkb/appkb/verify.py#_CLOSES")),
    ),
    Ask(
        id="avail.multi_zone",
        spec_field="multiZone",
        tier=SUGGESTED,
        question="한 가용영역이 통째로 죽어도 서비스가 계속돼야 합니까?",
        opens="가용영역 판정 · 서브넷 배치 조건 — aws EKS는 **서로 다른 AZ의 서브넷 "
              "둘**을 요구한다(같은 AZ 둘은 거부됐다, 실측)",
        basis=(Basis(CONCERN, "cn.redundancy-target"),
               Basis(CODE, "app/core/cloudkb/appkb/verify.py#_CLOSES"),
               Basis(CODE, "app/core/cloudkb/nim_agent/design_tools.py#_subnet_notes"),
               Basis(CLAIM, "aws/k8sCluster→subnet/existence")),
    ),
    # **권고에서 맥락으로 내렸다**(2026-08-01). 권고의 정의는 "채우면 이름 붙은
    # 판정이 하나 선다"인데 이 값으로 서는 판정이 없다 — `verify`의 규모 줄은
    # 문자 그대로 *"this knowledge base cannot judge"*다. 그렇다고 뺄 것은
    # 아니다: `sizing_floor.undecided_note`가 이것을 **되묻기의 근거**로 쓴다
    # ("동시 사용자 3,000인데 하한이 없다"가 "하한을 달라"보다 강한 요청이다).
    # 소비자가 있으니 남기고, 계층만 사실에 맞춘다.
    Ask(
        id="scale.expected_users",
        spec_field="expectedConcurrentUsers",
        tier=CONTEXT,
        question="예상 동시 사용자가 몇 명입니까?",
        opens="계획의 규모 진술과 되묻기의 근거 — **스펙을 정하지는 않는다**"
              "(동시 사용자→vCPU 변환은 1차 소스가 없어 KB에서 배제됐다)",
        basis=(Basis(CONCERN, "cn.expected-scale"),
               Basis(CODE, "app/core/cloudkb/nim_agent/sizing_floor.py#undecided_note"),
               Basis(CODE, "app/core/cloudkb/appkb/verify.py#verify_against_requirements")),
    ),
    # ── 맥락축 — 판정을 열진 않지만 계획에 실린다 ────────────────────────────
    Ask(
        id="scale.rps",
        spec_field="approxRequestsPerSecond",
        tier=CONTEXT,
        question="초당 요청 수를 대략 아십니까? (동시 사용자 대신 이것도 됩니다)",
        opens="규모 신호의 대안 — 소비자는 expectedConcurrentUsers와 같다",
        basis=(Basis(CONCERN, "cn.expected-scale"),
               Basis(CODE, "app/core/cloudkb/nim_agent/sizing_floor.py#undecided_note")),
    ),
    Ask(
        id="data.residency",
        spec_field="dataResidency",
        tier=CONTEXT,
        question="데이터가 특정 국가·지역에 머물러야 합니까?",
        opens="계획이 리전의 원본 표시 이름을 대조 자료로 싣는다 — **판정은 하지 "
              "않는다**(리전의 국가를 기계 판정할 소스가 없다, 실측)",
        basis=(Basis(CONCERN, "cn.data-residency"),
               Basis(CODE, "app/core/cloudkb/nim_agent/design_tools.py#compose"),
               Basis(CODE, "app/core/cloudkb/appkb/verify.py#verify_against_requirements")),
    ),
    Ask(
        id="carbon.low",
        spec_field="lowCarbonPreferred",
        tier=CONTEXT,
        question="에너지 사용이나 탄소 배출에 제약이 있습니까?",
        opens="리전 탄소 대조 — 판정이 아니라 대조다(출처 등급이 프로바이더마다 "
              "달라 서로 비교할 수 없다)",
        basis=(Basis(CONCERN, "cn.carbon-constraint"),
               Basis(CODE, "app/core/cloudkb/nim_agent/design_tools.py#_global_notices")),
    ),
)

#: 사용자에게 묻지 **않는** 칸과 그 이유. 스키마에 있는데 여기 없는 칸이 생기면
#: 테스트가 실패한다 — "빠뜨린 것"과 "안 묻기로 한 것"을 구별하기 위해서다.
NOT_ASKED: dict[str, str] = {
    "schemaVersion": "계약 판 — 스키마가 const로 못 박았고 생산자가 옮겨 적는다",
    "meta": "배관 — 부르는 쪽이 자기 맥락을 싣는 자리이지 우리가 묻는 값이 아니다",
    "regionAsWritten": "사용자가 쓴 원문을 생산자가 그대로 남기는 것이라 "
                       "물을 것이 없다(join.region의 부산물)",
}

#: **요구사항 단계에서 안 받고 인계로 넘기는 것**과 그 이유(2026-08-01).
#:
#: 계약이 받는 것은 "계획과 판정에 필요한 값"이다. 아래는 **렌더 시점에만**
#: 필요하고, 요구사항을 쓰는 사람이 그 시점에 알 이유가 없다. 받으면 어색하고
#: 안 받으면 사라지므로, **인계 항목으로 명시해서 낸다**(`cloud_artifact`의
#: `_handoff`) — 침묵과 인계는 다르다.
HANDOFF: dict[str, str] = {
    "containerRegistry": "컨테이너 이미지를 올릴 레지스트리. 이미지 태그와 같은 "
                         "종류이고 태그는 CI가 정한다 — 둘 중 하나만 요구사항에서 "
                         "받으면 선이 이상하다. 없으면 매니페스트에 자리표시자가 "
                         "남고, 그 자리표시자가 곧 인계 표시다",
    "ingressController": "인그레스 컨트롤러와 클래스. 우리 실측은 '컨트롤러를 "
                         "깔지는 사람이 정한다'까지만 말한다(k8sIngress 라운드)",
    "tlsCertificate": "TLS 인증서·시크릿. 운영·보안 결정이다",
}


@lru_cache(maxsize=1)
def by_id() -> dict[str, Ask]:
    return {a.id: a for a in ASKS}


@lru_cache(maxsize=1)
def by_field() -> dict[str, Ask]:
    return {a.spec_field: a for a in ASKS if a.spec_field}


def tier_of(spec_field: str) -> str:
    """그 칸이 어느 계층인가. 모르는 칸이면 빈 문자열."""
    ask = by_field().get(spec_field)
    return ask.tier if ask else ""


@lru_cache(maxsize=8)
def anchors_for(csp: str) -> tuple[str, ...]:
    """이 CSP에서 앵커가 될 수 있는 자원 — `topology.workloads`가 받는 값.

    **claims에서 뽑는다**(주체 집합 중 폐포가 서는 것). 손으로 적으면 다음
    실측에서 어긋나고, 그건 이 저장소가 반복해서 물린 자리다.
    """
    import json
    from pathlib import Path

    path = (Path(__file__).resolve().parent / "cloudkb" / "depkb" / "claims.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for subject in sorted({c["subject"] for c in doc["claims"] if c["csp"] == csp}):
        try:
            closure(subject, csp)
        except KeyError:
            continue
        out.append(subject)
    return tuple(out)


def _decision_asks(csp: str, workloads: tuple[str, ...]) -> tuple[Ask, ...]:
    """앵커가 정해진 뒤 열리는 결정 — **depkb가 낸 것을 그대로 옮긴다.**

    손으로 목록을 적지 않는 이유는 하나다: 새 실측이 결정을 하나 더 열면 여기가
    조용히 낡는다. `closure().decisions`가 진실이고 이 함수는 사영이다.
    """
    seen: dict[str, Ask] = {}
    for anchor in workloads:
        try:
            plan = closure(anchor, csp)
        except KeyError:
            continue  # 이 CSP에서 미측정인 앵커 — 계획 쪽이 따로 말한다
        for decision in plan.decisions:
            subject, _, obj = decision.about.partition("→")
            ask = Ask(
                id=f"decision.{csp}.{subject}.{obj.replace('|', '-or-')}",
                spec_field="",
                tier=DECISION,
                csp=csp,
                needs_resource=subject,
                question=f"{subject}의 {obj}를 어떻게 정합니까? — {decision.detail}",
                opens=f"{subject} 생성 단계 — 컨트롤 플레인이 이 선택을 "
                      f"요구한다({decision.kind})",
                basis=(Basis(CLAIM, f"{csp}/{decision.about}/existence"),),
                choices=tuple(obj.split("|")) if "|" in obj else (),
            )
            seen[ask.id] = ask
    return tuple(seen[k] for k in sorted(seen))


def asks_for(csp: str = "", workloads: tuple[str, ...] = ()) -> tuple[Ask, ...]:
    """지금 **물을 수 있는** 것 전부 — 선행조건이 채워진 순서대로.

    선행조건이 실재하는 이유: `decision.*`은 앵커를 모르면 물을 수 없고, 앵커
    질문은 CSP를 모르면 목록조차 못 낸다. 그래서 계약은 평면이지만 **묻는 순서는
    평면이 아니다** — 그 사실이 지금까지 어디에도 없었다.

    Args:
        csp: 정해졌으면 그 값. 없으면 CSP 의존 질문은 안 나온다.
        workloads: 사용자가 고른 앵커들. 없으면 결정 질문은 안 나온다.
    """
    out = list(ASKS)
    if csp:
        out = [a for a in out if not a.csp or a.csp == csp]
        out.extend(_decision_asks(csp, tuple(workloads)))
    return tuple(out)


def choices_for(ask: Ask, csp: str = "") -> tuple[str, ...]:
    """그 질문이 받는 값들 — 앵커 목록처럼 CSP에 매인 것을 여기서 푼다."""
    if ask.id == "topology.workloads":
        return anchors_for(csp) if csp else ()
    if ask.id == "join.provider":
        from app.core import regions

        return tuple(regions.providers())
    if ask.choices:
        return ask.choices
    field_name = ask.spec_field
    if field_name:
        spec = _contract.request_schema().get("properties", {}).get(field_name, {})
        return tuple(spec.get("enum", ()))
    return ()


def missing(spec: dict, tier: str = REQUIRED) -> tuple[Ask, ...]:
    """그 계층에서 아직 답이 없는 질문들.

    **규모·하한은 한 쌍이다** — 둘 중 하나만 있으면 같은 것을 두 번 묻지 않는다.
    쌍의 근거는 계약이 아니라 소비자다: 스펙 선택은 둘 중 큰 축이 필터를 잡고,
    규모 신호는 둘 다 같은 되묻기 근거로 쓰인다.
    """
    pairs = (("minVCpu", "minMemoryGiB"),
             ("expectedConcurrentUsers", "approxRequestsPerSecond"))
    satisfied: set[str] = set()
    for pair in pairs:
        if any(name in spec for name in pair):
            satisfied |= set(pair)
    return tuple(a for a in ASKS
                 if a.tier == tier and a.spec_field
                 and a.spec_field not in spec
                 and a.spec_field not in satisfied)


@dataclass(frozen=True)
class Gap:
    """못 받은 것 하나 — 화면·되묻기가 그대로 쓰는 꼴."""

    ask: Ask
    tier: str
    question: str
    why: str
    choices: tuple[str, ...] = field(default_factory=tuple)


def gaps(spec: dict, csp: str = "") -> tuple[Gap, ...]:
    """지금 사용자에게 낼 것 — 필수 → 권고 → 맥락 순.

    결정 질문(`DECISION`)은 여기 없다. 그건 앵커가 정해진 **뒤** 계획을 낼 때
    열리고, 그 자리에서 `asks_for(csp, workloads)`가 낸다.
    """
    out: list[Gap] = []
    for tier in (REQUIRED, SUGGESTED, CONTEXT):
        for ask in missing(spec, tier):
            out.append(Gap(ask=ask, tier=tier, question=ask.question,
                           why=ask.opens, choices=choices_for(ask, csp)))
    return tuple(out)
