# 우리가 다루는 클라우드 자원 — 목록과 필드 전수

> **보류 문서:** K8s·VPN·관리형 서비스가 포함될 수 있다. AWS·Azure·GCP의 Docker-on-VM 카탈로그로 재검증하기 전에는 활성 범위로 해석하지 않는다.

> **살아 있는 문서다.** 새 근거가 나오면 본문을 고친다. 아카이브에 넣지 않는다.
>
> 상위: `docs/cloud-native-extension.md`(현재 진실). 범위 판정의 **집은 코드**다 —
> `graphkb/scope.py`가 자원마다 역할·사유·뒤집힐 조건을 들고 있고 `test_scope.py`가
> **판정 없는 자원이 있으면 실패**시킨다. 이 문서는 그 판정에 **필드를 붙인 것**이다.
>
> 모든 필드는 `cb-tumblebug v0.12.25` 소스에서 뽑았고 `파일:줄`로 되짚을 수 있다.
> `!`는 `validate:"required"`.
>
> **§1.5의 비유는 우리가 만든 것이다.** 읽기를 돕는 장치이고 근거가 아니다 — 비유가
> 사실과 어긋나면 사실이 이긴다.

---

## 1. 범위 — 14 안 / 7 밖

조사한 자원 21종에 대한 판정이다(`scope.py`).

| 역할 | 자원 | 뜻 |
|---|---|---|
| **구성** 12 | `vNet` · `subnet` · `securityGroup` · `sshKey` · `infra` · `node` · `nodeGroup` · `k8sCluster`\* · `k8sNodeGroup`\* · `nlb`\* · `dataDisk`\* · `vpn`\* | IaC가 만드는 자원 · 다이어그램의 노드 |
| **선택** 2 | `spec` · `image` | 다른 자원의 **속성**으로 들어간다. 만드는 것이 아니라 고르는 것 |
| **밖** 7 | `sqlDb` · `objectStorage` · `customImage` · `publicIp` · `vNic` · `globalDns` · `fileSystem` | §4 |

\* **조건부** — 구성에 따라 등장한다(컨테이너 배포 · 외부 노출 · 영속 · 외부 연결).

---

## 1.5 각 자원이 무엇인가

**"뜻"은 소스에서 인용한 것이고 "비유"는 우리가 만든 것이다.** 비유는 읽기를 돕는 장치이지
근거가 아니다 — 비유가 사실과 어긋나면 사실이 이긴다.

전체를 한 그림으로 두면 이렇다. **아파트 단지 한 채를 짓는 일**에 견주었다(우리 비유).

```
infra            단지 전체                — 여러 클라우드에 걸친 것을 하나로 묶어 관리
 └ nodeGroup     같은 평형의 동(棟)         — 스펙·이미지가 똑같은 것들의 묶음
    └ node       집 한 채                 — 실제 서버 한 대
       spec      평형·구조 도면            — 몇 평, 방 몇 개 (고르는 것)
       image     인테리어 시공 패키지       — 어떤 OS가 깔려 나오나 (고르는 것)
       dataDisk  창고 한 칸                — 필요하면 붙이는 저장 공간

vNet             단지의 사유 도로망         — 이 단지만 쓰는 주소 체계
 └ subnet        구역(1구역·2구역)          — 도로망을 쪼갠 것. 동(棟)이 어느 구역에 서나
securityGroup    경비실 출입 규칙           — 어느 문으로 누가 들어올 수 있나
sshKey           현관 열쇠                 — 관리자가 집에 들어가는 수단
nlb              단지 정문 안내데스크        — 밖에서 온 요청을 어느 집으로 보낼지
vpn              옆 단지와 잇는 전용 통로    — 사내망·다른 클라우드와 연결
k8sCluster       단지와 평행한 다른 단지     — 컨테이너용. 안이 또 nodeGroup → node다
```

### 자원별

| 자원 | 뜻 (인용·관측) | 비유 (우리 것) |
|---|---|---|
| **`infra`** | *"a logical unit that **groups multiple compute Nodes deployed across different Cloud Service Providers** into a single manageable entity"*(자원 모델 문서) | **단지 전체.** AWS에 있는 집과 Azure에 있는 집을 한 단지로 묶어 관리한다 |
| **`nodeGroup`** | *"a logical grouping of **homogeneous** Nodes … share identical configurations (same spec, image, region) and are typically **scaled together**"* | **같은 평형의 동.** 몇 채를 더 지을지는 동 단위로 결정한다 — **대수가 붙는 자리** |
| **`node`** | *"a **server instance** that a cloud or infrastructure provider can allocate and manage for you"* — VM·베어메탈을 포함하도록 **일부러 넓게** 잡은 이름 | **집 한 채.** 여기서 앱이 돈다 |
| **`spec`** | *"the detailed information of a **VM specification**"*(cb-spider) — vCPU·메모리·단가·아키텍처 | **평형 도면.** 만드는 게 아니라 카탈로그에서 **고르는** 것 |
| **`image`** | OS 종류·배포판·아키텍처·리전 | **인테리어 패키지.** 집이 어떤 상태로 넘어오나 |
| **`vNet`** | *"a struct to handle 'Create vNet' request"*. 실체는 사설 IP 주소 공간(CIDR) | **단지의 사유 도로망.** 밖과 분리된 주소 체계다 |
| **`subnet`** | vNet을 쪼갠 대역. `zone`을 갖는다 | **구역.** 어느 구역에 두느냐가 **가용성**을 만든다 — 같은 구역에 다 두면 그 구역이 죽을 때 같이 죽는다 |
| **`securityGroup`** | 방화벽 규칙 묶음. 규칙마다 프로토콜·방향·포트·CIDR | **경비실 출입 규칙.** 집을 지어도 이게 안 열리면 아무도 못 들어온다 |
| **`sshKey`** | *"information about a **KeyPair**"*(cb-spider) | **현관 열쇠.** 만들 때 같이 발급되고, 잃으면 들어갈 수 없다 |
| **`dataDisk`** | *"the information of a **Disk** resource"*(cb-spider). `diskSize`가 필수 | **창고 한 칸.** 집에 붙였다 뗐다 할 수 있고, 집을 없애도 창고는 남는다 |
| **`nlb`** | *"the details of a **Network Load Balancer**"*(cb-spider) | **정문 안내데스크.** 밖에서 온 요청을 어느 집으로 보낼지 정하고, 죽은 집으로는 안 보낸다(헬스체크) |
| **`vpn`** | 두 `site`를 잇는다. site마다 `vNetId` + CSP별 속성 | **옆 단지와의 전용 통로.** 공용 도로(인터넷)를 안 거치고 잇는다 |
| **`k8sCluster`** | *"the details of a **Kubernetes Cluster**"*(cb-spider). 자원 모델 문서가 `infra`와 **평행한 최상위 추상**이라 적는다 | **평행한 다른 단지.** 컨테이너용이고, 안이 또 `k8sNodeGroup → 워커 노드`로 같은 3층이다 |
| **`k8sNodeGroup`** | 클러스터 안의 동질 노드 묶음. 오토스케일 범위를 갖는다 | **그 단지의 동.** 여기 `spec`·`image`가 붙어 **비용·성능 판정이 걸린다** |

### 왜 이 구분이 실무에서 값을 하나

- **`node`와 `nodeGroup`을 가르는 이유**: 대수는 집의 성질이 아니라 **동의 성질**이다.
  *"서버를 3대"*는 `nodeGroupSize: 3`이고, 집 하나에 3을 적을 자리는 없다.
- **`spec`·`image`가 "만드는 것"이 아닌 이유**: 도면과 인테리어 패키지는 **고르는** 것이라
  지워도 이미 지은 집이 안 무너진다. 그래서 참조 카운트를 걸지 않는다
  (`core/infra/control.go:1302`).
- **`securityGroup`이 따로 있는 이유**: 집을 다 지어도 **경비실 규칙이 없으면 아무도 못
  들어온다.** 존재와 작동이 다른 문제라는 것이 이 자원 하나에 드러난다.
- **`subnet`의 `zone`이 중요한 이유**: aws에서 관리형 DB나 k8s를 쓰려면 **서로 다른 구역의
  subnet 둘**을 요구한다(`assets/k8sclusterinfo.yaml:22` · `sqlDb.go:39`). 구역을 안 갈라
  두면 계획이 통과해도 만들어지지 않는다.

---

## 2. 구성 자원의 필드 전수

### 2.1 `vNet` — `core/model/vnet.go:18` `VNetReq`

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| **!** | `name` | string | 19 |
| **!** | `connectionName` | string | 20 |
| | `cidrBlock` | string | 21 |
| | `subnetInfoList` | `[]SubnetReq` | 22 |
| | `description` | string | 23 |

`cidrBlock`이 **필수가 아니다** — 안 주면 기질이 정한다. 프로바이더별 허용 범위는
`assets/networkinfo.yaml`의 `prefix-length min/max`에 있다.

### 2.2 `subnet` — `core/model/subnet.go:18` `SubnetReq`

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| **!** | `name` | string | 19 |
| **!** | `ipv4_CIDR` | string | 20 |
| | `zone` | string | 21 |
| | `description` | string | 22 |

`zone`이 **배치 술어가 걸리는 유일한 자리**다(aws k8s의 "서로 다른 AZ 둘").

### 2.3 `securityGroup` — `core/model/securitygroup.go:66` `SecurityGroupReq`

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| **!** | `name` · `connectionName` | string | 67 · 68 |
| | `vNetId` | string | 69 |
| | `firewallRules` | `*[]FirewallRuleReq` | 71 |
| | `description` · `cspResourceId` | string | 70 · 74 |

**`vNetId`에 `required`가 없다.** 주석: *"some CSPs (e.g., Azure, Tencent, NHN) don't
bind SG to VPC"* — CSP 조건부의 대표 사례다(cb-spider `SecurityInfo.VpcIID`는 필수).

**`FirewallRuleReq`** (`:78`)

| | 필드 | 타입 | 줄 | 값 |
|---|---|---|---:|---|
| | `Ports` | string | 83 | `"22,900-1000,2000-3000"` 꼴 |
| **!** | `Protocol` | string | 85 | `TCP,UDP,ICMP` |
| **!** | `Direction` | string | 87 | `inbound,outbound` |
| | `CIDR` | string | 89 | |

### 2.4 `sshKey` — `core/model/sshkey.go:39` `SshKeyReq`

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| **!** | `name` · `connectionName` | string | 40 · 41 |
| | `description` | string | 42 |
| | `cspResourceId` · `fingerprint` · `username` · `verifiedUsername` · `publicKey` · `privateKey` | string | 46–51 |

뒤의 여섯은 **기존 키를 등록하는 경로**(`option=register`)의 칸이다.

### 2.5 `infra` — `core/model/infra.go`

**명시 생성 `InfraReq`(:116)**

| | 필드 | 타입 | 줄 | 값 |
|---|---|---|---:|---|
| **!** | `name` | string | 117 | |
| **!** | `nodeGroups` | `[]CreateNodeGroupReq` | 131 | |
| | `installMonAgent` | string | 120 | `yes,no` (기본 `no`) |
| | `policyOnPartialFailure` | string | 140 | `continue,rollback,refine` (기본 `continue`) |
| | `label` · `systemLabel` · `placementAlgo` · `description` · `postCommand` | | 123–134 | |

**동적 생성 `InfraDynamicReq`(:285)** — 위와 같되 `nodeGroups`가
`[]CreateNodeGroupDynamicReq`이고 **`vNetTemplateId`(:341) · `sgTemplateId`(:346)**가 더 있다.

### 2.6 `node` · `nodeGroup` — `core/model/infra.go`

**명시 생성 `CreateNodeGroupReq`(:239)**

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| **!** | `name` · `connectionName` | string | 242 · 255 |
| **!** | `specId` · `imageId` | string | 256 · 258 |
| **!** | `vNetId` · `subnetId` · `sshKeyId` | string | 265 · 266 · 268 |
| **!** | `securityGroupIds` | `[]string` | 267 |
| | `nodeGroupSize` | int | 248 |
| | `rootDiskType` · `rootDiskSize` | string · int | 271 · 272 |
| | `dataDiskIds` | `[]string` | 273 |
| | `nodeUserName` · `nodeUserPassword` · `cspImageName` · `cspResourceId` · `label` · `description` | | 245–270 |

**동적 생성 `CreateNodeGroupDynamicReq`(:350)** — 필수가 **`specId`·`imageId` 둘뿐**이다.
네트워크·SG·키는 자동 생성된다. 추가 칸: `zone`(:377) · `vNetTemplateId`(:381) ·
`sgTemplateId`(:385).

> **같은 자원, 다른 요구.** 명시 경로는 참조 여섯이 필수이고 동적 경로는 둘이다.
> 이것이 *"의존은 (자원쌍 × 생성경로 × CSP)의 함수"*의 근거다.

`rootDiskType`의 주석에 **CSP별 허용값**이 나열돼 있다 — AWS `standard/gp2/gp3` ·
Azure `PremiumSSD/StandardSSD/StandardHDD` · GCP `pd-standard/pd-balanced/pd-ssd/pd-extreme` ·
ALIBABA · TENCENT. 인용하면 그대로 쓸 수 있는 값이다.

### 2.7 `k8sCluster` — `core/model/k8scluster.go`

**명시 생성 `K8sClusterReq`(:79)**

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| **!** | `connectionName` · `name` | string | 81 · 85 |
| **!** | `vNetId` | string | 89 |
| **!** | `subnetIds` | `[]string` | 90 |
| **!** | `securityGroupIds` | `[]string` | 91 |
| | `version` | string | 86 |
| | `k8sNodeGroupList` | `[]K8sNodeGroupReq` | 94 |
| | `label` · `systemLabel` · `description` · `cspResourceId` | | 82–104 |

**동적 생성 `K8sClusterDynamicReq`(:523)** — `specId`·`imageId`가 필수이고,
`onAutoScaling`(기본 `true`) · `desiredNodeSize` · `minNodeSize` · `maxNodeSize`(:547–550)가
더 있다.

**서브넷 개수가 CSP마다 다르다** — `assets/k8sclusterinfo.yaml`의 `requiredSubnetCount`:
**aws 2** · azure 1 · gcp 1. 그리고 `nodeGroupsOnCreation`이 *"클러스터와 함께 만들어야
하는가"*를 CSP별로 적는다.

### 2.8 `k8sNodeGroup` — `core/model/k8scluster.go`

**명시 생성 `K8sNodeGroupReq`(:133)** — **필수 필드가 하나도 없다.**

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| | `name` · `imageId` · `specId` · `sshKeyId` | string | 134 · 135 · 136 · 139 |
| | `rootDiskType` · `rootDiskSize` | string · int | 137 · 138 |
| | `onAutoScaling` · `desiredNodeSize` · `minNodeSize` · `maxNodeSize` | string · int | 142–145 |
| | `label` · `description` | | 148 · 150 |

**동적 생성 `K8sNodeGroupDynamicReq`(:558)** — `name`(560) · `specId`(568) ·
`imageId`(571)가 **필수**이고 `onAutoScaling` 기본값이 `true`다.

> 명시 경로는 필수가 0이고 동적 경로는 3이다. **동적 경로가 오히려 더 요구한다** —
> 추천기가 고를 자리를 사용자가 정해 줘야 하기 때문이다.

### 2.9 `nlb` — `core/model/nlb.go:197` `NLBReq`

| | 필드 | 타입 | 줄 | 값 |
|---|---|---|---:|---|
| **!** | `type` | string | 206 | `PUBLIC,INTERNAL` |
| **!** | `scope` | string | 207 | `REGION,GLOBAL` |
| **!** | `listener` | `NLBListenerReq` | 210 | `protocol` · `port` (:114–115) |
| **!** | `targetGroup` | `NLBTargetGroupReq` | 212 | `protocol` · `port` · `nodeGroupId` (:181–183) |
| **!** | `healthChecker` | `NLBHealthCheckerReq` | 214 | `interval` · `timeout` · `threshold` (:62–64) |
| | `description` · `cspResourceId` | string | 202 · 204 | |

**다섯이 전부 필수다.** 외부 노출을 하려면 프로토콜·포트·헬스체크를 누군가 정해야 한다.
`NLBReq`에 `vNetId`가 **주석 처리돼 있다** — 경로가 `/infra/{infraId}/nlb`라 부모에서 온다.

### 2.10 `dataDisk` — `core/model/datadisk.go:85` `DataDiskReq`

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| **!** | `name` · `connectionName` | string | 86 · 87 |
| **!** | `diskSize` | int (GB) | 89 |
| | `diskType` | string | 88 |
| | `description` · `cspResourceId` | string | 90 · 94 |

### 2.11 `vpn` — `core/model/vpn.go:125` `RestPostVpnRequest`

| | 필드 | 타입 | 줄 |
|---|---|---|---:|
| **!** | `name` | string | 126 |
| **!** | `site1` · `site2` | `SiteProperty` | 127 · 128 |

**`SiteProperty`(:71)** — `vNetId`(72) · `cspSpecificProperty`(73). 후자가 CSP별로 갈리고,
**azure만 `gatewaySubnetCidr`를 갖는다**(`AzureSpecificProperty:91`). 그래서
`assets/networkinfo.yaml`의 azure 절에 `gateway-subnet: required: true`(min /27)가 있다.

---

## 3. 선택 자원 — 만드는 것이 아니라 고르는 것

`spec`·`image`는 **다른 자원의 속성**으로 들어간다. 근거 둘 — (가)
`core/infra/control.go:1302`에서 노드 삭제가 다른 자원에는 참조 카운트를 되돌리는데
`spec`만 주석으로 꺼져 있다 (나) TOSCA에서 `num_cpus`·`mem_size`는 관계가 아니라
`host` **capability 속성**이다.

### 3.1 우리가 들고 있는 카탈로그 필드

| 출처 | 필드 |
|---|---|
| `costkb` (스펙 73,083) | `provider` · `region` · `specName` · `vCPU` · `memGiB` · `hourlyUSD` · `architecture` · `acceleratorCount` · `acceleratorMemoryGB` · `infraType` · `id` |
| `perfkb` (스펙 65,032) | `cpuModel` · `cpuVendor` · `cpuCores` · `cpuThreads` · `threadsPerCore` · `clockGHz` · `cpuClockMHz` · `cpuCacheKB` · `networkPerformance` · `networkIsBurst` · `maxNics` · `localSsdGB` · `ebsBaselineIops/Mbps` · `ebsMaxIops/Mbps` · `bareMetal` · `sustainedCpu` · `currentGeneration` · `hardwareEvidence` · `hardwareCheckedAt` |
| `envkb` (이미지) | `imageId` · `provider` · `regions` · `osType` · `osDistribution` · `osArchitecture` · `kinds` |

`costkb`의 `infraType`이 **73,083건 전부 `node`**다 — 인스턴스 축 전용 카탈로그다.

### 3.2 기질이 받는 선택 조건

`core/infra/recommendation.go`의 `RecommendSpecOptions()`가 **"자원 선택에 무엇을 받을 수
있는가"**를 목록으로 적어 뒀다.

```
필터(:1189)    id · providerName · regionName · cspSpecName · architecture ·
               acceleratorModel · acceleratorType · description
               vCPU · memoryGiB · acceleratorCount · acceleratorMemoryGB ·
               costPerHour · evaluationScore01
               연산자 >= <= ==

우선순위(:1282) cost · performance · location · latency · random   (+ weight 실수)
               location 파라미터 coordinateClose / Within / Fair (위경도)
```

**주석으로 꺼져 있는 것**: `diskSizeGB` · `maxTotalStorageTiB` · `netBwGbps` ·
`evaluationScore02~08`. 우리가 미러에서 독립적으로 잰 결과 `net_bw_gbps`·
`max_total_storage_ti_b`가 **전부 0**이었다 — 데이터가 없어서 끈 것이다.

---

## 4. 범위 밖 7 — 사유와 뒤집힐 조건

`scope.py`가 코드로 들고 있고 `test_scope.py`가 **사유 없는 판정을 막는다.**

| 자원 | 사유 | 뒤집힐 조건 |
|---|---|---|
| `sqlDb` | 미러 스펙 138,115건 중 `db.*` 클래스 **0건** — 비용·성능을 못 재고 제약만 남아 판정 1:1이 안 선다 | DB 인스턴스 클래스 카탈로그를 인용 가능한 형태로 확보하면 |
| `objectStorage` | **구조적** — 버킷은 고를 스펙이 없다. 데이터 부재가 아니라 선택지가 존재하지 않는다 | **없음(재검토 대상 아님)** |
| `customImage` | 빌드 산출물이지 배포 대상이 아니다 | — |
| `publicIp` · `vNic` | 대개 노드 생성에 암묵적으로 딸려온다 | — |
| `globalDns` | 지금 하류(deployment intent 19칸)에 DNS 필드가 없다 | **과적합 위험 구간** — DNS 소비자가 생기면 즉시 재검토 |
| `fileSystem` | cb-spider에는 있으나 cb-tumblebug이 노출하지 않는다 | 노출하면 |

### 4.1 그것들이 무엇인가 (비유는 우리 것)

| 자원 | 뜻 | 비유 | 왜 빠졌나 |
|---|---|---|---|
| `sqlDb` | 관리형 관계형 DB. CSP가 운영까지 대신한다 | **입주 청소·관리까지 맡기는 서비스** | 고를 수 있는 "평형"(DB 인스턴스 클래스) 카탈로그가 우리에게 **0건**이다 |
| `objectStorage` | 버킷. 파일을 키로 넣고 꺼낸다 | **무한히 늘어나는 사물함** | **고를 평형이 아예 없다** — 크기를 정하지 않는다. 구조적 제외 |
| `customImage` | 돌고 있는 노드를 떠서 만든 이미지 | **지금 사는 집을 그대로 찍어 둔 시공 도면** | 빌드 산출물이지 배포 대상이 아니다 |
| `publicIp` | 공인 IP 주소 하나 | **단지에 배정되는 도로명 주소** | 대개 집 지을 때 딸려온다 |
| `vNic` | 가상 네트워크 인터페이스 | **집에 들어가는 인터넷 회선 하나** | 같음 — 암묵적으로 붙는다 |
| `globalDns` | 도메인 이름 레코드(Route53 기반) | **주소를 이름으로 바꿔 주는 등기소** | 지금 하류가 DNS를 안 다룬다. **과적합 위험 구간** |
| `fileSystem` | 여러 노드가 함께 붙는 파일 시스템 | **여러 집이 같이 쓰는 공동 창고** | cb-tumblebug이 노출하지 않아 **배포 경로가 없다** |

`sqlDb`와 `objectStorage`의 제외 사유가 **다르다는 것**이 중요하다 — 전자는 데이터 부재라
조건이 바뀌면 돌아올 수 있고, 후자는 구조적이라 아니다.

---

## 5. 이 문서의 한계

- **필드는 `v0.12.25` 기준이다.** 태그가 바뀌면 표가 낡는다. 자원 목록과 판정은
  `scope.py`·`tumblebug_resources.json`이 진실이고 **여기는 읽기 쉽게 편 것**이다.
- 필드의 **뜻**은 대부분 주석에서 왔고, 주석이 없는 칸은 이름만 적었다. 추측해서 채우지
  않았다.
