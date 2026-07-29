# 클라우드 리소스 의존 — 조사와 관측

> **살아 있는 문서다. 계속 갱신한다.** 아카이브에 넣지 않는다.
>
> **이 문서에는 아직 "모델"이 없다.** 조사 결과와 근거 있는 관측만 있다. 모델을 세우려면
> 근거가 필요한데, 그 근거를 어디서 얻을지가 §5의 미결이다.
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

## 5. 미결 — 모델의 근거를 어디서 얻을 것인가

이것이 지금 가장 큰 공백이고, 이 문서가 "모델"을 못 적는 이유다.

1. **cloud-barista에는 개념 모델이 없다**(§2). 관측만으로 모델을 세우면 **그건 다시 우리
   발명**이다 — 방금 그렇게 했다가 지웠다.
2. **남은 길은 외부 정박뿐이다.** 자원 의존을 형식으로 다루는 기존 체계가 있다 —
   TOSCA(`requirements`/`capabilities`/`occurrences`/`HostedOn`), CloudFormation
   `DependsOn`, Terraform 암시 의존, Kubernetes `ownerReferences`/`finalizers`,
   UML 합성/연관. **아직 우리 관측을 이것들에 걸어 보지 않았다.**
3. 그 대조는 **우리 형식을 바꿀 수 있어야** 대조다. 못 바꾸면 장식이다.
4. **학술 문헌을 아직 안 봤다.** 클라우드 자원 모델링·의존 분석에 선행 연구가 있는지가
   미확인이고, 논문 관점에서는 이것이 가장 먼저다.
5. 보류 6건(§1.3)과 `cm-model`의 dependency analyzer(§2) 확인.
