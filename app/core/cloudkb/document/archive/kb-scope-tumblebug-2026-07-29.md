# 지식베이스 범위 재정의 — 경계를 cb-tumblebug으로 (2026-07-29)

> **과거 범위:** 현재 범위는 AWS·Azure·GCP의 Docker-on-VM이며 cb-tumblebug을 제품 경계로 사용하지 않는다.

> **이력이다. 참조하지 않는다.**
>
> 현재 진실은 [`docs/cloud-native-extension.md`](cloud-native-extension.md). 이 문서는 그때의 판단을 남긴 기록이고,
> 전제가 바뀐 자리가 있다. **여기 적힌 결정·계획을 근거로 새 작업을 시작하지 말 것.**
> 이 안의 **실측치는 유효하다** — 다시 재지 말고 인용한다.

**실행 전 계획입니다.** 이 문서 시점에 코드·산출물은 바뀌지 않았습니다. 실행 뒤에도
본문은 고치지 않습니다 — 계획과 결과를 섞으면 무엇을 예상했는지가 사라집니다.

---

## 0. 결정 기록 (2026-07-29, 사용자 승인)

| # | 결정 | 사유 |
|---|---|---|
| **S1** | 범위를 **폭과 깊이 둘 다** 자른다 | 하나만 자르면 죽은 무게나 못 지킬 약속 중 하나가 남는다 |
| **S2** | 경계는 **cb-tumblebug**이 만들 수 있는 자원이다 | 배포 기질이 못 만드는 것을 계획이 지목하면 **그건 처음부터 환각**이다 |
| **S3** | 경계의 판정 근거는 **swagger 전수**(142 paths) — 우리 파서가 지금 뽑는 것이 아니라 | 파서의 한계를 경계로 착각하면 범위가 우연히 정해진다 |
| **S4** | 프로바이더는 **aws · azure · gcp** 셋 | 나머지 일곱은 코어↔벤더 매핑 근거가 한 단계 얕고, 계획이 지목하지 않는다 |
| **S5** | 경계 밖은 **파서까지 지운다** | 데이터만 지우면 다음 빌드가 되살린다 |
| **S6** | **관리형 가격은 통째로 지운다** | AWS는 약관상 재배포 금지라 커밋도 못 하고, Azure는 차원이 곱으로 늘어난다 |
| **S7** *(추가)* | **CSP 특화 데이터는 기본 삭제 · 순서는 맨 뒤 · 부족분만 제한적으로** | 기준은 cb-tumblebug의 **벤더 중립 코어**다. 프로바이더별 상세를 담기 시작하면 코어 모델이 CSP 카탈로그가 된다 — 범위를 좁힌 이유 그 자체다. 남기는 것은 **빼면 간선이 거짓이 되는 조건**뿐 |

---

## 1. 문제 — 넓은 축이 "관리형"이 아니었다

범위가 넓다는 진단에서 출발했고, 처음 세운 가설은 *"관리형 서비스까지 하니 감당이
안 된다"*였습니다. **실측이 그 가설을 기각했습니다.**

| | |
|---|---:|
| graphkb가 들고 있는 벤더 타입 | **9,796종** |
| capacitykb 제약·쿼터 | **135,745건** / 9,409종 |
| 우리 산출물이 이름 부를 수 있는 타입 | **68종** (앱 개념 13 → 68) |
| 의존을 **끝까지** 따라가도 | **158종** |
| capacitykb 중 닿는 것 | 155종(1.6%) · 제약 **4,204건(3.1%)** |

`aws-capacity`의 상위가 무엇인지가 결정적입니다.

```
QuickSight 6,047 · SageMaker 2,470 · EC2 1,529 · Bedrock 1,362 ·
BedrockAgentCore 1,212 · Connect 994    (EC2는 전체의 3.2%)
```

**EC2가 3.2%입니다.** 그러니 "인스턴스만 남긴다"로 자르면 96.9%의 죽은 무게는 그대로
두고 실제로 쓰는 68종(RDS·SQS·Secrets Manager…)을 지우게 됩니다 — 자르는 선이 반대
축입니다. 넓은 것은 관리형이 아니라 **프로바이더 카탈로그 전체**를 받아 온 것이었고,
그 대부분은 배포 계획이 영영 지목하지 않는 AI·분석·컨택센터 제품입니다.

## 1.1 그래서 경계를 무엇으로 삼나

`docs/research.md`가 적은 것은 *"클라우드 리소스 및 개발 단계별 산출물을 생성, **배포**,
실행"*입니다. 배포가 목적이면 경계는 **실제로 배포할 수 있는 것**이어야 합니다. 이
저장소에는 그 기질이 이미 있습니다 — cb-tumblebug입니다. costkb·perfkb·envkb가 이미
그 미러를 씁니다.

**"cb-tumblebug이 만들 수 없는 자원은 우리 계획에 나오지 않는다."** 이 한 문장이 경계이고,
근거는 취향이 아니라 **환각 방지**입니다(과제 문제 ①). 만들 수 없는 것을 그린 배포
다이어그램은 검증기를 통과해도 배포되지 않습니다.

---

## 2. 경계의 정의 — 선정 절차와 포화

### 2.1 절차

1. **후보 고정** — cb-tumblebug swagger(`v0.11.8`, 142 paths)의 자원 경로를 **전수로**
   뽑는다. 우리 파서가 무엇을 뽑는지는 보지 않는다(S3).
2. **코어 타입 확정** — 자원 경로가 있는 것만 코어 타입으로 센다.
3. **벤더 투영** — `graphkb/parsers/core_vendor_map.json`(사람 검수 완료, 1차 근거는
   cb-spider 드라이버 소스)으로 aws/azure/gcp 타입에 매핑한다.
4. **포화 기준** — swagger 경로 소진. 새 자원이 안 나오면 멈춘다. 버전이 오르면 경계도
   오르므로 **핀을 박고 그 핀을 기록한다.**

### 2.2 결과 — 코어 16종

```
vNet · subnet · securityGroup · sshKey · vm · mci · dataDisk ·
image · customImage · spec · nlb · k8sCluster · k8sNodeGroup
＋ sqlDb · objectStorage · vpn          ← swagger에 있는데 우리 파서가 안 뽑는다
```

뒤의 셋이 이 조사의 산출입니다. **경계를 "파서가 뽑는 것"으로 잡았으면 못 봤을
자리**이고, 이것이 S3을 결정으로 세운 이유입니다. `core-graph`는 지금 13종입니다.

### 2.3 벤더 투영 — 28종 (+ 신설분)

`mapping-graph`에 이미 사람 검수를 마친 매핑이 있고, aws/azure/gcp만 남기면 **28종**입니다.

| 코어 | aws | azure | gcp |
|---|---|---|---|
| vNet | `EC2::VPC` | `Network/virtualNetworks` | `ComputeNetwork` |
| subnet | `EC2::Subnet` | `…/subnets` | `ComputeSubnetwork` |
| securityGroup | `EC2::SecurityGroup` | `networkSecurityGroups` | `ComputeFirewall` |
| sshKey | `EC2::KeyPair` | `Compute/sshPublicKeys` | — (등가물 없음) |
| vm | `EC2::Instance` | `Compute/virtualMachines` | `ComputeInstance` |
| dataDisk | `EC2::Volume` | `Compute/disks` | `ComputeDisk` |
| customImage | — | `Compute/images` | `ComputeMachineImage` |
| nlb | `ELBv2::LoadBalancer` | `Network/loadBalancers` | `ComputeForwardingRule` |
| k8sCluster | `EKS::Cluster` | `ContainerService/managedClusters` | `ContainerCluster` |
| k8sNodeGroup | `EKS::Nodegroup` | `…/agentPools` | `ContainerNodePool` |
| **sqlDb** | *신설 필요* | *신설 필요* | *신설 필요* |
| **objectStorage** | *신설 필요* | *신설 필요* | *신설 필요* |
| **vpn** | *신설 필요* | *신설 필요* | *신설 필요* |

`mci`·`image`·`spec`은 cb-tumblebug 내부 개념이라 벤더 등가물을 만들지 않습니다(기존
매핑 문서의 판단을 그대로 잇습니다).

> 신설 9칸은 **같은 절차**로 채웁니다 — cb-spider 드라이버 소스를 읽고 대상 이름을
> 확정하고 `confidence`와 근거 문장을 붙입니다. 근거를 못 쓰면 항목을 만들지 않습니다.

---

## 3. 무엇이 남고 무엇이 지워지나

### 3.1 데이터 (57 파일 · 396,853 레코드)

**남는다 — 인스턴스 축(실측이 있는 것)**

| 파일 | 레코드 | 왜 |
|---|---:|---|
| `tumblebug-cost` | 73,083 | 스펙 단가. 경계 그 자체 |
| `tumblebug-perf` | 65,032 | 스펙 성능 |
| `azure-discount-pricing` | 32,073 | VM 예약·절감 단가(스펙 축) |
| `gcp-spot-commit` | 11,193 | VM 스팟·약정 단가(스펙 축) |
| `region-latency` | 10,890 | 리전 간 지연 |
| `basic-images` | 6,033 | 이미지 = 코어 타입 |
| `cloud-regions`·`region-carbon`·`cbspider-support` | 677 | 리전 축 |
| `tumblebug-sizing`·`reviewed-sizing`·`container-presets` | 65 | 사이징 규칙 |
| `tumblebug-bundles` | 23 | cb-tumblebug 자체의 연계 군 |
| `core-graph`(확장)·`mapping-graph`·`svcmap-graph`(축소) | ~350 | 경계의 뼈대 |

**경계 필터를 거쳐 남는다 — 리소스 축**

| 파일 | 지금 | 필터 후 |
|---|---:|---:|
| `aws/azure/gcp-capacity` 외 aws·azure 보조 7종 | 102,998 | **1,434 (1.4%)** |
| `service-lifecycle` | 17 products | EKS 등 경계 안만 |
| `aws-regions` | 385 | 경계 안 타입만 |

**지운다**

| 무엇 | 레코드 | 왜 |
|---|---:|---|
| `aws/azure/gcp-graph` + `azure-deploy-graph` | 12,100 | 카탈로그 전수. 경계 밖 |
| 프로바이더 7종의 graph·capacity (`oracle`·`tencent`·`alibaba`·`ibm`·`nhn`·`ncp`·`openstack`) | 43,170 | S4 |
| `ibm-perf` | 2,002 | 같음 |
| `azure-managed-pricing`·`gcp-managed-pricing`(+ AWS 로컬) | 24,294 | S6 |
| `pattern-corpus`·`aws-pattern-corpus`·`aws-pattern-bundles` | 575 | 관리형 아키텍처 패턴 |
| `aqt`·`avm`·`kcc`·`awscfn` 동시출현 | 2,903 | 관리형 카탈로그의 연계 군 |
| `azure-operations`·`aws-endpoints` | 9,369 | 경계 밖 서비스 축 |
| `aws/azure/gcp-capacity`의 경계 밖 | 101,564 | 필터 |

대략 **396,853 → 약 200,000**입니다. 레코드로는 절반이지만 **타입으로는 9,796 → 28**,
**KB 코드로는 30,397줄 중 14,484줄**(graphkb 4,754 · capacitykb 6,513 · patternkb 1,131 ·
bundlekb 2,086)이 사정권입니다.

### 3.2 삭제가 두 종류라는 것 — 실행에서 가장 조심할 자리

S5("파서까지 지운다")를 문자 그대로 적용하면 **경계 안 데이터도 같이 죽습니다.**
경계 안 1,434건을 만드는 것이 바로 `capacitykb/parsers/cfn.py`·`azure.py`·`gcp.py`이기
때문입니다. `EC2::Instance` 제약 107건이 그 파서의 산출입니다.

그래서 삭제를 둘로 가릅니다.

| 종류 | 대상 | 처리 |
|---|---|---|
| **(가) 경계 밖 전용 소스** | patternkb 전부 · bundlekb의 aqt·avm·kcc·awscfn · costkb의 `*_managed` · capacitykb의 `azure_operations`·`azure_secret`·`aws_endpoints` · graphkb의 `cfn`·`azure`·`gcp`·`avm` · 프로바이더 7종 경로 | **파서째 삭제** |
| **(나) 경계 안팎을 함께 만들던 것** | capacitykb `cfn`·`cfnlint`·`azure`·`gcp`·`azure_quota`·`azure_mutability`·`aws_limits`·`tpaws`·`tpcsp`·`tpg` | 삭제가 아니라 **경계 화이트리스트를 파서에 박는다** — 밖은 애초에 만들지 않는다 |

(나)는 하나의 상수를 공유합니다: `kbcommon`에 **경계 화이트리스트 한 벌**을 두고 모든
빌드가 그것을 참조합니다. 두 곳에 두면 한 곳이 뒤처집니다(`field_map` 결정과 같은 규율).
그리고 **경계 밖으로 버린 건수를 `_coverage`에 남깁니다** — 침묵하면 "원래 없었다"로
읽힙니다.

### 3.3 연계 리소스 군을 무엇으로 얻나 — 코퍼스를 버려도 되는 근거

문제 ②(*"연계되는 다양한 리소스 군을 획득"*)를 얻는 방법이 지금 둘입니다.

| | 방법 | 산출 |
|---|---|---|
| **(가)** | **연쇄 의존 자원 집합** — swagger 필드가 요구하는 것을 따라간다 | VM 하나 → `image`·`spec`·`securityGroup`·`sshKey`·`subnet`·`vNet` + `mci`. 도구 `kb_creation_order`·`kb_deletion_impact`가 이미 쓴다. `tumblebug::dynamic-vm` 번들은 `provisioning.go`(v0.12.25)를 읽어 만든 것이다 |
| **(나)** | **코퍼스 동시출현** — 템플릿·모듈에서 같이 나오는 것을 센다 | `awscfn` 1,147 · `aqt` 1,253 · `avm` 207 · `kcc` 296 |

**(나)를 지우면 (가)가 좁아지는가**를 쟀습니다(2026-07-29).

```
동시출현 2,400쌍 → 경계 안 56쌍 → 의존 그래프가 모르던 쌍 12개
  └ 그 12개의 의존 그래프상 거리:  2 hop 12개 · 3 hop 이상 0개
번들 503개 → 앵커가 경계 안인 것 27개
```

**12개 전부가 거리 2입니다.** `sshKey ↔ subnet`은 둘이 관계있다는 뜻이 아니라 *"같은 VM이
둘 다 쓴다"*는 뜻이고, 그래프가 이미 아는 사실의 다른 표현입니다. 그래프로 설명되지 않는
쌍은 **0건**입니다.

→ **(나)의 삭제가 (가)를 좁히지 않습니다.** 이것이 bundlekb 4/5와 patternkb를 지우는
근거이고, 이 문단이 나중에 *"코퍼스를 왜 버렸나"*의 답입니다.

> **이 측정이 말하지 않는 것.** 우리가 잰 것은 *"이 코퍼스가 경계 안에서 무엇을 더
> 아는가"*이지 *"동시출현이라는 방법이 쓸모없다"*가 아닙니다. 이 코퍼스들은 관리형
> 카탈로그를 찍은 것이라 경계 안 표본이 얇습니다(1,147 중 28). 경계 안에서 경험적
> 연계 근거가 필요해지면 옳은 코퍼스는 AWS CFN 템플릿이 아니라 **cb-tumblebug의 실제
> MCI 요청 예제·테스트**입니다. 그때는 새 소스이지 되살리기가 아닙니다.

### 3.4 아직 모르는 한 칸

> **채워졌다 (2026-07-29).** `tumblebug-resource-dependency-2026-07-29.md` ·
> 산출물 `app/core/cloudkb/graphkb/parsers/tumblebug_resources.json`.
> 본문은 고치지 않는다(계획과 결과를 섞지 않는다). 다만 **이 계획의 전제 둘이
> 조사로 뒤집혔으므로** 그것만 여기 적는다.
>
> - **자원이 16종이 아니라 21종이다.** `publicIp`·`vNic`·`globalDns`가 v0.12.25에서
>   생겼고(우리 핀은 v0.11.8), `fileSystem`은 cb-spider에만 있다. §2.2의 목록은
>   그만큼 좁게 잡은 것이다.
> - **의존 간선이 19가 아니라 38이다.** 그중 20개가 우리에게 없었고, 하나
>   (`securityGroup→vNet`)는 `required·stated`로 적어 둔 것이 **거짓**이다 —
>   실제로는 CSP 조건부이고 TB 스키마에서는 선택이다.

신설 3종(`sqlDb`·`objectStorage`·`vpn`)의 **의존 엣지를 모릅니다** — sqlDb가 subnet을
요구하는지, vpn이 무엇에 매이는지. **P2가 곧 그 분석입니다**(소스는 캐시에 있다:
`cb-spider-v0.12.37` · `tumblebug-src-v0.12.25`). 엣지가 나오기 전에는 §2.3의 신설
매핑도 완성으로 치지 않습니다.

---

## 4. 아키타입 13 → 3, 그리고 계획이 못 하게 되는 것

앱 개념 13종 중 cb-tumblebug에 등가물이 있는 것은 **셋**입니다.

| 산다 | 죽는다 (10종) |
|---|---|
| `relationalDatabase` → `sqlDb` | `messageQueue` · `secretStore` · `keyValueCache` · |
| `objectStorage` → `objectStorage` | `searchIndex` · `eventStream` · `nosqlDatabase` · |
| `containerService` → `k8sCluster` | `serverlessFunction` · `apiGateway` · `cdn` · `dnsZone` |

**앞의 둘은 파서를 새로 써야 생깁니다**(§2.3의 신설 9칸). 지금 상태로 자르면 살아남는
아키타입은 `containerService` 하나뿐이므로, **삭제보다 신설이 먼저입니다**(§7 순서).

### 4.1 대체 동작 — 침묵이 아니라 명시적 미해결

큐가 필요한 앱에 큐를 안 그리면 그림이 거짓이 됩니다. 그래서 경계 밖 요구는 **지우는
것이 아니라 경계 밖이라고 말합니다.**

```
unresolved: message queue is required by the design but the deployment substrate
            (cb-tumblebug) does not provision one — this must be settled outside
            this plan
```

이 저장소가 다른 축에서 계속 지켜 온 구분(**없다 / 안 봤다 / 안 한다**)의 세 번째
칸입니다. `downstream.undecided`와 같은 성격이고, `blocking_decisions`가 이미 그 자리를
갖고 있습니다.

---

## 5. 과제 목표와의 대조 — 무엇을 포기하나

목표 2 원문은 *"클라우드 환경 특성(**클라우드 인스턴스 성능, 비용** 등), 클라우드
리소스의 특성(**리소스 용량, 리소스 의존성** 등)"*이고, 문제 ②는 *"특정 리소스를
선택하는 경우 **연계되는 다양한 리소스 군**을 획득"*입니다.

| 과제가 요구한 축 | 이 개편 뒤 |
|---|---|
| 인스턴스 성능 | **강해진다** — 남는 데이터가 전부 이 축이다 |
| 인스턴스 비용 | **강해진다** — 관리형 가격의 불확실성이 빠진다 |
| 리소스 용량 | **유지되나 좁아진다** — 28종에 대해서만. 1,434건 |
| 리소스 의존성 | **유지된다** — 코어 16종의 의존이 곧 배포 순서다 |
| 연계 리소스 군 | **좁아진다** — 관리형 동시출현 2,903건을 버리고 cb-tumblebug 번들만 남는다 |

**정직하게 적자면 뒤의 둘은 후퇴입니다.** 대가를 받는 것은 *"우리가 답하는 모든 것이
실제로 배포 가능하다"*는 성질이고, 그것이 과제 문제 ①(환각)에 대한 답입니다. 이
맞바꿈이 옳은지는 이 문서가 아니라 **결과가 판정**합니다 — §8의 완료 판정에 걸어 둡니다.

---

## 6. 도구 31개의 운명

| 모듈 | 도구 | 판정 |
|---|---|---|
| `cost_tools` 4 · `perf_tools` 3 · `sizing_tools` 2 | | **그대로** |
| `capacity_tools` 10 | | **유지, 답이 좁아진다**(경계 밖 질의는 "경계 밖"이라 답한다) |
| `graph_tools` 6 | `kb_search_types`·`kb_rank_types`는 9,796종을 전제한다 | **재정의** — 28종 위에서 뜻이 달라진다 |
| `pattern_tools` 1 | patternkb 삭제 | **제거** |
| `bundle_tools` 1 | 소스 4/5 삭제 | **유지, 축소** |
| `guideline_tools` 1 | patternkb·bundlekb를 읽는다 | **재작성** |
| `design_tools` 1 · `tools` 2 | | 그대로 |

프로브 68건 중 patternkb·카탈로그 질의를 태우는 것들은 **기대치가 바뀝니다.** 지우는
것이 아니라 **"경계 밖이라고 답하는가"로 판정을 뒤집습니다** — 커버리지를 줄이면서
프로브를 같이 줄이면 회귀가 눈을 감습니다.

---

## 7. 실행 순서

```
P1 경계 상수      → P2 신설 매핑 9칸 → P3 경계 필터   → P4 삭제      → P5 기대치 재정렬
   (화이트리스트)     (sqlDb·objectStorage·vpn)  (나 종류)   (가 종류)     (프로브·문서)
```

**P2가 P4보다 먼저인 이유**: 지금 지우면 아키타입이 하나만 남습니다(§4). 신설이 먼저
들어와야 "좁혔다"이고, 순서를 뒤집으면 한동안 "망가뜨렸다"입니다.

**P3이 P4보다 먼저인 이유**: 필터가 없는 상태에서 파서를 지우면 경계 안 1,434건이 같이
사라지고, 그러면 필터가 옳은지 대조할 기준선이 없어집니다.

기준선은 이미 있습니다 — `report/probe-baseline-2026-07-29/`. 이 개편의 대조군으로
그대로 씁니다.

---

## 8. 완료 판정

- 경계 화이트리스트가 **코드 한 곳**에 있고, 모든 빌드가 그것을 참조한다.
- 어떤 산출물에도 경계 밖 `type_id`가 **0건**이고, 그것을 지키는 테스트가 있다.
- 경계 밖으로 버린 건수와 사유가 `_coverage`에 남는다(침묵 금지).
- 아키타입 3종이 **실제로 계획에 나오고**, 나머지 10종 요구는 **명시적 미해결**로 나온다.
- 프로브 기준선 대비 **통과 → 실패로 뒤집힌 것이 0건**이다. (좋아졌다는 주장은 하지
  않는다 — 회차마다 뒤집히는 프로브가 58건 중 7건이다.)

## 9. 타당성 위협

| | 위협 | 대응 |
|---|---|---|
| T1 | **cb-tumblebug의 능력이 곧 클라우드의 능력은 아니다.** 기질이 못 만든다는 이유로 사용자에게 필요한 것을 없다고 말하게 된다 | 경계 밖은 *"없다"*가 아니라 *"이 도구가 배포하지 않는다"*로 말한다(§4.1). 두 문장은 다르다 |
| T2 | 경계가 **버전에 매인다.** 다음 태그에서 자원이 늘면 우리 경계가 조용히 뒤처진다 | 핀을 박고 기록한다. swagger 자원 목록과 화이트리스트가 어긋나면 **빌드가 죽는다**(`field_map` 전수성 검사와 같은 장치) |
| T3 | 지운 뒤에 되돌리고 싶어질 수 있다 | 데이터·파서는 지우되 **무엇을 왜 버렸는지는 `_coverage`와 이 문서에 남는다.** 소스는 캐시에 그대로 있으므로 복원 비용은 파서 재작성이다 |
| T4 | 신설 9칸(§2.3)이 **근거 없이 채워질 수 있다** — 지금 매핑은 드라이버 소스를 읽고 만든 것이다 | 같은 절차·같은 `confidence` 기록. 근거를 못 쓰면 항목을 만들지 않는다(기존 매핑이 sshKey/gcp를 비워 둔 것과 같다) |
| T5 | 커버리지가 줄어 **답의 유용성**이 준다 | 대가를 명시하고 간다(§5). 완화책은 남는 축의 깊이를 올리는 것이지 폭을 되돌리는 것이 아니다 |
| T6 | 프로브 기대치를 같이 낮추면 **회귀가 눈을 감는다** | 프로브는 지우지 않고 **판정을 뒤집는다**(§6) |

---

## 부록 — 이 계획으로 뒤집히는 것

1. `core-graph`가 13 → 16종이 되고, `mapping-graph`에 9칸이 생긴다.
2. 앱 개념 13 → 3. 배포 계획에서 큐·시크릿·서버리스가 **사라지고 미해결로 나온다.**
3. `kb_search_types`·`kb_rank_types`의 답이 9,796종에서 28종 위로 바뀐다.
4. patternkb가 사라지고 `resource_guideline`이 다시 쓰인다.
5. 관리형 가격 축이 없어진다 — `cost_estimate_monthly`의 대상이 인스턴스로 좁아진다.
6. kb-book의 상당 부분이 **없는 것을 설명하는 문서**가 된다(문서 갱신은 이 계획 밖,
   다음 라운드).
