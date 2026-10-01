# 수강신청 앱 Testing 재개 점검 (2026-10-01)

## 검증 기준

- 합성 수강신청 앱 `8b6702dd-1560-423f-bf06-d2e755cdf932`의 완료된 구현 체크포인트 `1683cb31acbf4e03828284cd213cbea1`을 기준으로 삼았다. 시나리오팩이나 보정된 앱 소스는 실제 구현 성공의 근거로 사용하지 않는다.
- 실제 Testing 분기 `923b788a-b8a9-48ca-a8fd-5065a1b27986`의 저장 결과는 `validationSkipped=false`, 전체 11개 워크플로 중 4개 PASS·7개 FAIL이다. 정적/IaC 검사는 PASS지만 전체 Testing은 FAIL이다.
- 이번 소규모 모델 검증은 계획 생성·투영·컴파일·문서 검증까지이며 HTTP 워크플로 실행이나 종단 PASS를 뜻하지 않는다.

## 확인된 결함과 조치

| 위치 | 실제 원인 | 이번 조치와 검증 | 남은 경계 |
| --- | --- | --- | --- |
| UC2·UC10 JSON 문자열 응답 | 생성된 `ResponseEntity<String>` 컨트롤러가 `application/json` 계약인데 평문 문자열을 반환한다. | 컨트롤러 생성기에서 JSON 응답 문자열을 명시적으로 JSON 인코딩하도록 수정했다. 집중 테스트 1개가 통과했고, 동결된 두 API 메서드의 렌더링에 새 코드가 들어가는 것을 확인했다. | 완료된 구현 체크포인트의 컨트롤러는 immutable이며 41개 작업 중 어느 것도 해당 파일의 쓰기 소유권이 없다. 기존 Testing 수리로는 이 패치가 반영되지 않는다. 새 구현 스캐폴드 생성이 필요하다. |
| UC8 계획 | 개요 모델이 `registerForCourse` 선행 작업을 누락하고, 서로 다른 자원 종류의 identity relation을 만들었다. | 그래프 입력에서 근거 없는 교차-operation identity relation을 제외하고 개요의 선행 occurrence를 보존하도록 했다. 집중 테스트 3개 통과. OSS 제품 경로의 UC3 계획은 구조 검증·컴파일·문서 검증 통과. | UC8의 실제 개요 재생성은 여전히 `registerForCourse`를 빠뜨렸다. 한 차례의 범용 프롬프트 변형 실험도 이를 해결하지 못했다. UC8 HTTP 성공을 주장할 수 없다. |
| UC7 실행 | 실제 생성 앱의 `CREATE`는 요청에 `courseOfferingId`가 없으면 `success:false, offeringId:null`을 반환한다. 해당 API/엔티티에는 요구되는 `waitlistEnabled`·published 상태도 없다. | 실제 체크포인트의 API·컨트롤러·서비스를 읽어 결함 위치를 분리했다. 생성 앱 산출물이나 DB는 수정하지 않았다. | 현 구현 API만으로는 full + waitlist-enabled offering을 만드는 유효한 워크플로를 입증할 수 없다. 상류 API/설계 계약 보완 및 새 구현이 필요하다. |

## 재개 조건

현재 체크포인트를 Testing만 다시 돌려도 UC2·UC10의 불변 컨트롤러와 UC7의 누락 계약은 고쳐지지 않는다. 먼저 시스템이 새 설계/API 근거에서 구현 스캐폴드를 재생성하고, 작은 실모델·집중 검증으로 UC7·UC8의 선행 상태를 확인해야 한다. 그다음에만 새 구현 결과의 실제 HTTP Testing을 실행한다. 자동 수리 결과나 계획의 구조 검증을 PASS로 승격하지 않는다.
