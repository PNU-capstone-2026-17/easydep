# 클래스 설계 최적화 E1 실행 결과 (2026-08-28)

## 결론

고정 `e1-aws` 입력으로 실행한 live protocol의 최종 판정은 **미채택**이다. 세 baseline과
세 단일 treatment 중 필수 machine gate를 통과한 실행이 없었고, 합성 후보 첫 실행도
class call-source와 sequence readiness gate를 통과하지 못했다. 프로토콜에 따라 후보 2·3과
same-process warm 검증은 실행하지 않았다.

따라서 운영 기본값은 다음과 같이 유지한다.

- operation payload compact: `false`
- inventory/operation/call-plan reasoning: `medium` / `medium` / `medium`
- inventory/operation/call-plan cap: `16384` / `8192` / `8192`

accepted-unit cache의 protocol, bounded process-local 구현, 재검증, single-flight, sealed-miss
계약은 유지한다. 다만 이번 live 후보가 cold gate에서 수락되지 않아 `warmPhysicalCallsZero`는
충족하지 못했으며, 이 실행 결과를 cache의 실제 호출 절감 채택 근거로 사용하지 않는다.

## 실행 결과

유효 report는
`artifacts/runs/easydep-class-opt-e1-20260828-approved-rerun/live.json`이며 저장소의 artifact
ignore 정책에 따라 commit하지 않는다. SHA-256은
`5384aff177d320112ff6ddb914f3ca75d3584f6e9ede15ec8cdd9530a0003e0a`다.

| cell | 상태 | physical calls | total tokens | repair / handoff | 중단 근거 |
| --- | --- | ---: | ---: | ---: | --- |
| baseline-1 | failed | 18 | 61,421 | 3 / 3 | call-source type, sequence flow order |
| baseline-2 | failed | 계측 회수 불가 | 계측 회수 불가 | 계측 회수 불가 | invalid local value-object fragment |
| baseline-3 | failed | 4 | 22,567 | 1 / 1 | schema repair 뒤에도 invalid JSON |
| compact | failed | 계측 회수 불가 | 계측 회수 불가 | 계측 회수 불가 | UC3 collaboration projection 누락 |
| call-plan-low | failed | 28 | 91,221 | 8 / 8 | UC2 collaboration projection 누락 |
| operation-low | failed | 4 | 13,715 | 1 / 1 | Control cohesion 위반 |
| candidate-1 | failed | 16 | 63,026 | 3 / 3 | call-source type, sequence flow order |

baseline-2와 compact는 provider 호출 뒤 local failure가 이전 runner의 checkpoint 경계를
벗어나 프로세스가 종료됐다. report는 두 cell을 `measurementStatus`가
`unavailable-after-process-exit`인 실패로 복구했으며 provider를 다시 호출하지 않았다.
이 값들은 중앙값과 p95 계산에서 제외했다.

candidate-1은 baseline에서 계산한 `4096 / 8192 / 4096` cap을 사용했다. operation 응답 한
건이 `finish_reason=length`에 도달해 bounded schema repair로 복구됐으나, 해당 단계 cap을
축소하지 않는다는 규칙과 전체 machine-gate 실패 때문에 나머지 후보 반복을 즉시 중단했다.
최종 report는 `status=stopped`, `stoppedAt=candidate-1`, `coldGenerationCount=7`,
`decision.adopted=false`다.

## 사전 실행 격리

최초 사전 실행은 full generation 뒤 readiness finding을 서로 다른 Pydantic `Finding` 타입으로
검증하면서 local harness가 종료됐다. 이 결함은 production 생성 결과와 무관한 adapter
정규화 문제였고 `fix: normalize cross-stage readiness findings`에서 수정했다. 해당 실행은
유효 protocol report에 합치거나 baseline으로 재사용하지 않았으며,
`artifacts/runs/easydep-class-opt-e1-20260828-approved/live.json`에 불완전 `inFlight` 기록만
남겼다. 이 별도 report의 SHA-256은
`a4e71051d9913ed4803efdbfc67811dbad209f73921303a0b17cc6ed4c4a340b`다.

## 판정 해석

report에 계산된 compact input reduction은 compact cell 계측을 프로세스 종료 후 회수하지
못했으므로 채택 근거가 아니다. token/wall 개선은 각각 음수였고, repair/handoff 비증가,
전체 machine gate, warm physical call 0, qualitative issue 비증가 조건도 충족하지 못했다.
따라서 qualitative review를 별도로 확정하지 않고 `pending`으로 남긴다.

## 별도 cache transport 실측

후보 실험을 재개하지 않고 현재 baseline 설정에서 cold 1회와 같은 프로세스의 sealed-cache
warm 1회를 추가로 실행했다. report는
`artifacts/runs/easydep-class-cache-verification-20260828/live.json`이며 SHA-256은
`751bfccf2fba448b7039c294861aa8c29452d90a0bd3e89a89faa4f009264d5b`다.

- cold: physical LLM 21회, accepted-unit cache miss를 채운 뒤 class model 생성
- warm: physical LLM 0회, cache hit 9회, cold와 외부 class/sequence artifact byte-equivalent
- 실행 조건: UTF-8 mode, 독립 run ID, SDK retry budget 0

warm의 provider 0회는 실측으로 확인됐지만 cold와 warm 모두 E1 product machine gate에서
누락된 collaboration projection 때문에 실패했다. 오프라인 재판정은 이 둘을 분리해
`warmPhysicalCallsZero=true`, `coldMachineGatesPassed=false`,
`warmMachineGatesPassed=false`, 전체 `status=failed`로 기록한다. 따라서 이 실측도
최적화 설정 채택 근거로 사용하지 않으며 provider 호출을 재시도하지 않는다.
