# 클래스 설계 실행 증거와 원자적 수락 개선안

## 1. 문서 상태와 결정

이 문서는 클래스 다이어그램 생성부의 목표 계약과 격리 프로토타입의 구현·검증 결과를 함께
기록한다. 현재 운영 실행 계약은 [클래스·시퀀스 설계 생성 로직](class-design-pipeline.md)에 있고,
이 문서의 프로토타입은 아직 운영 경로를 대체하지 않는다.

2절부터 17절까지의 상세 effect·guard·witness 계약은 격리 실험에서 검증한 강한 계약이다.
최초 운영 통합의 필수 범위는 아니며, 현재 작업 우선순위와 cross-stage revision의 수용 조건은
[전역 사용자 피드백과 revision 개선안](feedback-decision-revision-plan.md)을 따른다.

개선의 우선 목표는 LLM 토큰이나 호출 수를 줄이는 것이 아니다. 다음 두 종류의 결함을 줄이는
것이 목표다.

- 한 LLM 요청이 서로 다른 설계 책임을 동시에 결정하면서 만드는 최초 후보 결함
- 한 부분을 수리할 때 이미 올바른 다른 부분까지 다시 생성해 만드는 회귀 결함

이를 위해 다음 결정을 채택한다.

1. LLM의 생성 책임은 operation, call structure, argument binding으로 분리한다.
2. 분리한 중간 결과는 각자의 제한된 계약에 대해서만 `VALIDATED`로 표시한다.
3. operation catalog만으로 실행 가능성을 주장하지 않는다.
4. 각 유스케이스에 대해 실제로 성립하는 call·binding 조합을 execution witness로 확보한다.
5. 같은 catalog revision을 참조하는 모든 witness가 검증됐을 때 catalog와 behavior 전체를
   하나의 `AcceptedBehaviorModel` revision으로 원자적으로 수락한다.
6. 수락 이후 클래스·시퀀스·API 입력 산출은 새 설계 판단이 없는 순수 변환이어야 한다.
7. repair는 오류를 발견한 단계가 아니라 위반된 계약을 소유한 단위에만 적용한다.

핵심 원칙은 **작게 생성하고, 명시적으로 검증하며, 의존 단위 전체를 함께 수락하는 것**이다.

### 1.1 격리 프로토타입 상태

방법론의 첫 검증 구현은
[`app/design/services/executable_behavior`](../app/design/services/executable_behavior/README.md)에
분리했다. 현재 운영 `class_diagram` 그래프와 다른 디렉터리이며 MySQL, LLM provider, UI에
연결하지 않는다. 운영 경로가 선택할 분기가 없으므로 feature flag도 추가하지 않았다.

이 프로토타입은 실제 모델 성능 실험보다 다음 계약을 먼저 검사한다.

- operation, catalog, call structure, binding, witness의 책임과 입력 snapshot이 분리되는가
- 로컬 `VALIDATED` 결과가 실행 가능하다는 전역 성공으로 오인되지 않는가
- 모든 UC witness가 같은 catalog·scenario에서 성립할 때만 원자적으로 `ACCEPTED`가 되는가
- payload가 검증 뒤 변경되거나 다른 snapshot 결과가 섞이면 downstream에서 거부하는가
- binding 오류가 operation 전체 재생성으로 자동 확장되지 않는가
- 동일 input·candidate·finding repair 상태와 공유 예산 초과가 반복 호출 전에 종료되는가
- 수락 뒤 `BCEModel` 투영에 새 설계 판단이 들어가지 않는가

현재 테스트는 scripted 후보를 사용한다. prompt 품질, 실제 LLM 결함률, 영속 checkpoint,
wall-time·token까지 포함한 운영 예산, scenario의 include/extend와 모든 outcome 의미론은 다음
단계이며, 이 프로토타입의 `ACCEPTED`를 곧바로 운영 수락으로 해석하지 않는다.

### 1.2 실제 LLM 격리 검증 결과

2026-09-10에 앱 `522d73e6-3aee-42af-a787-15a22ab364b0`의 11개 UC와 13개
actor-entry 실행 그룹을 `@cf/zai-org/glm-5.3-flash`로 검증했다. 실행기는
[`scripts/run_executable_behavior_llm_experiment.py`](../scripts/run_executable_behavior_llm_experiment.py)이며,
운영 graph와 DB에는 쓰지 않고 `.easydep/experiments` 아래에만 checkpoint를 저장한다.

최초 전역 inventory 요청은 2개의 물리 호출이 각각 출력 한도 8,192토큰을 모두 추론에
사용하고 content 0자로 종료됐다. 전체 UC를 한 요청에서 처리하는 것만으로는 이 모델이
구조화 출력을 시작하지 못했다. inventory를 UC별 fragment로 나누고
`reasoning_effort=low`로 제한하자 각 최초 요청은 약 4~14초에 JSON을 반환했다.

실제 수락 흐름은 다음과 같다.

    UC별 inventory fragment 완전 검증
      → 같은 이름의 class/type 충돌만 개별 조정
      → Entity 관계 충돌만 개별 조정
      → 전역 inventory 검증
      → UC별 operation 완전 검증
      → sourceability 사전 검사
      → 공유 operation signature 충돌만 개별 조정
      → catalog 검증
      → UC별 call structure 검증
      → 유한 source binding
      → UC별 execution witness
      → 11개 witness 원자적 ACCEPTED

최종 결과는 class 33개, DataType 25개, operation 59개, call 57개, collaboration 13개다.
저장한 `AcceptedBehaviorModel`을 다시 읽어 순수 물질화한 digest와
`class-model.json`의 digest가 같음을 확인했고, 405줄의 `class-diagram.puml`을 만들었다.
기록된 checkpoint 계보는 물리 호출 59회, 입력 110,712토큰, 출력 21,977토큰이다.
중간에 수동 중단한 medium operation 4건과 그 schema-repair 요청, 관계 조정 1건은 이
합계에 없으므로 실험 디렉터리의 `run-notes.json`에 별도로 기록했다.

이 결과는 개선 계약의 실행 가능성을 검증한 것이며 운영 경로의 drop-in 검증은 아니다.
기존 운영 validator는 다중 actor-entry UC의 collaboration ID와 prototype source-ref 표기가
운영 저장 표기와 달라 finding을 보고한다. 또한 독립 inventory fragment가 서로 다른 이름으로
같은 개념을 만들 가능성은 아직 의미 중복 검사 대상이다. 운영 연결 전에는 순수 projection
adapter와 semantic duplicate review가 필요하다.

### 1.3 운영 통합의 선행 경계

이 프로토타입을 운영 생성 경로에 연결하기 전에
[전역 사용자 피드백과 revision 개선안](feedback-decision-revision-plan.md)의 운영
`Question`·`Decision`·`RevisionPlan` 경계와 첫 `UC 명세 → class bundle → sequence` 수직 경로를
확정한다. 사용되지 않은 범용 `ChangeSet` prototype은 제거했다. 클래스 단계에서 발견한
specification gap이 operation repair로 처리되지 않고 실제 요구사항 owner의 새 revision으로
돌아가야 하기 때문이다.

이 결정은 격리 프로토타입의 구조·validator·materializer 작업을 폐기하거나 중단한다는 뜻이
아니다. 이들은 첫 수직 경로의 class bundle producer와 reviser로 유지한다. 다만 다음 항목은
피드백 경계가 검증될 때까지 운영 통합의 선행 작업으로 확대하지 않는다.

- effect·obligation ontology의 추가 확장
- 클래스 생성기 내부의 독립 질문·revision 체계
- 모든 execution group을 위한 추가 봉인 추상화
- 불안정한 운영 경로를 전제로 한 모델별 세부 latency 최적화

피드백 프로토콜은 별도 범용 workflow engine으로 완성한 뒤 적용하지 않는다. 클래스와 시퀀스를
첫 실제 소비자로 연결하면서 cross-stage revision, stale downstream과 재개 불변식을 검증한다.

1.6절과 1.7절은 격리 실험의 구현·결과 기록이다. 기존의 상세 effect·guard·별도 witness 계약은
후속 품질 강화 후보이며 첫 운영 통합 gate가 아니다. 첫 통합은 class structure와
Collaborations의 동일 revision, 각 call의 필수 인자를 가용하고 타입 호환되는 source에 연결하는
최소 binding 검사, deterministic sequence projection을 요구한다. 성공·실패 outcome의 전체
도달성, derived object의 완전한 field source와 cross-root guard 함의는 첫 피드백 수직 경로를
완료한 뒤 필요성과 비용을 다시 평가한다.

### 1.4 검증 가능한 의미 리뷰 계층

결정론적 validator가 잡을 수 없는 의미 결함만 제한된 책임 범위에서 검토한다.

| 리뷰 지점 | 검토 대상 | HIGH finding의 repair 소유자 |
|---|---|---|
| 전역 inventory 검증 직후 | 같은 개념의 다른 이름, 불필요한 Boundary/Control 분절, Entity 수명·필드·식별자, 관계 의미·다중성, 누락된 공유 개념 | `inventoryFragment`의 UC ID 또는 공유 충돌의 `inventoryResolution` key |
| UC 로컬 effect 계약 직후 | scenario 의도, BCE operation 소유권, operation 의미 중복·책임 혼합, parameter 의미, 성공·실패·extension 누락, Entity 상태 변경 소유권 | `operationFragment`의 UC ID |
| 봉인 fragment 통합 직후 | 로컬 봉인과 충돌하는 공유 operation 계약 또는 통합 회귀 | 영향받은 `operationFragment`의 UC ID |

리뷰어는 `{decision, findings[]}`만 반환하며 patch나 대체 모델을 생성하지 않는다. 각 finding은
`ruleId`, `predicateKey`, `category`, `severity`, `ownerStage`, `ownerIds`,
`obligationIds`, `targetRefs`, `evidence`, `expected`, `observed`, `message`를 포함한다.
`targetRefs`와 `evidence`는 코드가 현재 snapshot에서 만든 typed evidence index의
`{kind, ref}`를 정확히 복사해야 한다. 문자열 포함 여부나 비슷한 이름은 근거로 인정하지 않는다.

LOW와 MEDIUM은 기록만 한다. HIGH는 곧바로 설계 결함으로 확정하지 않고 별도 adjudication에서
`CONFIRMED`, `REFUTED`, `UNRESOLVED`로 분류한다. `CONFIRMED`만 repair를 허용한다.
`REFUTED`는 reviewer false positive 지표로 남기되 설계와 repair 예산을 변경하지 않는다.
`UNRESOLVED`는 증거 계약이 부족한 것이므로 임의 수리하지 않고 명시적으로 실패한다.

finding identity는 가변적인 설명 문구나 현재 근거 문장 대신 rule, predicate, category, owner,
obligation, target 좌표를 해시한다. 따라서 같은 UC에서 연속으로 발견된 “capacity 복구 누락”과
“Control 상태 변경”을 같은 결함의 반복으로 오판하지 않는다. 상태 변화는
`REPORTED → CONFIRMED/REFUTED/UNRESOLVED → PATCHED → VERIFIED_RESOLVED/STILL_OPEN/REGRESSION`
ledger로 기록한다.

확인된 behavior 결함은 fragment 전체를 다시 생성하지 않는다. patch는 `baseDigest`, stable
operation target, `expectedDigest`, 허용 action, `findingIds`를 포함하는 compare-and-swap
계약이다. 코드가 patch를 적용한 뒤 operation validator와 로컬 effect 계약, 로컬 리뷰를 다시
통과해야만 재봉인한다. 같은 HIGH finding digest가 반복되거나 owner당 최대 두 번의 수리 한도를
넘으면 불완전한 결과를 수락하지 않는다.

Inventory와 behavior review는 서로 다른 JSON schema를 사용한다. 이 구분은 behavior review가
`ENTITY_LIFECYCLE`처럼 inventory 전용 category를 반환하는 오류를 provider의 구조화 출력 검증
단계에서 거부한다. 리뷰 입력의 `ownerScopes`에는 실제 fragment가 선언한 class, relationship,
operation 참조를 넣는다. 전역 behavior 리뷰는 모든 로컬 의미를 다시 심사하지 않고
`SHARED_CONTRACT_REGRESSION`만 허용한다.

### 1.5 GPT-OSS 120B 실제 실행 결과

2026년 9월 11일 같은 앱 snapshot과 `openai/gpt-oss-120b`로 격리 실행했다. 운영 graph와 MySQL에는
쓰지 않았고 `.easydep/experiments/executable-behavior-oss120b-app522-003` 아래의 checkpoint만
변경했다. 구현과 리뷰 계약을 실행 중 보완하며 실패 단계부터 재개했으므로 다음 누적치는 깨끗한
단일 cold run의 모델 간 A/B가 아니라 개발·재개 비용을 포함한다.

| 지표 | GLM 5.3 Flash 기준 실행 | GPT-OSS 120B 개선 실행 |
|---|---:|---:|
| 최종 상태 | `ACCEPTED` | `FAILED` — 반복 HIGH 차단 |
| 물리 LLM 호출 | 59 | 61 |
| 입력 / 출력 토큰 | 110,712 / 21,977 | 239,999 / 47,088 |
| 호출별 elapsed 합계 | 397.001초 | 216.536초 |
| schema validation 실패 | 0 | 1 — 같은 logical request 안에서 수리됨 |
| 최종/후보 class | 33 | 26 |
| 최종/후보 DataType | 25 | 24 |
| 최종/후보 operation | 59 | 62 |
| 검증된 operation fragment | 11 | 11 |

GPT-OSS 후보는 모든 operation fragment의 schema, step coverage, owner, type closure, durable Entity
책임과 sourceability 검증을 통과했고 순수 catalog 조립도 성공했다. Inventory 리뷰는 최종 PASS를
반환했다. 그러나 `CourseOffering-Student` 직접 관계와 `Registration` 경로가 함께 남았는데도 최종
inventory 리뷰가 이를 다시 찾지 못했다. Behavior에서는 UC5의 첫 수리가 상태 변경을
`Registration`으로 옮겼지만 반대 방향의 `decreaseCapacity`가 남았고, 두 번째 수리는 이를
`increaseCapacity`로 고쳤으나 `authorizeAndDrop`의 Control 책임이 계속 HIGH로 지적됐다. 동일 HIGH
집합이 반복되어 후속 call, binding, witness와 materialization은 실행하지 않았다.

이 결과는 단일 전역 리뷰가 PASS를 반환했다는 사실을 owner 무결성의 증거로 사용할 수 없음을
보여준다. 다음 개선에서는 operation 생성 직후 작은 UC fragment를 scenario slice와 함께 리뷰해
그 owner를 PASS 또는 명시적 실패로 닫고, 전역 catalog 리뷰는 cross-fragment 중복과 공유 책임만
검사해야 한다. 즉 리뷰 호출 수를 줄이는 것보다 리뷰 입력의 책임 범위를 줄이고, 다음 단계가 이전
owner의 새 로컬 결함을 발견하지 않도록 검증 경계를 앞당기는 것이 우선이다.

### 1.6 v3 방법론 구현 상태

위 실패에 대한 Astra 검토를 반영해 격리 프로토타입과 실제 LLM 실행기에 다음 경계를 구현했다.

- typed evidence index 밖의 class, relationship, operation, obligation, effect 참조 거부
- rule·predicate·target 기반 finding identity와 별도 `CONFIRMED/REFUTED/UNRESOLVED` 판정
- 모든 scenario source를 닫는 obligation과 모든 operation의 책임·outcome 계약
- Entity가 실제 소유한 stateRef만 변경할 수 있는 명시적 effect와 derived read/write
- 로컬 review closure 뒤의 `SEALED_UNDER_CONTRACT`
- 봉인된 fragment만 받는 catalog 조립과 `LOCAL_CONTRACT_ESCAPE` 재개방
- 선언한 mutation/delegation과 실제 call tree의 연결 검사
- 확인된 finding만 적용할 수 있는 operation-scoped compare-and-swap patch
- before/after와 input digest가 다른 시도에 의해 덮어써지지 않는 attempt 기록

이 단계는 방법론과 결정론적 경계를 검증한 상태다. 기존 GPT-OSS 실행 checkpoint는 review v2
계약이므로 v3 결과로 재해석하지 않는다.

### 1.7 GPT-OSS 120B v3 후속 검증과 보완

같은 앱 snapshot으로 `openai/gpt-oss-120b`를 다시 실행했다. V7과 V8은 새 디렉터리에서 시작한
cold run이고 V9는 구현을 고치면서 동일 체크포인트에서 실패한 소유자부터 재개한 개발 실행이다.
세 실행 모두 운영 graph와 MySQL에 쓰지 않았다.

| 실행 | 결과 | 시간 | logical / physical 호출 | 입력 / 출력 토큰 | 로컬 봉인 / escape |
|---|---|---:|---:|---:|---:|
| V7 cold | 형식상 `ACCEPTED`, 정성 검토에서 제외 | 66.019초 | 95 / 96 | 263,617 / 58,197 | 11 / 0 |
| V8 cold | 형식상 `ACCEPTED`, 정성 검토에서 제외 | 66.547초 | 99 / 99 | 261,228 / 59,438 | 11 / 0 |
| V9 resumed 최종 구간 | `ACCEPTED` | 19.034초 | 7 / 누적 123 | 누적 350,401 / 96,194 | 11 / 0 |

V7에서는 최초 operation 후보가 바로 통과한 UC가 2/11뿐이었다. V8에서는 4/11로 늘었지만,
첫 후보 결함 owner 비율은 여전히 63.6%였다. 따라서 이 흐름의 주된 개선은 모델의 최초 정답률이
높아졌다는 데 있지 않다. 결함을 UC 로컬 경계에서 닫고, 통과한 다른 owner를 다시 생성하지 않으며,
실패한 checkpoint부터 재개한다는 데 있다. V9 누적치는 그 개발·재개 호출을 모두 포함하므로 cold
run과 직접 비교하지 않는다.

정성 검토는 결정론적 validator를 통과하는 것만으로 충분하지 않다는 점도 드러냈다. V7은
`offeringId`를 사용 가능한 `studentId`로 바꿔 sourceability만 만족했고, V8은 기존 매개변수를
삭제하거나 의미가 없는 DTO로 바꾸는 방식으로 같은 제약을 우회했다. 다음 규칙을 추가했다.

- sourceability patch는 method name, return type, stepRefs와 기존 매개변수의 의미 역할을 보존한다.
- scalar를 다른 ID로 바꾸거나, 기존 의미를 포함하지 않는 DTO로 감싸거나, 매개변수를 삭제해
  binding을 회피하면 patch를 거부한다.
- scoped patch로 고칠 수 없으면 같은 patch를 반복하지 않고 해당 UC operation fragment의 제한된
  전체 repair로 전환한다.
- primary actor와 이름이 같은 Entity에 결과 표시·수신 operation을 강제로 만들지 않는다.
- `ByX`/`ForX` query는 parameter 이름·type·DTO field에 X가 있어야 하며, 서로 다른 Entity가 동일한
  query 계약을 중복 소유하지 못한다.
- 선행 query가 `List<X>`를 반환하고 후속 query가 그 항목들을 처리하면 후속 operation은 정확한
  `List<X>`를 받아 내부에서 순회할 수 있다. V9의 UC9는 이 규칙으로
  `getAssignedOfferings(professorId) → getStudentsByOffering(offerings)`의 source chain을 만들었다.
- 이미 충돌 조정된 inventory target의 review owner는 LLM이 고르지 않고 typed evidence index로
  결정한다. 저장된 review attempt가 새 결정 규칙을 통과하면 추가 호출 없이 승격한다.

V9 최종 결과는 class 24개, DataType 23개, operation 50개, call 46개, collaboration 13개,
명시적 state effect 15개다. 모든 11개 UC가 `SEALED_UNDER_CONTRACT`이고 전역
`LOCAL_CONTRACT_ESCAPE`는 0건이다. 다만 이는 정의한 계약 아래의 무결성이다. Entity receiver
instance 자체의 source와 컬렉션 fan-out의 개별 원소 identity는 아직 witness에 별도 필드로
표현하지 않는다. V9도 clean cold run이 아니므로 최종 규칙의 최초 후보 결함률은 후속 고정
snapshot 반복 평가에서 따로 측정해야 한다.

## 2. 문제 정의

### 2.1 현재 생성과 수리의 결합

현재 [`generation.py`](../app/design/services/class_diagram/generation.py)는 유스케이스마다
`CombinedUnitProposal`을 받아 operation fragment와 provisional calls를 동시에 생성한다.
operation은 먼저 정규화·검증하지만 calls의 실제 호출 방향과 값 출처는 최종 skeleton이 만들어진
뒤에야 구체화된다.

현재 흐름은 다음과 같다.

```text
Inventory
  → UC별 Operation + provisional Calls 병렬 생성
  → Operation 로컬 검증
  → 순차 전역 조립과 충돌 수리
  → final skeleton
  → Calls 구체화와 Binding
  → Call-plan-only 수리
  → 다시 실패하면 Operation + Calls 전체 교체
  → skeleton 재조립과 기존 Collaboration 재검사
```

이 구조에서는 call plan이나 binding의 국소 오류가 operation과 DataType의 재생성으로 확대될 수
있다. 새 operation 후보가 catalog를 바꾸면 이미 검증한 다른 collaboration도 다시 검사해야 한다.
수리 범위가 실제 결함 범위보다 커서 하나의 결함이 다른 결함을 만들 수 있다.

### 2.2 종료 조건 없는 repair

현재 inventory, operation proposal, 전역 충돌, combined replacement 경로에는 종료 예산이 없는
반복이 있다. 같은 정규화 후보와 finding이 반복되어도 중단하지 않고 다음 프롬프트에 경고를
추가한다. 후보 생성 책임을 분리하더라도 이 반복 구조를 유지하면 결함-수정 루프는 계속 커질 수
있다.

시도 횟수나 토큰 예산은 완전성의 증거가 아니다. 다만 모든 자동 수리는 종료 가능해야 하므로
실행 전체가 공유하는 유한한 수리 예산을 별도로 둔다. 예산을 소진하면 불완전한 결과를 수락하지
않고 `FAILED` 또는 `UNRESOLVED`로 종료한다.

### 2.3 operation catalog만으로 부족한 실행 가능성

operation의 타입과 step coverage가 유효해도 실제 호출 계획을 만들지 못할 수 있다. 특정
parameter가 요구하는 타입이 catalog 어딘가에 존재한다는 사실과, 그 값을 해당 호출 시점에
사용할 수 있다는 사실은 다르다.

값의 공급 가능성은 다음에 의존한다.

- 현재 call instance의 ancestor와 그 입력
- 앞선 호출이 실제로 완료한 뒤 제공하는 반환값
- 앞선 actor root가 export한 입력 또는 결과
- 분기와 precondition이 보장하는 값 존재 조건
- value object를 구성하는 모든 필수 필드의 공급 가능성
- 같은 타입이지만 의미가 다른 source의 구분

따라서 실행 가능성을 수락하려면 구체적인 call instances와 argument sources가 동시에 성립하는
execution witness가 필요하다.

## 3. 목표와 비목표

### 3.1 목표

- 각 LLM 요청이 한 종류의 의미 결정만 수행한다.
- 각 중간 결과가 무엇을 증명했고 무엇을 아직 증명하지 않았는지 타입과 상태로 구분한다.
- downstream에서 발견한 오류를 원래 계약 소유자에게 정확히 귀속한다.
- 이미 수락된 revision을 draft 수리 중간 상태로 덮어쓰지 않는다.
- 모든 최종 behavior가 같은 scenario, inventory, catalog, validator revision을 참조한다.
- 클래스·시퀀스·API 단계가 동일한 accepted behavior를 source of truth로 사용한다.
- 증명하지 못한 성질이나 요구사항의 의미적 공백을 성공으로 표시하지 않는다.

### 3.2 비목표

- 자연어 요구사항의 모든 의미가 현실 세계에서 참임을 형식적으로 증명하지 않는다.
- 하나의 정답 클래스 구조나 메서드 이름을 강제하지 않는다.
- 특정 평가 사례의 이름과 클래스 개수를 검증 규칙에 하드코딩하지 않는다.
- 유한한 탐색 범위에서 witness를 못 찾은 사실만으로 일반적인 실행 불가능을 주장하지 않는다.
- 이 설계 단계에서 모델별 토큰·지연 최적값을 결정하지 않는다.

이 문서에서 실행 가능하다는 말은 **정의된 클래스 상호작용 모델의 호출·분기·값 흐름 규칙을
만족하는 구체적인 계획이 존재한다**는 뜻이다. 생성된 구현 코드가 모든 런타임 상황에서 올바르게
동작한다는 뜻은 아니다.

## 4. 목표 생성 흐름

```text
AcceptedScenarioContract
  → UC별 ValidatedInventoryFragment
  → ValidatedInventoryConflictResolution
  → ValidatedInventory
  → UC별 ValidatedOperationFragment
  → UC별 ValidatedLocalSemanticContract
  → UC별 typed-evidence review와 adjudication
  → UC별 LocallySealedOperationFragment(SEALED_UNDER_CONTRACT)
  → 공유 계약 통합과 LOCAL_CONTRACT_ESCAPE 재봉인
  → Sealed ValidatedCatalogDraft
  → shared-contract-only integration review
  → UC별 ValidatedCallStructure
  → effect/delegation-call link 검증
  → UC별 ValidatedBindingPlan
  → UC별 ValidatedExecutionWitness
  → 전역 cross-witness 검사
  → AcceptedBehaviorModel 원자적 commit
  → MaterializedArtifacts 순수 변환
```

`VALIDATED`는 해당 결과가 기록한 input revision과 validator version이 유지되는 동안 명시된
계약을 통과했다는 뜻이다. 전체 설계가 실행 가능하다는 뜻은 아니다. `ACCEPTED`는 전역 catalog와
모든 witness가 같은 snapshot에서 동시에 유효할 때만 사용한다.

## 5. 계약과 식별자

아래 이름은 개념 계약이다. 실제 Pydantic schema를 만들 때 의미와 상태 경계를 유지하되 이름은
코드 규칙에 맞게 조정할 수 있다.

| 계약 | 입력 revision | 출력의 핵심 내용 | 이 단계가 증명하지 않는 것 |
|---|---|---|---|
| `AcceptedScenarioContract` | 요구사항·유스케이스 원문 | actor, step, group, branch, include/extend, typed input·precondition·outcome, 의미 공백 | 클래스와 호출 구현 가능성 |
| `ValidatedInventory` | scenario | BCE class/type/relationship, 책임 범위 | 모든 UC의 operation·호출 가능성 |
| `ValidatedOperationFragment` | scenario, inventory, 공유 계약 snapshot | operation key, signature, step responsibility, local type | 상태 효과와 구체적인 call·binding 존재 |
| `ValidatedLocalSemanticContract` | scenario, inventory, operation fragment | scenario obligation, operation 책임, Entity state effect, outcome, delegation | 실제 call이 effect operation을 실행함 |
| `LocallySealedOperationFragment` | operation, local semantics, adjudicated review | 로컬 계약 안의 무결성과 review closure | 다른 UC와의 공유 계약 일관성 |
| `ValidatedCatalogDraft` | 모든 local seal | 충돌 없는 전역 operation/type namespace, seal provenance, 최종 정규화 결과 | 모든 UC의 실행 witness 존재 |
| `ValidatedCallStructure` | scenario, catalog | call-instance key, operation key, parent, root/group, order, guard | 모든 argument의 값 공급 가능성 |
| `ValidatedBindingPlan` | call structure, typed source graph | parameter source, projection, construction, unwrap 조건, result/export | 다른 UC witness와의 전역 일관성 |
| `ValidatedExecutionWitness` | 동일 UC의 모든 위 계약 | 동시에 성립하는 concrete call·binding·guard·result flow | 모델 밖 런타임 동작 |
| `AcceptedBehaviorModel` | catalog와 모든 witness의 동일 snapshot | immutable revision과 전역 검증 provenance | 구현 코드의 실행 검증 |
| `MaterializedArtifacts` | accepted behavior revision | BCE JSON, 클래스·시퀀스 PlantUML, downstream projection | 새로운 의미 결정 |

### 5.1 revision과 digest

모든 `VALIDATED`·`ACCEPTED` 결과는 다음 정보를 기록한다.

- 자신의 stable ID와 schema version
- 직접 입력한 모든 revision 또는 digest
- prompt version과 proposal schema version
- validator set version
- 정규화된 payload digest
- 검사 결과와 `findingCount=0`
- 생성 모델 정보는 provenance로 기록하되 무결성 판단에는 사용하지 않음

입력 digest가 하나라도 바뀌면 해당 결과는 자동으로 유효하지 않게 된다. 문자열 내용이 우연히
비슷하거나 ID가 같다는 이유로 다른 snapshot의 검증 결과를 재사용하지 않는다.

### 5.2 안정 식별자

다음 식별자를 분리한다.

- `operationKey`: 메서드 계약의 논리적 동일성
- `parameterKey`: operation 안에서 parameter 계약의 동일성
- `callInstanceKey`: 같은 operation을 여러 번 호출할 수 있는 실행 인스턴스
- `sourceKey`: actor input, precondition, runtime source, call result와 field projection
- `groupKey`: actor entry와 실행 범위
- `witnessKey`: UC와 catalog revision에 귀속된 실행 증거

배열 위치와 표시용 이름은 stable identity가 아니다. `parentCallIndex`는 LLM proposal에서 사용할
수 있지만 정규화 뒤에는 `parentCallInstanceKey`로 바꾼다.

## 6. 단계별 생성 책임과 불변조건

### 6.1 Scenario contract

Scenario contract는 클래스 설계가 임의로 의미를 보충하지 않도록 하는 상위 경계다.

필수 불변조건은 다음과 같다.

- 모든 actor, step, group, include/extend reference가 존재한다.
- main·extension 순서와 anchor가 일관된다.
- actor가 제공하는 입력은 이름뿐 아니라 type과 semantic role을 가진다.
- precondition source는 type, scope, 존재 조건을 가진다.
- 성공과 예상 가능한 업무 실패의 outcome이 구분된다.
- 해석할 수 없는 입력·조건·결과는 `unresolvedItems`에 남는다.

필수 의미가 `unresolvedItems`에 남으면 해당 UC behavior는 수락하지 않는다. LLM이 DTO나
operation parameter를 새로 만들었다는 사실로 scenario gap을 해소한 것으로 간주하지 않는다.

### 6.2 Inventory

Inventory LLM은 전역 BCE 구조만 제안한다. operation과 call을 생성하지 않는다.

필수 불변조건은 다음과 같다.

- 클래스·DataType 이름이 유일하고 모든 참조가 닫혀 있다.
- Boundary·Control·Entity 역할과 구조 관계가 유효하다.
- Entity의 durable field와 identifier가 일관된다.
- 구조 타입은 참조 그래프에서 도달 가능하다.
- 클래스와 타입의 UC 책임 범위가 scenario에 존재한다.

Inventory는 구조 계약만 보장한다. 특정 UC에 필요한 메서드나 실행 경로가 있다는 보장은 하지
않는다.

### 6.3 Operation fragment

Operation LLM은 한 UC의 메서드 계약과 operation-local type만 제안한다. call forest와 argument
source를 생성하지 않는다.

입력은 다음으로 제한한다.

- 해당 UC의 scenario contract
- 고정 inventory
- 이미 합의된 공유 operation/type 계약 snapshot
- 허용된 step IDs와 effect vocabulary

출력은 다음만 포함한다.

- owner class key
- operation key와 display name
- parameter keys, names, types와 semantic roles
- return/result type과 declared effects
- 담당 step IDs
- operation-local value object와 enumeration
- 재사용하는 공유 계약의 exact key

필수 불변조건은 다음과 같다.

- parameter·return·field type reference가 닫혀 있다.
- operation name과 parameter key가 owner 안에서 유일하다.
- 모든 step reference가 현재 UC 범위에 있다.
- 명시된 step responsibility가 누락되거나 중복 소유되지 않는다.
- durable state의 읽기·변경 책임이 적절한 Entity operation에 있다.
- Control helper가 Entity의 durable responsibility를 대신하지 않는다.
- 결과 타입의 optionality와 outcome 구분이 scenario contract와 일치한다.

이 단계는 구체적인 호출 순서와 값 공급을 증명하지 않는다. 그러므로 결과 이름에
`AcceptedOperation`을 사용하지 않는다.

### 6.4 Catalog draft

모든 operation fragment는 하나의 전역 namespace로 조립한다. 제안은 병렬로 만들 수 있지만
조립과 충돌 해소는 명시적인 catalog draft transaction에서 수행한다.

필수 불변조건은 다음과 같다.

- 같은 operation key의 signature와 effect가 동일하다.
- 같은 DataType key의 kind와 정의가 동일하다.
- 같은 이름의 다른 논리 계약이 암묵적으로 병합되지 않는다.
- 공유 계약 재사용이 exact key와 provenance를 보존한다.
- 최종 pruning·canonicalization 뒤 모든 타입과 operation dependency가 유효하다.
- step responsibility의 전역 합집합이 scenario contract와 맞는다.

충돌은 먼저 deterministic identity와 canonical type으로 판정한다. 의미적으로 다른 두 계약의
이름 충돌은 LLM에게 전체 UC를 다시 쓰게 하지 않고, catalog transaction이 충돌 당사자와 허용된
변경 필드를 명시해 해당 fragment만 새 draft로 만든다.

### 6.5 Call structure

Call structure LLM은 완성된 catalog draft에서 실제 operation keys만 선택한다. 새 클래스,
operation, parameter와 type을 만들 수 없다.

출력은 다음만 포함한다.

- `callInstanceKey`
- `operationKey`
- `parentCallInstanceKey`
- `groupKey`
- branch/guard reference

필수 불변조건은 다음과 같다.

- 각 actor entry에 정확히 하나의 Boundary root가 있다.
- root Boundary는 해당 actor의 interface다.
- 각 Boundary root가 Control에 위임한다.
- BCE 호출 방향과 외부 Boundary 호출 규칙을 지킨다.
- parent가 같은 root 안의 합법적인 선행 call instance다.
- required steps와 include/extend 경로를 추적할 수 있다.
- 같은 operation을 여러 번 호출해도 call instance identity가 충돌하지 않는다.
- 호출 시작·완료와 branch 조건을 표현할 수 있는 실행 순서를 가진다.

Call structure 검증은 아직 모든 parameter source가 존재한다는 사실을 보장하지 않는다.

### 6.6 Binding plan

Binding 후보는 LLM이 자유 문자열로 만들지 않는다. 코드는 scenario input, precondition, runtime
source, ancestor parameter, 사용 가능한 앞선 call result와 field projection에서 타입과 scope가
맞는 유한 후보를 계산한다.

후보가 0개면 `MISSING_SOURCE`, 하나면 코드가 선택하고, 여러 개면 의미적 구분이 가능한 metadata와
함께 제한된 선택만 LLM에 맡긴다.

필수 불변조건은 다음과 같다.

- 모든 필수 parameter에 정확히 하나의 source가 있다.
- source type은 parameter type에 assignable하다.
- source semantic role이 parameter role과 호환된다.
- source가 소비 call이 시작되기 전에 존재한다.
- 자식 완료 뒤 생성되는 부모 반환값을 자식 입력으로 사용하지 않는다.
- 조건부 source는 소비 branch가 그 생성 조건을 보장한다.
- optional source를 non-optional parameter에 전달하려면 존재 guard가 있다.
- derived value object는 모든 필수 field source를 가진다.
- conversion이나 projection은 등록된 deterministic rule로 표현된다.

후보 선택 LLM이 실패해도 call structure나 operation을 즉시 다시 생성하지 않는다. 같은 후보
집합에서 binding만 다시 선택하거나 판단 불가능 상태를 반환한다.

## 7. Execution witness

`ValidatedExecutionWitness`는 한 UC가 정의된 상호작용 모델 안에서 실제로 실행 가능하다는
구체적 증거다. 다음 snapshot을 함께 보존한다.

```text
scenario revision
inventory revision
catalog digest
call structure digest
binding plan digest
call instances와 parent 관계
각 parameter의 source와 가용 시점
branch/guard와 include/extend 연결
outcome과 actor-visible result 흐름
validator version과 전체 검사 결과
```

Execution witness 검증은 각 parameter를 따로 검사한 결과의 합이 아니다. 모든 call·binding·guard
선택이 동시에 성립하는지 하나의 실행 그래프로 검사한다.

필수 불변조건은 다음과 같다.

- 호출 그래프가 유한하고 허용되지 않은 순환이 없다.
- 각 call의 필수 입력이 해당 실행 시점에 존재한다.
- 각 group의 required step과 outcome이 실행 그래프에 대응한다.
- include 결과와 cross-root export의 scope·조건·수명이 소비 위치와 맞는다.
- 성공과 실패 경로 모두 scenario contract가 요구하는 actor-visible result에 도달한다.
- 모든 call과 source가 같은 catalog·scenario snapshot을 참조한다.

결정론적 solver를 사용할 수 있지만 solver의 탐색 완전성과 validator의 soundness를 구분한다.
제한된 깊이 또는 후보 수에서 계획을 찾지 못한 것은 `UNSAT` 증명이 아니다.

## 8. 상태 전이와 원자적 commit

각 draft 단위는 다음 상태 기계를 따른다.

```text
PROVISIONAL
  ├─ 계약 실패 → REJECTED(findings, input digest)
  └─ 계약 통과 → VALIDATED(input digest, validator version)

모든 연관 단위 VALIDATED
  ├─ 전역 일관성 실패 → 새 draft revision 또는 UNRESOLVED
  └─ 전역 계약 통과 → ACCEPTED revision 원자적 commit
```

초기 구현의 commit 단위는 다음 전체다.

```text
AcceptedBehaviorModel
  ├─ AcceptedScenarioContract reference
  ├─ ValidatedInventory snapshot
  ├─ Validated operation/type catalog
  ├─ 모든 UC의 ValidatedExecutionWitness
  └─ validation provenance
```

새 draft를 생성·수리하는 동안 기존 accepted revision은 계속 읽을 수 있어야 한다. 새 revision이
모든 검사를 통과하기 전에는 기존 revision을 부분적으로 덮어쓰거나 downstream에 공개하지 않는다.

향후 부분 commit을 허용하려면 공유 operation/type, include, cross-UC export의 transitive dependency
closure를 계산해야 한다. 이 closure를 정확히 계산하고 재검증하지 않는 한 UC별 부분 accepted를
도입하지 않는다.

## 9. Execution group 경계

기본 LLM 생성·witness 단위는 UC 전체다. 현재 값 출처 모델은 앞선 actor root의 입력을 뒤 root에서
재사용할 수 있으므로 execution group들이 항상 독립적이지 않다.

Group별 생성이 필요하면 먼저 명시적인 import/export 계약을 둔다.

```text
GroupContract
  inputs:
    - actor inputs
    - preconditions
    - previous-group imports
  exports:
    - source key
    - type과 semantic role
    - 생성 조건
    - 가용 시점과 수명
```

조건부 export를 소비하는 group의 guard는 export 생성 조건을 함의해야 한다. group 간 순환
의존이 있거나 import/export 경계를 확정할 수 없으면 해당 group들을 독립적으로 수락하지 않고
UC witness 안에서 함께 검증한다.

Group별 LLM 요청은 나중에 도입할 수 있지만 최종 UC witness 검증은 생략하지 않는다.

## 10. 실패 분류와 repair 소유권

실패는 다음과 같이 분류한다.

| 상태 | 의미 | 수락 가능 여부 |
|---|---|---|
| `INVALID_CANDIDATE` | 제안된 구체 후보가 계약을 위반함 | 불가 |
| `NO_WITNESS_WITHIN_BOUND` | 정한 탐색 범위에서 witness를 찾지 못함 | 불가, 일반적 UNSAT로 해석 금지 |
| `UNSAT_UNDER_CONTRACT` | 정의된 유한 모델 전체에서 실행 불가능함을 증명함 | 불가, upstream revision 필요 |
| `SPECIFICATION_GAP` | actor input·조건·outcome 의미가 부족함 | 불가, scenario 보완 필요 |
| `VALIDATOR_ERROR` | 검사기 실행 실패 또는 상호 모순 | 불가, 생성 후보 repair 금지 |
| `PROVIDER_FAILURE` | 429, timeout, incomplete stream 등 의미와 무관한 실패 | 불가, 동일 draft에서 운영 정책에 따라 재개 |

Repair는 오류 발견 위치가 아니라 위반된 계약 소유자에게 귀속한다.

| 위반 계약 | 기본 repair 범위 |
|---|---|
| operation schema/type/step/effect | 해당 operation fragment |
| 공유 signature 또는 DataType 충돌 | catalog transaction이 지정한 충돌 fragment |
| root/parent/호출 순서/BCE 방향 | call structure |
| 유효 source 후보 중 잘못된 선택 | binding plan의 해당 parameter |
| 모든 합법적 plan에서 필수 source 부재 | 관련 operation 또는 scenario contract의 새 draft |
| actor input·outcome 의미 부족 | scenario contract의 새 revision |
| accepted behavior의 투영 실패 | materializer |

Call plan 하나가 실패했다는 이유만으로 operation을 교체하지 않는다. `UNSAT_UNDER_CONTRACT` 또는
`SPECIFICATION_GAP`처럼 upstream 변경이 필요한 근거가 있을 때만 새 upstream draft를 만든다.
이는 암묵적인 오류 역류가 아니라 dependency invalidation을 기록하는 명시적 revision이다.

### 10.1 반복 판정과 종료

반복은 finding 문자열만으로 판정하지 않는다. 서로 다른 후보가 같은 규칙을 위반할 수 있기
때문이다. 다음 tuple이 같은 경우에 동일 상태로 본다.

```text
(input snapshot digest, normalized candidate digest, normalized finding key set)
```

동일 상태가 반복되면 같은 repair 전략을 다시 호출하지 않는다. 다른 허용된 전략이 없으면
`UNRESOLVED`로 종료한다.

중첩 repair 모두가 하나의 실행 예산을 공유한다.

- 전체 physical LLM call 수
- 단계와 단위별 call 수
- 누적 wall time
- 누적 provider token 사용량
- 반복 상태 수

예산은 종료성을 위한 안전장치이며 후보의 무결성을 증명하지 않는다.

## 11. LLM과 결정론 코드의 역할

LLM이 담당하는 일은 요구사항의 의미를 해석하는 유한 결정이다.

- operation 책임·시그니처·effect 제안
- 검증된 catalog에서 call structure 제안
- 의미적으로 구별되는 유한 binding 후보 중 선택
- 명시된 finding과 허용된 변경 필드 안에서 국소 replacement 제안

코드가 담당하는 일은 다음과 같다.

- stable identity와 revision 관리
- 타입 폐쇄성, step 소유권과 namespace 조립
- 유한 operation/source 후보 계산
- 호출 방향·순서·scope·lifetime 검사
- call과 binding의 동시 실행 그래프 검증
- deterministic projection과 materialization
- failure classification, repair routing과 budget accounting
- checkpoint와 atomic commit

LLM에게 전체 모델을 다시 작성하게 하는 repair는 허용하지 않는다. 변경 가능한 필드와 대상 stable
key를 repair contract에서 제한한다.

## 12. 저장과 재개

Process-local cache는 성능 최적화일 뿐 수락 증거나 체크포인트가 아니다. 다음 draft와 검증 결과를
영속 저장해야 한다.

- draft execution ID와 parent accepted revision
- scenario, inventory, catalog revision
- UC별 provisional·validated operation fragment
- UC별 call structure, binding plan과 execution witness
- 각 단위의 input/output digest와 validator version
- finding, repair strategy와 반복 상태 digest
- 실행 전체의 누적 예산
- 최종 atomic commit record

재개할 때는 app ID만 비교하지 않는다. execution ID, scenario revision, catalog digest, 완료 단위,
schema·prompt·validator version이 모두 대응하는지 확인한다.

Provider failure나 서버 재시작 뒤에는 마지막으로 검증된 단위 다음부터 재개한다. 부분 응답이나
검증되지 않은 후보를 accepted cache에 넣지 않는다.

## 13. 현재 코드와의 변경 대응표

| 현재 요소 | 결정 |
|---|---|
| `AcceptedInventory` 불변 경계 | 유지하고 revision·validation provenance 추가 |
| `AcceptedFragment` | 의미를 `ValidatedOperationFragment`로 축소하고 executable accepted라는 해석 금지 |
| `CombinedUnitProposal` | operation-only와 call-structure proposal로 분리 |
| `compose_operation_units(..., final=True)` | catalog draft의 최종 pruning·정규화 검사로 유지 |
| finite operation ID schema | call structure proposal에서 유지 |
| `_binding_candidates()` | typed source graph 계산의 기반으로 유지하되 scope·lifetime·semantic role 강화 |
| `select_ambiguous_bindings()` | materializer에서 제거하고 explicit binding-plan 단계로 이동 |
| `materialize()` | validated witness를 canonical model로 바꾸는 순수 함수로 축소 |
| `CombinedReplacementRequired` | 제거하고 typed failure classification과 repair routing으로 대체 |
| 반복 `while True` | shared repair budget과 terminal state machine으로 대체 |
| process-local accepted cache | 최적화로만 유지하고 persistent draft/checkpoint와 구분 |
| `COLLABORATION_CHECKS` | execution witness validator의 일부로 유지·확장 |

현재 코드의 구조화 schema, Pydantic 경계, deterministic normalization, finite candidate schema,
stable digest와 최종 validation은 재사용한다. 문제는 이 요소들의 존재가 아니라 실제로 증명한
범위보다 넓은 의미로 결과를 수락하고, 실패 시 수정 범위가 다시 합쳐지는 데 있다.

## 14. MaterializedArtifacts 경계

Accepted behavior 이후 다음 산출물은 같은 revision에서 결정론적으로 만들어야 한다.

- 영속 `BCEModel`
- 클래스 PlantUML
- 유스케이스별 sequence projection
- API 단계가 소비할 Boundary·Control·outcome reference
- 호출에서 파생한 표시용 dependency

Materializer는 다음을 할 수 없다.

- 새 operation·type·call을 추가
- argument source를 선택
- missing step을 임의 operation에 할당
- signature나 type spelling을 의미적으로 수정
- 요구사항 gap을 기본값으로 채움

투영 결과가 accepted behavior를 표현할 수 없으면 `MATERIALIZATION_ERROR`로 실패한다. 설계 LLM
repair를 다시 시작하지 않는다.

## 15. 방법론 검증 기준

구현 완료 여부는 특정 모델의 성공률보다 다음 계약 검사로 판단한다.

### 15.1 상태와 수락

- `VALIDATED`와 `ACCEPTED`가 코드와 저장 schema에서 구분된다.
- accepted revision은 catalog와 모든 execution witness를 원자적으로 포함한다.
- 새 draft 실패가 기존 accepted revision을 변경하지 않는다.
- 서로 다른 input 또는 validator revision의 검증 결과를 조립할 수 없다.

### 15.2 단계 책임

- operation proposal schema에는 call과 binding 필드가 없다.
- call proposal schema는 실제 catalog operation key만 선택할 수 있다.
- binding proposal schema는 코드가 계산한 source key만 선택할 수 있다.
- materializer 실행 중 LLM 호출이 발생하지 않는다.
- Call-plan candidate 실패만으로 operation repair가 시작되지 않는다.

### 15.3 Witness

- 각 accepted UC에 하나 이상의 concrete execution witness가 있다.
- witness가 call order, source availability, type, branch와 outcome 검사를 통과한다.
- 앞선 배열 위치와 실제 값 가용 시점을 구분한다.
- 조건부·optional source 소비에 guard 증거가 있다.
- include와 cross-root source가 scope와 lifetime을 보존한다.

### 15.4 실패와 종료

- 모든 실패가 정의된 failure category로 끝난다.
- `NO_WITNESS_WITHIN_BOUND`를 `UNSAT_UNDER_CONTRACT`로 바꾸지 않는다.
- 동일 input·candidate·finding 상태에 같은 repair를 반복하지 않는다.
- 모든 중첩 repair가 공유 예산을 사용하며 예산 소진 뒤 accepted 결과를 만들지 않는다.
- provider failure와 semantic invalidity를 구분한다.

### 15.5 재개

- 완료된 validated 단위는 동일 snapshot에서 재사용된다.
- 서버 재시작 뒤 실패한 단위부터 재개된다.
- schema·prompt·validator version이 바뀌면 영향을 받는 단위만 무효화된다.

## 16. 격리 실험의 내부 구현 순서

방법론의 의존 순서를 보존하기 위해 다음 순서로 구현한다.

아래 순서는 격리된 executable behavior 내부의 계약 의존 순서다. 운영 graph, MySQL과 UI에
연결하는 단계는 8번 이후를 바로 실행하지 않고, 먼저 전역 피드백 문서의 공통 계약과 첫 수직
경로를 통과해야 한다.

1. Scenario input·outcome·unresolved contract와 stable identity를 확정한다.
2. draft/validated/accepted 상태, revision, digest와 validation provenance schema를 만든다.
3. 현재 `CombinedUnitProposal`에서 operation-only proposal을 분리한다.
4. catalog draft 조립·pruning·충돌 validator를 독립 경계로 만든다.
5. call structure proposal을 stable operation/call keys 기반으로 분리한다.
6. typed source graph와 binding-plan schema를 만든다.
7. call·binding·guard를 함께 검사하는 execution witness validator를 만든다.
8. 전역 `AcceptedBehaviorModel` atomic commit과 persistent checkpoint를 연결한다.
9. materializer의 binding LLM과 implicit repair를 제거한다.
10. typed failure classification, repair routing과 shared termination budget을 연결한다.
11. 기존 combined generation과 `CombinedReplacementRequired` 경로를 제거한다.

각 단계는 다음 단계가 필요로 하는 계약과 validator가 준비된 뒤 연결한다. 중간 마이그레이션에서
새 `VALIDATED` 결과를 기존 `AcceptedFragment`처럼 downstream에 공개하지 않는다.

## 17. 격리 실험의 강한 수용 조건

이 절은 격리 executable behavior 방법론이 최종적으로 지향한 강한 계약이다. 최초 운영 통합의
필수 gate는 아니며, 운영 연결에서는 전역 피드백 문서의 첫 완료 기준을 먼저 적용한다.

이 개선안은 다음 조건을 모두 만족할 때 완료로 본다.

- 한 LLM 요청이 operation, call topology, binding 중 하나만 결정한다.
- operation과 catalog는 자신이 증명하지 않은 실행 가능성을 `accepted`로 표시하지 않는다.
- 모든 accepted UC behavior에 동일 catalog snapshot의 concrete execution witness가 있다.
- 전체 catalog와 witness는 하나의 immutable revision으로 원자적 공개된다.
- downstream 산출물 생성은 accepted revision을 수정하거나 LLM으로 보완하지 않는다.
- 모든 repair가 계약 소유자와 허용된 변경 필드로 제한된다.
- upstream 변경은 새 draft revision과 dependency invalidation으로만 일어난다.
- 종료하지 않는 repair 경로가 없고, 미해결 결과는 성공으로 저장되지 않는다.
- 검사기가 증명한 범위와 증명하지 않은 범위가 schema·상태·문서에 명시된다.

이 기준은 “LLM이 항상 올바른 답을 낸다”는 보장이 아니다. 각 후보의 오류 표면을 좁히고,
결함이 다른 정상 부분으로 전파되지 않게 하며, 최종 성공이라고 표시한 모델마다 구체적인 실행
증거를 남기는 방법론적 완전성을 목표로 한다.
