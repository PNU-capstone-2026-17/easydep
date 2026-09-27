# Testing 병목 분석 — 2026-09-27

## 확인된 흐름

- Testing 입력은 구현 산출물 ID와 계약을 고정한 뒤 임시 application tree로 복원·digest 검증한다: [`artifact_source.py`](../app/testing/utils/artifact_source.py#L25), [`materialized_testing_application`](../app/testing/utils/artifact_source.py#L80).
- Dynamic functional gate가 선택되면 애플리케이션 기동/readiness가 verification graph 호출보다 먼저다: [`verification.py`](../app/testing/runtime/verification.py#L377). Graph 내부에서는 dynamic과 static branch가 병렬로 실행된다: [`testing_graph.py`](../app/testing/graphs/testing_graph.py#L64).
- Arazzo workflow 계획은 후보 최대 4개까지 병렬 생성하지만, 생성 후 실제 workflow는 순차 실행한다: [`dynamic_functional.py`](../app/testing/nodes/dynamic_functional.py#L1053), [`dynamic_functional.py`](../app/testing/nodes/dynamic_functional.py#L1670), [`dynamic_functional.py`](../app/testing/nodes/dynamic_functional.py#L1867).
- SUT repair가 여러 implementation owner group으로 나뉘면 각 그룹마다 `request_owner_repair` 후 `_monitor_implementation`으로 구현 job 완료를 순차 대기한다. 이 완료 경계에는 구현 job의 하류 최종 integration/build/startup도 포함되므로, 다음 source-owner group은 그 뒤에 시작된다. 그룹별로 전체 Testing을 다시 실행하지는 않고, 모든 그룹 완료 뒤 Testing을 한 번 재실행한다. 재검사에서 새 repairable SUT defect가 나오면 다음 repair cycle로 이어질 수 있다: [`workspace/service.py`](../app/workspace/service.py#L2916), [`workspace/service.py`](../app/workspace/service.py#L2961), [`workspace/service.py`](../app/workspace/service.py#L2660).

## 측정 근거와 한계

- 과거 Testing command `8101461e`는 12분 35초였다. UC10 `POST /admin/terms` 요청은 8.268초였고, UC1은 HTTP 400을 기록했다. 당시 source-owner repair 호출은 없었으므로 나머지 시간을 특정 모델·검사 단계에 귀속할 수 없다.
- 저장된 UC1만 재생한 부분 검증은 총 146.494초였으며, 여기에는 앱 startup이 포함된다. HTTP 결과는 200, body는 `[]`, 계약·성공 기준·semantic 판정은 PASS였다. 이는 전체 Testing 실행 시간이 아니다.
- 별도 실제 OSS 계획 호출 두 건은 5.701초와 1.068초였다. 이는 해당 호출의 관측 시간일 뿐, 전체 Testing 단계 비용이나 일반 성능을 대표하지 않는다.
- root/Terra 관찰에서 integration attempt 9가 성공한 다음 ScheduleView source owner가 시작됐다. 이는 위 구현 job 완료 경계와 일치한다. owner 간 진행 순서는 확인됐지만 각 phase의 개별 시간은 저장되지 않았다.

따라서 구현 구조상 여러 owner repair가 Testing 완료까지 누적 비용을 만들 가능성은 확인된다. 다만 현재 자료로 그 구조가 전체 wall time의 지배적 병목이라고 정량 결론 내릴 수는 없다. 단계별 기동, plan 호출, static 검사, HTTP workflow, owner repair/build 시간을 분리한 계측과 최적화는 아직 수행하지 않았다.

## 미적용 후보

기동/health, artifact 복원, plan 호출, workflow별 HTTP, static 검사, owner repair와 재검증 시간을 기존 progress/result 경계에서 분리 측정한 뒤 병목을 선택한다. 캐시 재사용, 단계 병렬화, 실행 구조 변경 등 성능 최적화는 아직 적용하지 않았다.

## 22시 이후 검증 보정

- 저장된 focused Java unit 결과 세 쌍에서는 `compileJava`와 `compileTestJava`가 모두 `FROM-CACHE`였고, source-owner 검증의 `compileJava`는 실제 실행됐다. 현재 duration만으로 캐시 구조를 다시 설계할 근거는 없다.
- 실제 source 출력 파일 크기는 6.8–9.5KB 범위로 확인됐다. 토큰 사용량 및 모델 호출만의 시간은 저장되지 않아, 파일 크기와 모델 지연을 인과 관계로 기록하지 않는다.
- integration attempt 16은 backend test·frontend build·application startup을 포함해 131,093ms에 성공했다. 이는 반복 integration 비용의 실제 한 표본이지만, 전체 Testing 병목의 비율을 뜻하지 않는다.
- owner batch scheduler/reader와 Workspace handoff의 집중 검사는 총 3개가 PASS했다. 다만 현재 live run에는 아직 채택되지 않았으므로 실제 속도 개선은 측정되지 않았다. optional `edit_source` 실제 검증도 진행 대기 중이다.

현재 run의 저장 보고서는 implementation 단계 중심이어서 Testing 내부 비용을 실측할 근거가 없다. `fixed_arazzo_document` 재사용 시 계획 LLM은 생략되므로 중복 계획 생성으로 단정하지 않는다. `verification.py`의 graph 진입 전 application startup과 dynamic node의 동일 workflow/input prior-result `REUSED` 경계는 전체 재사용 때 startup 낭비 후보가 될 수 있으나, 이번 실행에서 발생 여부와 시간 비중은 미확인이다. 다음 동일 checkpoint Testing 관찰에서만 측정하며, 지금은 prelaunch cache 분기나 다른 구조 변경을 추가하지 않는다.

UC11의 500은 성능 병목이 아닌 wire-contract 결함이다. 보존 runtime log `681d523…e6184.log`의 `09:00 AM`→`LocalTime` 역직렬화 실패는 OpenAPI의 형식 없는 `string`과 생성 Java `LocalTime`의 불일치에서 발생했으며, 서비스-owner 타임아웃의 근거로 취급하지 않는다.
