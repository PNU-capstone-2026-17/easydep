"""클라우드 네이티브 관심사 — **사용자가 쓰지 않은 요구사항**을 드러내는 축.

## 규칙과 무엇이 다른가 (왜 `rules.py`가 아닌가)

판정 방향이 반대다.

  - `rules.py`  — 우리가 **낸 산출물**을 보고 위반을 찾는다. 위반이면 결함이다.
  - 여기        — 요구사항에 **없는 것**을 찾는다. 없는 것은 결함이 아니라 **뒤 단계가
    정해야 할 일**이다.

한 파일에 두면 심각도 어휘가 곧 갈린다. "위반했다"와 "안 적혔다"를 같은 목록에 두면
검증 프롬프트가 후자를 지적으로 바꾼다 — 그러면 사용자가 안 쓴 것이 전부 결함이 되고
오탐이 대부분이 된다(`docs/cloud-native-requirements.md` §6의 판단).

## 이것은 분류체계가 아니라 **읽기 체크리스트**다

형식의 근거는 checklist-based reading(CBR)이다 — 검토자가 질문 목록을 들고 요구사항
문서를 읽어 **누락 결함**을 찾는, 요구사항 인스펙션의 확립된 기법이다. 그래서 항목이
규범문("그래야 한다")이 아니라 질문문("정해졌는가")이고, 미충족이 결함이 아니라 사람이
판단할 인계 항목이다. 분류체계였다면 아무 요구도 안 걸리는 항목이 결함이겠지만,
체크리스트에서는 **아무도 안 쓴 항목이 가장 정보량이 큰 항목**이다.

도출 절차는 Nickerson 외(2013)의 분류체계 개발법에서 **메타 특성과 종료 조건**만 빌려
온다(차원×특성 구조는 안 쓴다 — 우리 산출물은 1층 목록이다). 근거 코퍼스가 전량
회색문헌(벤더 프레임워크)이라 출처 취급은 Garousi 외의 MLR 지침을 따른다. 전거와 각
규칙의 검증 결과는 `docs/cloud-native-requirements.md` §6.6에 있다.

## 도출 규칙 (2026-07-28 확정 · 문헌 대조 후 개정)

목록이 **어떻게 이 목록이 됐는지**에 답이 없으면 인용이 아무리 정확해도 편의표본이다.
실제로 그랬다 — 첫 판 12건은 346편 중 46편짜리 두 절에서 11건을 뽑았고, 이 저장소가
이미 잰 NFR 분포에서 **가장 큰 덩어리(지연/성능 20%)에 대응 관심사가 하나도 없었다.**

**메타 특성** — 무엇이 관심사가 될 수 있는지의 단일 기준이다(`META_CHARACTERISTIC`).
Nickerson의 방법에서 모든 특성은 메타 특성의 논리적 귀결이어야 하고, 메타 특성은
산출물의 **목적과 사용자**에서 나온다. 아래 배제 기준은 독립된 규칙 목록이 아니라 메타
특성에서 따라 나오는 것들이다 — 그래야 기준을 늘릴 때 사후 정당화가 되지 않는다.

  - **E1 요구사항 시점 판정 가능** — 설계·구현 산출물 없이 요구사항 텍스트만 보고
    "정해졌는가"를 물을 수 있어야 한다. ADR 유지·테스트 전략·DevOps 문화가 여기서
    걸린다(좋은 지침이지만 요구사항이 아니다).
  - **E2 클라우드 특이성** — 정하지 않았을 때 **실행 환경이 답을 대신 정해 버리는** 것만
    해당한다. 전통 개발에서도 같으면 일반 RE의 몫이다. `Concern.cloud_specific`이
    **필수 필드**인 이유이고, 이 축의 신규성 주장이 정확히 여기 걸려 있다.
  - **E3 명세 밖** — 유스케이스 명세에 들어갈 것은 제외한다(아래 "명세에는 …").
  - **E4 좌표 가능** — 코퍼스 문서 하나로 인용할 수 있어야 한다. 이건 메타 특성의 귀결이
    아니라 **근거 규율**이다(무엇이 관심사인가가 아니라 무엇을 주장해도 되는가의 문제).

**입도 = 분화(robustness)** — Nickerson의 종료 조건은 특성이 관심 대상들을 **구별해
주어야 한다**고 요구한다. 우리에게 대상은 요구사항이므로: 두 관심사가 갈리려면 *한쪽만
다루고 다른 쪽은 안 다루는 요구사항이 실제 코퍼스에 있어야 한다.* 이건 선언이 아니라
**측정 대상**이다(§6.6에 측정 결과와 미분화로 나온 쌍).

`doc_id` 유일성은 **기준이 아니라 트립와이어**다. 문헌의 기준은 대상 구별이지 출처
구별이 아니어서, 같은 문서를 둘이 인용한다는 사실만으로는 아무것도 증명되지 않는다.
다만 값싸고 일찍 울린다 — 첫 판의 `cn.observability`를 이게 잡았고, 갈라 보니 "무엇이
기록으로 남는가"와 "운영이 무엇을 보는가"는 실제로 따로 정해지는 것이 맞았다.

**소비자는 선정 기준이 아니라 범위 표시다.** Nickerson의 주관적 종료 조건
*comprehensive*(대상 영역의 모든 것이 분류될 수 있어야 한다)를 소비자로 거르면 바로
어긴다. 게다가 **"근거가 없어서 뺐다"와 "우리 코드가 안 읽어서 뺐다"가 구별되지 않는다**
— 첫 판에서 규제 준수를 그렇게 조용히 버렸고, 그건 표집 편향과 구현 편향을 섞은 것이다.
`consumer=None`도 목록에 남고, 산출물에서 `handoff`(소비자 있음)와 `noted`(없음)로
갈라 센다.

## 왜 이 목록이 임의 사전이 아닌가

`docs/ARCHITECTURE.md`가 경계하는 "임의 사전 금지"가 여기에 정확히 걸린다. 클라우드
관심사는 얼마든지 지어낼 수 있고, 지어내면 그건 우리 취향이지 지식이 아니다.

그래서 규칙과 같은 선을 긋는다 — **문장은 우리 것, 좌표는 코퍼스**. 모든 관심사가
`app/deployment/patternkb`의 문서 id와 그 본문에 실재하는 열쇠 구절(`probe`)을 달고,
`verify_concerns`가 대조한다.

**그리고 이 축은 규칙 축이 못 하는 것을 한다: 대조가 CI에서 돈다.** 도서 인용
(`verify_citations`)은 로컬 사본이 있어야 돌아서 자동 검사가 될 수 없었는데, 패턴
코퍼스는 저장소 안에 커밋돼 있다(`app/deployment/data/pattern-corpus.json.gz`, 346편).

## 심각도 축을 두지 않는다

값이 하나뿐이기 때문이다 — 관심사는 전부 "안 정했으면 인계"다. 값 하나짜리 축은
분류가 아니라 장식이고, 나중에 두 번째 값이 생기면 그때 만든다. (`basis.py`가
`observed` 등급을 미리 만들지 않은 것과 같은 이유: 없는 등급을 먼저 만들면 라벨이
등급을 못 받는다.)

## 명세에는 들어가지 않는다

`spec.black-box-no-internal-components`·`spec.no-protocol-mechanics`가 명세에 내부
컴포넌트·프로토콜을 금지한다. 클라우드 리소스를 시나리오 스텝에 넣으면 **에이전트가
자기 규칙을 위반한다.** 관심사는 NFR 층과 나란한 별도 산출물에 살고 유스케이스 명세는
손대지 않는다(`docs/cloud-native-requirements.md` §5).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core import advisory
from app.requirements.knowledge import basis

#: **메타 특성** — 무엇이 관심사가 될 수 있는지를 정하는 단일 기준(Nickerson 외 2013).
#:
#: 상수로 두는 이유: 배제 기준 E1~E3는 이 문장의 귀결이어야 하고, 새 관심사를 넣을 때
#: 대조할 것이 목록이 아니라 **이 한 문장**이어야 사후 정당화가 안 된다. 기준을 늘리고
#: 싶어지면 그건 메타 특성이 틀렸다는 신호이므로 여기를 고치고 목록을 다시 훑는다.
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

#: 이 축의 유일한 근거 라벨과, 관심사를 실은 모든 출력에 붙는 고지.
#: **어떤 출력 경로에서도 고지를 떼면 안 된다.**
#:
#: 둘 다 `patternkb`가 정의하고 여기서는 `app/core`를 거쳐 받는다. 한동안은 사본이었다 —
#: `app/requirements`가 `app/deployment` 없이 돌아야 한다는 규약 때문이었고, 그래서
#: 사본이 갈라졌는지 대조하는 검사까지 따로 있었다. 그 규약은 2026-07-28에 `app/core`가
#: 문을 하나로 좁히면서 바뀌었고, **사본을 둘 이유도 그때 사라졌다.**
#: basis는 영원히 `inferred`다 — 설계 산문은 사람이 검수해도 클라우드 사실이 되지 않는다.
EVIDENCE = advisory.EVIDENCE
ADVISORY_NOTICE = advisory.ADVISORY_NOTICE


@dataclass(frozen=True)
class Concern:
    """관심사 하나. **질문은 우리 표현이고, 인용은 코퍼스 좌표다**(본문 아님)."""

    id: str
    #: 요구사항에 이 관심사가 다뤄졌는지 묻는 한 문장. 프롬프트에 그대로 들어가므로
    #: 영어로 쓴다. **질문이지 규범이 아니다** — "그래야 한다"가 아니라 "정해졌는가"다.
    #:
    #: **하나의 결정만 묻는다**(입도 규칙). 둘을 묻고 있으면 관심사를 쪼개야 한다.
    question: str
    #: **E2의 답.** 왜 이것이 클라우드 네이티브 때문에 요구사항 단계로 당겨지는가.
    #: 전통 개발에서도 똑같이 성립하면 여기 적을 말이 없고, 그러면 관심사가 아니다.
    cloud_specific: str
    #: 근거 문서의 코퍼스 id(`app/deployment/patternkb`). **관심사마다 달라야 한다**
    #: — 입도 규칙의 기계 대리 기준(모듈 docstring).
    doc_id: str
    #: 그 문서 본문에 있어야 하는 짧은 구절(소문자). 좌표가 맞는지 보는 열쇠일 뿐이고,
    #: 본문을 옮겨 담는 자리가 아니다.
    probe: tuple[str, ...]
    #: 사람이 읽을 좌표.
    citation: str
    #: 이 관심사가 걸리는 ISO/IEC 25010:2023 품질 특성(`ISO25010`의 값들).
    #:
    #: **벤더 중립 축에 대는 것이 목적이다.** 근거 코퍼스가 전량 벤더 문서라 목록이
    #: 자기참조가 되기 쉬운데, 표준에 걸어 두면 두 방향이 다 보인다 — 우리 관심사 중
    #: 표준에 집이 없는 것(비용·탄소·규제)과, 표준에 있는데 우리에게 없는 칸
    #: (`unmapped_characteristics()`). 후자가 벤더 편향 잔여분의 추정치다.
    #:
    #: 빈 튜플은 누락이 아니라 **사실**이다. ISO 25010은 제품 품질 모델이라 비용·지속
    #: 가능성을 다루지 않고, 규정 준수 하위특성은 2011년 개정에서 빠졌다.
    iso25010: tuple[str, ...] = ()
    #: **오늘 실재하는 기계 소비자.** 이 답이 실제로 흘러 들어가는 칸의 이름을 적는다.
    #:
    #: 좁게 정의한다 — 이 저장소에서 **지금 그 값을 읽는 코드가 있어야** 한다.
    #: 오늘 그것은 `RESOURCE_SPEC`의 칸뿐이다(`app/deployment/appkb/request.json`,
    #: `app/design/api.py`의 `EXTERNAL_STAGES`가 `resource_spec`으로 받는다).
    #:
    #: `None`은 배제가 아니라 **범위 표시**다 — 근거는 있고 사람은 읽지만 받아 줄 기계가
    #: 아직 없다는 뜻이고, 산출물에서 `noted`로 따로 센다.
    #:
    #: 넓게 두면 안 되는 이유가 있다. 처음에는 "설계 인계" 같은 말을 적었는데, 확인해 보니
    #: 요구사항→설계 통로는 `EXTERNAL_STAGES` 넷뿐이고 관심사를 받는 자리는 없었다.
    #: **`multiZone`을 받아 놓고 아무도 안 읽던 그 결함을 우리가 그대로 저지른 것이다.**
    #: 없는 소비자를 적으면 `handoff` 목록이 부풀고, 부푼 목록은 도구가 하는 거짓말이 된다.
    consumer: str | None = None
    #: 결정론 층이 쓰는 열쇠말(소문자). **한국어도 담는다** — 서빙 입력은 한국어이고
    #: 평가 코퍼스(PURE)는 영어라, 한쪽만 담으면 한쪽에서 결정론 층이 통째로 죽는다.
    #:
    #: 영문은 단어 경계로 매칭되고 한국어는 부분 문자열로 매칭된다(`steps/step_cloud.py`).
    #: 그래서 **한국어 열쇠말은 다른 낱말에 안 묻히는 것만 담는다** — `"건"`은 조건·사건에
    #: 걸려서 뺐다.
    #:
    #: 비어 있을 수 있다. 그건 "신호가 없다"는 사실이고 LLM 층만 판정한다는 뜻이다 —
    #: 억지 열쇠말을 채우면 오탐이 결정론의 이름을 달고 나온다.
    signals: tuple[str, ...] = ()
    evidence: str = EVIDENCE

    @property
    def hedged(self) -> bool:
        """이 근거로 말할 때 출처의 한계를 밝혀야 하는가. 이 축은 **항상 참**이다."""
        return basis.needs_hedge(self.evidence)

    def prompt_line(self) -> str:
        """판정 프롬프트 한 줄."""
        return f"- {self.id}: {self.question}"


# ---------------------------------------------------------------------------
# 관심사 목록
#
# 좌표는 2026-07-27에 코퍼스 346편과 대조해 실증했다. 새 관심사를 넣으면
# `python -m app.requirements.knowledge.verify_concerns`를 돌린다.
# ---------------------------------------------------------------------------
_12F = "The Twelve-Factor App"
_AZ = "Azure Architecture Center"
_WAF = "Azure Well-Architected Framework"
_GCP = "Google Cloud Architecture Framework"

CONCERNS: tuple[Concern, ...] = (
    Concern(
        id="cn.stateless-process",
        question=(
            "Do the requirements say whether the application may keep state in the "
            "process itself (in-memory session, local files) or whether all state must "
            "live in a backing service?"
        ),
        cloud_specific=(
            "플랫폼이 인스턴스를 죽이고 옮긴다 — 프로세스에 남은 상태는 설계가 아니라 요구사항 단계에서 정해져야 서버리스·오토스케일 가능 공간이 "
            "닫히지 않는다."
        ),
        consumer="RESOURCE_SPEC.stateless",
        doc_id="twelve-factor/processes",
        probe=("processes are stateless", "backing service"),
        iso25010=("flexibility",),        citation=f"{_12F} VI. Processes",
        signals=("stateless", "session", "무상태", "세션"),
    ),
    Concern(
        id="cn.disposability",
        question=(
            "Do the requirements say what may be lost when an instance is stopped or "
            "moved — in-flight work, uploads, background jobs?"
        ),
        cloud_specific=(
            "쿠버네티스는 파드를 임의로 종료·재배치한다. 전통 배포에서는 종료가 예외 상황이지만 여기서는 상시라, 무엇을 잃어도 되는지가 운영 사고가 "
            "아니라 요구사항이다."
        ),
        consumer=None,
        doc_id="twelve-factor/disposability",
        probe=("shut down gracefully", "minimize startup time"),
        iso25010=("reliability",),        citation=f"{_12F} IX. Disposability",
        # 열쇠말이 없다. "종료 시 무엇을 잃어도 되는가"는 특정 단어로 쓰이지 않는다.
        signals=(),
    ),
    Concern(
        id="cn.backing-services",
        question=(
            "Do the requirements name the backing services the application consumes "
            "over the network (database, queue, cache, mail, object store)?"
        ),
        cloud_specific=(
            "백킹 서비스를 고르면 딸린 리소스군·비용·리전 가용성이 함께 정해진다(과제 문제 ②). 전통 개발에서는 설치 대상이지만 클라우드에서는 "
            "조달 결정이다."
        ),
        consumer=None,
        doc_id="twelve-factor/backing-services",
        probe=("attached resources", "over the network"),
        iso25010=("compatibility", "flexibility",),        citation=f"{_12F} IV. Backing services",
        signals=(
            "database", "queue", "cache", "storage", "message broker",
            "데이터베이스", "큐", "캐시", "스토리지",
        ),
    ),
    Concern(
        id="cn.config-externalised",
        question=(
            "Do the requirements say which values differ between environments "
            "(endpoints, credentials, feature switches) and who supplies them?"
        ),
        cloud_specific=(
            "같은 이미지가 여러 환경에 배포되고 설정은 주입된다. 무엇이 환경마다 다른지가 정해지지 않으면 이미지 하나로 갈 수 있는지 자체가 미정이다."
        ),
        consumer=None,
        doc_id="twelve-factor/config",
        probe=("strict separation of config from code", "credentials"),
        iso25010=("flexibility", "maintainability",),        citation=f"{_12F} III. Config",
        signals=("configuration", "secret", "credential", "환경 변수", "설정", "자격 증명"),
    ),
    # 아래 둘(`scale-out`·`traffic-shape`)은 **병합 검토를 거쳐 갈라 둔 것이다**(§6.11).
    # 입력 11종에서는 열쇠말·LLM 두 층 모두에서 `scale-out ⊃ traffic-shape`으로 나왔지만,
    # 두 측정이 **같은 코퍼스**를 읽고 있었다. PURE 18편(7,659문장)에서 다시 재니 교집합이
    # 0이고 부하 모양만 말하는 요구사항이 실재한다("under both standard and peak conditions").
    # 미분화의 원인은 개념 겹침이 아니라 표본이었다. 다시 논쟁하려면 코퍼스부터 늘릴 것.
    Concern(
        id="cn.scale-out",
        question=(
            "Do the requirements say whether load is met by adding instances, and do "
            "they demand anything that pins a client to one instance (session affinity)?"
        ),
        cloud_specific=(
            "탄력적 수평 확장이 클라우드의 기본 값매김이다. 세션 고착 같은 요구가 하나 있으면 그 가능성이 통째로 닫히므로 나중에 발견하면 되돌릴 "
            "수 없다."
        ),
        consumer=None,
        doc_id="guide/design-principles/scale-out",
        probe=("avoid instance stickiness", "scale horizontally"),
        iso25010=("flexibility",),        citation=f"{_AZ} — Design to scale out",
        signals=("scale", "scaling", "concurrent", "throughput", "확장", "동시", "처리량"),
    ),
    Concern(
        id="cn.traffic-shape",
        question=(
            "Do the requirements describe the shape of the load over time — steady, "
            "bursty, or scheduled peaks — and not only its size?"
        ),
        cloud_specific=(
            "종량 과금과 오토스케일이 부하의 **모양**에 값을 매긴다. 전통 개발에서는 최대 부하 하나면 장비를 살 수 있지만, 여기서는 같은 "
            "총량도 모양에 따라 비용·구성이 갈린다."
        ),
        consumer="RESOURCE_SPEC.trafficPattern",
        doc_id="best-practices/auto-scaling",
        probe=("autoscaling", "burst"),
        iso25010=("performance efficiency",),        citation=f"{_AZ} — Autoscaling guidance",
        signals=("peak", "burst", "spike", "seasonal", "피크", "급증", "성수기"),
    ),
    Concern(
        id="cn.transient-fault",
        question=(
            "Do the requirements say what the application must do when a dependency "
            "fails briefly — retry, queue, degrade, or surface the error?"
        ),
        cloud_specific=(
            "분산 컴퓨팅의 오류 8가지가 말하듯 네트워크 실패가 상시다. 의존 서비스가 잠깐 사라지는 것이 예외가 아니라 정상 운영의 일부라 대응이 "
            "요구사항으로 올라온다."
        ),
        consumer=None,
        doc_id="best-practices/transient-faults",
        probe=("transient fault", "retry"),
        iso25010=("reliability",),        citation=f"{_AZ} — Transient fault handling",
        signals=("retry", "timeout", "failure", "재시도", "장애", "실패"),
    ),
    # 입도 규칙이 첫 판의 `cn.observability`를 여기서 갈랐다. "어떤 사건이 기록으로
    # 남는가"와 "운영이 무엇을 보는가"는 **따로 정해진다** — 감사 로그 요구가 있는
    # 시스템이 운영 지표를 안 정할 수 있고 그 반대도 된다. 근거 문서도 갈렸다(유일성).
    Concern(
        id="cn.event-record",
        question=(
            "Do the requirements say which events must be recorded as a durable trail, "
            "and for how long they must be kept?"
        ),
        cloud_specific=(
            "인스턴스가 사라지면 그 안의 파일도 사라진다. 12-factor가 로그를 파일이 아니라 "
            "**흘려보내는 이벤트 스트림**으로 보는 이유이고, 무엇을 남겨야 하는지가 정해지지 "
            "않으면 보관 자체가 설계에서 누락된다."
        ),
        consumer=None,
        doc_id="twelve-factor/logs",
        probe=("event streams", "never concerns itself with routing or storage"),
        iso25010=("security",),        citation=f"{_12F} XI. Logs",
        signals=("audit", "audit trail", "retention", "감사", "보존", "이력"),
    ),
    Concern(
        id="cn.operational-signal",
        question=(
            "Do the requirements say what operations must be able to observe while the "
            "system runs — which measures are reported and which conditions raise alarm?"
        ),
        cloud_specific=(
            "운영팀이 하드웨어를 못 본다. 남은 관측 수단이 애플리케이션이 내보내는 것뿐이라, "
            "무엇을 내보낼지가 운영 관행이 아니라 시스템 요구사항이 된다."
        ),
        consumer=None,
        doc_id="guide/design-principles/design-for-operations",
        probe=("make all things observable", "tracing"),
        iso25010=("maintainability",),        citation=f"{_AZ} — Design for operations",
        signals=("monitor", "monitoring", "metric", "telemetry", "trace", "모니터", "지표"),
    ),
    Concern(
        id="cn.redundancy-target",
        question=(
            "Do the requirements state an availability target, or which parts must "
            "survive the loss of a single instance, zone, or region?"
        ),
        # appkb는 `availabilityTarget` 칸을 **일부러 닫아 뒀다**(복제 수 판정의 SLA 근거가
        # 부재 확정). 그러니 이 답이 흘러 들어갈 칸은 아직 없다 — 소비자는 인계뿐이고,
        # 그렇게 적는다. 열려 있는 칸인 척하면 A 트랙이 없는 칸을 채우러 간다.
        cloud_specific=(
            "실패 단위가 인스턴스·존·리전이고 그 경계는 플랫폼이 정한다. 무엇이 어느 단위의 상실을 견뎌야 하는지가 리전·존 선택을 요구사항 "
            "단계에서 강제한다."
        ),
        consumer="RESOURCE_SPEC.multiZone",
        doc_id="guide/design-principles/redundancy",
        probe=("single points of failure", "critical paths"),
        iso25010=("reliability",),        citation=f"{_AZ} — Make all things redundant",
        signals=("availability", "uptime", "sla", "가용성", "무중단"),
    ),
    Concern(
        id="cn.service-limits",
        question=(
            "Do the requirements state a volume that could meet a platform limit — "
            "stored data size, request rate, connection count, object count?"
        ),
        cloud_specific=(
            "모든 관리형 서비스에 계정·리전 단위 한도가 있고 그 한도는 우리가 못 정한다. 규모가 한도에 닿는지는 설계가 아니라 조달·리전 선택을 "
            "바꾼다."
        ),
        consumer=None,
        doc_id="guide/design-principles/partition",
        probe=("all services have limits", "partitioning"),
        iso25010=("performance efficiency",),        citation=f"{_AZ} — Partition around limits",
        # `"limit"`은 뺐다. 이 관심사가 묻는 것은 **볼륨**인데 `rate limiting`·
        # `time-limited access token`처럼 볼륨과 무관한 문장에 붙는 일반어다(실측).
        # 열쇠말은 그 관심사의 **답이 되는 문장에만** 나타나야 한다.
        signals=("per second", "per day", "gb", "tb", "quota", "한도", "동시 접속"),
    ),
    Concern(
        id="cn.managed-vs-self",
        question=(
            "Do the requirements constrain whether managed platform services may be "
            "used, or whether the team must run the component itself?"
        ),
        cloud_specific=(
            "PaaS/IaaS 선택 자체가 클라우드에만 있는 축이고, 단가·운영 책임·가용 리전이 그 축에서 갈린다."
        ),
        consumer=None,
        doc_id="guide/design-principles/managed-services",
        probe=("use paas instead",),
        iso25010=("flexibility", "maintainability",),        citation=f"{_AZ} — Use PaaS options",
        signals=("managed", "self-hosted", "on-premise", "관리형", "자체", "온프레미스"),
    ),
    Concern(
        id="cn.data-residency",
        question=(
            "Do the requirements say where data must physically reside, or which "
            "jurisdiction's rules apply to it?"
        ),
        cloud_specific=(
            "데이터가 물리적으로 어디 있는지를 리전 선택이 결정하고, 규제는 리전을 강제한다. 전통 개발에서는 데이터센터가 이미 정해져 있어 "
            "요구사항이 되지 않던 것이다."
        ),
        consumer="RESOURCE_SPEC.dataResidency",
        doc_id="well-architected/design-guides/regions-availability-zones",
        probe=("data residency", "availability zones"),
        iso25010=("security",),        citation=f"{_WAF} — Availability zones and regions",
        signals=("residency", "gdpr", "region", "국내", "리전", "개인정보"),
    ),
    # --- 회차 1: well-architected (199편) ------------------------------------
    Concern(
        id="cn.expected-scale",
        question=(
            "Do the requirements state the expected load in numbers — concurrent users, "
            "requests per second, or data volume per period?"
        ),
        cloud_specific=(
            "용량을 미리 사는 것이 아니라 규모에 따라 값을 치른다. 수치가 없으면 인스턴스 "
            "타입·개수·요금제 중 무엇도 고를 수 없고, 그 빈칸을 제공자의 기본값이 채운다."
        ),
        consumer="RESOURCE_SPEC.expectedConcurrentUsers|approxRequestsPerSecond",
        doc_id="well-architected/performance-efficiency/capacity-planning",
        probe=("capacity planning", "demand", "forecast"),
        iso25010=("performance efficiency",),        citation=f"{_WAF} — Capacity planning",
        signals=("concurrent user", "requests per second", "rps", "동시 사용자", "동시 접속자"),
    ),
    Concern(
        id="cn.performance-target",
        question=(
            "Do the requirements state a target for response time or throughput, with "
            "the load at which it must hold?"
        ),
        cloud_specific=(
            "공유 인프라 위에서 돌고 오토스케일이 지연을 뒤늦게 따라간다. 목표 수치가 "
            "없으면 '느리다'를 판정할 기준이 없고, 스케일 정책도 세울 수 없다."
        ),
        consumer=None,
        doc_id="well-architected/performance-efficiency/performance-targets",
        probe=("performance targets", "baseline"),
        iso25010=("performance efficiency",),        citation=f"{_WAF} — Performance targets",
        signals=(
            "response time", "latency", "throughput", "p95", "p99",
            "응답 시간", "지연", "처리량",
        ),
    ),
    Concern(
        id="cn.critical-flow",
        question=(
            "Do the requirements say which flows must keep working, or keep their "
            "performance, when resources are scarce?"
        ),
        cloud_specific=(
            "자원이 탄력적이라 전부를 항상 최대로 받칠 이유가 없고 비용이 그것을 막는다. "
            "무엇을 먼저 살릴지가 정해지지 않으면 스케일·격리 우선순위를 세울 수 없다."
        ),
        consumer=None,
        doc_id="well-architected/performance-efficiency/prioritize-critical-flows",
        probe=("critical flows", "prioritize"),
        iso25010=("performance efficiency", "reliability",),        citation=f"{_WAF} — Prioritize critical flows",
        signals=("critical", "priority", "핵심", "우선"),
    ),
    Concern(
        id="cn.cost-ceiling",
        question=(
            "Do the requirements state a spending ceiling, and what must happen when "
            "usage approaches it?"
        ),
        cloud_specific=(
            "종량 과금이라 상한이 없으면 비용이 트래픽과 함께 무한히 열린다. 전통 개발에서 "
            "예산은 조달 시점의 일이지만 여기서는 런타임 제약이다."
        ),
        consumer="RESOURCE_SPEC.monthlyBudgetUSD",
        doc_id="well-architected/cost-optimization/set-spending-guardrails",
        probe=("budget", "guardrail"),
        iso25010=(),        citation=f"{_WAF} — Set spending guardrails",
        signals=("budget", "cost", "예산", "비용"),
    ),
    Concern(
        id="cn.recovery-objective",
        question=(
            "Do the requirements state how much data may be lost and how quickly "
            "service must be restored after a disaster (RPO/RTO)?"
        ),
        cloud_specific=(
            "리전 단위 장애가 실재하고 복구는 우리가 아니라 제공자의 복제·백업 기능 위에서 "
            "이뤄진다. 목표가 없으면 어느 복제 등급을 살지가 임의가 되고, 그 선택이 비용을 "
            "몇 배로 가른다."
        ),
        consumer=None,
        doc_id="well-architected/reliability/disaster-recovery",
        probe=("recovery time objective", "recovery point objective"),
        iso25010=("reliability",),        citation=f"{_WAF} — Disaster recovery",
        signals=("rpo", "rto", "backup", "disaster recovery", "백업", "복구"),
    ),
    Concern(
        id="cn.degradation-policy",
        question=(
            "Do the requirements say what the system may shed or degrade when it is "
            "overloaded or a dependency is unavailable?"
        ),
        cloud_specific=(
            "확장에는 시간이 걸리고 한도가 있어 과부하는 예외가 아니라 예상 상태다. "
            "무엇을 버려도 되는지가 없으면 플랫폼이 임의로 버린다 — 요청을 죽이거나 "
            "파드를 재시작하는 형태로."
        ),
        consumer=None,
        doc_id="gcp-framework/reliability/graceful-degradation",
        probe=("graceful degradation", "overload"),
        iso25010=("reliability",),        citation=f"{_GCP} — Design for graceful degradation",
        signals=("degrade", "degradation", "fallback", "저하", "축소"),
    ),
    Concern(
        id="cn.release-continuity",
        question=(
            "Do the requirements say whether service may be interrupted during a "
            "release, and whether a release must be reversible?"
        ),
        cloud_specific=(
            "플랫폼이 인스턴스를 교체하는 방식으로 배포한다(롤링·블루그린). 중단 허용 여부가 "
            "정해지지 않으면 배포 전략과 필요 여유 용량이 함께 미정으로 남는다."
        ),
        consumer=None,
        doc_id="well-architected/operational-excellence/safe-deployments",
        probe=("safe deployment", "rollback"),
        iso25010=("reliability", "maintainability",),        citation=f"{_WAF} — Safe deployment practices",
        signals=("deployment window", "rollback", "무중단", "롤백"),
    ),
    Concern(
        id="cn.identity-access",
        question=(
            "Do the requirements say who may access what, and where identities come "
            "from (own accounts, corporate directory, external provider)?"
        ),
        cloud_specific=(
            "경계가 네트워크가 아니라 신원이고, 신원은 대개 플랫폼 서비스에서 온다. "
            "출처가 정해지지 않으면 리소스 구성과 리전 가용성이 함께 미정이 된다."
        ),
        consumer=None,
        doc_id="well-architected/security/identity-access",
        probe=("identity", "access management"),
        iso25010=("security",),        citation=f"{_WAF} — Identity and access management",
        signals=(
            "authentication", "authorization", "role", "sso", "권한", "인증", "역할",
        ),
    ),
    Concern(
        id="cn.data-classification",
        question=(
            "Do the requirements say which data is sensitive, and what class it "
            "belongs to (personal, financial, health, public)?"
        ),
        cloud_specific=(
            "분류가 리전·서비스 적격성·암호화 요구를 한꺼번에 정한다. 분류가 없으면 그 "
            "제약들이 조회되지 않고, 데이터는 그냥 기본 설정이 놓는 자리에 놓인다."
        ),
        consumer=None,
        doc_id="well-architected/security/data-classification",
        probe=("data classification", "sensitivity"),
        iso25010=("security",),        citation=f"{_WAF} — Data classification",
        signals=("personal data", "pii", "sensitive", "개인정보", "민감"),
    ),
    Concern(
        id="cn.encryption-obligation",
        question=(
            "Do the requirements state an encryption obligation for data at rest or "
            "in transit, and who must hold the keys?"
        ),
        cloud_specific=(
            "제공자가 기본 암호화를 해 주기 때문에 요구를 안 적어도 충족된 것처럼 보인다. "
            "갈리는 지점은 키를 누가 쥐느냐이고, 그건 요구사항에만 적힐 수 있다."
        ),
        consumer=None,
        doc_id="well-architected/security/encryption",
        probe=("at rest", "in transit"),
        iso25010=("security",),        citation=f"{_WAF} — Encryption",
        signals=("encrypt", "encryption", "tls", "암호화"),
    ),
    Concern(
        id="cn.network-exposure",
        question=(
            "Do the requirements say what must be reachable from the public internet "
            "and what must not?"
        ),
        cloud_specific=(
            "기본값이 공개 엔드포인트인 관리형 서비스가 많다. 노출 범위가 정해지지 않으면 "
            "가상망·프라이빗 링크 구성이 통째로 미정이고, 그건 나중에 못 되돌린다."
        ),
        consumer=None,
        doc_id="well-architected/security/networking",
        probe=("segmentation", "public internet"),
        iso25010=("security",),        citation=f"{_WAF} — Networking and connectivity",
        signals=("public", "private network", "vpn", "firewall", "내부망", "방화벽"),
    ),
    Concern(
        id="cn.tenancy-model",
        question=(
            "Do the requirements say whether multiple customers or organisations share "
            "one deployment, and where the isolation boundary must fall?"
        ),
        cloud_specific=(
            "격리 경계를 어디에 긋느냐가 리소스 개수·비용·확장 단위를 한꺼번에 정한다. "
            "온프레미스에서는 고객마다 설치가 기본값이지만 여기서는 선택이다."
        ),
        consumer=None,
        doc_id="well-architected/saas/compute",
        probe=("tenant", "isolation"),
        iso25010=("security", "flexibility",),        citation=f"{_WAF} — Compute for SaaS workloads",
        signals=("tenant", "multi-tenant", "테넌트", "고객사별"),
    ),
    Concern(
        id="cn.usage-quota",
        question=(
            "Do the requirements set a limit on how much a single user or tenant may "
            "consume?"
        ),
        cloud_specific=(
            "종량 과금에서는 한 사용자의 폭주가 곧 우리 비용이고 다른 사용자의 성능이다. "
            "플랫폼 한도(`cn.service-limits`)가 우리에게 걸리는 천장이라면 이건 우리가 "
            "사용자에게 거는 천장이라 요구사항에만 있을 수 있다."
        ),
        consumer=None,
        doc_id="well-architected/design-guides/throttling",
        probe=("throttling", "rate limit"),
        iso25010=("performance efficiency",),        citation=f"{_WAF} — Throttling",
        signals=("throttle", "throttling", "rate limit", "사용량 제한", "요청 제한"),
    ),
    Concern(
        id="cn.carbon-constraint",
        question=(
            "Do the requirements place any constraint on energy use or carbon "
            "emissions?"
        ),
        cloud_specific=(
            "리전마다 전력 구성이 다르고 그 값이 리전 코드로 색인돼 있다. 제약이 있으면 "
            "리전 선택이 좁아지므로 요구사항 단계에서 알아야 한다."
        ),
        consumer=None,
        doc_id="gcp-framework/sustainability/low-carbon-regions",
        probe=("carbon", "low-carbon"),
        iso25010=(),        citation=f"{_GCP} — Use regions that consume low-carbon energy",
        signals=("carbon", "sustainability", "탄소", "친환경"),
    ),
    # --- 회차 2: gcp-framework (57편) ----------------------------------------
    Concern(
        id="cn.regulatory-obligation",
        question=(
            "Do the requirements name a regulation or certification regime the system "
            "must satisfy?"
        ),
        cloud_specific=(
            "책임이 제공자와 나뉘어 있어(shared responsibility) 어느 인증을 우리가 지고 "
            "어느 것을 제공자가 지는지가 서비스·리전 선택을 좁힌다."
        ),
        # 소비자가 없다. 첫 판에서는 이 이유로 목록에서 조용히 뺐는데, 그게 표집 편향과
        # 구현 편향을 섞은 것이었다(모듈 docstring의 소비자 규칙). 이제 남기고 `noted`로 센다.
        consumer=None,
        doc_id="gcp-framework/security/meet-regulatory-compliance-and-privacy-needs",
        probe=("regulatory", "compliance"),
        iso25010=(),        citation=f"{_GCP} — Meet regulatory, compliance, and privacy needs",
        signals=("gdpr", "hipaa", "pci", "iso 27001", "규제", "준수", "컴플라이언스"),
    ),
    # --- 회차 3: patterns (44편) ---------------------------------------------
    Concern(
        id="cn.cross-service-consistency",
        question=(
            "Do the requirements say what must hold when a multi-step operation fails "
            "half-way — must it be undone, retried, or left visible?"
        ),
        cloud_specific=(
            "관리형 저장소를 여럿 조합하면 그것들을 아우르는 트랜잭션이 제공되지 않는다. "
            "보장 수준을 안 적으면 남는 것은 '보장 없음'이고, 그건 선택한 적 없는 답이다."
        ),
        consumer=None,
        doc_id="patterns/saga",
        probe=("compensating", "distributed transaction"),
        iso25010=("functional suitability", "reliability",),        citation=f"{_AZ} — Saga pattern",
        signals=("consistency", "정합성", "일관성", "보상 트랜잭션"),
    ),
)

BY_ID: dict[str, Concern] = {c.id: c for c in CONCERNS}


def unmapped_characteristics() -> tuple[str, ...]:
    """어떤 관심사도 걸리지 않은 ISO/IEC 25010 특성 — **벤더 편향 잔여분의 추정치**.

    표로 적어 두면 목록이 자라도 표가 안 따라온다. 계산하면 항상 지금 목록의 답이다.

    빈 튜플이 목표가 아니라는 점이 중요하다 — 실행 환경이 대신 정하지 않는 특성은
    애초에 우리 메타 특성 밖이고(`interaction capability`가 그 예다), 그건 이 축이
    좁아서가 아니라 정확해서 비어 있는 것이다. 해석은 사람이 한다.
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

    고지(`ADVISORY_NOTICE`)를 함께 싣는다 — 판정자에게도 이것이 클라우드 사실이 아니라
    설계 지침이라고 말해야 한다. 그러지 않으면 모델이 "패턴 문서가 요구한다"는 투로
    답하고, 그 투가 그대로 산출물에 실린다.
    """
    picked = CONCERNS if only is None else tuple(BY_ID[i] for i in only)
    lines = [c.prompt_line() for c in picked]
    return "\n".join([*lines, "", ADVISORY_NOTICE])
