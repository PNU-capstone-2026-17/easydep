# 클라우드 제약의 도출 — 자유 변수와 단계 배분

> **살아 있는 문서다. 계속 갱신한다.** 아카이브에 넣지 않는다.
>
> 상위 근거는 `docs/research.md`(과제 원문)와 `docs/cloud-native-extension.md`(현재 진실).
> 이 문서는 그 아래에서 **"사용자·설계자에게 무엇을 받아야 하는가"를 도출한 기록**이고,
> 새 근거가 나오면 표를 고친다.

>
> **표시 규율.** §3은 원본 인용이고 §4부터는 **우리 결정·추론이 섞인다.** 칸마다
> 어느 쪽인지 적는다 — 표시 없이 섞으면 우리 판단이 증거로 읽힌다.
---

## 0. 무엇을 하는 문서인가

우리는 기존 소프트웨어 개발 지원 시스템에 **클라우드 네이티브 요소를 더한다.** 그
요소는 단계마다 하나씩 붙는다.

```
요구사항 단계  + 클라우드 리소스 제약
설계 단계      + 배포 다이어그램 · 형상 결정 · 워크로드 경계
구현 단계      + 인프라 intent → IaC   (계약은 우리, 렌더러는 IaC 담당)
검증           + 요구사항 부합 판정
```

그래서 질문이 *"사용자에게 무엇을 물을까"*가 아니라 **"어느 단계의 확장이 이 값을
결정하는가"**다. 요구사항 단계에서 전부 받으려 하면 **사용자에게 설계 결정을 떠넘기게
되고**, 그건 과제가 말한 *"개발자의 의사결정 부담 감소"*와 정면으로 반대다.

## 1. 도출 규칙

> **결정해야 할 값 = 의존 술어와 요청 스키마의 자유 변수**
> **그중 계약 칸이 되는 것 = 플랫폼도 우리도 정할 수 없는 것**

자유 변수마다 셋으로 갈린다.

| | 뜻 | 처리 |
|---|---|---|
| **플랫폼이 정한다** | CSP 조건표·의존 폐포가 채운다 | 묻지 않는다 |
| **우리가 정한다** | KB에 근거가 있어 기본값을 댈 수 있다 | 묻지 않는다. **근거와 함께 제시하고 뒤집을 수 있게** |
| **아무도 못 정한다** | 어느 단계에선가 사람이 진술해야 한다 | **어느 단계인지**를 정한다 |

## 2. 근거 등급 — 이 표의 신뢰도

각 자유 변수에 등급을 붙인다. **등급 없이 적힌 값은 이 문서에 있으면 안 된다.**

| 등급 | 뜻 |
|---|---|
| **측정** | 파서가 소스에서 뽑았다. `file:line`으로 되짚을 수 있다 |
| **명시** | 소스가 목록·열거형으로 직접 적어 뒀다 |
| **추론** | 우리가 구조에서 끌어낸 것. **소스에 그 문장은 없다** |

## 3. 자유 변수 전수 — 원본 인용

**출처를 빠뜨리지 않는다.** 아래는 전부 아래 두 tarball(캐시에 있다)에서 직접 뽑았고,
`파일:줄`로 되짚을 수 있다. 요약이나 우리 분류가 아니라 **원문**이다.

```
cb-tumblebug  v0.12.25   tumblebug-src-v0.12.25.tar.gz
cb-spider     v0.12.37   cb-spider-v0.12.37.tar.gz
```

**형태**를 함께 적는 이유는, 같은 사실이라도 Go 구조체 태그로 있는 것과 YAML 데이터로
있는 것과 REST 라우트로만 있는 것의 **믿을 수 있는 정도가 다르기** 때문이다.

### 3.1 자원 요청 스키마 — Go 구조체 필드 + 태그

`validate:"required"`가 붙은 것이 **필수**다. 자원 참조(`vNetId` 등)는 폐포가 채우므로 뺐다.

| 자유 변수 | 출처 (`src/` 기준) | 원문 |
|---|---|---|
| `specId` **필수** | `core/model/infra.go:363`<br>(`CreateNodeGroupDynamicReq`, 350) | `SpecId string \`json:"specId" validate:"required" example:"aws+ap-northeast-2+t3.nano"\`` |
| `imageId` **필수** | `core/model/infra.go:365` | `ImageId string \`json:"imageId" validate:"required" example:"ami-01f71f215b23ba262"\`` |
| `nodeGroupSize` | `core/model/infra.go:355` | `NodeGroupSize int \`json:"nodeGroupSize" example:"3"\`` |
| `rootDiskType` | `core/model/infra.go:367` | `\`json:"rootDiskType,omitempty" example:"gp3" default:"default"\`` — 주석에 **CSP별 허용값**이 나열돼 있다 (AWS `standard/gp2/gp3`, Azure `PremiumSSD/StandardSSD/StandardHDD`, GCP `pd-*`, ALIBABA, TENCENT) |
| `rootDiskSize` | `core/model/infra.go:368` | `\`json:"rootDiskSize,omitempty" example:"50"\`` `// 0 = use CSP default` |
| `zone` | `core/model/infra.go:377` | `Zone string \`json:"zone,omitempty" example:"ap-northeast-2a" default:""\`` |
| `version` | `core/model/k8scluster.go:528`<br>(`K8sClusterDynamicReq`, 523) | `Version string \`json:"version,omitempty" example:"1.29"\`` |
| `onAutoScaling`·`desired/min/maxNodeSize` | `core/model/k8scluster.go:547–550` | `OnAutoScaling string \`… default:"true"\`` · `MinNodeSize int \`… example:"1"\`` · `MaxNodeSize int \`… example:"3"\`` |
| `cidrBlock` | `core/model/vnet.go:21` (`VNetReq`, 18) | `CidrBlock string \`json:"cidrBlock" example:"10.0.0.0/16"\`` |
| `ipv4_CIDR` **필수** | `core/model/subnet.go:20` (`SubnetReq`, 18) | `IPv4_CIDR string \`json:"ipv4_CIDR" validate:"required" example:"10.0.1.0/24"\`` |
| `diskSize` **필수** | `core/model/datadisk.go:89` (`DataDiskReq`, 85) | `DiskSize int \`json:"diskSize" validate:"required" example:"77"\` // Disk size in GB` |
| `diskType` | `core/model/datadisk.go:88` | `DiskType string \`json:"diskType" example:"default"\`` |
| NLB `type` **필수** | `core/model/nlb.go:206` (`NLBReq`, 197) | `Type string \`json:"type" validate:"required" enums:"PUBLIC,INTERNAL" example:"PUBLIC"\`` |
| NLB `scope` **필수** | `core/model/nlb.go:207` | `Scope string \`json:"scope" validate:"required" enums:"REGION,GLOBAL" example:"REGION"\`` |
| NLB `listener`·`targetGroup`·`healthChecker` **전부 필수** | `core/model/nlb.go:210·212·214` | `Listener NLBListenerReq \`json:"listener" validate:"required"\`` 외 둘 |
| VPN `site1`·`site2` **필수** | `core/model/vpn.go:127–128` (`RestPostVpnRequest`, 125) | `Site1 SiteProperty \`json:"site1" validate:"required"\`` |

**동적 경로의 필수는 `specId`·`imageId` 둘뿐이다** — 네트워크·SG·키는 자동 생성된다.
반면 **NLB는 다섯 칸이 전부 필수**이고, 외부 노출을 하려면 그만큼을 누군가 정해야 한다.

### 3.2 선택 가능한 제약의 전수 — Go 슬라이스 리터럴

`core/infra/recommendation.go`의 `RecommendSpecOptions()`가 **"자원 선택에 무엇을 받을 수
있는가"를 목록으로 적어 둔 것**이다. 우리 추론이 아니라 저쪽의 진술이다.

**필터** — `core/infra/recommendation.go:1189` `AvailableMetrics: []string{`

```
문자열   id · providerName · regionName · cspSpecName · architecture ·
         acceleratorModel · acceleratorType · description
수치     vCPU · memoryGiB · acceleratorCount · acceleratorMemoryGB ·
         costPerHour · evaluationScore01
```

**우선순위** — `core/infra/recommendation.go:1282`

```
cost · performance · location · latency · random
```

붙는 형식은 `core/model/infra.go`에 있다.

| | 출처 | 원문 |
|---|---|---|
| 비교 연산자 | `infra.go:1527` (`Operation`, 1526) | `Operator string \`… enums:">=,<=,=="\`` |
| 가중치 | `infra.go:1539` (`PriorityCondition`, 1537) | `Weight float64 \`json:"weight" example:"0.3"\`` |
| 위치 파라미터 | `infra.go:1545` (`ParameterKeyVal`, 1544) | `Key string \`… enums:"coordinateClose,coordinateWithin,coordinateFair"\`` · `Val []string \`… example:"44.146838/-116.411403"\`` — **위경도** |

**주석으로 꺼져 있는 것** (같은 리터럴 안, 1200–1215): `diskSizeGB` · `maxTotalStorageTiB` ·
`netBwGbps` · `evaluationScore02~08`.

> **교차 확인.** 우리가 미러를 전수로 재면서 `net_bw_gbps`·`max_total_storage_ti_b`가
> **전부 0**임을 독립적으로 관측했다(2026-07-29 인벤토리). 저쪽이 그 지표를 주석 처리해
> 둔 것과 일치한다 — **데이터가 없어서 끈 것이다.** 다른 두 관측이 같은 결론에 닿았다.

여기서 **우리 계약에 없는 것 둘**이 드러난다.

- **가속기(GPU) 축이 통째로 없다** — `acceleratorModel`·`Type`·`Count`·`MemoryGB`.
- **필터와 우선순위의 구분이 없다.** `monthlyBudgetUSD`가 우리에겐 사후 판정용인데 저쪽
  어휘로는 `costPerHour <=` **필터**다.

### 3.3 프로바이더별로 정해진 것 — YAML 데이터

코드가 아니라 **데이터로** 빠져 있다. 프로젝트가 *"CSP마다 다르다"*를 알고 표로 뺀
자리이므로, 조건과 개수를 우리가 추정할 필요가 없다.

| 사실 | 출처 | 원문 |
|---|---|---|
| k8s 서브넷 최소 개수 | `assets/k8sclusterinfo.yaml:22·50·86` | `requiredSubnetCount: 2` (aws) / `1` (azure·gcp) — 헤더 주석 9행: *"required number of subnets to create a kubernetes cluster, default value is 1"* |
| azure VPN 게이트웨이 서브넷 | `assets/networkinfo.yaml:138–141` | `gateway-subnet:` / `required: true` / `name: GatewaySubnet` / *"GatewaySubnet is required for deploying Azure VPN Gateway … Deploying other resources into this subnet is not supported"* |
| vNet·subnet prefix 범위, 서브넷 예약 IP | `assets/networkinfo.yaml` (CSP 절마다) | `prefix-length: min/max` · `reserved-ips: value` |

### 3.4 코드에도 데이터에도 없고 **경로로만 있는 것**

**형상(VM/컨테이너)** — 필드가 아니다. 라우트 등록이 갈라져 있을 뿐이다.

```
src/interface/rest/server/server.go:413   g.POST("/:nsId/infraDynamic",      …)
src/interface/rest/server/server.go:587   g.POST("/:nsId/k8sClusterDynamic", …)
```

**cb-tumblebug은 이 결정을 받지 않는다 — 이미 정해져 왔다고 전제한다.** 그러니 이 값은
우리가 물려받을 칸이 없고, **어느 단계에서 누가 정할지를 우리가 정의해야** 한다.

### 3.5 출처가 우리 쪽인 것 — 낮은 등급으로 표시한다

아래는 cb-tumblebug·cb-spider의 진술이 아니라 **우리 저장소의 문장**이거나 **우리
추론**이다. 같은 표에 섞으면 근거가 부풀려진다.

| 항목 | 무엇에서 끌어냈나 | 약한 지점 |
|---|---|---|
| **형상(VM/컨테이너)** | `POST /infraDynamic`과 `POST /k8sClusterDynamic`으로 **엔드포인트가 갈린다**(`server.go:413·587`) | cb-tumblebug은 이걸 **필드로 두지 않았다.** 사용자가 이미 정하고 온다고 전제한다 |
| **외부 노출 필요** | `NLBReq.Type`이 `PUBLIC/INTERNAL` 필수(`nlb.go:206`) | *"유스케이스 액터에서 유도된다"*는 **우리 시스템에 대한 우리 추론**이다 |
| **영속 저장 필요** | `DataDiskReq.diskSize` 필수(`datadisk.go:89`) | ER 엔티티 → 영속 사슬은 `appkb/__init__.py:13`에 **우리가 적은 것**이다 |
| **지연 요구** | `latency`가 우선순위 지표인 것까지가 명시(`recommendation.go:1282`) | *"사용자에게 지연 요구를 받는다"*는 추론 |
| **OS·런타임은 정해져 있다** | 구현 에이전트가 **Spring 애플리케이션**을 생성한다(`docs/implementation-agent.md`) | 스택이 바뀌면 무너진다 |

## 4. 단계 배분 — **여기부터는 우리 결정이다**

앞 절(§3)은 원본 인용이고 **이 절은 아니다.** 자유 변수를 어느 단계가 결정할지는
소스가 말해 주지 않는다. 그래서 칸마다 **근거인지 우리 결정인지**를 갈라 적는다.
섞어 적으면 우리 판단이 증거처럼 읽힌다 — 실제로 이전 판이 그랬다.

| 결정 대상 | 결정 단계 | 무엇에 기대나 |
|---|---|---|
| `providerName` · 위치 요구 · 비용 상한 · 데이터 소재 · `architecture` · 가속기 | 요구사항 | **근거** — 추천기가 받는 필터 지표다(§3.2) |
| 서브넷 개수 · 게이트웨이 서브넷 · prefix 범위 | 플랫폼 | **근거** — 자산 YAML에 값이 있다(§3.3) |
| 자원 참조 관계 | 플랫폼 | **근거** — 요청 스키마의 필수 참조(§3.1) |
| 스펙 하한 · 디스크 용량 · 대수 | 설계 | **근거 + 결정** — 계약에 `ORIGIN_DESIGNER`가 실재하는 것은 사실. *그 칸들이 거기로 가야 한다*는 것은 **우리 결정** |
| 형상(컨테이너/VM) | 설계 | **근거 + 결정** — 하류가 k8s 매니페스트만 낸다는 것은 문서로 확인한 사실. *설계 단계가 정한다*는 것은 **우리 결정** |
| 외부 노출 · 영속 · 외부 연결 필요 | 요구사항 | **우리 추론** — 유스케이스·ER에서 유도된다는 사슬은 §3.5의 낮은 등급이다 |
| 워크로드 경계 · 노출 지점 · 포트 | 설계 | **우리 결정** — 배포 다이어그램이 담기로 한 것이지 소스가 말한 바 없다 |
| CIDR · zone 분산 · k8s 버전 · 디스크 타입 · 선호 가중치 | 우리(KB) | **우리 결정** — 기본값을 댈 재료는 있으나(§3.3, `rootDiskType` 주석의 CSP별 허용값) *우리가 정한다*는 것은 판단이다 |

**가중치를 묻지 않는 것은 의도된 판단이다.** 추천기는 `weight` 실수를 받지만, 그걸
사용자에게 물으면 의사결정 부담이 늘어난다. 근거와 함께 기본을 제시하고 뒤집게 한다.

## 5. 실행 계획 — `RESOURCE_SPEC` 개편

전체 계획은 `docs/cloud-native-extension.md` §10이고 **여기는 그 1단계**다.
§3의 원본 인용이 그대로 근거이므로 **새 조사가 필요 없다.**

### 5.1 무엇을 바꾸나

| | 칸 | 근거 |
|---|---|---|
| **뺀다** | `expectedConcurrentUsers` · `approxRequestsPerSecond` | 어느 자유 변수도 아니고(§3) 추천기 지표에도 없다(§3.2). 이미 필수에서 내려와 있었고 **독립된 두 절차가 같은 답을 냈다** |
| **재정의** | `stateless` | 유일한 소비자였던 서버리스 적합 판정이 범위 밖으로 나갔다. *"영속 저장이 필요한가"*로 바꾸면 `dataDisk` 등장 결정이라는 새 소비자를 얻는다 |
| **형태 변경** | `monthlyBudgetUSD` → **필터**(`costPerHour <=`) · `region` → 위치 제약 · `multiZone` → 배치 술어 | §3.2의 필터/우선순위 구분과 `coordinateClose/Within/Fair` |
| **신설** | 형상 · 외부 노출+포트 · 외부 연결 · `architecture` · **가속기** · 지연 요구 · 대수 | §3.1·§3.2·§3.4 |
| **유지** | `providerName` · `minVCpu` · `minMemoryGiB` · `dataResidency` | §3.2 필터 지표 |

### 5.2 순서와 멈추는 조건

```
5.2.1  빼기·재정의       소비자 없는 칸을 지우고 stateless를 다시 정의한다
5.2.2  형태 변경         예산을 필터로, 위치를 제약으로
5.2.3  신설 — 형상 먼저   나머지 여섯이 형상에 매달린다(§3.4)
5.2.4  되묻기 문구 갱신   각 칸의 "왜"가 곧 질문이다
```

**5.2.3에서 형상이 먼저인 이유**: 형상이 안 정해지면 필터가 노드에 붙을지 노드그룹에
붙을지도 안 정해지고, 폐포가 앵커를 못 받는다.

**멈추는 조건**: 칸마다 **판정 하나와 1:1로 묶이지 않으면 열지 않는다.** 이건 계약이
스스로 적어 둔 판정식이고, 필수 칸마다 *"지우면 무너지는 판정"* 테스트가 이미 있다
(`test_required_fields.py`) — 신설 칸도 같은 검사를 통과해야 한다.

### 5.3 건드리는 곳

`app/core/cloud_contract.py`(공용 투영) · `app/core/cloudkb/appkb/{contract,request,schema}` ·
`app/requirements/agent/steps/step_resource.py`(되묻기) · `app/requirements/prompts.py` ·
그리고 각각의 테스트.

> **위험**: 계약을 넓히면 되묻기가 늘어 *"의사결정 부담 감소"*와 부딪힌다. 완화책은
> §4의 단계 배분이다 — **요구사항 단계에서 받는 것은 클라우드 제약으로 직접 오는 것뿐**이고
> 스펙 하한·디스크 용량·대수는 설계 단계(`ORIGIN_DESIGNER`)로 내린다.

---

## 6. 미결

1. **5.2를 실행하지 않았다.** 도출은 §3에 있고 코드는 그대로다.
2. 가속기 축을 열 것인가 — 추천기는 받지만 우리 KB에 가속기 판정이 없다. **소비자를
   지목할 수 있어야 연다.**
3. 필터/우선순위 형태를 계약이 받을 것인가 — 받으면 가이드라인이 강해지고 계약이
   복잡해진다.
4. §3.5의 추론 등급 다섯을 어떻게 올릴 것인가.

---

## 부록 — 검증에서 드러난 우리 오류

규율은 `CLAUDE.md` 문서 정책 5번에 있다. 여기는 이 문서에서 실제로 난 것만 남긴다.

| 오류 | 무엇이었나 |
|---|---|
| **가속기 축 누락** | 요청 스키마만 보고 `RecommendSpecOptions`를 안 봤다 |
| **근거를 잘못 댐** | 아키텍처를 `Operand`의 **주석 예시**로 인용했다. 진짜 근거는 `AvailableMetrics` 목록 |
| **단계 사고** | *"설계 산출물이 우리보다 먼저 나온다"* — 우리를 한 단계에 세우고 남이 값을 준다고 적었다 |
| **분류로 근거를 갈음** | `D1~D9`는 우리 분류이지 근거가 아니다 |
| **전수가 아닌데 전수라고 적음** | 후보를 언급 횟수로 잘랐다. 실제 저장소는 59개였다 |
