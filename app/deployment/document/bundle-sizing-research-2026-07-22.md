# bundlekb · sizingkb 소스 조사 (2026-07-22)

`kb-roadmap.md` §7·§8이 "조사 먼저"로 미뤄 둔 두 축이다. `research.md` 목표 2 중
마지막 미충족분이기도 하다.

> 문제 2: *"특정 리소스를 선택하는 경우 **연계되는 다양한 리소스 군을 획득**할 수
> 있어야 하며, 사용자가 클라우드 리소스를 선택할 수 있는 가이드라인 제공이 필요하다"*

**결론부터.** 둘 다 만들 수 있다. bundlekb는 근거가 튼튼하고 **서로 독립인 두 소스가
같은 답을 낸다.** sizingkb는 좁게 만들어야 하며, 넓히려는 유혹이 곧 환각이다.

---

## 0. 조사 방법과 지켜야 했던 것

이 저장소가 비싸게 배운 것들을 규칙으로 걸고 시작했다.

| 규칙 | 이번에 실제로 걸린 곳 |
|---|---|
| 이미 받아 둔 소스의 안 쓰는 부분부터 | **네 번 나왔다** (아래 §1) |
| 재조사 금지 10축을 다시 파지 않는다 | 지켰다 |
| 세지 않고 채택하지 않는다 | 오탐 89→10→15 (아래 §2) |
| 반례를 세라 | AVM 판별자를 두 번 고쳤다 |
| 200이 살아 있다는 뜻은 아니다 | RDS 문서는 200이지만 **2023년에 아카이브** |
| 산문 추출은 데인 적이 있다 | 사이징을 표·구조체로만 한정 |

---

## 1. 이미 손에 있던 것 — 네 번째·다섯 번째

### 1-1. cb-tumblebug 덤프에 **안 쓰는 테이블이 둘** 있었다

우린 `spec_infos`만 읽고 있었다.

```
image_infos     174,759행   OS 이미지 · is_basic_image/is_gpu_image/is_kubernetes_image 플래그
latency_infos    10,890행   리전 간 실측 지연 (source 99곳 × target 110곳, ms)
```

`latency_infos`는 **아예 새 축**이다. 프로바이더를 넘나든다(`alibaba-ap-south-1` →
…). 멀티 리전 배치 가이드라인의 근거가 된다. 원본은 `assets/cloudlatencymap.csv`
(166 KB)로 같은 저장소에 있다.

> 주의: `measured_at`이 전부 같은 초(17:53:20.21…)다. **적재 시각이지 측정 시각이
> 아니다.** 언제 잰 값인지는 이 테이블만으로 알 수 없다.

### 1-2. `init/templates/` — 이름 붙은 인프라 번들 22개

`resourceType`별로 갈린다.

| 종류 | 개수 | 성격 |
|---|---|---|
| `infra` | 16 | 12개는 "리전마다 VM 하나"인 **테스트 픽스처**, 4개가 진짜 유스케이스 |
| `securityGroup` | 3 | `sg-default`(전 포트 개방·개발용) · `sg-aws-web` · `sg-usecase-web` |
| `vNet` | 2 | `vnet-default`(서브넷 2·멀티존) · `vnet-aws-ap-northeast-2`(public/private/database 3층) |
| `k8sCluster` | 1 | 멀티 클라우드 |

진짜 유스케이스 넷이 값어치가 있다.

```
infra-usecase-aws-ap-northeast-2-web   t3.small + gp3 30GB          "Small AWS web server"
infra-aws-gpu-simple                   g6.8xlarge + g6e.2xlarge     NVIDIA L4/L40S
infra-usecase-llm-bench                4개 노드 · 3개 CSP           LLM 벤치마킹
infra-usecase-llm-d-high               control(b8als_v2) + H100     LLM 서빙 고성능
```

**`sg-default`는 스스로 경고를 달고 있다** — *"Opens all TCP/UDP ports … For production,
use a more restrictive template"*. 번들을 담을 때 이 문장을 같이 담아야 한다.

### 1-3. `assets/networkinfo.yaml` — 네트워크 계획 제약 (신규 축)

CSP 10곳. 손 큐레이션이고 **채움이 고르지 않다.**

```
csp         vnet/min max  sub/min max  예약IP  VPN게이트웨이  가용CIDR
alibaba          8   28      16   29      4        -            3
aws             16   28      16   28      -        -            3
azure            8   29       8   29      5      True           3
gcp              -    -       8   29      -        -            3
ibm              9   28       9   29      5        -            3
kt               -    -        -    -      -        -            0
```

- **예약 IP는 사이징 질문에 직접 답한다** — "/24 서브넷에 몇 대 띄우나" = 256 − 예약.
- **AWS 예약 IP가 비어 있다.** AWS는 실제로 5개를 예약한다. 즉 이 파일은
  **불완전하다** — 비어 있는 것을 "예약 없음"으로 읽으면 거짓이 된다.
  ('없음'의 뜻을 좁히는 원칙이 그대로 적용된다.)

### 1-4. `assets/k8sclusterinfo.yaml` — 클러스터 번들 제약

CSP 8곳. **번들 질문에 바로 답하는 구조화된 사실**이다.

```
              requiredSubnetCount  nodeGroupsOnCreation  nodeImageDesignation
aws                    2                 false                 true
azure                  1                 true                  false
gcp                    1                 true                  true
```

`azure`에는 `nodeGroupNamingRule: ^[a-z][a-z0-9]*$`도 있다(capacitykb 소재).

### 1-5. tumblebug이 **VM 하나에 실제로 만드는 것**

`getNodeGroupReqFromDynamicReq`(provisioning.go:3216)를 읽어 확정했다.

```
VM 하나 요청  →  vNet + Subnet   (연결당 공유, 없으면 생성)
                 SSHKey          (연결당 공유)
                 SecurityGroup   (연결당 공유, 템플릿 지정 가능)
                 Image           (없으면 CSP에서 자동 등록)
                 VM(Node)
```

**이건 "클라우드가 요구하는 것"이 아니라 "우리 실행 경로가 만드는 것"이다.**
가이드라인 KB 원칙상 그 경계를 답변에 밝혀야 한다.

### 1-6. 곁다리 — 상위 도구의 성능 추천이 죽어 있다

`RecommendNodePerformance`는 `EvaluationScore01`로 정렬하는데, 그 컬럼은 덤프에서
전부 `-1`이다(이미 확인된 사실). 즉 **tumblebug의 성능 기준 추천은 사실상 무작위**다.
우리 perfkb가 상위 도구보다 이 축에서 낫다.

`validateK8sMinimumRequirements`에는 **K8s 노드 최소치 vCPU ≥ 2, 메모리 ≥ 4 GiB**가
상수로 박혀 있다. 도구가 강제하는 값이지 클라우드 사실은 아니다.

---

## 2. AVM — 이미 핀 박힌 소스의 안 쓰는 부분 (다섯 번째)

지금 파서는 `avm/res/*/*/main.json`의 `dependsOn`만 본다. **모듈이 무엇을 배포하는지**는
안 본다. 그리고 `avm/ptn/`(패턴 모듈)은 아예 안 읽는다.

```
res 173개 · ptn 49개 · utl 1개
```

### 2-1. 판별자를 두 번 틀렸다

**1차** — `condition` 유무로만 갈랐더니 패턴 하나가 104종을 "항상 배포"한다고 나왔다.
한 Cosmos 계정에 Cassandra·Gremlin·Mongo·SQL이 **동시에** 들어 있었다. 원인은
`copy`(파라미터 배열 루프)를 안 센 것.

**2차** — `defaultValue` 부재를 '필수'로 읽었더니 `Insights/diagnosticSettings`가
**173개 중 89개 모듈의 필수 동반자**로 나왔다. 명백한 오탐. 원인은 AVM이 nullable
파라미터를 `coalesce(parameters('x'), createArray())`로 처리한다는 것.

**3차(확정)** — 판별자는 이렇다.

```
condition 있음                        → 선택
copy.count에 coalesce/createArray     → 선택 (빈 배열 폴백이 있다)
copy.count에 폴백 없음                → **필수 동반** (값을 반드시 줘야 한다)
둘 다 없음                            → 무조건
```

그리고 **중첩 배포를 재귀로 풀어야 한다.** VM의 NIC은
`deployments → deployments → networkInterfaces`로 **두 단** 중첩이라, 한 단만 보면
VM의 유일한 진짜 필수 동반자를 통째로 잃는다. 그대로 담았으면 **"VM은 아무것도 필요
없다"**는, 사실과 정반대인 KB가 됐다.

### 2-2. 확정된 3층 모델

```
res 173개 · 무조건 1종인 모듈 152개 (모듈이 감싸는 그 리소스)
            필수 동반이 있는 모듈 15개
            선택 평균 5.4종 · 최대 44종
```

검증되는 사례:

```
compute/virtual-machine          무조건 virtualMachines      필수동반 networkInterfaces
network/virtual-network-gateway  무조건 virtualNetworkGateways  필수동반 publicIPAddresses
db-for-my-sql/flexible-server    필수동반 administrators · databases · firewallRules
```

**둘 다 클라우드 사실과 일치한다** — Azure VM은 NIC이 있어야 하고, VPN 게이트웨이는
공인 IP가 있어야 한다.

3층(선택)은 곧 *"이 리소스에 붙일 수 있는 것의 메뉴"*이고, 이게 research.md가 말한
**"연계되는 다양한 리소스 군"**이다.

```
148/173 모듈  Microsoft.Authorization/roleAssignments   ← 어디에나 붙는 횡단 관심사
148/173       Microsoft.Authorization/locks
 72/173       Microsoft.Insights/diagnosticSettings
 43/173       Microsoft.Network/privateEndpoints
 30/173       Microsoft.KeyVault/vaults
```

> **손 검수가 필요한 잔여 오탐**: `network/load-balancer`의 필수동반
> `inboundNatRules`는 모듈 설계이지 클라우드 요구가 아닐 가능성이 크다. 핀이
> 고정돼 있으므로 예외는 손으로 검수해 표에 적는다.

### 2-3. `avm/ptn` — 완성된 솔루션 번들 49개

`ptn/sa/build-your-own-copilot`처럼 이름이 곧 유스케이스다. 무조건 평균 13.5종 ·
조건부 평균 21.4종. **다만 `res`의 조건부까지 전이돼 여전히 과다 보고**이므로,
패턴은 "무조건" 층만 담는 것이 안전하다.

---

## 3. 핵심 실험 — 빈도가 판별자가 된다

graphkb의 한계는 **"가능한 것"과 "실제로 필요한 것"을 못 가르는 것**이다. 스키마는
`EC2::Instance`에서 `KMS::ReplicaKey`까지 이어 준다. 가능하지만 아무도 안 쓴다.

가설: **사람이 실제로 쓴 템플릿 수천 개의 동시 출현 비율**이 그 판별자다.

`Azure/azure-quickstart-templates`(MIT, 커밋 `331d6f39`, 2026-07-17)에서
`azuredeploy.json` **1,152개**를 파싱해 타입 530종을 뽑았다.

```
[Microsoft.Compute/virtualMachines] 이 있는 템플릿 330개

   100.0%  networkInterfaces          ← 사실상 필수
    92.4%  virtualNetworks
    92.1%  publicIPAddresses
    72.4%  networkSecurityGroups      ← 강한 관행
    61.5%  virtualMachines/extensions
    53.6%  storageAccounts
    17.0%  availabilitySets           ← 선택
    16.1%  loadBalancers
     5.8%  routeTables                ← 드묾
     5.5%  bastionHosts
```

**분포가 뚜렷이 갈린다.** 100%·92% 무리와 5~7% 꼬리 사이에 큰 골이 있다.

다른 앵커에서도 성립한다.

```
[Microsoft.Web/sites]  94.6% serverfarms    ← App Service는 플랜이 있어야 한다
[Microsoft.Sql/servers] 31.4% servers/databases
```

### 독립 두 소스가 같은 답을 낸다

| | VM의 필수 동반 |
|---|---|
| AVM (마이크로소프트 모듈 설계, **명시**) | `networkInterfaces` |
| Quickstart 코퍼스 (템플릿 330개, **실측**) | `networkInterfaces` **100.0%** |

이 저장소에는 이미 선례가 있다 — `aws-cross-checked`는 **두 공식 소스가 같은 값을
말했을 때만** 그 라벨을 단다. 같은 규율을 쓸 수 있다.

### 반드시 함께 밝혀야 할 한계

1. **코퍼스의 사실이지 클라우드의 사실이 아니다.** "100% NIC"은 "Azure가 강제한다"가
   아니라 "이 1,152개에서 늘 함께 나왔다"이다.
2. **표본 편향.** Quickstart는 데모·튜토리얼 쪽으로 기운다. VM과 스토리지 계정이
   53.6%인 것은 옛 부트 진단 관행의 흔적이다.
3. **표본이 적으면 비율이 소음이다.** `ContainerService/managedClusters`는 17개뿐이라
   35.3%가 6건이다. **최소 표본 수 기준이 필요하다.**
4. tarball이 **326 MB**다. 빌드 시점에 받아 **파생 표만** 산출물로 담는다.

---

## 4. 조사한 소스 전체 (존재·라이선스·규모 실측)

### 번들 후보

| 소스 | 상태 | 라이선스 | 규모 | 판정 |
|---|---|---|---|---|
| cb-tumblebug `init/templates` | 200 | Apache-2.0 | 22 템플릿 | **채택 1순위** · 이미 핀 |
| cb-tumblebug `provisioning.go` | 200 | Apache-2.0 | 함수 1개 | **채택** · 우리 실행 경로 |
| AVM `res`/`ptn` | 200 | MIT | 173+49 모듈 | **채택 1순위** · 이미 핀 |
| Azure Quickstart Templates | 200 | MIT | 1,423 → 파싱 1,152 | **채택** · 빈도 판별자 |
| awslabs/aws-solutions-constructs | 200 | Apache-2.0 | **패턴 83개 · 서비스 41종** | **채택 후보** · AWS 측 유일 대안 |
| aws/serverless-application-model | 200 | Apache-2.0 | model *.py 119개 | 보류 — 규칙이 파이썬 코드 |
| aws-cloudformation-templates | 200 | Apache-2.0 | — | 후보 (AWS 코퍼스) |
| terraform-aws-modules/* | 200 | Apache-2.0 | 모듈별 | 후보 |
| GoogleCloudPlatform/cloud-foundation-fabric | 200 | Apache-2.0 | — | 후보 (GCP) |
| pulumi/pulumi-awsx · aws/aws-cdk | 200 | Apache-2.0 | — | 보류 — 코드 파싱 |
| Azure/ALZ-Bicep | 200 | MIT | — | 후보 |

`aws-solutions-constructs`는 **이름이 곧 조합**이라 파싱 없이도 신호가 나온다.

```
패턴 83개 · 서비스 41종
  lambda 36 · s3 13 · fargate 12 · apigateway 11 · sqs 11 · kinesisstreams 8 …
  3개 이상 묶인 것: cloudfront-apigateway-lambda · cognito-apigateway-lambda
                    dynamodbstreams-lambda-elasticsearch-kibana · iot-lambda-dynamodb
```

### 사이징 후보

| 소스 | 상태 | 라이선스 | 내용 | 판정 |
|---|---|---|---|---|
| `networkinfo.yaml` 예약 IP | — | Apache-2.0 | 서브넷 가용 IP 계산 | **채택** · 결정론적 |
| `k8sclusterinfo.yaml` | — | Apache-2.0 | requiredSubnetCount 등 | **채택** |
| tumblebug K8s 최소치 | — | Apache-2.0 | vCPU≥2 · 4 GiB | **채택**(도구 규칙임을 밝히고) |
| `init/templates` 유스케이스 | — | Apache-2.0 | web/LLM 스펙 참조점 | **채택** · "정답" 아닌 참조점 |
| bitnami/charts `resourcesPreset` | 200 | Apache-2.0(헤더) | nano~2xlarge 표 | **채택 후보** (아래) |
| MicrosoftDocs/azure-compute-docs | 200 | CC-BY-4.0 | **`/sizes/` 아래 562개** | 후보 — 표 추출 주의 |
| MicrosoftDocs/azure-databases-docs | 200 | CC-BY-4.0 | 계층별 한도 | 후보 |
| awsdocs/amazon-rds-user-guide | 200 | — | **2023-06-15 아카이브** | 보류 — 3년 묵음 |
| aws/karpenter-provider-aws | 200 | Apache-2.0 | 인스턴스 선택 로직 | 후보 |
| SPEC 등 벤치마크 | — | — | — | **재조사 금지** (인스턴스 타입 0건) |

`bitnami/charts`의 프리셋은 그 자체로 정직하다.

```
nano    cpu 100m  mem 128Mi      large   cpu 1.0  mem 2048Mi
micro   cpu 250m  mem 256Mi      xlarge  cpu 1.0  mem 3072Mi
small   cpu 500m  mem 512Mi
medium  cpu 500m  mem 1024Mi
```

> 원본 주석: *"These presets are for basic testing and **not meant to be used in
> production**"* — 담는다면 이 문장을 값과 **함께** 담아야 한다.

### 새로 알게 된 것: Azure 문서가 쪼개졌다

우리 `azure-limits-doc` 핀은 `MicrosoftDocs/azure-docs`를 본다(핀은 살아 있음을
앞서 확인). 그런데 **컴퓨트·데이터베이스 문서가 별도 저장소로 분리**됐다
(`azure-compute-docs` 3,594파일, `azure-databases-docs`, 둘 다 CC-BY-4.0, 현재도 갱신).
VM 크기·DB 계층 표는 이제 그쪽에 있다.

---

## 5. RAG는 어디에 쓰고 어디에 안 쓰나

사용자가 물은 대로 고정관념 없이 봤다. **결론: 이번에 찾은 것들에는 RAG가 필요 없고,
쓰면 오히려 나빠진다.**

- 번들 소스는 전부 **구조화**돼 있다(ARM/Bicep JSON, YAML, 템플릿 JSON). 결정론적
  파싱이 가능한데 검색·요약을 끼우면 **이 저장소가 이미 세 번 고친 표류**를 다시
  들인다.
- 동시 출현은 **세는 문제**다. 1,152개를 세면 100.0%가 나온다. 검색으로는 그 숫자가
  안 나온다.

RAG가 값을 하는 자리는 하나뿐이다.

- **`azure-compute-docs` 같은 산문 562편.** 여기는 표·산문이 섞여 있고 규모가 커서
  전량 구조화가 비싸다. 다만 조건이 붙는다 — **원문을 그대로 인용**하고
  (출처 + 오프셋), **숫자를 추출해 사실로 만들지 않는다.** 이 저장소는 산문에서 뽑은
  단위가 3,600배 어긋난 사건을 겪었다.
- 참고: Citations API는 structured outputs와 함께 못 쓴다(이미 기록됨). 인용은 별도
  경로여야 한다.

---

## 6. 권고 — 무엇을 어떤 순서로

### bundlekb (근거 충분, 바로 착수 가능)

1. **AVM 3층 추출** — `res` 173 + `ptn` 49. 판별자는 §2-1 확정본. 중첩 재귀 필수.
   `basis=stated`(모듈이 그렇게 선언), 단 **"모듈 저자의 설계이지 API 강제가 아님"**을
   `avm-dependson`과 같은 방식으로 명시.
2. **tumblebug 동적 생성 번들** — VM→(vNet·Subnet·SSHKey·SG·Image). `basis=stated`,
   **"우리 실행 경로가 만드는 것"**으로 범위 표시.
3. **`init/templates` 22개** — 이름 붙은 번들. `sg-default`의 자체 경고문 포함.
4. **동시 출현 표** — Quickstart 1,152개에서 파생. **별도 `basis`가 필요하다**:
   `stated`도 `inferred`도 아니고 **`observed`(코퍼스 실측)**다. 최소 표본 수 기준
   필수. 파생 표만 산출물로 담고 326 MB tarball은 빌드 시점에만 받는다.
5. AWS 쪽은 `aws-solutions-constructs` 83패턴부터. 이름 분해만으로도 서비스 조합
   그래프가 나온다.

**교차 검증 규율**: AVM(명시)과 코퍼스(실측)가 **같은 답을 낸 것만** 강한 등급을 준다.
`aws-cross-checked` 선례를 그대로 따른다.

### sizingkb (좁게, 그리고 정직하게)

로드맵 §7의 판단이 옳았다 — **"정답"을 담으면 그게 환각을 권위 있게 포장하는 것**이다.
담을 수 있는 것은 **원본이 공식으로 적어 둔 변환 규칙**뿐이다.

담는다:
- 서브넷 가용 IP = 2^(32−prefix) − 예약IP (`networkinfo.yaml`, CSP별)
- K8s `requiredSubnetCount`·노드 최소치(도구 규칙임을 밝히고)
- 유스케이스 참조점 — "LLM 서빙 고성능 예시: H100 40코어 + 500GB PremiumSSD"
- 컨테이너 프리셋(nano~2xlarge) — 원본의 "프로덕션용 아님" 경고와 함께

담지 않는다:
- **"동시 사용자 N명 → vCPU M"** 형태의 어떤 것도. 소스가 없다.
- 벤치마크 기반 성능 환산 (재조사 금지 축)

도구는 값을 단정하는 대신 **참조점을 제시하고 부하 테스트를 권한다.**

### 안 한 것 (정직하게)

- `aws-solutions-constructs` 83패턴의 **정확한 CFN 타입** 추출 — TypeScript 파싱이
  필요해 이름 분해까지만 했다.
- SAM 변환 규칙 — 파이썬 코드라 비용이 크다고 판단해 보류.
- `azure-compute-docs` 562편의 표 구조 — 목록만 확인했고 내용은 안 뜯었다.
- AWS·GCP 코퍼스 동시 출현 — Azure에서만 실험했다. 같은 방법이 통할 개연성은
  높지만 **재지 않았으므로 통한다고 말하지 않는다.**
- `latency_infos`·`image_infos`는 **찾기만 했다.** 두 축 어느 쪽도 아니라 별도 상신.

---

# 2차 조사 (2026-07-23) — 방법이 어디까지 전이되나

1차에서 미측정으로 남긴 것을 쟀다. *"AWS·GCP 코퍼스 동시 출현은 Azure에서만 쟀으므로
통한다고 말하지 않는다."*

## AWS — 전이된다. 그리고 **더 흥미롭게** 갈린다

`aws-cloudformation/aws-cloudformation-templates`(Apache-2.0, 커밋 `a0f43bc6`)에서
템플릿 **299개** · 타입 150종을 파싱했다.

```
AWS::Lambda::Function  → AWS::IAM::Role          100.0% (38/38)
AWS::EC2::Instance     → AWS::EC2::SecurityGroup   90.2%
                         AWS::EC2::Subnet          78.0%
                         AWS::EC2::KeyPair         75.6%
AWS::EC2::VPC          → AWS::EC2::SecurityGroup   72.5%
                         AWS::EC2::Subnet          71.0%
```

**Lambda는 100%, EC2는 90%대에서 멈춘다.** 이건 잡음이 아니라 신호다 — Lambda는 실행
역할이 **없으면 안 되고**, EC2는 기본 VPC·기본 SG를 쓸 수 있다. **분포가 "구조적
필수"와 "관행"을 갈라 보여준다.** Azure에서 VM→NIC이 100%였던 것과 같은 성질이다.

한계도 분명하다. 템플릿이 **299개**(Azure는 1,152개), 앵커가 **22종**(Azure는 43종)이라
훨씬 얇다. 비율만 보면 두 코퍼스가 같은 무게로 읽히므로 그 차이를 coverage에 적었다.

> 표본 편향도 다르게 나타난다. `EC2::KeyPair`가 75.6%인데, CFN 리소스로서의 KeyPair는
> 실무에서 그만큼 흔하지 않다 — AWS 자신의 샘플이라 스타일이 일관된 결과다.

## GCP — **전이되지 않는다** (측정된 부정 결과)

`GoogleCloudPlatform/cloud-foundation-fabric`(Apache-2.0, 커밋 `11c1d248`)의 모듈
**86개**를 Terraform `resource "google_*"` 선언으로 셌다.

```
무조건 리소스 0개인 모듈 : 63 / 86
net-vpc              무조건 []   조건부 [google_compute_network, …]
gke-cluster-standard 무조건 []   조건부 [google_container_cluster, …]
```

**AVM에서 통한 판별자가 여기서는 안 통한다.** Terraform 모듈은 주 리소스에도
`count = var.vpc_create ? 1 : 0`을 걸어 **"만들거나 참조하거나"**를 표현한다. ARM의
`condition`/`copy`와 겉모습은 같은데 뜻이 다르다.

되게 하려면 변수 기본값까지 따라가야 하고, 그건 별도 작업이다. **억지로 맞추지 않고
부정 결과로 남긴다** — 반쯤 읽은 값을 담는 것보다 낫다.

## 그 밖에 두드려 본 것

| 소스 | 규모 | 판정 |
|---|---|---|
| `Azure/ALZ-Bicep` | 컴파일 산출물 551개 | 후보 — 대부분 파라미터 파일이라 선별 필요 |
| `terraform-aws-modules/*` | 최상위 `.tf` 5개씩 | GCP와 같은 `count` 문제가 예상됨 |
| `upbound/platform-ref-aws` | 파일 37개 | 얇다 — 후보 아님 |
| `pulumi/pulumi-awsx` | yaml 92개(대부분 CI) | 선언적 추출 대상 아님 |

## 담은 것

`bundlekb build --source awscfn` — 앵커 22종 · 쌍 720건. 이로써 동시 출현은
**Azure(43앵커) + AWS(22앵커)** 두 프로바이더에서 나온다.
