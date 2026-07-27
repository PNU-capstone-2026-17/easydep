# 클라우드 네이티브를 요구사항 단계에서 잇기 — 조사 (2026-07-27)

## 0. 이 문서가 답하려는 질문

`docs/research.md`(과제 뼈대)의 목표 ②는 *"클라우드 환경 특성(인스턴스 성능·비용),
클라우드 리소스의 특성(용량·의존성)을 고려한 클라우드 네이티브 환경 가이드라인 제공"*이다.
그런데 지금 애플리케이션과 클라우드 네이티브가 만나는 자리는 **배포 다이어그램 하나뿐**이고,
요구사항 분석 단계에는 연결 방법이 없다.

이 문서는 두 가지를 정리한다.

1. **클라우드 네이티브 개발이 일반 소프트웨어 개발과 무엇이 다른가** — 요구사항 관점에서만.
2. **그 차이를 요구사항 단계에 어떻게 들일 것인가** — 무엇이 이미 있고 무엇이 공백인가.

각 문장의 출처를 구분해 적는다. **이 저장소에서 잰 것**, **문헌**, **우리 판단** 셋은
무게가 다르다(`app/requirements/knowledge/basis.py`가 규칙에 대해 그은 선과 같다).

---

## 1. 이미 있는 것 — `RESOURCE_SPEC` 계약

> 근거: 이 저장소의 코드. `app/deployment/appkb/request.json` · `contract.py`

클라우드 리소스 제약의 스키마가 **이미 있다**. 그리고 스키마 설명이 의도된 배선을
그대로 적어 두었다:

> *"easydep에서 **요구사항 분석이 `resource_constraints_text`(원문 산문)로부터 만들어
> `RESOURCE_SPEC` 산출물로 저장하고**, 배포 구성이 읽는다."*

### 필수의 기준은 목록이 아니라 판정식이다

이것이 "제약의 필수 요건은 무엇인가"에 대한 이 프로젝트의 답이다:

> *"그 칸이 없으면 **뒤 단계 산출물의 요구사항 부합을 잴 수 없는** 것만 필수다."*
> *"모든 칸에는 **소비자**(어느 조인·어느 판정이 읽는가)가 있어야 한다 — `multiZone`을
> 받아 놓고 안 읽던 결함의 일반화."*

| 칸 | 필수 | 소비자 |
| --- | --- | --- |
| `provider` | ✅ | 모든 값 조인의 축. 없으면 비용·성능·번들·타입대응이 전부 닫힌다 |
| `region` | ✅ | 단가·용량·탄소가 리전 **코드**로 색인됨 |
| `monthlyBudgetUSD` | ✅ | 비용 부합 판정의 기준값 |
| `expectedConcurrentUsers` \| `approxRequestsPerSecond` | ✅ (택1) | 사이징 판정 기준. 없으면 스펙 추천이 전부 임의 |
| `regionAsWritten` | | 리전 해석이 틀렸을 때 되짚을 근거 |
| `multiZone` | | vNet 정책 · k8s `requiredSubnetCount` 조인 |
| `trafficPattern` | | 버스트 적합 판정(`steady`인데 버스트 경고가 붙으면 상충) |
| `stateless` | | 서버리스 적합 판정 |
| `dataResidency` | | 리전 표시 이름 대조 — **판정 불가를 명시**하는 것이 규약 |

**일부러 닫아 둔 칸**도 기록돼 있다: `availabilityTarget`(복제 수 판정의 SLA 근거가
부재 확정), 규모→스펙 변환 계수(*"사이징은 판단이지 사실이 아니다"*).

### 지명은 코드로 바꿔 담는다

`region`은 리전 **코드**여야 한다. 지명('서울')이 그대로 들어오면 조인이 조용히 빈 답이
된다(스키마가 실측이라고 적어 둠). 해석은 `app/deployment/envkb/regions.py:resolve_region`.

### 공백은 이미 진단돼 있다

`docs/agent-sdk-merge-plan.md` — *"`RESOURCE_SPEC`은 **아무도 만들지 않는다**"*.
`app/requirements/api.py`가 *"resource_spec은 이 에이전트가 만들지 않으므로 비운다"*고
적고 있고, 사용자가 쓴 제약 원문(`apps.resource_constraints_text`)은 저장되지만
구조화되지 않는다. 배선은 병합 범위를 좁히려고 **일부러 끊어 둔 상태**다.

**정리: "무엇을 받을 것인가"는 답이 나와 있고, 요구사항 쪽 생산자만 없다.**

---

## 2. 조사 — 클라우드 네이티브는 요구사항 관점에서 무엇이 다른가

> 근거: 문헌(절 끝 출처). 요구사항 관점으로의 압축은 우리 판단이다.

### 2.1 환경이 구현 세부가 아니라 **제약**이다

`provider`·`region`·`budget`·`dataResidency`는 "어떻게 만들지"가 아니라 **가능 공간을
미리 자른다.** 아키텍처적으로 유의미한 요구사항(ASR) 문헌은 *엄격한 문턱(예: 99.999%)이
실행 가능한 아키텍처 해를 제한*하고, *규제 제약이 데이터 저장 방식·암호화·접근통제·
감사로그를 지시*한다고 적는다. 데이터 레지던시는 리전 선택 자체를 강제한다(GDPR·CCPA·
HIPAA·중국 사이버보안법이 각각 다른 규칙).

전통적 RE는 이것들을 설계 이후로 미룰 수 있다. 클라우드 네이티브에서는 미룰 수 없다 —
미루면 설계가 끝난 뒤에 "그 리전에는 그 서비스가 없다"를 만난다.

### 2.2 **운영 성질이 요구사항이 된다** — 여기가 핵심

CNCF 정의(v1.1)는 클라우드 네이티브를 *"secure, resilient, manageable, sustainable,
observable 하게 상호작용하는 느슨히 결합된 시스템"*으로 규정한다. 12-factor는 설정
외부화 · 백킹 서비스를 붙였다 뗄 수 있는 자원으로 · **무상태 프로세스** · **폐기가능성
(disposability)**을 요구한다. 쿠버네티스 프로덕션 체크리스트는 requests/limits · 3종
프로브(liveness·readiness·startup) · graceful shutdown · PodDisruptionBudget · HPA ·
설정/시크릿 외부화 · 관측성을 요구한다.

**이것들은 사용자가 절대 쓰지 않는 요구사항이다.** 그런데 전통 개발과 결정적으로 다른
점이 있다 — **플랫폼이 그 성질에 작용한다.** 쿠버네티스는 파드를 죽이고 옮긴다. 폐기
가능하지 않으면 데이터를 잃는다. 분산 컴퓨팅의 8가지 오류(*"네트워크는 신뢰할 수 있다"*,
*"지연은 0이다"* …)가 말하듯 **실패는 예외가 아니라 상시**다.

즉 전통 개발에서 "배포 관심사"인 것이 클라우드 네이티브에서는 **요구사항**이다.
그것이 충족되지 않으면 기능이 동작하지 않는 것이 아니라 **운영 중에 조용히 깨진다.**

ISO/IEC 25010이 2023 개정에서 **scalability를 flexibility의 하위특성으로 정식 편입**하고
safety를 최상위로 신설한 것도 같은 방향의 신호다.

### 2.3 추론 단위가 바뀐다 — 그리고 Cockburn과 충돌한다

전통 RE는 "시스템"을 하나의 블랙박스로 본다. 클라우드 네이티브의 단위는 **교체 가능한
인스턴스 + 붙어 있는 백킹 서비스**다.

그런데 **Cockburn의 블랙박스 원칙이 정확히 클라우드 네이티브가 드러내야 할 것을 가린다** —
무엇이 상태를 갖는가, 무엇이 붙어 있는가, 무엇을 잃어도 되는가. 우리 규칙
`spec.black-box-no-internal-components`가 "System이 무엇을 하는가만 말하고 어느 부품이
하는지는 말하지 말라"고 요구하는데, 이건 좋은 유스케이스 규칙이면서 동시에
**클라우드 네이티브 성질을 명세에서 볼 수 없게 만든다.**

→ 결론: **제약과 운영 성질은 명세가 아니라 별도 산출물에 살아야 한다**(§4).

### 2.4 요구공학 쪽 현황

조사 중 확인한 것: *"클라우드 컴퓨팅에서 요구공학은 크게 연구되지 않은 주제
(greatly under-researched)"*. 클라우드 이관 맥락의 요구사항 분석은 *탄력성·확장성,
컴퓨팅 요구, 배포 상호운용성, 보안·규제, 스토리지 용량*에 초점을 둔다고 정리된다.
NFR 문헌은 클라우드용 NFR을 *QoS 지표(가용성·처리량)*와 **배포 제약(예산·관할권)**으로
나눈다 — 후자가 §2.1과 같은 이야기다.

즉 "요구사항 단계에서 클라우드 네이티브를 잇는 방법"은 **표준화된 것이 없다.**
과제의 신규성 주장이 서는 자리이면서, 참고할 완성된 답이 없다는 뜻이기도 하다.

---

## 3. 연결은 세 갈래다 — 그리고 B가 공백이다

| | 무엇 | 상태 |
| --- | --- | --- |
| **A. 제약 수집·구조화** | `resource_constraints_text` → `RESOURCE_SPEC` | 스키마·되묻기 문구까지 **이미 있음**. 생산자만 없음 |
| **B. 클라우드 네이티브 관심사 커버리지** | 사용자가 **안 쓴** 요구사항을 드러낸다 | **아무것도 없음 — 진짜 공백** |
| **C. 추적성·부합 측정** | 제약·관심사를 FR/NFR처럼 추적 | `agent/traceability.py`가 자리를 잡음 |

**왜 배포 다이어그램만으로 부족한가**: 다이어그램은 A를 **늦게** 반영하고, B와 C는 아예
나타나지 않는다. 다이어그램에는 "이 앱이 무상태인가"를 적을 칸이 없고, 있어도 그때는
요구사항·유스케이스·명세가 모두 굳은 뒤다.

**B가 "요구사항 분석 때부터 연결"의 실체다.** A는 전달이고(당기면 좋지만 성질이 배관),
B는 요구사항 분석이 **클라우드 네이티브라는 성질 때문에 다르게 이루어지는** 지점이다.

### 기존 기계와 정확히 대응된다

`check_coverage`가 "아무 유스케이스도 다루지 않는 FR(orphan)"을 찾는다. B는 같은 모양으로
**"아무 요구사항도 다루지 않는 클라우드 네이티브 관심사"**를 찾는다. 커버리지 판정은
집합 연산이라 결정론이고, 이 저장소가 이미 그 형태를 신뢰하고 있다.

---

## 4. B를 정직하게 만드는 법

관심사 목록을 우리가 지어내면 그건 **임의 사전**이고, 요구사항 KB가 명시적으로 금지하는
바로 그것이다(`knowledge/rules.py`의 `NON_RULE` 심각도가 존재하는 이유).

근거는 이미 저장소 안에 있다 — **`app/deployment/patternkb`**:

- 담긴 것: Azure Architecture Center의 클라우드 설계 패턴·아키텍처 스타일·설계 원칙
  (CC-BY-4.0) + **12-factor 배포 원칙**(MIT).
- 규율: 모든 답에 *"설계 지침이지 클라우드 사실이 아닙니다"*가 붙고, 근거 라벨은
  `pattern-advisory` 하나이며 basis는 **영원히 `inferred`**다. *"검수해도 사실이 되지
  않는 성격이라 reviewed를 붙이지 않는다."*

여기에 CNCF 정의와 ISO/IEC 25010은 공개 인용이 가능하다(도서와 달리 저작권 문제 없음).

**따라서 B의 관심사는 `patternkb`에서 파생하고, 요구사항 KB와 같은 basis 규율을 받는다.**
`GUIDANCE`/`NON_RULE` 구분도 그대로 쓸 수 있다 — 대부분의 관심사는 "위반이면 결함"이
아니라 "안 정했으면 설계가 정해야 할 것"이다.

---

## 5. 경계 — 명세는 기술중립으로 남는다

`spec.black-box-no-internal-components`·`spec.no-protocol-mechanics`가 명세에 내부
컴포넌트·프로토콜을 금지한다. **클라우드 리소스를 시나리오 스텝에 넣으면 에이전트가
자기 규칙을 위반한다.**

제약(A)과 관심사(B)는 NFR 층과 **나란한 별도 산출물**에 살고, 유스케이스 명세는 손대지
않는다. 이건 타협이 아니라 §2.3의 결론이다 — 두 관점은 같은 문서에 들어갈 수 없다.

---

## 6. 미결 — 제약 입력 방식과 BERT (2026-07-27 시점)

**결정되지 않았다.** 갈래와 각각이 건드리는 것만 적어 둔다.

### 입력 경로

| 갈래 | 뜻 | 건드리는 것 |
| --- | --- | --- |
| 별도 입력 | 제약을 애초에 따로 받는다 | API·프론트엔드에 입력 자리(merge-plan: *"프론트엔드는 `deployment_diagram`만 알고 `resource_spec` 입력 자리가 없다"*) |
| 같이 받고 분리 | 요구사항 산문에 섞여 오는 것을 갈라낸다 | **분류 층** — 아래 |

### 분류기

지금 `classifier.py`는 **이진 분류**다: 파인튜닝 BERT, 라벨 `0=NFR, 1=FR`, 가중치 417MB,
`materials/BERT_FR_NFR_Classifier` 노트북 산출물.

"같이 받고 분리"를 택하면 **제약이라는 세 번째 갈래**가 필요하고, 선택지는:

1. **BERT 재튜닝** — 3-class로 다시 학습. 라벨 데이터가 필요하고, 지금 매핑이 학습 시점에
   고정돼 있어 `_ID2LABEL`부터 바뀐다.
2. **BERT 폐기** — LLM 분류로 대체. 이 저장소의 측정이 이미 말하는 위험이 있다: LLM 판정은
   도메인에 따라 78~90% 흔들린다(`requirements-agent-improvements.md` §7~§9). 다만 그건
   *의미 규칙 판정*의 수치이고 *문장 분류*의 수치가 아니다 — **다른 과제이므로 그대로
   옮겨 쓸 수 없다.**
3. **제약만 별도 경로** — 분류를 건드리지 않고 제약은 따로 받는다(위 표의 첫 줄).

**판단 기준은 성능이고, 성능은 재서 정한다.** 이 저장소에는 그 계기가 이미 있다
(`evaluation/` — 라벨 대비 채점, 반복 측정, 조건 기록). 셋 중 무엇을 고르든 **같은
조건으로 재서 비교한 표**가 근거가 되어야 한다.

### 아직 답이 없는 것

- 제약 원문에서 구조화할 때 **LLM을 쓸 것인가.** `region`은 지명→코드 해석이 필요하고
  (도구가 있다), `monthlyBudgetUSD`는 통화 환산이 필요한데 **환율 소스가 없어 계약이
  거부한다**(다른 통화는 진입에서 USD로 받아야 한다).
- B의 미충족을 **게이트로 막을지 표시만 할지.** 사용자가 안 쓴 것을 결함이라 부르면
  오탐이 대부분이 된다 — 현재 판단은 "인계 항목으로 표시".

---

## 7. 이 저장소에서 잰 것

> 아래는 문헌이 아니라 실측이다.

**입력 11종의 NFR 85건이 어떤 성격인가**(키워드 기반 추정, 2026-07-27):

| 성격 | 비중 |
| --- | ---: |
| 어디에도 안 걸림 | **42%** |
| 지연/성능 | 20% |
| 보안·규정 | 16% |
| 가용성 | 9% |
| 사이징/용량 | **8%** |
| UI/클라이언트 | 5% |

**뜻**: "NFR을 클라우드 KB에 질의한다"는 접근은 커버리지가 낮다. 게다가 `sizingkb`가
*"1000명이면 vCPU 2입니다"는 근거 없는 단정*이라며 그 추론을 **원칙적으로 거부한다**
(그리고 그것이 옳다). 이 수치가 §3에서 A보다 B를 공백으로 지목한 근거 중 하나다.

---

## 출처

문헌:

- [CNCF Cloud Native Definition v1.1](https://github.com/cncf/toc/blob/main/DEFINITION.md)
- [The Twelve-Factor App](https://12factor.net/)
- [Kubernetes production best practices](https://learnkube.com/production-best-practices)
- [AWS Well-Architected Framework — 6 pillars](https://aws.amazon.com/blogs/apn/the-6-pillars-of-the-aws-well-architected-framework/)
- [ISO/IEC 25010:2023 update (arc42 Quality Model)](https://quality.arc42.org/articles/iso-25010-update-2023)
- [8 fallacies of distributed computing](https://ably.com/blog/8-fallacies-of-distributed-computing)
- [Analyzing Requirements Engineering for Cloud Computing](https://www.researchgate.net/publication/315913697_Analyzing_Requirements_Engineering_for_Cloud_Computing)
- [Architecturally significant requirements](https://grokipedia.com/page/Architecturally_significant_requirements)
- [Data residency requirements](https://expanso.io/blog/data-residency-requirements/)
- [Non-functional requirements capture (Microsoft engineering playbook)](https://microsoft.github.io/code-with-engineering-playbook/design/design-patterns/non-functional-requirements-capture-guide/)

저장소 안:

- `app/deployment/appkb/request.json` — `RESOURCE_SPEC v1` 계약과 각 칸의 소비자
- `app/deployment/appkb/contract.py` — `REQUIRED_WHY`(되묻기 문구의 원본)
- `app/deployment/patternkb/` — 12-factor · Azure 패턴 산문, `pattern-advisory` basis
- `app/deployment/envkb/regions.py` — `resolve_region`
- `docs/agent-sdk-merge-plan.md` — "제약이 아직 안 흐른다"
- `docs/requirements-agent-improvements.md` §7~§9 — LLM 판정 흔들림 측정
