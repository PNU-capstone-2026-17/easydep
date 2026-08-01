"""클라우드 네이티브 관심사 — **사용자가 쓰지 않은 요구사항**을 드러내는 축.

## 규칙과 무엇이 다른가 (왜 `rules.py`가 아닌가)

판정 방향이 반대다.

  - `rules.py`  — 우리가 **낸 산출물**을 보고 위반을 찾는다. 위반이면 결함이다.
  - 여기        — 요구사항에 **없는 것**을 찾는다. 없는 것은 결함이 아니라 **뒤 단계가
    정해야 할 일**이다.

한 파일에 두면 심각도 어휘가 곧 갈린다. "위반했다"와 "안 적혔다"를 같은 목록에 두면
검증 프롬프트가 후자를 지적으로 바꾼다 — 그러면 사용자가 안 쓴 것이 전부 결함이 되고
오탐이 대부분이 된다(`docs/cloud-native-requirements.md` §6의 판단).

형식은 checklist-based reading(CBR)이다 — 질문 목록을 들고 요구사항을 읽어 **누락**을
찾는다. 그래서 항목이 규범문이 아니라 질문문이고, 미충족이 결함이 아니라 인계다.

## 2026-08-02 실측 재도출 — 문헌 축을 버렸다

**앞 판(29건)은 벤더 회색문헌 코퍼스(346편)에서 도출했다.** 그 판의 기록·측정은
`docs/cloud-native-requirements.md` §6과 커밋 이력에 유효하게 남아 있다. 버린 이유는
계보 감사(`document/archive/contract-lineage-audit-2026-08-02.md`)가 보인 것과 같다 —
벤더 문헌은 "벤더가 관심사라 부르는 것"의 표본이고, 우리 실측 커버리지와 괴리가
있었다(29건 중 PURE 실물 0이 7건 · 탄소는 오탐뿐). 사용자 결정(2026-08-02):
**환경측 실측만을 근거로 제로베이스 재도출.**

**메타 특성은 그대로다**(`META_CHARACTERISTIC`) — 바뀐 것은 입장 관문이다:

    구  판   문헌 좌표(doc_id·probe)가 실재하면 후보 → E1~E3 게이트
    신  판   **컨트롤 플레인 실측이 "침묵의 답"을 보였을 때만** 후보 → E1 게이트

"침묵의 답"이란: 요구사항이 그 결정을 안 정했을 때 **환경이 실제로 무엇을 하는지를
우리가 관측한 것**이다. claims.json의 부류가 그 관측이다 —

    server-default   명시 안 하면 서버가 기본값을 채운다 (기본 VPC·default 네트워크…)
    server-implicit  플랫폼이 자원을 암묵 합성한다 (LB·디스크·identity·노드풀…)
    동반 정리         삭제가 부속 자원까지 지운다 (Service→LB·PVC→디스크)
    잔존             삭제해도 데이터가 남는다 (OS 디스크·부트 디스크)
    무방비           실행 중 결속 변이를 컨트롤 플레인이 막지 않는다 (15곳)

거부(required) 계열은 관심사가 아니다 — 거부는 "환경이 대신 정함"이 아니라 계획이
처리할 제약이고, 그쪽은 `app/core/infra_planning`이 이미 소비한다.

**군집은 우리 구성이다.** 관측 53좌표를 결정 7개로 묶은 것은 우리 판단이고, 그렇다고
표시한다. 관측 자체는 실측이다(전 좌표가 `claims.json`에 실재해야 하며
`verify_concerns`가 CI에서 대조한다).

**좌표 유일성 규칙(구 판 doc_id 유일성의 계승)**: 한 claim 좌표는 한 관심사에만
속한다. 두 관심사가 같은 좌표를 인용하면 미분화 신호다 — 기계가 막는다.

**신호(열쇠말) 승계 규칙**: 결정론 층의 열쇠말은 구 판에서 코퍼스로 채굴·측정한
것이다. 새 관심사의 질문이 구 관심사와 **같은 결정**을 물을 때만 승계한다
(`cn.load-shape` ← `cn.traffic-shape` · `cn.reachability` ← `cn.network-exposure`).
나머지는 비워 둔다 — 억지 열쇠말은 오탐이 결정론의 이름을 달고 나오는 길이다.

## 수요측은 근거로 인정하지 않는다 (사용자 결정)

코퍼스의 사용자 진술(다중화 24건·규모 4건 등)은 이 축의 입장 근거가 아니다 —
실증이 제한된 범위·워크로드·환경에서 이뤄질 것이기 때문이다. 수요측 실물이 있는
결정은 **계약**(`request.json`)이 직접 묻는다(multiZone·scale 등 — 계보 감사 판정표
참조). 두 축의 역할이 갈린다: 계약은 "값이 뭔가"를 받고, 관심사는 "정해졌는가"를
읽는다.

## 심각도 축을 두지 않는다

값이 하나뿐이기 때문이다 — 관심사는 전부 "안 정했으면 인계"다. 값 하나짜리 축은
분류가 아니라 장식이고, 나중에 두 번째 값이 생기면 그때 만든다.

## 명세에는 들어가지 않는다

`spec.black-box-no-internal-components`·`spec.no-protocol-mechanics`가 명세에 내부
컴포넌트·프로토콜을 금지한다. 관심사는 NFR 층과 나란한 별도 산출물에 살고
유스케이스 명세는 손대지 않는다(`docs/cloud-native-requirements.md` §5).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core import advisory
from app.requirements.knowledge import basis

#: **메타 특성** — 무엇이 관심사가 될 수 있는지를 정하는 단일 기준(Nickerson 외 2013).
#:
#: 2026-08-02 재도출에서도 이 문장은 그대로다. 바뀐 것은 "환경이 대신 정한다"의
#: 근거가 문헌 서술에서 **컨트롤 플레인 관측**으로 올라간 것이다.
META_CHARACTERISTIC = (
    "요구사항 단계에서 확정할 수 있고, 확정하지 않으면 클라우드 실행 환경이 그 답을 "
    "대신 정해 버리는 결정."
)

#: ISO/IEC 25010:2023 제품 품질 특성 9종. **벤더 중립 대조 축**이다.
#:
#: 2023 개정판을 쓴다 — `usability`가 `interaction capability`로, `portability`가
#: `flexibility`로 바뀌었고 `safety`가 최상위로 신설됐다. 2011판 이름을 쓰면 매핑이
#: 조용히 옛 모델을 가리킨다.
ISO25010: tuple[str, ...] = (
    "functional suitability",
    "performance efficiency",
    "compatibility",
    "interaction capability",
    "reliability",
    "security",
    "maintainability",
    "flexibility",
    "safety",
)

#: 이 축의 근거 라벨과, 관심사를 실은 모든 출력에 붙는 고지.
#: **어떤 출력 경로에서도 고지를 떼면 안 된다.**
#:
#: 관측(claims)은 실측이지만 **"이것이 요구사항 단계의 결정이다"라는 승격과 군집은
#: 우리 구성**이다 — 그래서 basis는 여전히 `inferred`다.
EVIDENCE = advisory.EVIDENCE
ADVISORY_NOTICE = advisory.ADVISORY_NOTICE


@dataclass(frozen=True)
class Concern:
    """관심사 하나. **질문은 우리 표현이고, 근거는 실측 좌표다.**"""

    id: str
    #: 요구사항에 이 관심사가 다뤄졌는지 묻는 한 문장. 프롬프트에 그대로 들어가므로
    #: 영어로 쓴다. **질문이지 규범이 아니다** — "그래야 한다"가 아니라 "정해졌는가"다.
    #:
    #: **하나의 결정만 묻는다**(입도 규칙). 둘을 묻고 있으면 관심사를 쪼개야 한다.
    question: str
    #: **침묵의 답** — 이 결정을 안 정하면 환경이 실제로 무엇을 하는지, 관측 그대로.
    #: 구 판의 `cloud_specific`(E2의 답) 자리이고 뜻이 좁아졌다: 서술이 아니라 관측이다.
    cloud_specific: str
    #: **실측 좌표** — `claims.json`의 주장 키(`csp/subject->object/question`).
    #: 전 좌표가 실재해야 하고(`verify_concerns`), 한 좌표는 한 관심사에만 속한다.
    claims: tuple[str, ...] = ()
    #: claims 밖의 실측 KB를 근거로 쓸 때 그 패키지 이름(예: "perfkb").
    #: claims가 비어 있으면 이것이 있어야 한다 — 근거 없는 관심사는 없다.
    kb_ref: str = ""
    #: 사람이 읽을 근거 요약.
    citation: str = ""
    #: 이 관심사가 걸리는 ISO/IEC 25010:2023 품질 특성(`ISO25010`의 값들).
    #: 매핑은 우리 구성이고, 빈 튜플은 누락이 아니라 사실이다.
    iso25010: tuple[str, ...] = ()
    #: **오늘 실재하는 기계 소비자** — 이 답이 실제로 흘러 들어가는 `RESOURCE_SPEC` 칸.
    #: `None`은 배제가 아니라 범위 표시다(받아 줄 기계가 아직 없다 — `noted`).
    consumer: str | None = None
    #: **배포 계획이 소비할 수 없는 관심사라면 그 이유**(어느 단계의 것인가).
    #: `consumer=None`(예정)과 다르다 — 이쪽은 "안 하기로 한 것"(경계)이다.
    out_of_scope: str = ""
    #: 결정론 층의 열쇠말(소문자). **승계 규칙**: 구 판에서 코퍼스로 채굴·측정한
    #: 열쇠말을, 질문이 같은 결정일 때만 물려받는다. 비어 있으면 LLM 층만 판정한다.
    signals: tuple[str, ...] = ()
    evidence: str = EVIDENCE

    def __post_init__(self) -> None:
        if not self.claims and not self.kb_ref:
            raise ValueError(f"{self.id}: 실측 좌표가 없다 — 근거 없는 관심사는 없다")

    @property
    def hedged(self) -> bool:
        """이 근거로 말할 때 한계를 밝혀야 하는가. 이 축은 **항상 참**이다 —
        관측은 실측이어도 승격·군집이 우리 구성이라서다."""
        return basis.needs_hedge(self.evidence)

    def prompt_line(self) -> str:
        """판정 프롬프트 한 줄."""
        return f"- {self.id}: {self.question}"


# ---------------------------------------------------------------------------
# 관심사 목록 — 2026-08-02 실측 재도출 (7건 · claims 53좌표 + perfkb 1)
#
# 새 관심사를 넣으려면: 컨트롤 플레인 실측(claims 또는 실측 KB)이 "침묵의 답"을
# 보여야 하고, `python -m app.requirements.knowledge.verify_concerns`를 돌린다.
# ---------------------------------------------------------------------------

CONCERNS: tuple[Concern, ...] = (
    Concern(
        id="cn.network-isolation",
        question=(
            "Do the requirements say whether the application needs a dedicated, "
            "isolated network, or whether the provider's default network is "
            "acceptable?"
        ),
        cloud_specific=(
            "정하지 않으면 기본 네트워크에 놓인다 — aws는 VPC를 생략하면 기본 VPC가 "
            "대체하고(DryRunOperation 실측), gcp는 default 네트워크·auto 모드 서브넷을 "
            "서버가 채운다(서버가 채운 subnetwork 실물 기록). azure는 대체가 없어 "
            "생략이 거부된다 — 3사 반전 자체가 이 결정이 사용자 몫이라는 근거다."
        ),
        claims=(
            "aws/firewall->network/existence",
            "aws/vm->subnet/existence",
            "gcp/firewall->network/existence",
            "gcp/k8sCluster->network/existence",
            "gcp/k8sCluster->subnet/existence",
            "gcp/loadBalancer->network/existence",
            "gcp/nic->subnet/existence",
        ),
        citation="depkb 실측 7좌표 — server-default 대체(기본 VPC·default 네트워크·auto 서브넷)",
        iso25010=("security",),
    ),
    Concern(
        id="cn.reachability",
        question=(
            "Do the requirements say what must be reachable from the public internet "
            "and what must not?"
        ),
        cloud_specific=(
            "정하지 않으면 기본 방화벽·스킴이 접근성을 정한다 — aws는 SG를 생략하면 "
            "기본 SG가 부착되고(서버가 채운 그룹 실물), LB는 내부/외부 스킴이 자원 "
            "요구까지 가른다(azure frontend 배타 술어 · gcp INTERNAL은 서브넷 필수). "
            "노출은 우리가 정할 수 없는 값이다."
        ),
        claims=(
            "aws/nic->firewall/existence",
            "aws/vm->firewall/existence",
            "azure/loadBalancer->subnet|publicIp|publicIPPrefix/existence",
            "gcp/loadBalancer->subnet/existence",
        ),
        citation="depkb 실측 4좌표 — 기본 SG 부착·LB 스킴 조건부",
        iso25010=("security",),
        # 승계: cn.network-exposure(구 판, 같은 결정) — 코퍼스 채굴·측정 이력 유효.
        signals=("public", "private network", "vpn", "firewall", "내부망", "방화벽"),
    ),
    Concern(
        id="cn.platform-owned-resources",
        question=(
            "Do the requirements constrain who creates and controls infrastructure "
            "resources (naming, audit, ownership), or may the platform create them "
            "implicitly?"
        ),
        cloud_specific=(
            "정하지 않으면 플랫폼이 자원을 암묵 합성한다 — k8s Service가 LB를(3사, "
            "노드 0에서도), PVC가 디스크를(WaitForFirstConsumer 실측), AKS가 노드 RG에 "
            "vnet을, GKE가 default-pool을, VM 생성이 NIC·디스크를 만든다. 합성물은 "
            "우리 명명·감사 규칙 밖에서 생긴다."
        ),
        claims=(
            "aws/k8sCluster->firewall/existence",
            "aws/k8sPvc->disk/existence",
            "aws/k8sService->loadBalancer/existence",
            "aws/vm->disk/existence",
            "aws/vm->nic/existence",
            "azure/k8sCluster->subnet/existence",
            "azure/k8sPvc->disk/existence",
            "azure/k8sPvc->fileSystem/existence",
            "azure/k8sService->loadBalancer/existence",
            "gcp/k8sCluster->k8sNodeGroup/existence",
            "gcp/k8sIngress->loadBalancer/existence",
            "gcp/k8sPvc->disk/existence",
            "gcp/k8sService->loadBalancer/existence",
        ),
        citation="depkb 실측 13좌표 — server-implicit 합성(LB·디스크·노드풀·vnet·NIC)",
        iso25010=("maintainability",),
    ),
    Concern(
        id="cn.data-fate-on-removal",
        question=(
            "Do the requirements say what data or resources must survive — or must "
            "not survive — when a workload is removed?"
        ),
        cloud_specific=(
            "정하지 않으면 경로가 운명을 정하고, 경로마다 반대다 — VM을 지우면 OS· "
            "부트 디스크는 남고(azure·gcp 잔존 실측), PVC를 지우면 디스크가 함께 "
            "사라지며(3사 동반 정리), Service를 지우면 LB가 함께 죽는다. 같은 데이터가 "
            "올라탄 경로에 따라 남거나 사라진다."
        ),
        claims=(
            "aws/k8sPvc->disk/lifecycle",
            "aws/k8sService->loadBalancer/lifecycle",
            "azure/k8sPvc->disk/lifecycle",
            "azure/k8sPvc->fileSystem/lifecycle",
            "azure/k8sService->loadBalancer/lifecycle",
            "gcp/k8sIngress->loadBalancer/lifecycle",
            "gcp/k8sPvc->disk/lifecycle",
            "gcp/k8sService->loadBalancer/lifecycle",
            "azure/vm->disk/lifecycle",
            "gcp/vm->disk/lifecycle",
        ),
        citation="depkb 실측 10좌표 — 동반 정리 8 + 디스크 잔존 2",
        iso25010=("reliability", "security"),
    ),
    Concern(
        id="cn.runtime-binding-protection",
        question=(
            "Do the requirements identify flows that must keep working while the "
            "system runs, so that operations must guard them?"
        ),
        cloud_specific=(
            "실행 중 결속 변이를 컨트롤 플레인이 막지 않는다 — 공인 IP 분리·방화벽 "
            "해제·라우트 삭제·디스크 분리 전부 API가 수락하고 서비스만 죽는다(무방비 "
            "12좌표, 상실·회복 실측). apply 전 검사로는 영영 안 잡히므로, 지켜야 할 "
            "흐름은 요구사항이 지목해야 운영이 지킨다. (IAM 축 무방비 2건은 "
            "cn.cloud-api-access 소속.)"
        ),
        claims=(
            "aws/subnet->internetGateway/function",
            "aws/vm->firewall/function",
            "aws/vm->publicIp/function",
            "azure/globalDns->network/function",
            "azure/loadBalancer->vm/function",
            "azure/nic->publicIp/function",
            "azure/subnet->firewall/function",
            "azure/vm->disk/function",
            "gcp/k8sService->k8sCluster/function",
            "gcp/network->internetGateway/function",
            "gcp/vm->firewall/function",
            "gcp/vm->publicIp/function",
        ),
        citation="depkb 실측 12좌표 — 무방비 변이 + 기능 신호 상실·회복",
        iso25010=("reliability",),
    ),
    Concern(
        id="cn.cloud-api-access",
        question=(
            "Do the requirements say whether the application itself calls cloud "
            "APIs and therefore needs a cloud identity?"
        ),
        cloud_specific=(
            "정하지 않으면 3사가 다른 답을 정한다 — gcp는 무권한(serviceAccounts:null "
            "실물), azure AKS는 identity를 서버가 합성, aws VM은 무프로필로 부팅되고 "
            "이후 앱의 클라우드 호출이 죽는다(EKS CSI 'no EC2 IMDS role found' 기제를 "
            "VM 층에서 격리 실측). 붙였다 떼는 것도 무방비다."
        ),
        claims=(
            "aws/vm->iamRole/existence",
            "azure/vm->iamRole/existence",
            "gcp/vm->iamRole/existence",
            "aws/vm->iamRole/function",
            "aws/k8sCluster->iamRole/existence",
            "aws/k8sCluster->iamRole/function",
            "azure/k8sCluster->iamRole/existence",
        ),
        citation="depkb 실측 7좌표 — iamRole 3사 3색 + 기능 결속",
        iso25010=("security",),
    ),
    Concern(
        id="cn.load-shape",
        question=(
            "Do the requirements describe the shape of the load over time — steady, "
            "bursty, or scheduled peaks — and not only its size?"
        ),
        cloud_specific=(
            "정하지 않으면 카탈로그 경제가 답을 정한다 — 하한만으로 고르면 최저가 "
            "상위가 버스트 인스턴스다(perfkb 실측: 최저가 100 중 37이 버스트·구세대· "
            "공유CPU). 상시 부하에 버스트를 주면 크레딧 소진 시점에 성능이 꺼진다."
        ),
        kb_ref="perfkb",
        citation="perfkb 실측 — 최저가 100 중 37 함정(버스트·구세대·공유CPU)",
        consumer="RESOURCE_SPEC.trafficPattern",
        iso25010=("performance efficiency",),
        # 승계: cn.traffic-shape(구 판, 같은 결정) — 코퍼스 채굴·측정 이력 유효.
        signals=("peak", "burst", "spike", "seasonal", "피크", "급증", "성수기"),
    ),
)

BY_ID: dict[str, Concern] = {c.id: c for c in CONCERNS}


def unmapped_characteristics() -> tuple[str, ...]:
    """어떤 관심사도 걸리지 않은 ISO/IEC 25010 특성.

    실측 재도출 뒤 이 목록은 커졌다 — 실행 환경이 대신 정하는 것을 관측으로만
    입장시키면, 환경이 대신 정하지 않는 특성(interaction capability 등)은 당연히
    비고, **우리가 아직 안 잰 특성**도 빈다. 두 뜻을 이 함수는 못 가른다 —
    해석은 사람이 한다.
    """
    covered = {c for concern in CONCERNS for c in concern.iso25010}
    return tuple(c for c in ISO25010 if c not in covered)


def all_ids() -> tuple[str, ...]:
    """관심사 id 전부(선언 순서)."""
    return tuple(c.id for c in CONCERNS)


def chunks(size: int) -> tuple[tuple[str, ...], ...]:
    """관심사 id를 `size`개씩 묶는다. `size<=0`이면 한 덩어리(전부).

    **선언 순서 그대로 자른다.** 주제별로 묶으면 "나눠 물으면 나아지는가"를 재는 실험에
    주제 응집이라는 두 번째 변수가 섞인다 — 그러면 무엇이 효과를 냈는지 못 가린다.
    """
    ids = all_ids()
    if size <= 0:
        return (ids,)
    return tuple(ids[i:i + size] for i in range(0, len(ids), size))


def prompt_block(only: tuple[str, ...] | None = None) -> str:
    """판정 프롬프트에 실을 관심사 목록.

    고지(`ADVISORY_NOTICE`)를 함께 싣는다 — 관측은 실측이어도 "요구사항이 정할
    결정"이라는 승격은 우리 구성이라고 판정자에게도 말해야 한다.
    """
    picked = CONCERNS if only is None else tuple(BY_ID[i] for i in only)
    lines = [c.prompt_line() for c in picked]
    return "\n".join([*lines, "", ADVISORY_NOTICE])
