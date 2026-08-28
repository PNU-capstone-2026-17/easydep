# 클래스 설계 최적화 E1 프로토콜

이 문서는 `evaluation/class_design_evaluation.py`의 오프라인 evaluator와
`evaluation/class_design_optimization_run.py`의 실제 생성 실행기가 함께 사용하는 고정
실험 계획이다. evaluator는 frozen upstream checkpoint를 digest 검증하고 후보의
schema/reference/call/sequence gate를 실행한다. 실제 LLM 호출은 live runner에만 있으며
`python -X utf8`과 외부 네트워크 권한으로 실행한다.

## 고정 입력과 실행 상한

- case는 `e1-aws` 하나이며, `evaluation/baselines/course-registration-cases/goldset/e1-aws`
  아래 `manifest.json`과 requirements/specifications checkpoint를 변경하지 않는다.
- 한 실험 묶음은 최대 9회 독립 실행이다.
  - baseline 3회
  - `compact` 1회
  - `call-plan-low` 1회
  - `operation-low` 1회
  - 사전에 기록한 합성 후보 3회
- 각 cell은 새로운 독립 `run_id`를 사용한다. 이전 checkpoint, cache, telemetry를 다음
  cell의 입력으로 재사용하지 않는다. retry 예산은 0이다.
- 처리량·출력 토큰·reasoning budget은 baseline과 treatment에서 명시적으로 기록한다.
  treatment는 이름에 적은 한 정책만 바꾸며 inventory/operation/call-plan 선택 공간과
  repair 범위는 그대로 둔다.
- baseline 한 run의 최대 total token 중 125%를 candidate별 누적 token 상한으로 사용한다.
  이 상한을 넘긴 후보는 실패이며 다음 후보 반복을 실행하지 않는다.

## Checkpoint와 재개

live runner는 각 cold cell 직전에 `inFlight`를 원자적으로 기록하고, 성공한 cell 직후
완료 prefix를 같은 report에 교체 저장한다. `--resume-report`는 schema version, case ID,
retry budget, cold-cell 순서와 설정이 현재 프로토콜에 정확히 일치하는 완료 prefix만
재사용한다. 완료된 cell에는 provider를 다시 호출하지 않는다.

프로세스가 `inFlight` 상태에서 종료되면 해당 요청이 provider에 도달했는지를 증명할 수
없으므로 runner는 자동 재호출을 거부한다. candidate 3 cold와 process-local warm 확인은
하나의 원자적 cell로 취급하며 둘이 모두 끝나기 전에는 candidate 3을 완료 prefix로
기록하지 않는다. 이 정책은 불확실한 재호출로 9회 상한을 넘기는 것보다 중단을 우선한다.

## Gate와 즉시 중단

각 run은 아래 순서로 평가하고, 필수 gate가 하나라도 실패하면 해당 run을 즉시 중단한다.

1. frozen checkpoint manifest와 SHA-256 digest 일치
2. `BCEModel` schema 및 canonical ID
3. class structure/reference/type integrity
4. collaboration call order, step provenance, argument source
5. 제공한 경우 downstream sequence projection

schema 오류, digest 불일치, 허용되지 않은 후보 ID, retry 발생, token 상한 초과도 즉시
중단 조건이다. bounded schema/semantic repair와 handoff는 기존 범위에서 허용하되 횟수를
기록하고 candidate 중앙값이 baseline보다 증가하면 채택하지 않는다. 실패한 run을 재시도해
성공한 것처럼 대체하지 않으며, 이미 완료된 다른 cell은 보존한다.

`compact`, `call-plan-low`, `operation-low` singleton은 서로 독립된 정책 측정이므로 한
singleton의 gate 실패가 뒤 singleton을 생략하지는 않는다. 각 결과 중 gate를 통과한 정책만
합성 candidate 설정에 포함한다. 합성 candidate 반복은 한 번이라도 실패하면 남은 반복을
즉시 중단한다.
세 baseline도 서로 독립된 측정이며 baseline 하나의 gate 실패가 singleton 측정을 생략하게
하지 않는다. 실패 baseline은 채택 가능한 결과로 세지 않되, 확보된 timing/token 관측은
비교 기준에 남긴다. 프로세스 종료로 계측을 회수하지 못한 cell은 중앙값 계산에서 제외한다.

baseline의 단계별 최대 output token에 50% 여유를 더하고 2K/4K/8K/16K 중 가장 작은
수용 tier를 후보 cap으로 쓴다. 어느 baseline에서든 `finish_reason=length`, schema failure가
관측된 단계는 기존 cap을 유지한다.

## Cold/warm cache 확인

cache 최적화 treatment에는 같은 입력으로 cold와 warm 단계를 한 cell 안에서 각각 기록한다.
이 두 단계는 별도 E1 cell을 추가하지 않으며(따라서 총 run 상한 9회를 늘리지 않는다),
cold는 빈
`ProcessLocalAcceptedUnitCache`에서 시작하고, warm은 cold가 수락한 unit만 읽는다.
warm에서 accepted inventory/operation/call-plan 단위가 hit이면 provider의 physical `llm_calls`는 0이어야 한다.
관측을 위해 남는 `physicalRequest=False` logical cache event는
호출 수에 합산하지 않는다. cache hit도 schema와 결정론 검사를 다시 통과해야 하며,
실패/partial repair 후보는 저장하지 않는다.

각 결과에는 run ID, treatment, cold/warm 상태, gate 결과, input/output token 수,
wall-time, reasoning 설정, cache unit/key/status와 digest metadata를 남긴다. prompt의
비공개 추론 내용이나 raw provider credential은 기록하지 않는다.

## 채택 판정

세 candidate 반복이 모두 schema·class·sequence·readiness gate를 통과해야 한다. 또한
repair/handoff 중앙값 비증가, compact input token 15% 이상 감소, total token 또는 wall
median 10% 이상 개선, wall p95 악화 10% 이하, warm physical call 0을 모두 만족해야 한다.
product-contract와 qualitative rubric은 생성 뒤 오프라인으로 검토하며 issue 수가 baseline보다
늘지 않았을 때만 `--review-report`로 결과를 확정한다. 검토 확정은 LLM generation을 다시
실행하지 않는다.
