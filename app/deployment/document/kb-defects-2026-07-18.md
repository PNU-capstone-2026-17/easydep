# KB 내부 결함 조사 (2026-07-18)

`audit-2026-07-18-findings.md`가 **에이전트 답변 품질**(도구 라우팅·누출)을 다뤘다면,
이 문서는 **KB 자체가 자신 있게 틀린 답을 주는 지점**을 다룬다. 네 축(graphkb /
capacitykb / costkb / perfkb)과 도구 계층을 산출물 실측으로 전수 조사했다.

> 전제: 상위 cb-spider의 메모리 버그(`ConvertMBToMiBInt64`)는 **수정되지 않았다고 가정**한다.
> 즉 `memGiB`(미러 62.5) / `memGiBActual`(보정 64) 설계는 유효하다. 별도 조사 결과는
> 아래 「부록: 미러 전제 재확인」 참조.

조사 방법: 코드 읽기 + **산출물 JSON 직접 쿼리로 반증 시도**. 아래 수치는 전부 실측이며,
🔎 표시는 이 문서 작성 중 별도로 재현해 확인한 것이다.

---

## 🔴 치명

### C1. `creation_order`가 대상을 선행 리소스보다 **먼저** 만들라고 답한다 🔎
`graphkb/query.py:109-116`

> ✅ **2026-07-20 수정됨.** 폐포를 **SCC로 축약한 뒤** 위상정렬한다(`_strongly_connected`,
> 반복형 Tarjan). 대상이 마지막이 아닌 노드 **503 → 0개**(3,225개 전수 재확인).
>
> 처음엔 "Kahn이 못 놓은 노드를 순서 미상으로 분리"만 하려 했는데, 그러면 순환에 *딸린*
> 노드까지 함께 쓸려나가 EC2::Instance에서 22개가 순서를 잃었다. SCC 축약으로 바꾸니
> 진짜 순환만 3개 그룹(4·7·4개)으로 묶이고 나머지 순서는 살아난다 — IAM::Role이 9번,
> InstanceProfile이 10번, 대상이 28번으로 **제 위치를 찾았다**.
> 순환 그룹은 "서로 참조해 순서를 정할 수 없습니다"로 본문에 표시하고, 경고도
> **반환 문자열에** 넣는다(예전엔 stderr뿐이라 모델이 알 방법이 없었다).
> 회귀 테스트: `tests/test_query.py::test_dependency_chain_cycle_keeps_target_last`,
> `::test_cycle_does_not_swallow_downstream_order`.

사이클이 감지되면 남은 노드를 `ordered` 뒤에 BFS 발견 순서로 덧붙인다. 대상 노드 자신이
사이클에 걸리면 대상이 `remaining`의 첫 원소가 되어, 진짜 선행 리소스가 대상 **뒤로** 밀린다.
`dependency_chain` docstring(:60)의 "자기 자신이 마지막 원소" 계약이 깨진다.

실측: 체인 보유 3,225개 노드 중 **503개(15.6%)** 에서 대상이 마지막이 아니다.
원인은 의존성 2-사이클 20개(`Lambda::Function ↔ S3::Bucket`, `IAM::Role ↔ IAM::ManagedPolicy` 등).

재현:
```
aws::AWS::Lambda::Function 생성에 필요한 선행 체인 (먼저 만들 것부터):
 11. aws::AWS::SQS::Queue
 12. aws::AWS::Lambda::Function  ← 대상
 13. aws::AWS::Lambda::LayerVersion
 16. aws::AWS::IAM::Role         ← 실제로는 Lambda보다 먼저 필요
 18. aws::AWS::Logs::LogGroup
```
`AWS::EC2::Instance`도 동일(대상 18/30위, 뒤에 IAM::InstanceProfile·IAM::Role).
꼬리에는 **역방향** 노드(AutoScalingGroup 등 Instance에 의존하는 것)까지 "선행"으로 섞인다.

악화 요인 🔎: 사이클 경고가 `print(..., file=sys.stderr)`로만 나가고 **반환 문자열에는 없다.**
```
사이클 경고가 반환문자열에 있나?: False
첫줄: ... 생성에 필요한 선행 체인 (먼저 만들 것부터):
```
LLM은 이 답이 위상순이 아님을 알 방법이 없고, 헤더는 오히려 순서를 단언한다.
README 간판 질의 1-1a("VM 만들려면 뭐부터?")가 직격.

**수정 방향**: 사이클 꼬리를 본문에서 분리해 "순서를 정할 수 없는 항목"으로 별도 표기하고,
대상 노드는 항상 본문 마지막에 둔다. 경고를 **반환 문자열에 포함**한다.

### C2. 빌드 한 번 실패하면 costkb가 영구 정지한다
`costkb/cli.py:88-93` · `costkb/dataset.py:65-69`

`build_dataset()` 결과를 **검증 없이** 즉시 `output/tumblebug-cost.json`에 쓴다. 상위 스키마가
드리프트해 새 프로바이더(`oracle` 등)가 들어오면 `format_audit`이 ⚠️ 한 줄만 찍고 **exit 0**으로
끝나며 파일은 남는다. 이후 `_load_cached`는 `built.exists()`만 보고 그 파일을 골라
`ValidationError`를 던진다 — **번들 36건 폴백 경로가 없다.**

```
UNKNOWN PROVIDER  -> ValidationError 'oracle' is not one of ['aws', ...]
agent_api on poisoned build -> ValidationError   ← recommend_specs()가 예외를 그대로 던짐
```

`costkb/agent_api.py:1-8`의 명시적 계약("예외 대신 한국어 텍스트를 반환한다", "번들 36건이
항상 폴백으로 있어 산출물이 없을 수가 없다")을 정면 위반. `nim_agent/cost_tools.py:88`에도
try/except가 없어 예외가 에이전트 런타임까지 올라간다.

컬럼 리네임은 더 나쁘다: `memory_gi_b`가 사라지면 전 행 None → `specs: []` →
`cli.py:101`의 `priced / len(specs)`에서 **ZeroDivisionError**(파일은 이미 기록된 뒤) →
이후 모든 로드가 `minItems: 1` 위반. **`output/`를 손으로 지우기 전까지 복구 불가.**

**수정 방향**: 빌드가 산출물을 쓰기 **전에** 스키마 검증하고, 실패하면 파일을 쓰지 않고
0이 아닌 종료 코드로 끝낸다. 로드 측은 검증 실패 시 번들로 폴백하고 그 사실을 응답에 밝힌다.

### C3. 번들 모드에서 성능 경고가 100% 침묵한다 — 그리고 그게 기본 상태다
`nim_agent/cost_tools.py:36` · `costkb/specs.json` · `perfkb/agent_api.py:30-37`

> ✅ **2026-07-20 수정됨** (C4와 함께). 조인이 `id` → `(provider, specName)` 폴백으로 걸린다
> (`perfkb/dataset.py:get_by_spec_name`). 번들 36건에 `id`를 손으로 넣지 않은 이유는,
> 폴백이 "id는 있는데 그 리전 성능 레코드가 없는" 미러 케이스까지 함께 덮기 때문이다.
> 회귀 테스트: `tests/test_cost_perf_join.py::test_bridge_joins_bundled_spec_without_id`,
> `::test_bundle_mode_recommendation_carries_warning`.

`_perf_annotate`는 `spec.get("id")`로 조인하는데 번들 36건에는 `id` 키 자체가 없다(실측 0건).
하필 번들 구성이 최악이다 — **t3.micro/small/medium/large/xlarge, Standard_B1s/B2s/B2ms/B4ms,
e2-small/e2-medium**이 들어 있고 이들이 가장 싸서 `sort_by='cost'` 상위를 독점한다.

미러 미빌드 + perfkb 빌드 상태에서:
```
- GCP e2-medium (us-central1): 2 vCPU / 4 GiB, $0.0335/h   ← 공유코어, 경고 없음
- AWS t3.medium (us-east-1):   2 vCPU / 4 GiB, $0.0416/h   ← 버스트,   경고 없음
- AZURE Standard_B2s (eastus): 2 vCPU / 4 GiB, $0.0416/h   ← B계열,   경고 없음
```
`perfkb/parsers/build.py:5-6`이 "t3a.medium을 그대로 1순위로 추천하게 된다"고 막으려던
바로 그 상황이 **기본 설정에서 재현된다.**

문서도 틀렸다. `document/kb-test-queries.md:63`과 `document/cloud-kb-guide.md:980`은 전제로
`python -m perfkb build`만 든다. 실제 전제는 `costkb build`(미러) **와** `perfkb build` 둘 다다.

**수정 방향**: 번들 36건에 `id`를 부여하거나, 조인을 `(provider, specName)` 폴백까지 지원한다.
문서의 전제를 정정한다.

### C4. "성능 문제 없음"과 "성능 정보 없음"이 출력에서 완전히 같다
`perfkb/agent_api.py:30-49` · `costkb/agent_api.py:105-110`

> ✅ **2026-07-20 수정됨.** `perfkb.agent_api.recommend_note`가 다섯 상태를 구분해 돌려주고
> (`warn`/`ok`/`no_record`/`untracked`/`not_built`), 도구 계층이 경고는 `⚠`, 정보 없음은
> `·`로 표시한다. 후보마다 "확인됨"을 적으면 노이즈라, 침묵의 의미는 블록 끝 꼬리말이
> 한 번만 밝힌다(사용자 결정). `costkb`가 하드코딩하던 `⚠`도 콜백 소유로 옮겼다 —
> "모든 주석은 경고"라는 가정이 비경고 주석을 막고 있었다.
> 실측 확인: `ecs.t5-lc1m2.large`에 "alibaba는 성능 신호를 추적하지 않습니다"가 붙는다.
> 저수준 `recommend_warning`은 하위 호환으로 남기되 docstring에 사용 금지를 명시했다.

`recommend_warning`은 (a) 성능이 멀쩡할 때, (b) id가 없을 때, (c) perfkb에 레코드가 없을 때,
(d) 미추적 프로바이더일 때 **전부 `None`**을 반환하고, costkb는 `if note:`로 줄을 안 붙인다.
네 경우가 **바이트 단위로 동일한 출력**을 낸다.

```
- AWS c6a.large (us-east-1):              2 vCPU / 4 GiB, $0.0765/h  ← 검증된 비버스트
- ALIBABA ecs.t5-lc1m2.large (us-east-1): 2 vCPU / 4 GiB, $0.0307/h  ← 성능 데이터 자체가 없음
```
`ecs.t5-lc1m2.large`의 `t5-lc1m2`는 **Alibaba의 버스트 계열**이다. 사용자는 "경고가 없으니
괜찮은 스펙"으로 읽는다.

미추적 프로바이더는 미러 기준 **7,051건 / 73,083건 (9.6%)**, 프로바이더별로 alibaba·tencent·
ibm·ncp·kt·nhn·openstack이 **각각 100% 미커버**. 추천 결과 어디에도 고지가 없다.
`kb-test-queries.md:202`가 공백을 표로 적어두긴 했지만 그건 `perf_instance_profile` 직접 호출
이야기고, **`cost_recommend_specs` 경로에는 고지가 전혀 없다** — 조인이 자동이라 사용자가
물어볼 기회조차 없다.

**가장 위험한 결함이다.** 침묵을 안전 신호로 오독하게 만드는 구조라 자동 경고 기능 전체의
신뢰도를 무효화한다. C3과 뿌리가 같다.

**수정 방향**: `recommend_warning`이 "확인함/정보없음/미추적"을 **구분해 반환**하고, costkb가
정보 없음을 명시적으로 표기한다(예: `· 성능 정보 없음`).

---

## 🟠 중간 — 유형별

### (가) 미러 충실도가 실제로 깨진다

**region 부분 일치** 🔎 (`costkb/dataset.py:135`, `reg in s["region"].lower()`).
Tumblebug `FilterSpecsByRange`는 `AcceleratorModel`/`Description`만 LIKE이고 region은 정확 비교다.
실존 리전 **15쌍이 서로의 부분문자열**이라 이론적 문제가 아니다:
`(centralus, northcentralus/southcentralus/westcentralus)`, `(eastus, eastus2)`,
`(us-east, us-east-1/us-east1/us-east4)`, `(kr, kr1/kr2)`, `(westus, westus3)` …

```
-- region='centralus', vCPU>=4, mem>=8 --
  northcentralus Standard_B4as_v2  $0.150   ← 1위가 다른 리전
  northcentralus Standard_B4s_v2   $0.166
  northcentralus Standard_B4ms     $0.166
  centralus      Standard_B4as_v2  $0.170   ← centralus 진짜 최저가는 4위
-- region='us-east' (IBM 실존 리전), vCPU>=4 --
  alibaba us-east-1 ×4, gcp us-east1 ×1     ← IBM 스펙 0건
```
`tests/test_costkb_dataset.py:127`에 이걸 **의도로 고정한 테스트**가 있으나, conftest가
output_dir을 빈 tmp로 묶어 리전 4개짜리 번들만 보므로 충돌을 구조적으로 못 잡는다.
"부분 일치 편의"와 "미러 불변식" 중 하나를 포기해야 하는데 트레이드오프가 미기록.

**priced_only 기본 True**(`dataset.py:139`)도 이탈 — Tumblebug의 `ELSE 999999`는 가격 미상을
**뒤로 밀 뿐 제외하지 않는다.** kt(85.9% 미가격)·ncp(54.7%)·alibaba(21.7%)에서 자주 갈린다.

그럼에도 `parsers/tumblebug.py:41-46`의 `SOURCE_NOTE`는 "**두 경로의 답이 일치합니다**"를
무조건 단언하고, 이 문장이 `costkb coverage`와 `dataset_note()`로 사용자·에이전트에 노출된다.

### (나) 저신뢰·미상을 확정처럼 말한다

이 저장소가 가장 신경 쓴 축인데 네 군데서 샌다.

1. **`is_burst_bandwidth`가 정성 등급에 `False`를 단언** 🔎 (`perfkb/parsers/details.py:112-119`).
   `details.py:20-21` 주석은 "`Up to N Gigabit`(버스트) 또는 `N Gigabit`(고정) 두 가지뿐"이라고
   실측을 주장하지만 **distinct 값은 72종**이다. 정성 등급 실재:
   `High` 234건 / `Moderate` 192건 / `Low to Moderate` 80건(**t2 전 계열**) / `Low` 24 / `Very Low` 12.
   `startswith("up to")` False → `networkIsBurst=False` → 버스트 경고 줄이 안 붙는다.
   docstring은 "판단 불가면 None"이라고 하는데 실제로는 **False(=문제 없음)를 단언**한다.
   ```
   > perf_instance_profile('aws','t2.medium')
     네트워크: Low to Moderate          ← ⚠ 줄 없음
   ```
   `perfkb/schema.json`의 `networkPerformance` description도 같은 거짓을 반복한다.

2. **Azure 0.8 추론이 `compare`에서 무조건부 단언** (`perfkb/agent_api.py:137-141`).
   `_describe`(:58)는 `conf < 1.0`에 "(이름 규칙 추론, 신뢰도 0.8)"을 붙이는데 `compare`는 안 붙인다.
   `Standard_D2s_v5=보장`이 AWS `BurstablePerformanceSupported`(conf 1.0) 기반 "보장"과
   **글자 그대로 동일**하다. 게다가 Azure `sustainedCpu=True`는 "family가 `standardB`로 시작하지
   않음"일 뿐이고 이게 **33,456건(Azure의 96%)**에 붙는다 — 부정은 이름 근거가 있지만 **긍정은
   근거의 부재**다. `project.py:26-27`이 인정한 "B가 아닌 새 버스트 패밀리를 놓친다"는 경우,
   침묵이 아니라 "보장됨"이라고 적극적으로 오답한다.

3. **`equivalent_types`가 confidence/evidence를 버린다** (`graphkb/agent_api.py:123-124`).
   `f"- {item.id} ({item.provider})"`만 출력. `core::securityGroup → gcp::ComputeFirewall` **0.7**
   (GCP Firewall은 네트워크 단위 규칙이라 인스턴스에 붙는 AWS SG와 등가가 아님)이 0.95짜리
   VPC↔vNet과 **같은 형식**으로 나온다. `creation_order`(:82-86)·`deletion_impact`(:102-104)도
   required/optional 구분(실측 3,002 / 1,899)을 버려 선택적 참조가 필수처럼 섞인다.
   → **capacitykb는 5개 도구 전부 근거를 출력. graphkb는 6개 중 3개가 버린다.**

4. **Azure 280개 타입 전부 `cap_immutable_properties`가 "없습니다"** (`capacitykb/query.py:58-68`).
   azure 파서는 `read_only` 4,704건만 만들고 `create_only`는 **0건**(Bicep 스키마에 플래그 없음).
   메시지는 `"{type_id} 에 변경 불가로 알려진 속성이 없습니다."` — **데이터 부재가 사실 부재로
   읽힌다.** AKS `location`/`dnsPrefix`는 실제로 재생성을 유발하므로 거짓 안심.

### (다) 도구가 서로 상충하는 그림을 준다

**`_COMPARE_AXES`에 `currentGeneration`이 없다** 🔎 (`perfkb/agent_api.py:97-102`).
채움률 실측(AWS 18,564건): `currentGeneration` **100%**, `networkPerformance` 100%,
`clockGHz` 99.6%, `ebs*` 98.8%. Azure `acu`는 **37.7%**. 37.7%짜리는 축인데 100%짜리는 아니다.
```
> perf_compare('aws',['m5.large','m6i.large'])
  상시 CPU: m5.large=보장 / m6i.large=보장
  클럭(GHz): 3.1 / 3.5   …
```
**m5.large는 구세대**인데 한 줄도 없다. 같은 스펙을 `cost_recommend_specs`로 만나면 "구세대"
경고가 붙는다 — 두 도구가 상충한다. 문서 `kb-test-queries.md:73`이 예시로 든 바로 그 질의다.

**GCP 프로파일이 사실상 한 줄** (`perfkb/agent_api.py:62-71`). `_describe`의 필드 목록
(`currentGeneration, clockGHz, networkPerformance, ebs*, acu, diskIops`)에 **GCP가 가진 필드가
하나도 없다.** GCP 실보유는 `maxPersistentDisks`(100%)·`maxPersistentDiskGB`(100%)·
`vendorDescription`(100%). 사람용 `cli.py:120`은 `vendorDescription`을 출력한다 —
**CLI가 에이전트보다 많이 보여준다.** GCP 11,622건(17.9%)의 프로파일 질의가 빈 답이 된다.

### (라) 산문에서 뽑은 단위 값 자체가 틀렸다
`capacitykb/prose.py:128-130` (`_unit_of`) → `:205, :217`

블록의 **첫 번째** 단위 토큰을 반환할 뿐, 그 단위가 추출한 숫자에 걸린 것인지 확인하지 않는다.

| 레코드 | 기록된 단위 | 실제 | 원문 |
|---|---|---|---|
| `RDS::DBCluster.BacktrackWindow` max=259200 | **hours** | seconds | "in seconds … 0 to 259,200 (72 **hours**)" |
| `AppStream::Stack.UserSetting/MaximumLength` max=20971520 | **MB** | characters | "number of **characters** … (20 **MB**)" |
| `RDS::DBInstance.Iops` min=1000 | **second** | IOPS | "operations per **second** (IOPS)" |

`cap_check_value('AWS::RDS::DBInstance','Iops',500)` → `"500 second는 최소 1000 second을(를) 벗어남"`.
BacktrackWindow는 3600배 어긋난 단위다. 알려진 결함 (c)는 "단위를 비교에 안 쓴다"는 것이었고,
이건 **저장된 단위 값 자체가 틀린** 별개 문제다.

### (마) 손상된 산출물이 예외를 던진다
`perfkb/dataset.py:36-42` · `nim_agent/cost_tools.py:36`

`_load_cached`가 `exists()`만 확인하고 `json.loads` + `validate`를 무방비 호출하며
`_perf_annotate`에도 try/except가 없다. `perfkb build`가 쓰기 도중 중단되면 부분 파일이 남고,
그 순간부터 **성능 도구가 아니라 비용 추천 전체가 죽는다.**
```
잘린 JSON         → RAISED: JSONDecodeError
스키마 불일치 JSON → RAISED: ValidationError
```
`cost_tools.py:33-34`가 약속한 "조용히 None(fail-open)"은 파일 **부재**에만 성립한다. C2와 동류.

---

## 🟡 사소

| # | 결함 | 위치 |
|---|---|---|
| m1 | `deletion_impact`에 상한 없음 — VPC → **477줄**, 추이 폐포인데 직접/간접 미구분. 다른 도구엔 전부 limit이 있다 | `graphkb/agent_api.py:99-104` |
| m2 | `search_types`가 관련도 없이 알파벳순 — `vpc` 검색에 `EC2::VPC`가 **9위**, `database`에 `RDS::DBInstance` **미출현**(부분문자열 불일치). azure `Microsoft.AwsConnector/*` 112개가 결과 오염 | `graphkb/agent_api.py:214` |
| m3 | 스키마 검증이 로드마다 **costkb 13.1초 / perfkb 9.9초** 🔎 — 근거("손으로 편집하는 파일이라 오타")는 36건 번들에만 해당. 세션 첫 도구 호출에 그대로 얹힌다 | `costkb/dataset.py:53`, `perfkb/dataset.py:35-42` |
| m4 | 번들 폴백에서 architecture 필터 무력 — 키가 없으면 통과시켜 `arm64` 요청에 x86 36건 전부 반환 | `costkb/dataset.py:138` |
| m5 | "없다"면서 커버리지는 "있다"고 함 — `openstack: 6건`(전량 미가격)을 보여주며 "조건을 조정하세요"라는 **실행 불가능한** 조언 | `costkb/agent_api.py:98-102` |
| m6 | `estimate_monthly_cost`가 입력 무검증 — 환각·음수 단가를 그대로 포맷. 계획 게이트는 코드로 만들면서 여긴 프롬프트 권고에 맡김 | `costkb/agent_api.py:129-141` |
| m7 | `DetailsMismatch` 한 건이 3개 프로바이더 빌드 전체를 막음(잡는 곳이 없음). 현재 값 불일치 0건이라 잠복 | `perfkb/parsers/details.py:65-69` |
| m8 | "성능은 리전 불변"이 거짓 — `aws c8gn.48xlarge`가 me-central-1만 60000, 나머지 24리전 120000. `cli.py:139`가 "성능 값은 동일합니다"를 **명시적으로 거짓 진술**하고, `specs_meeting_ebs_baseline`은 last-wins로 120000 채택 | `perfkb/agent_api.py:83,167-172` |
| m9 | 동점 tie-break 부재 — `sort_by="vcpu"`에서 896 vCPU 동점 중 2배 비싼 것이 2위 | `costkb/dataset.py:40-41` |
| m10 | 스키마가 `provider`는 enum으로 잠그면서 `architecture`는 안 잠금(정반대 정책). `diskSizeGB`의 `-1` 센티널 **37,466건(51.3%)** 이 그대로 실림 — `cost_per_hour`의 `-1`은 정성껏 null로 바꾸면서 | `costkb/schema.json:54-59` |
| m11 | `allowed_values`가 enum 없을 때 기본값을 "허용값 정보"로 제시 — `VolumeType: 기본값 gp2`가 "gp2만 가능"으로 오독 | `capacitykb/agent_api.py:184-193` |
| m12 | capacitykb에 짧은 이름 해석 없음 — `Volume`/`Lambda` 입력이 전부 실패. graphkb는 `display_name`으로 해석되는데 비대칭 | `capacitykb/query.py:30-40` |
| m13 | `_MAX_RULES`에 상한 표현이 `"maximum allowed value is N"` 하나뿐 — "up to"/"no more than"/"maximum of" 미탐 7건. 하한만 뽑고 같은 문장의 상한을 버리는 비대칭 | `capacitykb/prose.py:73-75` |
| m14 | `check_value`가 `min_items`/`max_items`를 안 씀 — 신뢰도 1.0짜리 스키마 제약 **3,887건**이 판정에서 죽어 있음 | `capacitykb/query.py:153` |
| m15 | `read_only` 위반이 신뢰도 필터를 우회(분기가 `if weak:` 앞). 현재 전부 conf 1.0이라 잠복 | `capacitykb/query.py:149-151` |
| m16 | `capacitykb audit`의 "불일치 0건"이 출하된 산문 레코드를 **한 건도** 검증하지 않음 — 표본 24건은 R1이 산문을 버리는 경우와 동일 집합. (라)가 audit을 통과한 이유 | `capacitykb/cli.py:208-253` |
| m17 | `zip(..., strict=False)`가 스키마 드리프트를 조용히 삼킴 — 모듈 docstring의 "크게 실패한다" 약속과 불일치. 현재 42컬럼 정렬이 맞아 무해 | `kbcommon/tumblebug_dump.py:120,143` |
| m18 | `limit <= 0`이 조용히 1이 됨 | `perfkb/agent_api.py:178` |
| m19 | CLI가 cp949 콘솔에서 전 서브커맨드 실패(em dash). 광범위 except가 원인을 삼킴. 저장소 전반 이슈로 추정 | `costkb/cli.py:145,169-174` |

### 문서 수치 오류
- `README.md:141` / `cloud-kb-guide.md:817` — "vCPU 최대 **896**" → 실제 **1920**(gcp `x4-megamem-1920-metal`). `costkb coverage`가 직접 1920을 출력하므로 도구 자신과 모순.
- `README.md:141` — "메모리 최대 **32 TB**" → 실제 32,000 GiB = **31.25 TiB**.
- `README.md:81` / `guide:1027` — "**163개 리전**" → distinct 리전은 **154개**. 163은 (provider, region) 쌍 수.
- `kb-test-queries.md:63` / `guide:980` — perfkb 경고의 전제가 `perfkb build`만으로 적혀 있음 → `costkb build`도 필요(C3).
- `details.py:20-21` — "`Up to N` 또는 `N Gigabit` 두 가지뿐" → **72종**((나)-1).
- `details.py:78-79` — "최상위에 한 번 나온다(실측상 값은 같다)" → 18,564건 **전부 다중 매치**,
  552건은 값이 다름. 다만 `found[-1]`이 항상 최상위와 일치함을 552건 전수 확인 —
  **동작은 옳고 주석의 근거만 틀렸다.** 실제 불변식(Go가 필드를 알파벳순으로 찍어
  `NetworkCards` < `NetworkPerformance`)이 기록돼 있지 않아 다음 사람이 잘못된 전제로 고칠 위험.

---

## 🟢 확인했고 문제 없는 영역

억지 결함이 아님을 보이기 위해 명시한다.

- **정규식 접미사 버그 잔존분 0건** — AWS 18,564건에 깊이 인식 파서를 별도 구현해 대조.
  조회 키 7종(`BaselineBandwidthInMbps`, `MaximumBandwidthInMbps`, `BaselineIops`, `MaximumIops`,
  `NetworkPerformance`, `SustainedClockSpeedInGhz`, `DefaultThreadsPerCore`) 전부 오추출 0.
  Go 맵의 공백 구분자 미스도 전수 스캔 0건.
- **투영에서 조용히 버려지는 행 0건** — 73,083행 전부 `namespace='system'`, `v_cpu<1` 0건,
  메모리 무효 0건. guide:809의 "무효 0"도 정확.
- **가격 센티널** — `0`(6건) + `-1`(4,372건) = 4,378건 전부 null, priced 68,705 = 73,083 − 4,378.
  Tumblebug `CASE WHEN cost_per_hour > 0`과 일치.
- **id 유일성** — costkb 73,083 / perfkb 65,032 전부 유일, 중복 0.
- **`creation_order` ↔ `deletion_impact` 방향 일관성** — 300개 노드 표본에서
  `B ∈ chain(A) ⟺ A ∈ dependents(B)` 위반 **0건**. 자기 루프 0건.
- **`equivalent_types` 매핑 정확도** — 28개 엣지 전수 확인, VPC↔vNet↔ComputeNetwork,
  EKS↔AKS↔GKE, EBS↔disks↔ComputeDisk 등 **전부 정확**(커버리지는 core 13개 중 9개).
- **fail-open 값 판정** — 확정 판정에 쓰이는 conf≥0.8 산문 제약 7건 전부 원문 대조 정확.
  저신뢰(0.6/0.7)는 예외 없이 advisory. **잘못된 단정 violation 경로 없음.**
  센티널 방어(`-1`/`0` 허용)·envelope 병합·veto 큐 모두 정상.
- **Azure `^standardB` 정규식** — 매치 family 4종 전부 진짜 B계열, 175개 family 중 오탐 0,
  spec명이 `Standard_B*`인데 미탐인 것도 0. (추측: `standardBasicAFamily`류가 들어오면 오탐 여지)
- **GCP `IsSharedCpu` 결측 처리** — `sustainedCpu` 커버리지 aws/azure/gcp 전부 **100%**.
  `None`을 `False`로 오독하는 경로 없음.
- **`details` 컬럼** — 73,083행 전부 파싱 성공, 실패·결측 0건.
- **프로바이더 간 비교 차단** — `compare`가 단일 provider 파라미터라 구조적으로 유효.
- **kbcommon** — 태그별 캐시 키, `--refresh`, `.part`+`os.replace` 원자적 교체, 테이블 부재 시
  크게 실패, pgdumplib 미설치 안내 전부 의도대로 동작.
- **번들 폴백의 KeyError** — 없음. `memGiBActual`·`id` 모두 `.get()`으로 안전 처리.
- **문서 수치 대부분 실측 일치** — burst_network AWS 47.9%(8,897/18,564), old_gen 11.2%(2,085),
  Azure ACU 37.7%(13,135/34,846), `kb-test-queries.md`의 graphkb·capacitykb 기대치 전수 재현 일치.

---

## 커버리지의 실제 모양 (참고)

- **capacitykb**: AWS 1,628개 타입 전부 제약 보유. 다만 `required`(12,244)·`mutability`(8,603)·
  문자열 길이(13,048)가 압도적이고 **수치 한도는 3.9%**(min 1,045 + max 787). 산문 추출은
  설명문 있는 숫자형 프로퍼티 **1,341개 중 41개(3.1%)** 만 걸리고, 산출물 46,810건 중
  산문 유래는 **158건(0.34%)**, 44개 타입에 집중. Azure 6,608건은 `mutability` 4,704건이
  전부 `read_only`((나)-4의 원인). 쿼터는 Azure 52건뿐, `type_id`가 붙은 건 10건.
- **표본 품질**: EBS Size/Iops/Throughput ✓, Lambda Timeout ✓, RDS 백업/스케일링 ✓ — 쓸 만함.
  반면 **Lambda.MemorySize는 기본값 128만 있고 128–10240 범위 없음**, S3.BucketName은
  길이·패턴 제약 전무, EC2::Volume은 createOnlyProperties가 스키마에 없어 immutable 빈손.
- **costkb 미가격 편중**: openstack 100%, kt 85.9%, ncp 54.7%, alibaba 21.7% vs tencent 0.1%, aws 2.3%.
- **perfkb 미커버**: alibaba·tencent·ibm·ncp·kt·nhn·openstack 각 100%(합 7,051건 / 9.6%).

---

## 우선순위 판단

기존 `audit-2026-07-18-findings.md`의 P1~P3은 **답변 품질**(라우팅·누출) 문제였다.
이번 건은 성격이 다르다 — **C1·C3·C4는 KB가 자신 있게 틀린 답을 주는 부류**라 위에 둔다.

1. ~~**C3 + C4를 함께**~~ — ✅ 2026-07-20 완료. 뿌리가 같다. 조인이 실패해도, 데이터가 없어도, 미추적 프로바이더여도
   출력이 전부 같아서 **"경고 없음"이 아무 정보도 담지 않는다.** Phase 2의 안전장치가
   기본 설정에서 작동하지 않는다는 뜻이므로 여기부터.
2. ~~**C1**~~ — ✅ 2026-07-20 완료. 단독으로 가장 명확한 버그이고 수정이 국소적이다.
3. **C2 + (마)** — 같은 부류(산출물 손상 시 폴백 부재). 함께 처리.
4. **(나)** — "저신뢰를 확정처럼"은 이 저장소의 핵심 원칙 위반이라 우선순위가 높지만,
   네 건이 서로 다른 파일이라 개별 처리.
5. **(가)·(다)·(라)** — 각각 독립. (가)는 트레이드오프 결정이 먼저 필요하다
   (부분 일치를 버릴지, 미러 불일치를 문서화하고 남길지).
6. 사소 19건 — m3(로드 시간)와 m8(리전 불변 거짓 진술)이 그중 임팩트가 크다.

---

## 부록: 미러 전제 재확인 (2026-07-18)

로컬 `C:\Users\projw\Desktop\dev\cb-spider` HEAD = `4b923730`, 브랜치
`fix/gcp-azure-memory-already-mib` (2026-07-18). **메모리 버그 수정이 이미 커밋돼 있다.**
```diff
 func ConvertMBToMiBInt64(mb int64) string {
-	mib := int(mb * 1000 / 1024)
-	return strconv.Itoa(mib)
+	mib := int64(math.Round(float64(mb) / 1.048576))
+	return strconv.FormatInt(mib, 10)
 }
```
같은 커밋이 `gcp/.../VMSpecHandler.go:75,129`와 `azure/.../VMSpecHandler.go:603`을 항등으로 바꿨다
(`ConvertMBToMiBInt64`는 이제 호출자 0건인 죽은 코드).

**단, upstream master에는 없다** (`git branch -a --contains 4b923730` → 로컬·fork뿐).
따라서 덤프 `v0.12.25`는 수정 전 산출물이고 라이브 MCP도 여전히 버그 값으로 필터링한다 →
**`memGiB = 62.5` 유지가 옳다.** 그리고 수정 후 GCP 경로가 항등이므로 결과는 65536/1024 =
**64 GiB** — 즉 `memGiBActual`은 "추정"이 아니라 **수정이 머지됐을 때의 실제값**이 됐다.

### 재확인에서 나온 두 가지

**(A) 머지되면 보정이 조용히 뒤집힌다.** `costkb/parsers/tumblebug.py:88-89`가 프로바이더만 보고
**무조건** ×1.024를 한다. 수정된 cb-spider로 덤프가 재생성되면 `memGiB`가 64로 들어와
`memGiBActual = 65.536`이 된다. 오류 방향이 뒤집히고, 조용하다.

판별자를 실측했다 — 버그 공식이 ×1000을 하므로 결과 MiB가 항상 1000의 배수다:
```
azure  n=34846  MiB%1000 = 99.1%      gcp  n=11622  MiB%1000 = 97.1%
aws / alibaba / ibm / kt / ncp / nhn / openstack / tencent = 모두 0.0%
```
빌드 시점에 이 비율이 임계치(예: 50%) 아래면 "수정 후 덤프 → 보정 금지"로 크게 실패시킨다.
프로바이더 하드코딩보다 데이터 자체를 보는 편이 맞다.

**(B) 감사 수치가 자기를 낮춰 부른다.** `tumblebug.py:173-176`이 `if mem == int(mem) … elif
<fingerprint>` 체인이라 **정수인데 버그인 값**이 fingerprint에 도달하지 못한다(14,448건:
`azure 125.0→128.0` 3,617, `azure 250.0→256.0` 3,130, `gcp 125.0→128.0` 730 …).
`README.md:104-106`·`cloud-kb-guide.md:784-788`·`tumblebug.py:26-28`의 **gcp 77.6% / azure 64.2%**는
하한이며 실제는 **gcp 97.1% / azure 99.1%**다. 보정 자체는 옳고 근거표만 틀렸다.

**(C) 블랜킷 보정 결정은 실측으로 버틴다.** 이미 맞는 정수가 부풀려지는 실패 모드
(`8 → 8.192`)는 46,468건 중 **0건**. 정수 GCP/Azure 값은 ×1.024에서 전부 다른 정수로 떨어진다.
비정수로 남는 646건은 원래 GB 단위 크기라 여전히 옳은 미러다
(`gcp f1-micro 0.5849609375 → 0.599`). 다만 `tumblebug.py:92`의 `round(corrected, 4)` 때문에
`0.6`이 아니라 `0.599`로 표시되는 표시 경로 잡티가 있다.

**(D) 미추적 5개 프로바이더가 해소됐다.** alibaba `MemorySize*1024`, ibm `memValue*1024`,
openstack·kt·nhn은 OpenStack `RAM`(이미 MiB) 항등 — **전부 정상**. 즉 "보정하지 않음"이
가정이 아니라 확인된 사실이 됐다. 다만 ibm은 스펙 *이름*을 정규식으로 파싱해 메모리를 얻는다
(`bx2.4x16` → 16). 별건으로 `ncp/resources/PriceInfoHandler.go:446`이 GB 값을 `MemSizeMiB`에
그대로 넣는 버그가 있으나, 우리는 `spec_infos`를 읽지 가격 핸들러를 읽지 않으므로 경로 밖이다.

### capacitykb에는 MB-MiB 문제가 없다 (확인된 부정)
전수 확인 결과 capacitykb에 **단위 변환 산술이 아예 없다**. `unit`은 `model/records.py:57,73,90`에서
`str | None`로 저장·왕복만 하고, `prose.py:49`의 정규식은 토큰을 캡처만 하며,
`query.py:104-107`·`agent_api.py:56,80`은 메시지에 문자열로 이어붙일 뿐이다.
**MB-MiB는 costkb 단독 이슈다.** capacitykb의 단위 관련 약점은 위 (라)(저장된 단위 값 오류)와
기존에 알려진 "`check_value`가 단위 인식 없이 비교" 두 가지다.
