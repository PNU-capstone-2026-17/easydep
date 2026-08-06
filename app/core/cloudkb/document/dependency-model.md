# 클라우드 리소스 의존 — 현재 아는 것

> **보류 문서:** graphkb에서 depkb로 전환하는 과정의 내용이 섞여 있다. VM 의존성의 현재 기준을 확정하기 전에는 구현 지시로 사용하지 않는다.

> **살아 있는 문서다.** 새 근거가 나오면 본문을 고친다. 아카이브에 넣지 않는다.
>
> 상위: `docs/research.md`(과제 원문) · `docs/cloud-native-extension.md`(현재 진실).
> 표시 규율: **관측**(인용 있음)과 **우리 구성**(분류·이름·배치)을 가려 적는다.
> 관측을 조직하려면 이름이 필요하지만 **그 이름은 근거가 아니다.**

---

## 1. 무엇을 아는가

`cb-tumblebug v0.12.25` · `cb-spider v0.12.37` 소스에서 전수로 뽑았다. 산출물은
`graphkb/parsers/tumblebug_resources.json`, 절차는 `graphkb/tumblebug_closure.py`.

```
자원 21종 · 의존 간선 39 · 관측 83
관측마다 출처(저장소·태그) · 형태(어떤 코드/데이터) · 인용(file:line)
```

**간선에 `questions`·`authorities`가 실렸다 (2026-07-30, 계획 P1).** 관측의
형태·출처에서 **기계적으로 재계산 가능한 사영**이고, 대응표는
`graphkb/edge_semantics.py`에 **우리 구성으로 표시**되어 있다 — 정합은 테스트가
강제한다(어긋나면 죽는다). 분포: existence만 32 · lifecycle만 1(`node→customImage` —
노드는 이미지 없이도 만들어지므로 정합) · 둘 다 6. 권위는 tumblebug만 15 ·
spider만 9 · 둘 다 15이고, **`cloud` 권위는 아직 0이다** — CB 소스 관측만으로는
클라우드의 요구임을 말할 수 없고, 벤더 원문 대조(계획 P2)가 그 승격의 유일한 길이다.
기능(functional) 의존은 어떤 관측 형태도 말하지 않으므로 명시적 공백으로 남는다(§7).

**관측의 형태별 분포** — 같은 사실이라도 어디에 어떤 꼴로 있느냐로 믿을 정도가 다르다.

| 형태 | 수 |
|---|---:|
| 요청 스키마 필드 · CSP 중립 인터페이스 | 28 · 28 |
| REST 경로 중첩 · 삭제 보호 코드 | 7 · 7 |
| 프로바이더별 자산 데이터 · 자동 생성 코드 | 4 · 4 |
| 생성 전 존재 확인 코드 · 운영 스크립트 순서 | 3 · 2 |

### 1.1 근거 있는 사실

| 사실 | 근거 |
|---|---|
| 의존은 **(자원쌍 × 생성경로 × CSP)**의 함수다 | `K8sNodeGroupReq`는 image·spec이 선택인데 `K8sNodeGroupDynamicReq`는 필수(`core/model/k8scluster.go`) · `RequiredCSPResourceForSqlDB`(`sqlDb.go:30`)가 CSP별로 다른 요구를 적는다 |
| **참조 방향 ≠ 삭제 제약 방향** | 삭제 거부는 `core/resource/common.go:417` *"cannot delete … still referenced by N object(s)"*, 반면 vNet 삭제는 subnet을 **먼저 지운다**(`vnet.go:934`) |
| **개수만이 아니라 배치 조건이 있다** | `sqlDb.go:39` `subnet2ID example:"subnet-xxxx in different AZ"` · `assets/k8sclusterinfo.yaml:22` `requiredSubnetCount: 2`(aws) |
| **동시성 요구가 있다** | `k8sclusterinfo.yaml`의 `nodeGroupsOnCreation` — 클러스터와 **함께** 만들어야 하는가 |
| `spec`·`image`는 간선이 아니라 **속성**이다 | (가) `core/infra/control.go:1302` — 노드 삭제가 다른 자원에는 참조 카운트를 되돌리는데 `spec`만 주석으로 꺼져 있다 (나) TOSCA에서 `num_cpus`·`mem_size`는 관계가 아니라 `host` **capability 속성** |
| `customImage`는 노드**로부터** 만들어진다 | 생성 경로가 `POST /ns/{ns}/infra/{i}/node/{n}/snapshot` |

### 1.2 폐포 — 자원 하나를 고르면 무엇이 딸려오나

절차는 코드에 있다. **딸려오는 것을 세는 것으로 끝나지 않고, 자동으로 채워지는 것과
사람이 정해야 하는 것을 가른다.**

| 앵커 | CSP | 딸려오는 것 | 사람이 정할 것 |
|---|---|---|---|
| `node`(VM) | aws · azure | infra · vNet · subnet · securityGroup · sshKey (+ spec·image 선택) | **infra 하나** |
| `k8sCluster` | aws | vNet · **subnet ×2** · securityGroup | 셋 다 |
| `k8sCluster` | azure | vNet · subnet ×1 · securityGroup | 셋 다 |
| `sqlDb` | aws | vNet · **subnet ×2**(다른 AZ) | 둘 다 |
| `sqlDb` | azure | **없음** | 없음 |
| `vpn` | azure | infra · vNet · **subnet**(GatewaySubnet) | 셋 다 |
| `objectStorage` | 전부 | **없음** | 없음 |

### 1.3 의존이 아닌 것 — 기각 목록

실제로 틀린 답을 냈던 것이라 `test_tumblebug_resources.py`가 지킨다.

- **순서는 의존을 함의하지 않는다.** 스크립트의 선형 순서에서 쌍을 만들었더니
  `image→vNet` 같은 거짓 간선 10개가 나왔다.
- **연산의 인자는 생성 의존이 아니다.** 디스크를 붙이는 요청이 디스크 id를 요구하는
  것은 연산의 인자다.

---

## 2. 무엇으로 아는가 — 재료와 그 한계

### 2.1 cloud-barista 전수 (저장소 59개)

`api.github.com/orgs/cloud-barista` → `public_repos: 59`. 판정 기준: *"이 저장소가
자원 사이의 관계에 대해 무언가를 말하는가."*

| 판정 | 저장소 |
|---|---|
| **채택** | `cb-spider` · `cb-tumblebug` · `mc-terrarium` |
| **채택 후보** | `cm-beetle` · `cm-model` · `cm-honeybee` · `cm-damselfly` · `cm-ant` |
| 기각 | 주변 도구(메타·로그·UI·모니터링·데이터 이관) · CSP SDK 3 · archived 다수 · 문서/사이트 · AI 플랫폼 5 |
| **보류** | `cb-larva` · `cm-cicada` · `cm-mayfly` · `cm-centipede` · `cloud-migrator` · `mc-meta-365` — 설명이 없어 판정 불가 |

> **한계.** 목록을 받을 때 요약기가 쪽마다 2건씩 흘려 **54건만 직접 받았고** 다섯은 다른
> 응답에서 관측된 이름으로 메웠다. 그리고 **보류 6건을 0으로 세지 않는다** — 설명이
> 없다는 것이 관계가 없다는 뜻은 아니다.

### 2.2 발견 — cloud-barista에 자원 의존의 개념 모델이 없다

READMEs를 직접 읽어 넷을 확인했고 양상이 일관된다.

| 저장소 | 의존·순서·묶음을 말하나 |
|---|---|
| cb-spider | **없다.** Quick Start가 `VPC → SG → KeyPair → VM`을 **예제로** 보일 뿐 |
| mc-terrarium | **없다.** *"multi-cloud networking features, such as site-to-site VPN setup"*까지 |
| cm-beetle | **없다.** *"recommendation of optimal configuration"*이라고만 하고 방법론을 안 적는다 |
| cloud-barista(우산) | **없다.** *"integrated archive"* — 구성 요소 나열뿐 |
| cm-model | **가장 가깝다.** *"dependency analyzer script to understand struct relationships"* — 다만 **자원 의존인지 코드 의존인지 확인 전이라 근거로 쓰지 않는다** |

**그러니 우리가 관측한 의존은 전부 다른 것을 하려다 남은 흔적이다** — 필수 필드, 삭제
거부 메시지, 스크립트 순서, 자산표. 우리 일은 새 사실을 만드는 것이 아니라
**암묵적으로 흩어진 의존을 명시화하는 것**이다.

### 2.3 하위 기제가 둘이고 그것이 의존의 가시성을 바꾼다

`src/core/resource/*.go`의 언급 수.

| 자원 | terrarium | spider |
|---|---:|---:|
| `vpn` · `sqlDb` | **73** · **53** | 1 · 0 |
| `objectStorage` · `vNet` · `securityGroup` · `k8sCluster` | 0 | 169 · 110 · 52 · 104 |

드라이버 갈래(명령형)는 상위가 순서·참조를 정해야 해서 **요청 스키마에 의존이 드러나고**,
OpenTofu 갈래(선언형)는 도구가 참조로 스스로 풀어 **템플릿 안에 숨는다.**
`RequiredCSPResourceForSqlDB`가 그 자리다 — 템플릿 입력이라 상위가 명시해 모은다.

> **`vpn`·`sqlDb`의 의존이 얇게 잡히는 것은 실제로 얇아서가 아니다.**

---

## 3. 표준·선행 연구와의 대응

### 3.1 TOSCA 정박 — 39간선을 실제로 걸었다

**대응되는 것** (§3.1.1의 사전 정의와 짝지은 것)

| 우리 간선 | TOSCA 사전 정의 |
|---|---|
| `node ↔ dataDisk` | `Compute.local_storage` → `AttachesTo` → `BlockStorage` |
| `nlb → node` | `LoadBalancer.application` → **`RoutesTo`** → `Endpoint` |
| `vNic → subnet` · `vNic → node` | `network.Port.link` → `LinksTo` · `.binding` → `BindsTo` |
| (모든 간선의 상위) | `Root.dependency` → `DependsOn`, `[0, UNBOUNDED]` |

**대응되지 않는 것 넷**

| 안 맞는 것 | 간선 | 왜 |
|---|---:|---|
| 카탈로그 참조 `→spec`·`→image` | 6 | TOSCA에서 이건 관계가 아니라 **capability 속성**이다 |
| 그룹 소속 `→infra` 계열 | 5 | `groups`는 **관계 타입이 아니라 별도 구성**이다 |
| 파생 `customImage↔node` | 2 | **타입 상속**(`derived_from`)은 있으나 인스턴스 파생 관계는 규범 타입에 없다 |
| 조건부·개수 | 4 | 규범 관계 타입에 조건을 붙일 자리가 없다 → §3.2 |

> **정박이 실제로 바꾼 것**: `spec`·`image`를 간선에서 **속성**으로 옮겨야 한다.
> `scope.py`가 이미 그 둘을 "선택 역할"로 갈라 뒀고, **관측과 표준이 같은 답에 닿았다.**

**네트워크 관계 타입 확인됨(2026-07-30).** `tosca.nodes.network.Port`가
`link`(→`tosca.relationships.network.LinksTo`)와 `binding`(→`BindsTo`) 요구를 갖는다.
앞서 "확인 실패"로 남겼던 것이 닫혔다.

### 3.1.1 TOSCA가 사전 정의한 의존 — **Simple Profile 1.3 기준**

TOSCA는 **의존을 타입에 미리 박아 둔다.** 노드 타입이 자기 `requirements`를 선언하고,
그것이 다른 노드의 `capabilities`로 충족된다. 아래는 **Simple Profile 1.3**의 원문이다
(판을 못 박는 이유는 §3.1.2 — 2.0에서 이 타입들이 표준 밖으로 나갔다).

| 노드 타입 | 요구 이름 | 대상 capability | 관계 타입 | occurrences |
|---|---|---|---|---|
| `tosca.nodes.Root` | `dependency` | `Node` | **`DependsOn`** | `[0, UNBOUNDED]` |
| `tosca.nodes.Compute` | `local_storage` | `Attachment` | **`AttachesTo`** (→`BlockStorage`) | `[0, UNBOUNDED]` |
| `tosca.nodes.SoftwareComponent` | `host` | `Compute`* | **`HostedOn`** (→`Compute`) | (기본 `[1,1]`) |
| `tosca.nodes.WebApplication` | `host` | `Compute`* | `HostedOn` (→`WebServer`) | (기본) |
| `tosca.nodes.Database` | `host` | `Compute`* | `HostedOn` (→`DBMS`) | (기본) |
| `tosca.nodes.Container.Application` | `host` | `Compute`* | `HostedOn` | (기본) |
| `tosca.nodes.LoadBalancer` | `application` | `Endpoint` | **`RoutesTo`** | `[0, UNBOUNDED]` |
| `tosca.nodes.network.Port` | `link` · `binding` | `network.Linkable` · `network.Bindable` | **`LinksTo`** · **`BindsTo`** | (기본) |

`requirements`가 **없는** 규범 타입: `WebServer` · `DBMS` · `ObjectStorage` ·
`BlockStorage` · `Container.Runtime` · `network.Network`.

두 가지가 중요하다.

- **`Root`가 `dependency`를 갖는다.** 모든 타입이 일반 의존 슬롯을 상속하므로, 의미가
  안 맞는 관계도 `DependsOn`으로는 적을 수 있다. **다만 그건 폴백이고 의미 대응이 아니다.**
- **`LoadBalancer`는 `RoutesTo`다.** 앞서 `nlb→node`를 "`ConnectsTo`/`RoutesTo` 후보"로
  적어 뒀는데, 규범 정의가 `RoutesTo`로 확정한다.

`Compute`* 표시: `SoftwareComponent.host` 등의 대상 capability는 **1.0·1.1에서
`tosca.capabilities.Container`였고 1.2에서 `Compute`로 개칭됐다**(`{1.0,1.1}/node.yaml:130`
vs `{1.2,1.3}/node.yaml:140`). 판을 안 적으면 어긋난다.

### 3.1.2 TOSCA 2.0에서 이 표는 표준 밖으로 나갔다 (2026-07-30 확인)

**v2.0은 2025-07-22에 OASIS Standard가 됐다.** 그리고 §1.1이 이렇게 적는다 —
*"TOSCA v2.0 removes the Simple Profile type definitions from the standard. These type
definitions are now managed as an open source project in the TOSCA Discussion github
repository."* 즉 §3.1.1의 규범 타입은 **표준이 아니라 커뮤니티 프로파일**
(`oasis-open/tosca-community-contributions`)에 있다.

문법도 바뀌었고, 그것이 우리 대조를 뒤집는다.

| | 1.x | 2.0 (표준 원문) |
|---|---|---|
| 요구의 개수 | `occurrences`, 기본 **`[1,1]`**(필수) | **`count_range`**, 기본 **`[0, UNBOUNDED]`**(선택) |
| `occurrences` | 정상 키명 | *"is deprecated in TOSCA 2.0"* |
| 필연 표현 | 요구 정의의 기본값 | 요구 **배정**의 `optional`(기본 false = 채워야 한다) |
| 용량 필터 | 없음 | 요구 배정의 `allocation` 블록 |
| 지연 결속 | `node_filter` + `in_range` | `node_filter`가 **불리언 함수**. `$in_range`는 §10.2.2.2 비교 함수 목록에 **없다**(표준 자기 예제는 쓴다 — 결함) |

> **뒤집힌 것.** 2.0에서는 **요구 정의가 필연을 표현하지 않는다.** 그래서 커뮤니티의
> AWS·Azure·GCP 프로파일이 `count_range`를 생략한 채 쓴 요구들은, 표준대로 읽으면
> 하나도 필수가 아니다. **그 프로파일들은 우리 간선의 모양은 대조해 주지만
> `required` 판정은 대조해 주지 않는다.**

벤더 중립 어휘로는 `com.ubicity:2.5` 프로파일이 더 가깝다 — `Network`·`Subnet`·
`SecurityGroup`·`KeyPair`·`VmImage`·`Port`·`VirtualCompute`를 갖고 AWS·Azure·GCP·
OpenStack 프로파일이 여기서 파생한다. Azure 프로파일은 NSG를 VNet 대신 Region 밑에 두어
**우리 `securityGroup->vNet.required`의 azure 예외와 일치한다.**

조사 전문은 `archive/resource-dependency-2026-07-30.md`(방법과 결과) ·
`archive/reference-implementation-iac-2026-07-30.md`(원자료).

### 3.2 우리가 하려던 것은 대부분 선행 연구가 있다

`Bellendorf & Mann(Computing 2019)` 서베이 — 124편·6범주 19하위범주, **`Topology
completion`이 독립 하위범주**이고 결론이 **verification and validation**을 미탐구
영역으로 지목한다.

| 우리 것 | 선행 |
|---|---|
| 의존 폐포 | **Hirmer, Breitenbücher, Binz, Leymann**(Informatik 2014) — 불완전 토폴로지 자동 완성, Eclipse Winery 구현 |
| 후보 매칭·탐색 | **Brogi & Soldani**(ESOCC 2013 · SCP 2016) · `DrACO`(2017) |
| 토폴로지 검증 | **Brogi, Di Tommaso, Soldani**(2017) · `Sommelier` |
| 멀티클라우드 분할 | **Saatkamp 외**(CLOSER 2017) |
| 필터 층(vCPU≥·cost≤) | TOSCA **`node_filter`** — *"capabilities and property constraints"* + `greater_or_equal`·`in_range`, **late binding** |
| CSP 조건부 | **변이성 관리** — VaMoS 2025 · UCC 2023. *"assigning variability conditions to elements to specify their presence"* |
| 거절 | **abstention/selective prediction** — 서베이 `arXiv:2407.18418` |
| 출처 추적 | **추적성·프로비넌스** — SoSyM 2010 서베이 |

거절만 기제가 갈린다. 문헌은 **모델 불확실성**(NLL·엔트로피)으로 거절하고 우리는
**지식 부재**로 거절한다 — 결정적이고 감사 가능하다. **착상의 차이는 아니다.**

### 3.3 가장 가까운 선행 연구가 남긴 자리

`Nekrasov, Fossati, Kumara, Tamburri, van den Heuvel, TOSEM (doi:10.1145/3817608)`.
Terraform 생성 458건에서 오류 15종을 분류하고 Graph RAG로 설정 지식을 주입했다.

> *"IaC correctness depends on adherence to **provider schemas**, proper handling of
> **resource dependencies**, and alignment with **operational requirements that are
> often implicit**."*

| | 값 |
|---|---|
| 기술 검증 | Schema 오류 94.5% · 사실오류 65.0% · 불완전 26.5% |
| **의도 검증** | **의도 불일치 자원 37.0% · 필요 자원 누락 30.4% · 폐기 자원 23.9% · 오설정 8.7%** |
| 환각 비중 | 미지원 인자 94.8% · 블록 98.1% · **자원 100%**가 "존재한 적 없는" 것 |
| 성공률 | 27.1% → 기술 75.3% · 전체 62.7%에서 **의도 정렬 정체** |

**결론이 우리 자리를 지목한다** — *"intent alignment plateaued, revealing a
**'Correctness-Congruence Gap'** where LLMs can become proficient 'coders' but remain
limited 'architects'."* **과제 문제 ③이 정확히 그 갭이다.**

---

## 4. 우리가 구성한 것 (근거가 아니다)

| 구성 | 성격 |
|---|---|
| 관측의 **형태** 이름(요청 스키마 필드·삭제 보호 코드 …) | 출처를 서술한 것. 등급이 아니다 |
| 자원의 **역할** `compose`/`select` | `select`는 관측+표준 근거가 있다(§1.1). 분류 자체는 우리 구성 |
| 폐포의 **자동/결정** 갈림 | 자동 생성 관측(`provisioning.go`)에 기댄다 |
| 단계 배분 | `constraint-derivation.md` §4 — 거기서 근거/결정을 갈라 적는다 |

---

## 5. 실행 계획에서 이 문서가 대는 것

전체 계획은 `docs/cloud-native-extension.md` §10.

| 단계 | 이 문서가 대는 근거 |
|---|---|
| 2 · 설계 단계 산출물 정의 | §1.2 폐포가 **다이어그램에 무엇이 나와야 하는지** 정한다 |
| 3 · 부합 판정 축 도입 | §3.3의 **의도 오류 4종**을 판정문 축으로 쓴다 — 발명이 아니라 인용이고 **남의 수치와 비교 가능해진다** |
| 4 · 측정 | 거절을 켜고 끌 때 그 4종이 어떻게 달라지나 |

> **벤치마크는 가져올 수 없다.** IaC-Eval은 Terraform/AWS 전용이고 우리 사슬은
> k8s 매니페스트 + 클라우드 자원이다. **가져오는 것은 분류와 방법이지 데이터가 아니다.**

---

## 6. 미결

1. **측정이 없다** — 계획 4단계.
2. ~~TOSCA 2.0으로 다시 대조~~ → **닫았다(§3.1.2).** 규범 타입이 표준 밖으로 나갔고
   `count_range` 기본값이 뒤집혔다. 남은 것은 **`com.ubicity:2.5` 어휘로 39간선을 다시
   짝짓는 일**이다.
3. 파생 관계의 부재 확인(§3.1) — **못 찾은 것이지 없음을 확인한 것이 아니다.**
4. 보류 6건(§2.1)과 `cm-model` dependency analyzer(§2.2).
5. **공식 스키마가 말하는데 우리에게 없는 간선 6개**(2026-07-30). 벤더 스키마 전수
   (aws 2,391 · azure 2,514 · gcp 1,052 간선)를 `core_vendor_map.json`으로 투영해
   나왔다 — `k8sNodeGroup→subnet`(**aws에서 필수**) · `nlb→subnet` · `nlb→securityGroup`
   · `k8sNodeGroup→vNet` · `subnet→securityGroup` · `customImage→dataDisk`.
   셋 다 있는 뼈대는 `subnet→vNet` · `k8sNodeGroup→k8sCluster`(둘 다 필수) ·
   `vm→subnet` · `vm→dataDisk`(둘 다 선택) 넷이다.

---

## 부록 — 폐기 기록

첫 판에 **근거 없는 모델**을 적었다가 지웠다. 규율은 `CLAUDE.md` 문서 정책 5번에 있다.

| 지운 것 | 왜 |
|---|---|
| 필연의 출처 여섯 · 관계의 종류 넷 · 7항 형식 · "격리 경계" 발생론 | 어떤 소스에도 없다 |
| "반증됐다"는 서술 | **내가 만든 가설을 내가 만든 절차로 반증했다** — 검증의 외양을 입힌 자기 대화 |
| `D1~D9` 증거층 | 우리 분류가 1급 필드가 되어 **라벨이 인용을 가렸다** |
| 빈 역할 `BOUND` | 분류하는 자원이 하나도 없었다 |

---

## 참고문헌

**표준** — OASIS TOSCA v1.0 / Simple Profile YAML v1.1 · v1.2 · v1.3 / **v2.0 OASIS Standard(2025-07-22)** · 커뮤니티 프로파일 `oasis-open/tosca-community-contributions`(`org/oasis-open/simple/2.0` · `com.ubicity:2.5`) ·
Terraform 암시·명시 의존 · Kubernetes `ownerReferences`/`finalizers` ·
Eclipse Winery *TOSCA Topology Completion*

**서베이** — Bellendorf & Mann, *Specification of cloud topologies and orchestration
using TOSCA: A survey*, Computing 2019 · Weerasiri 외, *A taxonomy and survey of cloud
resource orchestration techniques*, ACM CSUR 50(2) 2017 · Bergmayr 외, *A systematic
review of cloud modeling languages*, ACM CSUR 51(1) 2018

**의존·완성·매칭** — Hirmer 외, Informatik 2014, 247–258 · Brogi & Soldani, ESOCC 2013 W/S
218–232 · SCP 115–116, 2016 · Brogi, Cifariello, Soldani, CSRD 32(3–4) 2017 ·
Brogi, Di Tommaso, Soldani, 2017 · Saatkamp 외, CLOSER 2017

**변이성 관리** — *Cross-Vendor Variability Management for Cloud Systems Using the TOSCA
DSL*, VaMoS 2025, doi:10.1145/3715340.3715433 · *Enhancing Deployment Variability
Management by Pruning Elements in Deployment Models*, UCC 2023,
doi:10.1145/3603166.3632143

**거절·추적성** — *The Art of Refusal: A Survey of Abstention in LLMs*, arXiv:2407.18418 ·
*Task Abstention for LLMs in Code Generation*, arXiv:2605.17029 · *A survey of
traceability in requirements engineering and model-driven development*, SoSyM 2010

**LLM · IaC** — Nekrasov 외, *IaC Generation with LLMs: An Error Taxonomy and A Study on
Configuration Knowledge Injection*, ACM TOSEM, doi:10.1145/3817608 (arXiv:2512.14792)
