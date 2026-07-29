# 클라우드 리소스 의존 — 조사와 관측

> **살아 있는 문서다. 계속 갱신한다.** 아카이브에 넣지 않는다.
>
> **이 문서에는 아직 "모델"이 없다.** 조사 결과·근거 있는 관측·**선행 연구 대조**가 있다.
> 모델은 §5.4의 정박을 실제로 수행한 뒤에 선다.
>
> 규율: **우리가 만든 것을 근거로 쓰지 않는다.** 근거는 `파일:줄`·원문·인용 가능한
> 외부 문헌이다.

---

## 0. 폐기 기록 (2026-07-30)

이 문서의 첫 판에 **근거 없는 모델**을 적었다가 전부 지웠다. 무엇을 왜 지웠는지 남긴다.

| 지운 것 | 왜 |
|---|---|
| **필연의 출처 여섯** (식별·주소 / 자원 할당 / 제어 권한 / 데이터 도달 / 생명주기 / 정책) | **어떤 소스에도 없다.** 내가 나열한 것이다 |
| **관계의 종류 넷** (요구 / 참조 / 파생 / 포함) | 같음. `포함`만 TOSCA `HostedOn`에 대응이 있고 나머지는 근거 없음 |
| **형식** `(주체·대상·관계 종류·필연의 출처·양상·방향쌍·술어)` | 위 둘을 조합한 것이라 근거가 없다 |
| **"격리 경계" 발생론** | 그럴듯하지만 인용할 문헌이 없다 |
| **"반증됐다"는 서술** | **내가 만든 가설을 내가 만든 절차로 반증했다.** 그건 검증이 아니라 자기 대화다 |

**남은 것은 관측뿐이다**(§4). 관측은 유효하다 — 인용이 있다.

---

## 1. 전수조사 (2026-07-30)

### 1.1 앞선 선정은 전수가 아니었다

첫 판은 후보를 **cb-tumblebug이 몇 번 언급하느냐**로 잘랐다. 그건 전수가 아니라
**한 프로젝트의 시야**다. 그 방식으로 `cm-beetle`을 기각했는데, 실제 설명은
*"A computing infrastructure migration framework that **recommends target configurations**"*
— 우리가 하려는 일과 가장 가까운 프로젝트였다.

### 1.2 실제 목록

`api.github.com/orgs/cloud-barista` → `public_repos: 59`.
`?per_page=20&sort=full_name`으로 쪽을 나눠 받았다.

> **출처의 한계.** 페이지마다 요약기가 2건씩 흘려 54건만 직접 받았고, 나머지 다섯
> (`cb-mapui`·`cb-milkyway`·`cm-grasshopper`·`cm-honeybee`·`poc-specialized_services`)은
> 다른 응답에서 관측된 이름으로 메웠다. **59건을 한 번에 받은 것이 아니다.**

### 1.3 판정

판정 기준: *"이 저장소가 **자원 사이의 관계**에 대해 무언가를 말하는가."*

| 무리 | 저장소 | 판정 |
|---|---|---|
| **자원을 만든다** | `cb-spider` · `cb-tumblebug` · `mc-terrarium` | **채택** — 관계가 구현에 드러난다 |
| **자원을 추천·이관한다** | `cm-beetle` · `cm-model` · `cm-honeybee` · `cm-damselfly` · `cm-ant` | **채택 후보** — cm-beetle·cm-model 확인함(§2), 나머지 미확인 |
| 주변 도구 | `cb-store`(메타) · `cb-log` · `cb-mapui`·`cm-butterfly`·`ai-ui`(UI) · `cb-dragonfly`·`ai-o11y`(모니터링) · `mc-data-manager`·`cm-data-mold`(데이터 이관) | 기각 — 자원 관계를 만들지도 강제하지도 않는다 |
| CSP SDK | `ktcloud-sdk-go` · `ktcloudvpc-sdk-go` · `nhncloud-sdk-go` | 기각 — 단일 CSP 접속 계층 |
| 보관·POC | `poc-*` 8종 · `cb-client` · `cb-ladybug` · `cb-operator` · `cb-webtool` · `cloud-barista.github.com` | 기각 — **archived** |
| 문서·사이트 | `docs` · `cloud-barista.github.io` · `*-api-web` · `.github` · `api` · `archive` · `cb-coffeehouse` · `cb-fw-template` | 기각 — 자원을 다루지 않는다 |
| AI 플랫폼 | `ai-mcmp` · `ai-adm` · `ai-app` · `ai-ha` · `ai-ops` | 기각 — 별개 제품군 |
| 미확인 | `cb-larva`(인큐베이터 연구) · `cm-cicada` · `cm-mayfly` · `cm-centipede` · `cloud-migrator` · `mc-meta-365` | **보류** — 설명이 없어 판정 불가 |

**보류 6건을 0으로 적지 않는다.** 설명이 없다는 것은 관계가 없다는 뜻이 아니다.

---

## 2. 조사가 답한 것 — 어디에도 개념 모델이 없다

READMEs를 직접 읽어 확인했다.

| 저장소 | 자원 관계·의존·순서를 말하나 |
|---|---|
| **cb-spider** | **없다.** 자원 목록(`VPC, VM, Disk, NLB, Kubernetes, Object Storage`)은 적지만 의존·순서·묶음을 다루지 않는다. Quick Start가 `VPC → Security Group → KeyPair → VM`을 **예제로** 보일 뿐이다 |
| **mc-terrarium** | **없다.** *"multi-cloud networking features, such as site-to-site VPN setup"*까지만 |
| **cm-beetle** | **없다.** *"recommendation of optimal configuration of target cloud infrastructure"*라고만 하고 **방법론·입력·고려 요소를 적지 않는다** |
| **cloud-barista**(우산) | **없다.** *"integrated archive for repository of major frameworks"* — 구성 요소 나열과 폐기 공지뿐이고 아키텍처 모델이 없다 |
| **cm-model** | **가장 가깝다.** *"managing source and target models for cloud migration… standardized Go structs"*. 그리고 **"Use the dependency analyzer script to understand struct relationships"** — cloud-barista에서 **의존을 명시적으로 다루는 유일한 자리**로 지금까지 확인된 것 |

> **결론.** cloud-barista에는 자원 의존의 **개념 모델이 없다.** 넷을 직접 확인했고
> 양상이 일관된다. 그러니 우리가 관측한 의존은 전부 **다른 것을 하려다 남은 흔적**이다 —
> 필수 필드, 삭제 거부 메시지, 스크립트 순서, 프로바이더별 자산표.
>
> **이것이 이 작업의 값이자 한계다.** 명시화할 것이 있다는 뜻이고, 동시에 **모델의
> 근거를 이 생태계 안에서는 못 얻는다**는 뜻이다.

**다음 확인점: `cm-model`의 dependency analyzer가 무엇을 관계로 세는가.** 이름만으로는
Go struct 참조 분석일 수 있는데, 그렇다면 자원 의존이 아니라 코드 의존이다. 확인 전까지
근거로 쓰지 않는다.

---

## 3. 하위 기제가 둘이고 그것이 의존의 가시성을 바꾼다

`cb-tumblebug v0.12.25`의 `src/core/resource/*.go`에서 `terrarium`/`spider` 언급을 센 것.

| 자원 | terrarium | spider |
|---|---:|---:|
| `vpn` | **73** | 1 |
| `sqlDb` | **53** | 0 |
| `objectStorage` | 0 | **169** |
| `vNet` · `securityGroup` · `k8sCluster` | 0 | 110 · 52 · 104 |

두 기제가 의존을 다르게 다룬다. 드라이버 갈래는 상위가 호출 순서와 참조를 정해야 해서
**요청 스키마에 의존이 드러나고**, OpenTofu 갈래는 도구가 참조로 스스로 순서를 풀어
**템플릿 안에 숨는다.**

`core/model/sqlDb.go:30`의 `RequiredCSPResourceForSqlDB`가 그 자리다 — 템플릿이 입력으로
받아야 하니 상위가 CSP별로 명시해 모은다(aws는 `vNetID`+`subnet1ID`+`subnet2ID`,
azure는 `resourceGroup`만).

**함의**: `vpn`·`sqlDb`의 의존이 얇게 잡히는 것은 **실제로 얇아서가 아니다.** 관측
가능성이 기제에 좌우된다.

---

## 4. 근거 있는 관측 — 모델이 아니라 사실

인용 없이는 적지 않는다. 이것들이 언젠가 모델의 재료가 된다.

| 관측 | 근거 |
|---|---|
| `spec`·`image`는 참조 카운트를 **일부러 걸지 않는다** | `core/infra/control.go:1302` — 다른 자원에는 `UpdateAssociatedObjectList`를 부르는데 spec 줄만 주석 |
| **참조 방향과 삭제 제약 방향이 다르다** | 삭제 거부는 `core/resource/common.go:417` *"cannot delete … still referenced by N object(s)"*, 반면 vNet 삭제는 subnet을 **먼저 지운다**(`core/resource/vnet.go:934`) |
| **개수만이 아니라 배치 조건이 있다** | `core/model/sqlDb.go:39` `subnet2ID example:"subnet-xxxx in different AZ"` |
| **같은 자원쌍이 생성 경로마다 요구가 다르다** | `K8sNodeGroupReq`는 image·spec이 선택, `K8sNodeGroupDynamicReq`는 **필수** (`core/model/k8scluster.go`) |
| **CSP마다 요구가 다르다** | `SecurityGroupReq.VNetId`에 `required`가 없고 주석이 *"some CSPs (e.g., Azure, Tencent, NHN) don't bind SG to VPC"*(`core/model/securitygroup.go:69`). 반면 cb-spider `SecurityInfo.VpcIID`는 `required` |
| **동시성 요구가 있다** | `assets/k8sclusterinfo.yaml`의 `nodeGroupsOnCreation` — 클러스터와 **함께** 만들어야 하는가 |
| `customImage`는 노드**로부터** 만들어진다 | 생성 경로가 `POST /ns/{ns}/infra/{i}/node/{n}/snapshot` (`server.go`) |

전수 결과(자원 21 · 간선 39 · 관측 83)는 `graphkb/parsers/tumblebug_resources.json`에
있고 여기서 다시 재지 않는다.

### 4.1 관측으로 기각한 판정 기준

실제로 틀린 답을 냈던 것들이라 테스트로 굳혀 뒀다.

- **순서는 의존을 함의하지 않는다** — 스크립트의 선형 순서에서 쌍을 만들었더니
  `image→vNet` 같은 거짓 간선 10개가 나왔다.
- **연산의 인자는 생성 의존이 아니다** — 디스크를 붙이는 요청이 디스크 id를 요구하는
  것은 연산의 인자다.

---

## 5. 선행 연구와 외부 정박 (2026-07-30)

**모델의 근거를 이 생태계 안에서는 못 얻는다**(§2)는 결론에서 밖으로 나갔다. 결과는
**우리가 하려던 것의 상당 부분이 이미 있다**는 것이다. 이걸 모르고 쓰면 논문이 아니다.

### 5.1 외부 정박 — TOSCA가 우리 어휘를 이미 갖고 있다

`OASIS TOSCA Simple Profile in YAML v1.1`. 위상은 **typed directed graph**이고, 노드의
`requirements`가 다른 노드의 `capabilities`로 충족된다.

**규범 관계 타입**이 우리 관측과 이렇게 대응한다.

| TOSCA | 정의 | 우리 관측 |
|---|---|---|
| `HostedOn` | 소프트웨어가 컴퓨트 자원 위에 설치되는 호스팅 관계 | `node ⊂ infra` · `subnet ⊂ vNet` |
| `AttachesTo` | 저장소 등의 부착 | `dataDisk ↔ node` |
| `ConnectsTo` | 데이터·서비스 연결 | `nlb → node` · SG의 허용 흐름 |
| `RoutesTo` | 네트워크 구성 요소 간 라우팅 | (우리 층에 아직 없음) |
| `DependsOn` | *"generic dependencies between nodes **to influence orchestration order**"* | — |

`DependsOn`이 중요하다. **TOSCA는 순서를 1급 의존 타입으로 둔다.** 우리가 *"순서는 의존을
함의하지 않는다"*(§4.1)고 기각한 것과 모순처럼 보이지만 아니다 — 우리가 기각한 것은
**관측된 순서에서 의존을 추론하는 것**이고, TOSCA는 **선언된 의존이 순서를 강제하는
것**이다. 방향이 반대다. 다만 이 구분은 우리가 문서에 적어야 하는 것이지 자명하지 않다.

**`node_filter`가 결정적이다.** *"A node template can describe a requirement for another
node without including it in the topology. Instead, the node provides a node_filter to
describe the target node type along with its capabilities and property constraints"* —
`greater_or_equal`·`in_range`·`equal` 연산자로 **late binding**한다.

> 우리가 `constraint-derivation.md`에서 "필터 층"이라 부른 것(vCPU≥·memory≥·cost≤)은
> **TOSCA `node_filter`의 형식과 같다.** 우리가 발명할 것이 아니라 정박할 자리다.

Terraform/OpenTofu는 **암시 의존**(속성 참조)과 **명시 의존**(`depends_on`)을 갈라
DAG를 만들고 그것으로 생성·파기 순서와 병렬성을 정한다. Kubernetes는
`ownerReferences`/`finalizers`로 생명주기를 건다. **우리가 관측한 "참조 방향 ≠ 삭제
제약 방향"(§4)이 이 둘에 각각 대응한다.**

### 5.2 선행 연구 — 우리 폐포는 새 것이 아니다

`Bellendorf & Mann, "Specification of cloud topologies and orchestration using TOSCA:
A survey", Computing (2019)` — 124편을 6범주 19하위범주로 분류한 체계적 문헌 조사.
**그 분류에 `Topology completion`이 하위범주로 따로 있다.**

| 선행 연구 | 무엇을 했나 | 우리와의 관계 |
|---|---|---|
| **Hirmer, Breitenbücher, Binz, Leymann**, *Automatic topology completion of TOSCA-based cloud applications*, Informatik 2014, 247–258 | **불완전 토폴로지를 자동 완성**한다. 사용자는 업무 관련 구성 요소만 모델링하고 나머지는 채워진다 | **우리 폐포 절차와 같은 문제다.** Eclipse Winery에 구현돼 있고 `requirements`가 완성 범위를 제한한다 |
| **Brogi & Soldani**, *Matching cloud services with TOSCA*, ESOCC 2013 W/S, 218–232 · *Finding available services in TOSCA-compliant clouds*, SCP 115-116 (2016) | 요구에 맞는 클라우드 서비스를 **매칭**한다 | 우리 "후보 선별"과 같은 자리 |
| **Brogi, Cifariello, Soldani**, *DrACO: Discovering available cloud offerings*, CSRD 32(3-4) (2017) | 가용한 클라우드 제공물을 **발견**한다 | 우리 미러·카탈로그 축 |
| **Brogi, Di Tommaso, Soldani**, *Validating TOSCA application topologies* (2017) · *Sommelier* (2017) | 토폴로지의 **타당성 검증** | 우리 `verify`·`verify_diagram` |
| **Saatkamp et al.**, *Topology splitting and matching for multi-cloud deployments*, CLOSER 2017 | 멀티클라우드로 토폴로지를 **분할·매칭** | 우리 멀티 CSP 축 |
| **Weerasiri, Barukh, Benatallah, Sheng, Ranjan**, *A taxonomy and survey of cloud resource orchestration techniques*, ACM CSUR 50(2) (2017) | 자원 오케스트레이션 기법의 **분류 체계** | 우리가 만들려던 분류의 선행 |
| **Bergmayr et al.**, *A systematic review of cloud modeling languages*, ACM CSUR 51(1) (2018) | 클라우드 모델링 언어 전수 | 어휘 선택의 근거 |

**정직하게 적자면**: 폐포(topology completion) · 매칭 · 검증 · 멀티클라우드 분할이
**전부 선행 연구가 있다.** 우리가 "새로 만들었다"고 주장할 수 있는 것이 아니다.

### 5.3 선행 연구가 비워 둔 자리

그렇다고 남는 것이 없지는 않다. **문헌이 스스로 지목한 공백**과 우리 관측이 겹치는
자리가 셋 있다.

1. **후보가 여럿일 때의 선택 기준.** Winery 문서가 완성 알고리즘을 설명하면서
   *"selection criteria remain unspecified"* — **여러 후보가 매칭될 때 어떻게 고르는지,
   완성이 불가능하면 어떻게 하는지를 적지 않는다.** 우리 규율(*근거 없으면 고르지
   않고 미정으로 낸다*)이 정확히 그 자리다.
2. **검증·타당성이 미탐구 영역이다.** 서베이의 결론이 명시한다 — *"discovered areas that
   are hardly explored so far … Examples include security and privacy aspects, as well as
   **verification and validation** in connection with TOSCA models."*
3. **LLM 시대의 지식 주입** — 우리와 가장 가까운 선행 연구다.

### 5.3.1 Nekrasov et al. (TOSEM 2026) — 읽고 정리한 것

`Nekrasov, Fossati, Kumara, Tamburri, van den Heuvel, "IaC Generation with LLMs:
An Error Taxonomy and A Study on Configuration Knowledge Injection", ACM TOSEM,
doi:10.1145/3817608 (arXiv:2512.14792)`. Terraform 생성 **458건**을 분석해 **오류
15종의 2차원 분류**를 만들고, 설정 지식 주입을 Naive RAG → Graph RAG로 비교했다.

**서론이 우리 논지를 한 문장으로 적어 뒀다.**

> *"IaC correctness depends on adherence to **provider schemas**, proper handling of
> **resource dependencies**, and alignment with **operational requirements that are
> often implicit**."*

**측정 결과**(우리가 재지 않아도 되는 수치다):

| | 기술 검증 | 의도 검증 |
|---|---|---|
| 지배적 오류 | Schema 오류 **94.5%** — 그중 사실오류 65.0% · 불완전 26.5% | **문맥추론 실패 45.7%** · 불완전 30.4% · 사실오류 23.9% |
| 의도 오류의 내역 | | 의도에 안 맞는 자원 사용 **37.0%** · 필요한 자원 누락 **30.4%** · 폐기된 자원 23.9% · 오설정 8.7% |
| 성공률 | 기저 27.1% → 지식 주입 후 **75.3%** | **정체** — 전체 성공률은 62.7%에서 멈춘다 |

**환각이 낡은 지식보다 압도적이다.** 미지원 인자의 **94.8%**, 블록의 **98.1%**, 자원의
**100%**가 *"어떤 Terraform 명세에도 존재한 적 없는"* 것이었다. 즉 문제는 지식이 낡은
것이 아니라 **없는 것을 지어내는 것**이다.

**의존이 실패 유형으로 명시된다.** *"Cross-resource references, including `vpc_id`,
`subnet_ids`, and `target_vault_name`, indicate failures in modeling resource
inter-dependencies. The model struggles to dynamically link argument values to
attributes of other resources, revealing **limitations in its dependency tracking**."*

**그들의 지식 표현**: Terraform AWS provider 문서(v5.90.0) + `tfschema`로 바이너리에서
추출한 스키마를 합쳐, *"a knowledge graph that encodes **resources, arguments, and
relationships**"*를 만들고 Graph RAG로 주입한다.

### 5.3.2 그래서 우리 자리는 어디인가

**겹치는 것 — 새롭다고 주장하면 안 되는 것**

- 자원·인자·관계의 지식 그래프를 만들어 LLM에 주입하는 것 자체
- 의존을 모델링해 생성 품질을 올리는 접근
- 스키마 근거로 환각을 막는 착상

**그들이 하지 않은 것 — 기여 후보(확인 필요)**

| 후보 | 근거 |
|---|---|
| **다중 CSP의 조건부 의존** | 그들의 그래프는 **AWS 단일 프로바이더**다(IaC-Eval이 AWS 전용). 우리 관측의 핵심인 *"`sqlDb→vNet`이 aws에서 참이고 azure에서 거짓"*은 그 형식에 담을 자리가 없다 |
| **생성 경로별 차이** | 명시 생성과 동적 생성에서 요구가 달라지는 것. Terraform 단일 경로에는 이 축이 없다 |
| **근거의 출처 추적** | 그들은 문서+바이너리 스키마를 합쳐 그래프를 만들지만, **각 사실이 어디서 왔는지를 산출물이 들고 다니지는 않는다.** 우리는 `파일:줄`을 간선에 붙인다 |
| **거절하는 능력** | 그들의 파이프라인은 **항상 생성한다.** 우리 규율은 근거가 없으면 **미정으로 내고 막는다** |

마지막이 가장 날카롭다. 의도 오류의 **30.4%가 "필요한 자원 누락"**이고 **37.0%가 "의도에
안 맞는 자원"**인데, 둘 다 **모델이 확신 없이 뭔가를 내놓아서** 생기는 것이다. *"근거가
없으면 안 낸다"*는 커버리지를 깎는 대신 그 두 유형을 구조적으로 줄인다 — 다만 **그것이
실제로 낫다는 것은 우리가 아직 재지 않았다.**

> **가장 중요한 정박점**: 그 논문의 결론이 *"intent alignment plateaued, revealing a
> **'Correctness-Congruence Gap'** where LLMs can become proficient 'coders' but remain
> limited 'architects' in fulfilling nuanced user intent"*다. **우리 과제의 문제 ③
> (요구사항 부합 측정)이 정확히 그 갭이고, 그쪽은 아직 안 풀렸다.**
>
> 그러면 우리 기여의 자리가 옮겨진다 — **의존 폐포는 기여가 아니다**(선행 연구가 있다).
> **그것을 요구사항에 되묶어 부합을 판정하는 쪽**이 기여 후보다.

### 5.4 그래서 모델을 어디에 정박할 것인가

- **관계 어휘**: TOSCA 규범 관계 타입(`HostedOn`·`AttachesTo`·`ConnectsTo`·`DependsOn`)에
  정박한다. 우리가 만든 "요구/참조/파생/포함"은 폐기했고, 이쪽이 인용 가능하다.
- **제약 형식**: `node_filter` + 연산자에 정박한다.
- **생명주기**: Terraform 파기 순서 · Kubernetes `finalizers`에 대조한다.
- **정박되지 않는 것**을 명시한다 — 우리 관측 중 **파생**(`customImage ← node`)과
  **생성 경로별 차이**(명시/동적)와 **CSP 조건부**는 위 어디에도 자리가 없다.
  **그것이 기여 후보이고, 없다는 것을 먼저 확인해야 주장할 수 있다.**

## 6. 미결

1. **정박 작업을 실제로 하지 않았다.** §5.4는 계획이고, 우리 39간선을 TOSCA 관계 타입에
   실제로 걸어 보면 안 맞는 것이 나올 것이다. 그때 형식이 바뀌어야 대조다.
2. **"없다"를 확인하지 않았다.** 파생·생성경로·CSP 조건부가 정말 선행 연구에 없는지는
   **찾아서 없음을 확인한 것이 아니라 아직 못 찾은 것**이다. 기여로 주장하려면 그 셋을
   키워드로 다시 조사해야 한다.
3. 보류 6건(§1.3)과 `cm-model`의 dependency analyzer(§2) 확인.
4. 서베이 본문(§5 결과 절)을 아직 안 읽었다 — 범주별 논문 분포와 연도별 추세가 거기
   있고, 그것이 "무엇이 포화됐고 무엇이 안 됐나"를 말해 준다.

---

## 7. 참고문헌

**표준**

- OASIS. *Topology and Orchestration Specification for Cloud Applications (TOSCA)
  Version 1.0.* OASIS Standard, 2013.
- OASIS. *TOSCA Simple Profile in YAML Version 1.1.* — 규범 관계 타입(`HostedOn` ·
  `AttachesTo` · `ConnectsTo` · `DependsOn` · `RoutesTo`) · `node_filter` · 제약 연산자
- HashiCorp. *Terraform — implicit/explicit dependencies, resource graph.*
- Kubernetes. *Owner references and finalizers.*
- Eclipse Winery. *TOSCA Topology Completion* (문서) — 완성 알고리즘의 구현 설명

**서베이**

- Bellendorf, J., Mann, Z. Á. *Specification of cloud topologies and orchestration
  using TOSCA: A survey.* Computing, 2019. — 124편 · 6범주 19하위범주 ·
  `Topology completion`이 독립 하위범주 · 결론이 **verification and validation**을
  미탐구 영역으로 지목
- Weerasiri, D., Barukh, M. C., Benatallah, B., Sheng, Q. Z., Ranjan, R.
  *A taxonomy and survey of cloud resource orchestration techniques.*
  ACM Computing Surveys 50(2), 2017.
- Bergmayr, A. 외. *A systematic review of cloud modeling languages.*
  ACM Computing Surveys 51(1), 2018.

**의존·완성·매칭**

- Hirmer, P., Breitenbücher, U., Binz, T., Leymann, F. *Automatic topology completion
  of TOSCA-based cloud applications.* Informatik 2014, 247–258. — **우리 폐포와 같은 문제**
- Brogi, A., Soldani, J. *Matching cloud services with TOSCA.* ESOCC 2013 Workshops,
  218–232. · *Finding available services in TOSCA-compliant clouds.* SCP 115–116, 2016.
- Brogi, A., Cifariello, P., Soldani, J. *DrACO: Discovering available cloud offerings.*
  CSRD 32(3–4), 2017.
- Brogi, A., Di Tommaso, A., Soldani, J. *Validating TOSCA application topologies.*
  MODELSWARD 2017. · *Sommelier: a tool for validating TOSCA application topologies.* 2017.
- Saatkamp, K. 외. *Topology splitting and matching for multi-cloud deployments.*
  CLOSER 2017, 247–258.

**LLM · IaC**

- Nekrasov, R., Fossati, S., Kumara, I., Tamburri, D. A., van den Heuvel, W.-J.
  *IaC Generation with LLMs: An Error Taxonomy and A Study on Configuration Knowledge
  Injection.* ACM TOSEM, doi:10.1145/3817608 (arXiv:2512.14792). — **가장 가까운 선행 연구**
- (그 논문이 인용한 것 중 확인해야 할 것) Meflah 외 — NL 의도 ↔ TOSCA 청사진 상호 생성 ·
  Shao 외 — 도메인 지식 사전 + IaC 문법 비의존 중간 모델 · Zhang 외 — ChatGPT 생성
  Kubernetes 매니페스트의 **35% 이상이 설정 냄새**를 포함
