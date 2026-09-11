# 실행 가능한 behavior 계약 프로토타입

이 디렉터리는 클래스 생성 개선안의 계약만 검증하는 격리 프로토타입이다. 운영
`class_diagram` 그래프, MySQL, LLM provider, UI에는 연결하지 않는다. 따라서 feature flag도
두지 않는다.

검증 흐름은 다음과 같다.

```text
고정 scenario
  → UC별 inventory fragment 검증
  → class/type/relationship 충돌 제한 조정과 전역 inventory 검증
  → inventory 의미 리뷰(구조 중복·Entity·관계)
  → UC별 operation-only fragment와 sourceability 검증
  → UC별 scenario obligation·operation responsibility·Entity effect 계약 검증
  → typed evidence 기반 로컬 의미 리뷰와 독립 판정
  → 확인된 결함만 operation-scoped CAS patch
  → UC별 SEALED_UNDER_CONTRACT
  → 공유 operation signature 충돌 제한 조정
  → 변경된 owner를 LOCAL_CONTRACT_ESCAPE로 재개방·재봉인
  → 봉인된 fragment만 전역 catalog로 조립
  → 전역 리뷰는 shared contract regression만 검사
  → UC별 call structure 검증
  → 유한 source 집합의 binding plan 검증
  → call과 binding을 함께 재검사한 execution witness
  → 모든 UC witness와 catalog의 원자적 ACCEPTED 경계
  → 기존 BCEModel로 순수 materialization
```

각 `VALIDATED` 객체는 자신이 직접 검사한 범위만 보장한다. catalog가 유효해도 call과 binding이
성립한다는 뜻은 아니다. 전체 behavior는 같은 scenario·catalog snapshot의 모든 UC witness가
있을 때만 `ACCEPTED`가 된다. 각 경계는 상태 문자열을 신뢰하지 않고 provenance digest와 현재
payload를 다시 대조한다.

## 파일별 책임

- `operations.py`: operation과 local DataType만 허용하는 UC 로컬 계약
- `catalog.py`: 검증된 fragment만 받는 전역 operation/type namespace 조립
- `calls.py`: catalog operation만 선택하는 actor-root call forest와 BCE 방향 검사
- `bindings.py`: type·scope·guard·가용 시점을 적용한 유한 source 후보와 완전한 binding plan
- `witness.py`: call과 binding을 동일 snapshot에서 함께 재검사
- `orchestration.py`: 정확한 UC coverage를 요구하는 원자적 수락
- `materialize.py`: 수락 결과를 기존 `BCEModel`로 바꾸는 무결성 검사 포함 순수 투영
- `repair.py`: 실패 분류, repair 소유권, 공유 횟수 예산과 동일 상태 반복 차단
- `reviews.py`: typed evidence, 안정적인 finding ID, 독립 판정, immutable finding ledger
- `semantics.py`: UC obligation, 명시적 state effect, 로컬 봉인, call-effect 연결
- `patches.py`: 확인된 finding에만 허용하는 operation 단위 compare-and-swap patch
- `prototype.py`: 위 경계를 메모리 안에서 연결하는 테스트 진입점

## 현재 검증 범위

패키지 자체의 테스트는 scripted 후보를 사용해 순수 계약을 검증한다. 별도 실행기
`scripts/run_executable_behavior_llm_experiment.py`로 실제 LLM도 연결해 검증했다. 앱
`522d73e6-3aee-42af-a787-15a22ab364b0`의 11개 UC를 사용한 GLM 5.3 Flash 기준 실행은 inventory
fragment, 전역 충돌 조정, operation, call, binding과 witness를 모두 통과해 `ACCEPTED`가 됐다.
후속 GPT-OSS 120B v3 실행은 11개 로컬 봉인, call·binding·witness와 원자적 수락을 통과했다.
최종 V9는 구현을 보완하며 실패 owner부터 재개한 개발 실행이므로 cold-run 성능 기준으로 쓰지
않는다. 마지막 재개 구간은 UC9와 그 의존 단계만 7회 호출해 `ACCEPTED`가 됐고, 전체 V9
디렉터리에는 이전 실패·수리까지 물리 호출 123회가 누적돼 있다.

실험에서 확인한 추가 규칙은 다음과 같다.

- inventory도 전역 한 번이 아니라 UC별 완전 fragment와 제한된 전역 충돌 조정으로 나눈다.
- downstream parameter에 유한 source가 전혀 생길 수 없는 operation은 call 단계 전에 거부한다.
- sourceability patch는 method name, return type, stepRefs와 기존 parameter의 의미를 보존한다.
  다른 ID, 무관한 DTO, parameter 삭제로 binding을 회피하면 거부하고 UC 로컬 fragment repair로
  전환한다.
- primary actor와 이름이 같은 Entity는 단지 inventory에 존재한다는 이유로 operation을 강제하지
  않는다. actor 결과의 표시·수신 책임은 Boundary에 남긴다.
- `ByX`/`ForX` query의 X는 parameter 또는 선언된 valueObject field에서 공급돼야 한다. 같은 UC의
  서로 다른 Entity가 동일 query signature를 중복 소유하는 경우도 결정론적으로 거부한다.
- 선행 호출 결과가 `List<X>`이면 후속 consumer가 그 exact list를 받아 내부에서 순회할 수 있다.
- 충돌 조정된 inventory target의 review owner는 LLM 출력이 아니라 typed evidence index로
  결정하며, 유효한 저장 attempt는 추가 LLM 호출 없이 checkpoint로 승격한다.
- 구조화 응답 뒤에 중복된 `}` 또는 `]`만 붙은 경우에는 첫 완전 JSON 값 뒤의 delimiter만
  결정론적으로 제거해 기록한다. 두 JSON 값이 이어진 응답은 정상화하지 않고 schema 실패로
  남긴다.
- catalog가 바뀌면 기존 call과 binding payload를 현재 snapshot에서 결정적으로 재검증하고,
  통과한 단위는 LLM으로 다시 만들지 않는다.
- GLM 5.3 Flash는 medium reasoning에서 작은 operation 입력도 reasoning-only length로 끝났으므로,
  제한된 proposal은 low reasoning과 결정론적 validator를 결합한다.
- 리뷰어가 반환한 HIGH는 곧바로 결함으로 취급하지 않는다. finding은 코드가 만든 typed evidence
  index의 정확한 좌표만 인용해야 하며, 별도 판정이 `CONFIRMED`로 분류한 항목만 repair 대상이다.
  `REFUTED`는 설계를 변경하지 않고, `UNRESOLVED`는 불완전한 상태로 종료한다.
- finding ID는 category·owner·증거 문구만 해시하지 않고 rule·predicate·target·obligation 좌표로
  만든다. 따라서 “capacity 복구 누락”과 “Control이 상태를 변경함”처럼 같은 owner를 가리키는
  서로 다른 결함을 반복 상태로 오인하지 않는다.
- operation fragment는 step coverage만으로 봉인되지 않는다. 모든 scenario source가 obligation에
  포함되고, 모든 operation이 `COORDINATE/QUERY/MUTATE` 책임과 outcome을 가지며, 상태 전이는 실제
  Entity 소유 stateRef의 명시적 effect로 연결되어야 한다.
- HIGH finding은 실제 선언 provenance에 따라 inventory fragment, inventory resolution 또는
  operation fragment로 되돌린다. behavior repair는 전체 fragment 재생성이 아니라 base digest,
  expected prior digest, finding ID를 포함한 operation-scoped patch만 허용한다.
- 같은 HIGH finding digest가 반복되거나 owner별 수리 한도를 소진하면 불완전한 결과를 수락하지
  않는다. 리뷰어의 거짓 양성은 design repair 예산을 소비하지 않는다.
- 실제 GPT-OSS 실행에서 한 owner의 첫 수리가 한 결함을 없앤 뒤 다른 결함을 드러낼 수 있음이
  확인됐다. 따라서 owner 1회 제한은 완전성에 부족하며, 작은 owner 단위 리뷰와 제한된 재검증을
  결합해야 한다.

아직 운영 저장 계약과의 projection adapter는 없다. 특히 다중 actor-entry UC의 collaboration
식별자와 prototype source-ref 표기가 운영 형식과 다르다. 따라서 이 프로토타입의 `ACCEPTED`를
곧바로 운영 수락으로 해석하지 않는다.

관련 테스트는 다음 명령으로 실행한다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/design/executable_behavior
.\.venv\Scripts\python.exe -X utf8 -m mypy app/design/services/executable_behavior
.\.venv\Scripts\python.exe -X utf8 -m ruff check app/design/services/executable_behavior tests/design/executable_behavior
```
