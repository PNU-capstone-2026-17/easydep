# 유지보수성 rollup 계약

이 문서는 단계 경계 리팩터링 뒤에도 외부 산출물과 실행 범위가 유지되는지 확인할 최종
색인이다. 세부 prompt·repair 규칙을 복제하지 않고 각 bounded context의 README와 공개
contract test를 진실 원천으로 삼는다.

## origin/main 최종 감사

2026-08-28 rollup 시점에 fresh fetch한 `origin/main`은 통합 때 고정한
`d3caa86f49cc4c501cdfbdfd906f3b5f13b387b6`과 동일했다. 따라서 선택적 통합 이후
추가로 유입하지 못한 upstream commit은 없다.

## Bounded context 소유권

| 경계 | 소유권 | 상세 문서 |
|---|---|---|
| `app.cloudkb` | 공급자 지식·region·planning primitive | `app/cloudkb/README.md` |
| `app.requirements` | 입력 계약, resource/modeling 단계와 실행 조정 | `app/requirements/README.md` |
| `app.design` | typed 설계 모델, 검증과 결정론 projection | `app/design/services/README.md` |
| `app.orchestration` | cross-stage provider·checkpoint·artifact 조정 | `app/orchestration/README.md` |
| `app.implementation` | planning·runtime·delivery leaf | 각 하위 `README.md` |
| `app.metrics` | 선택적 tracing·LLM stall endpoint probe | `app/metrics/README.md` |

## 체크포인트 정책

지원 범위는 현재 checkpoint schema로 저장한 세션의 save→프로세스 재시작→resume이다.
과거 MySQL checkpoint shape를 읽는 parser, fallback 또는 migration은 범위 밖이며 새 facade로
되살리지 않는다. 현재 HTTP·JSON 응답 및 checkpoint 키는 변경하지 않는다.

## 최종 회귀 기준

- frozen state는 requirements→class→sequence→API→ERD→deployment 계약을 통과하고,
  implementation의 공개 parser가 같은 class/OpenAPI/ERD 산출물을 exact IR로 소비해야 한다.
  저장된 sequence model의 결정론 projection도 고정 digest와 일치해야 한다.
- class accepted-unit cache는 단일 use-case cold 실행에서 inventory·operation·call-plan
  proposal을 각각 1회 호출하고 같은 process의 warm 실행에서는 외부 호출이 0이어야 한다.
  cache 객체·key metadata는 `ArchitectureState`, checkpoint 또는 disk JSON에 저장하지 않는다.
- 별도 frozen E1 cache transport 실측은 cold physical 21회 뒤 warm physical 0회와 artifact
  byte equality를 확인했다. 다만 양쪽 product gate는 실패했으므로 report 전체는 실패로
  분류하고 최적화 설정은 채택하지 않았다. 상세와 ignored artifact SHA-256은
  `evaluation/class_design_optimization_result_20260828.md`에 기록한다.
- cache cold/warm은 `tests/test_class_design_service.py`, 현재 schema의 프로세스 재시작·재개는
  `tests/test_session_store.py`와 `tests/test_orchestration_checkpoint.py`의 공개 contract suite를
  최종 rollup gate에서 함께 실행해 보장한다.
- 리팩터링 단계의 LLM logical/physical 호출 수, 병렬도와 transport retry는 각 단계의 공개
  injection seam을 사용하는 contract test가 고정한다. 의미 수리는 숫자 상한 대신 누적 이력,
  finding 감소 및 새 전략·새 candidate라는 전진 조건으로 계속되며, 같은 실패를 반복하면
  `STALLED`로 멈춘다. `delegate_repair`는 사용자가 선택할 수 있는 일반 액션이고 auto mode도
  별도 우회 없이 그 액션을 선택한다. 최종 회귀에서는 실제 NIM을 추가 호출하지 않는다.
- `app/core` tracked path·import와 구 requirements orchestration active import는 0이어야 한다.
- 테스트는 production prompt literal이나 private helper가 아니라 공개 service/spec/report를
  검증한다.
