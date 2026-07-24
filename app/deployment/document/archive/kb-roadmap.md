# 지식베이스 로드맵 — research.md 목표 2 대비 갭과 다음 단계

이 저장소는 [`research.md`](research.md)의 **목표 2**를 맡는다:

> 2) 클라우드 환경 특성(**클라우드 인스턴스 성능, 비용** 등), 클라우드 리소스의 특성
> (**리소스 용량, 리소스 의존성** 등)을 고려한 클라우드 네이티브 환경 가이드라인 제공

(목표 1 = 요구사항 검증·피드백 → `bert`·`usecase`·`requirement-analysis` 트랙,
목표 3 = 단계별 산출물 생성 → `demo`·`langgraph-kube-test`·`instant-kube-test` 트랙)

---

## 1. 현재 상태 — 명시된 4개 중 3개 완료

| research.md 문구 | 담당 | 규모 | 상태 |
|---|---|---|---|
| 리소스 **의존성** | `graphkb` | AWS 1,631 / Azure 3,382 / GCP 95 / 매핑 28 | ✅ |
| 리소스 **용량** | `capacitykb` | AWS 46,810 / Azure 6,608 / 쿼터 52 | ✅ |
| **비용** | `costkb` | 미러 73,083 (10 프로바이더 · 163 리전) | ✅ |
| **인스턴스 성능** | — | — | ❌ **갭** |

**성능이 유일한 명시적 갭이다.**

---

## 2. 이 갭은 지금 실제로 틀린 답을 내고 있다

추상적 결함이 아니다. 실측:

```
costkb에 "웹서버급(vCPU 2, 메모리 4GiB) 최저가, AWS" 질의 → 상위 5개

   t3a.medium  $0.0246/h   ⚠ 버스트
   t3a.medium  $0.0376/h   ⚠ 버스트
   t3a.medium  $0.0376/h   ⚠ 버스트
   t3a.medium  $0.0376/h   ⚠ 버스트
   t3a.medium  $0.0408/h   ⚠ 버스트
```

**5개 전부 버스트 인스턴스다.** t3a는 CPU 크레딧이 소진되면 baseline(vCPU당 20%)으로
떨어진다. 상시 부하 API 서버에 이걸 1순위로 주는 건 싼 게 아니라 **틀린 가이드**다.

- 최저가 상위 100개 중 **37건**이 버스트·구세대·공유CPU
- AWS 인스턴스의 **11.2%**(2,085건)가 이미 구세대(`CurrentGeneration: false`) — `m5.large` 포함

즉 **costkb 혼자서는 "가이드라인"이 아니라 "가격표"다.** research.md가 "비용, **성능** 등"을
나란히 쓴 이유가 이것이다.

---

## 3. ⭐ 소스는 이미 손에 있다

costkb 미러를 만들 때 `spec_infos`의 **`details` 컬럼을 버렸다.** 그 안에 CSP 원본 응답이
통째로 들어 있다 — **73,083행 100% 채움**, 캐시된 34MB 덤프에 이미 존재.

```
AWS (= describe-instance-types 원본, 자격증명 불필요)
  BurstablePerformanceSupported : true                       ← 버스트
  CurrentGeneration             : false                      ← 세대
  ProcessorInfo                 : {SustainedClockSpeedInGhz:2.5}
  EbsInfo                       : {BaselineBandwidthInMbps:347, MaximumBandwidthInMbps:2085}
  NetworkInfo                   : {NetworkPerformance: "Up to 5 Gigabit"}   ← "Up to" = 버스트
  VCpuInfo                      : {DefaultThreadsPerCore:2}

Azure
  ACUs                          : 160        ← Azure Compute Units (상대 성능 지표)
  UncachedDiskIOPS              : 3200
  CombinedTempDiskAndCachedIOPS : 4000
  AcceleratedNetworkingEnabled  : True
  PremiumIO / vCPUsPerCore / MaxNetworkInterfaces
  Family                        : standardDSv3Family         ← B* = 버스트 계열

GCP
  IsSharedCpu                   : false      ← 공유 CPU
  MaximumPersistentDisks        : 128
  MaximumPersistentDisksSizeGb  : 263168
```

**새 소스 조사가 필요 없다.** costkb와 같은 미러 원칙(라이브 MCP와 같은 세계)이 그대로 적용된다.

### 실측한 지문 커버리지

| 신호 | 채움 | 비고 |
|---|---|---|
| AWS `BurstablePerformanceSupported` | 18,564 / 18,564 (100%) | true 609건(3.3%) |
| AWS `CurrentGeneration` | 18,564 / 18,564 (100%) | false **2,085건(11.2%)** |
| GCP `IsSharedCpu` | 11,622 / 11,622 (100%) | true 182건(1.6%) |
| Azure `ACUs` | 13,135 / 34,846 (**37.7%**) | 값 160~330 |
| Azure `Family=standardB*` | 1,390 (4.0%) | 버스트 계열 |

---

## 4. 정직한 한계 — 먼저 못 박을 것

과대 약속하면 설계가 무너진다. 실측으로 확인한 제약:

### 4-1. 프로바이더 간 성능 비교는 **불가능하다**
ACU는 Azure에만, 클럭은 AWS에만 있다. **"AWS m5.large vs Azure D2s_v3 중 뭐가 빨라?"는
답할 수 없다고 말해야 한다.** perfkb는 *같은 프로바이더 안에서의 비교*와 *함정 경고*로
범위를 좁힌다.

### 4-2. Azure ACU는 37.7%만 있고, **결측이 설명되지 않는다**
Phase 1 실측 결과 "신형이라 없다"는 가설은 **틀렸다**. 세대별로 섞여 있다:

| 세대 | ACU 있음 | ACU 없음 |
|---|---|---|
| v2 | 1,327 | 3,663 |
| v4 | 3,505 | 2,363 |
| v5 | 2,564 | 5,905 |
| v6 | 3,051 | 5,420 |
| v7 | 0 | 1,960 |

패밀리별로 산발적이다(v7만 전무). **설명하려 들지 말고 "모른다"로 처리한다** —
capacitykb의 3-상태 판정(fail-open)을 그대로 쓴다. ACU 비교는 *양쪽 다 값이 있을 때만*
가능하다고 도구가 밝혀야 한다.

### 4-3. 버스트를 프로바이더 간에 통일하려는 유혹을 조심할 것
이 셋은 **다른 개념**이다. 억지로 한 필드로 묶으면 거짓말이 된다.

| | 메커니즘 | 근거 | 신뢰도 |
|---|---|---|---|
| AWS t계열 | CPU 크레딧 소진 시 baseline 저하 | `BurstablePerformanceSupported` (명시 필드) | 1.0 |
| Azure B계열 | 크레딧 모델 (AWS와 유사) | `Family` 이름 패턴 **추론** | 0.8 |
| GCP 공유CPU | vCPU 자체를 공유 (크레딧 아님) | `IsSharedCpu` (명시 필드) | 1.0 |

→ 사용자가 실제로 묻는 건 **"상시 성능이 보장되나?"**다. 필드명을 `burstable`이 아니라
`sustainedCpu: bool | null` + `note`(왜 그런지) + `evidence`/`confidence`로 둔다.
capacitykb 패턴이다.

### 4-4. `details` 파싱이 취약하다
값이 JSON이 **아니다**. Go의 `%v` 포맷이다:
```
{EbsOptimizedInfo:{BaselineBandwidthInMbps:347,...},EbsOptimizedSupport:default}
{NetworkCards:[{...,NetworkPerformance:Up to 5 Gigabit}],NetworkPerformance:Up to 5 Gigabit}
```
따옴표가 없고, 값에 공백까지 있고(`Up to 5 Gigabit`), 중첩·배열이 섞인다. **표준 JSON
파서로 못 읽는다.**

→ 전체 파싱은 위험. **필요한 키만 정규식으로 뽑고 실패하면 null**(fail-open). 뽑는 키를
소수로 유지하는 게 안전장치다. Azure/GCP는 flat key-value라 훨씬 쉽다.
※ Azure는 같은 키가 두 번 나오는 경우가 있다(`MaxDataDiskCount`) — 실측상 값이 같아
안전하게 dedupe 가능하지만, **값이 다르면 크게 실패**시킬 것.

---

## 5. perfkb 계획

**결정: 별도 패키지** (성능과 가격이 `spec_infos`의 같은 행에서 나오지만, "하나에 막 섞지
말 것" 원칙과 research.md의 항목 분리를 따른다. 조인 키는 이미 있다 — `aws+region+spec`.)

### 구조
```
perfkb/
├── __init__.py / __main__.py
├── schema.json
├── dataset.py            # output/tumblebug-perf.json (없으면 "먼저 build" 안내)
├── agent_api.py
├── cli.py
└── parsers/
    ├── details.py        # Go %v 포맷 → dict (정규식, fail-open) ← 테스트 대부분 여기
    └── project.py        # 행 → 성능 레코드 (프로바이더별 투영)
nim_agent/perf_tools.py    # @function_tool 껍데기 (perfkb는 nim_agent를 모른다)
```

**graphkb/capacitykb와 달리 번들 폴백이 없다** — costkb처럼 미러이므로 `build`가 전제다.
(costkb는 손 큐레이션 36건이 있어서 폴백이 가능했다.)

### 데이터 모델 (초안 — Phase 1 실측으로 확정)

| 필드 | 출처 | 비고 |
|---|---|---|
| `id` | `id` | **costkb 조인 키** (`aws+us-east-1+t3.medium`) |
| `provider` / `specName` | | |
| `sustainedCpu` | 4-3절 참고 | `bool \| null` + `note` + `evidence`/`confidence` |
| `currentGeneration` | AWS `CurrentGeneration` | AWS만 명시. 나머지 null |
| `clockGHz` | AWS `ProcessorInfo` | AWS만 |
| `threadsPerCore` | AWS `VCpuInfo` / Azure `vCPUsPerCore` | |
| `networkPerformance` | AWS `NetworkInfo` | 원문 유지(`"Up to 5 Gigabit"`) |
| `networkIsBurst` | 파생 | `"Up to"`로 시작하면 true |
| `ebsBaselineMbps` / `ebsMaxMbps` | AWS `EbsInfo` | **baseline이 진짜 성능** |
| `ebsBaselineIops` / `ebsMaxIops` | AWS `EbsInfo` | |
| `diskIops` | Azure `UncachedDiskIOPS` | |
| `acu` | Azure `ACUs` | **Azure 내부에서만 비교 가능** |
| `acceleratedNetworking` | Azure | |
| `maxPersistentDisks` | GCP | |

**evidence/confidence를 넣는다** — costkb(미러라 출처 균일)와 달리, 같은 `sustainedCpu`가
명시 필드(1.0)에서 오기도 하고 이름 패턴 추론(0.8)에서 오기도 한다. capacitykb와 같은 이유.

### Phase 1 — 파서 + 투영 (⭐ 실측 먼저)
`details.py` 정규식 추출 + `project.py` 투영. **동시에 실측할 것**:
- Azure `Family` 채움율 (B계열 판정의 전제)
- GCP에 세대 정보가 있나 (`Name` 패턴 외에)
- Azure 중복 키 중 **값이 다른 경우가 있나**
- `ACUs` 없는 62%가 어떤 것들인가 (구형? 특수 계열?)

실패하면 여기서 멈추고 재상신.

### Phase 2 — ⭐ costkb 조인 (여기가 핵심)
새 도구를 늘리는 것보다, **`cost_recommend_specs`가 t3a.medium을 1순위로 줄 때 경고를
함께 주는 것**이 research.md의 "선택할 수 있는 가이드라인"에 직결된다.

```
- AWS t3a.medium (us-east-1): 2 vCPU / 4 GiB, $0.0246/h
  ⚠ 버스트 인스턴스 — CPU 크레딧 소진 시 baseline으로 저하. 상시 부하엔 부적합.
```

**조인은 도구 계층(`nim_agent/cost_tools.py`)에서 한다.** KB끼리는 서로 import하지 않는다
(graphkb·capacitykb도 그렇다). perfkb 산출물이 없으면 경고 없이 동작(fail-open).

> **왜 별도 도구로 두지 않나**: "추천받은 뒤 perf 도구도 부르세요"는 프롬프트 지시인데,
> 이 프로젝트는 그게 안 통한다는 걸 두 번 확인했다(계획 게이트·`cost_estimate_monthly`
> 건너뛰기). 경고는 **모델이 안 물어봐도 나와야** 한다.

### Phase 3 — 비교·필터 도구
`perf_compare`(같은 프로바이더 안에서만), `perf_instance_profile`, EBS baseline 기준 필터.

### 테스트
- `test_perfkb_details.py` — **대부분 여기**. Go `%v` 문자열 fixture로 정규식 검증.
  깨진 입력 → null(fail-open), Azure 중복 키(같은 값 OK / 다른 값 실패)
- `test_perfkb_projection.py` — 3-프로바이더 투영, `sustainedCpu`의 evidence/confidence 구분
- `test_cost_perf_join.py` — t3a.medium 추천에 경고가 붙는가, perfkb 없으면 조용히 동작하는가

---

## 6. 대응할 질의

### 지금 못 답하는 것 (perfkb가 생기면 답함)
```
t3.medium을 상시 부하 API 서버로 써도 돼?        → 버스트 경고
사용자 1000명 API 서버 추천해줘                  → 버스트 아닌 것 (지금은 t3a가 1순위)
m5.large가 최신 세대야?                          → 아니오 (구세대 11.2%에 해당)
EBS 처리량 500MB/s 필요한데 뭘 써야 해?          → baseline 기준 필터 (Maximum은 버스트)
이 인스턴스 네트워크 대역폭 얼마야?              → "Up to 5 Gigabit"이 버스트임을 명시
Azure에서 같은 값이면 어떤 게 성능이 좋아?       → ACU 비교 (37.7%만 가능함을 명시)
가장 싼 웹서버 추천해줘 (함정)                   → 최저가 + 버스트 경고 동반
```

### 답할 수 없다고 말해야 정답인 것
```
AWS랑 Azure 인스턴스 성능 비교해줘               → ⚠ 프로바이더 간 비교 불가 (4-1절)
이 Azure 인스턴스 ACU 얼마야? (62% 케이스)       → ⚠ 데이터 없음 (fail-open)
```

이 질의들은 [`kb-test-queries.md`](kb-test-queries.md)에 합류시킨다.

---

## 7. sizingkb — Phase 0 (조사 먼저)

**결정: 우리 책임.** "1000명 → 2vCPU"는 요구사항 분석이 아니라 클라우드 환경 지식이다.

### 왜 중요한가
지금 `agent.py`는 이렇게 지시한다:
> "앱 요구사항을 vCPU/메모리 최소치와 노드 수로 **직접 추론**해 사이징한 뒤…"

즉 **KB 근거가 0인 구간**이다. 파이프라인을 보면:
```
"사용자 1000명 REST API"  ──[근거 없음 · 환각]──▶  vCPU 2, mem 4  ──[costkb · 근거 있음]──▶  $30/월
```
**앞단이 틀리면 뒷단이 아무리 정확해도 답이 틀린다.** research.md 문제 1(환각)이 정확히
여기 남아 있다.

### 그런데 다른 세 KB와 성격이 다르다
graphkb·capacitykb·costkb는 **공개 스키마에 사실이 있다**. 사이징은 **사실이 아니라 판단**이다
— "1000명 = 2vCPU"는 워크로드 성격(요청당 CPU 시간, 캐시 히트율, 언어 런타임)에 따라 10배
넘게 갈린다. **"정답"을 담는 KB를 만들면 그게 바로 환각을 권위 있게 포장하는 것이다.**

### Phase 0에서 조사할 것
1. **덤프에 다른 테이블이 있나** — 우린 `spec_infos`만 봤다. Tumblebug의 추천 정책 테이블이
   있다면 1차 후보다 (`evaluation_score01..10`은 전부 -1로 죽어 있음을 이미 확인)
2. 벤더 공식 sizing guide에 **숫자**가 있나 (대부분 정성적일 것으로 예상)
3. Terraform/Helm 공개 모듈의 **기본값** — "실무 관행"의 증거는 되지만 "정답"은 아님
4. Kubernetes resource requests 권장값 (CNCF/공식 문서)

### 유력한 방향 (조사 결과에 따라)
"정답"이 아니라 **참조점(reference point)**을 주는 것:
- ❌ "1000명이면 vCPU 2입니다" (근거 없는 단정)
- ✅ "이 스펙의 벤더 명시 권장 용도: 개발/테스트, 저트래픽 웹서버" (사실)
- ✅ "사이징은 지식베이스 근거가 아닌 **추정**입니다. 부하 테스트로 검증하세요" (정직)

**조사 후 재상신.** 소스가 안 나오면 KB를 만들지 않고, 대신 답변에 "이 사이징은 추정"임을
드러내게 하는 것으로 끝낸다 — 그게 정직하다.

---

## 8. bundlekb — 후보 (우선순위 낮음)

research.md 문제 2: *"특정 리소스를 선택하는 경우 **연계되는 다양한 리소스 군을 획득**할 수
있어야"*

지금 graphkb는 `AWS::EC2::Instance` 선행 체인으로 `KMS::ReplicaKey`, `S3Tables::Table`까지
준다(23개 노드에서 사이클 경고도 뜬다). **"가능한 것"과 "실제로 필요한 것"을 구분 못 한다.**
`required_only` 옵션이 부분적으로 완화할 뿐이다.

소스 후보: cb-tumblebug의 `create_infra_dynamic` — 실제로 VM 하나 띄울 때 만드는 리소스 군.
**조사 필요.** perfkb·sizingkb 이후로 미룬다.

---

## 9. 순서

| # | 작업 | 근거 |
|---|---|---|
| 1 | **perfkb Phase 1** (파서 + 투영 + 실측) | 명시 갭 · 소스 확보 · 실패하면 조기 중단 |
| 2 | **perfkb Phase 2** (costkb 조인 = 함정 경고) | 틀린 추천을 실제로 고치는 지점 |
| 3 | **perfkb Phase 3** (비교·필터 도구) | |
| 4 | **sizingkb Phase 0** (조사) | 환각 노출 최대 구간이나 소스가 불투명 |
| 5 | bundlekb 조사 | |

병행 가능한 잔여 작업: capacitykb Phase 2(GCP/Tumblebug 제약), Neo4j 라이브 적재 검증,
도구 선택 실측([해설서 14-7절](cloud-kb-guide.md)에서 미룬 것).
