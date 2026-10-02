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
| UC7 실행 | 실제 생성 앱의 `CREATE`는 요청에 필수 `courseOfferingId`가 없으면 `success:false, offeringId:null`을 반환한다. 호출자 제공 ID는 가능한 설계이며, 이 부분은 계획이 필수값을 공급하지 않은 결함이다. 별도로 API/엔티티에는 요구되는 `waitlistEnabled` 상태가 없다. | 실제 체크포인트의 API·컨트롤러·서비스를 읽어 두 원인을 분리했다. 생성 앱 산출물이나 DB는 수정하지 않았다. | ID를 채워도 현 구현 API만으로는 waitlist-enabled 상태를 설정·검사할 수 없다. 이 계약은 상류 설계에서 보완하고 새 구현으로 검증해야 한다. |

## 재개 조건

현재 체크포인트를 Testing만 다시 돌려도 UC2·UC10의 불변 컨트롤러와 UC7의 누락 계약은 고쳐지지 않는다. 먼저 시스템이 새 설계/API 근거에서 구현 스캐폴드를 재생성하고, 작은 실모델·집중 검증으로 UC7·UC8의 선행 상태를 확인해야 한다. 그다음에만 새 구현 결과의 실제 HTTP Testing을 실행한다. 자동 수리 결과나 계획의 구조 검증을 PASS로 승격하지 않는다.

## 후속 설계 근거 점검 (2026-10-01)

이 절의 `UC11`은 아래 검증에 사용한 원본 앱의 강의 개설 관리 유스케이스 ID다. 별도 동결 요구사항 산출물에서는 같은 역할의 명세가 `UC12`로 번호 매겨져 있으므로, 두 체크포인트의 ID를 섞어 RTM 연결 근거로 사용하지 않는다.

- 실제 요구사항 `RR9`는 대기열이 활성화된 강의의 정원 초과를 전제로 하지만, `RR13`의 강의 관리 입력에는 대기열 설정이 없다. 실제 UC7은 이 조건을 trigger 문장에만 두며, UC11과 클래스 모델에는 설정·지속 상태가 전달되지 않았다. 공개 상태도 UC11 publish 분기는 있으나 생성된 엔티티에 저장되지 않는다.
- 동결된 클래스 모델과 OpenAPI는 `CourseOffering.course/term/section` 등 중첩 객체를 필수로 요구하면서, 강의 관리 요청·Control에는 이를 만들 입력이나 명시된 서버 출처가 없다. 실제 UC4 응답에서 학생과 해당 연관 객체가 null인 것은 단순 직렬화 문제가 아니라 이 상류 계약 불일치의 결과다.
- 제품과 같은 OSS 모델(`openai/gpt-oss-120b`)에 실제 동결 유스케이스를 주고, 여러 유스케이스 사이의 명시적 지속 상태를 비교하도록 한 한 번의 작은 inventory 호출에서는 `waitlistEnabled`와 공개 상태가 Entity 후보에 나타났다. 독립적인 주문 도메인 한 번에서도 스키마에 맞는 Inventory가 생성됐다. 이 결과는 inventory 단계의 개선 가능성을 보일 뿐 UC11 연산/API/구현의 성공 증거는 아니다.

### 범용 상태 검토의 실제 효과와 남은 계약

- 같은 inventory 프롬프트라도 다른 OSS 수락본은 `waitlistEnabled`를 빠뜨렸다. 입력 부족만의 문제가 아니라 의미상 누락을 받아들이는 검증 경계 문제임이 확인됐다.
- 별도의 작은 OSS 검토는 동결 클래스 모델에서 공개/취소 상태와 대기열 활성화 누락을 찾았고, 독립적인 주문 도메인에서는 외부 카탈로그 가용성을 내부 Entity 필드로 잘못 요구하지 않도록 제한한 후 PASS했다. 시스템은 이 검토에서 원문 인용·UC·Entity를 확인한 finding만 기존 inventory 수리 루프로 넘기도록 보완했다. 집중 테스트 3개가 통과했다.
- 제품 함수로 동결 inventory부터 UC11 연산까지 재생한 결과, inventory는 두 번의 수리와 두 번의 상태 검토 후 `CourseOffering.waitlistEnabled`, `published`를 포함해 수락됐다. 하지만 수락된 UC11 Control/Request는 `operationType`, `courseOfferingId`, `instructorId`, `meetingSchedule`, `enrollmentCapacity`만 전달했다. `waitlistEnabled` 설정 및 필수 course/term 출처는 여전히 연산에 연결되지 않았다. 이 실험은 OSS 6회 실호출(인벤토리 3, 상태 검토 2, UC11 연산 1)이었고 구현/HTTP Testing은 실행하지 않았다.
- 기존 요구사항 선택지 검토는 모든 UC 후보를 한 번에 읽더라도 선택한 UC의 직접 연결 요구사항만 출처로 인정한다. RR9·RR13을 함께 근거로 삼는 질문은 이 경로에서 아직 만들 수 없다. 범위를 넓힌 시험 호출은 관련 없는 UC6의 파일 형식을 질문했고, 인용도 현 검증 계약과 맞지 않았다. 이 프롬프트 변형을 제품에 반영하지 않았다. UC11의 대기열 설정 방식은 시스템이 실제로 질문·반영할 수 있는 경로가 생기기 전에는 Codex가 임의로 확정하지 않는다.

## 상태 생산자 규칙 실모델 선검증과 검증 경계 조사

- 실제 사용 OSS 모델에 소비 상태 하나와 생산자 연산 하나를 제시하면, 동결된 `UC7`의 `waitlistEnabled`가 `UC11` 연산에서 설정되지 않음을 판별했다. 독립 도서관 사례에서도 설정 입력이 없는 경우는 미확립, 명시적 `loanable` 입력을 저장하는 경우는 확립으로 구분했다. 문서화된 서버 계산으로 상태를 저장하는 독립 사례도 확립으로 판정했다. 다만 같은 응답의 사용자 결정 필요 여부는 `false`였으므로 질문 생성까지 검증된 것은 아니다. 이는 **좁은 판단의 가능성**이지 제품 경로의 검증 통과가 아니다.
- 한 Entity의 여러 상태·UC·연산을 한꺼번에 검토시키면 `capacity`와 변동 좌석 상태를 혼동하고 `waitlistEnabled`를 놓쳤다. 소비 상태 추출과 상태별 생산자 검토로 나누면 동결 수강신청 사례에서 `published`의 명시적 publish 전이는 확립, `waitlistEnabled`는 생산자 없음으로 구분했으나, 모든 Entity·도메인에 대한 안정성은 검증되지 않았다. 따라서 광범위한 상태 생산자 차단 규칙을 제품에 추가하지 않았다. 특히 생산자 누락을 곧바로 UC11 입력으로 자동 보충하면 설정 주체라는 미결정 정책을 임의로 정하게 된다.
- 서브에이전트가 요구사항→클래스, 클래스→API→구현, 테스팅 경계를 읽기 전용으로 조사했다. 확인된 모순은 다음과 같다: (1) 클래스 공개 계약 검토 프롬프트가 `system_result`의 `usage=both`를 결과 반환만으로 면제한다고 말하면서 다른 문장은 입력 바인딩을 요구했다. 이 프롬프트를 `usage=result`만 면제하도록 좁히고 집중 테스트 42개를 통과했다. (2) API 프롬프트는 Boundary 반환형을 기준으로 요구하지만 정규화기는 Control 반환형으로 204/본문을 결정한다. (3) 클래스 협업에서 허용한 runtime 값과 이전 호출 결과를 API 정규화기가 버려, API 검증기와 생성 컨트롤러가 실패할 수 있다. (4) Arazzo 문서 검증기는 연쇄 비교식을 받아들이지만 실행기는 거부한다. 각 항목은 경계의 실제 코드 계약 불일치이며, (2)~(4)는 이번 상태 규칙과 별도로 수정·집중 검증해야 한다.
- 테스팅의 선행 조건과 값 리터럴은 모델에 전달되지만 구조화된 조건이 아니라 조언 텍스트로 남아, 타입에 맞는 값이 의미상 조건을 위반할 수 있다. 이는 검증기끼리의 직접 모순과 구분되는 검증 공백이다. 조사한 Workspace 수리 경로에서는 결함 소유권과 근거 전달의 모순을 찾지 못했다.
- 요구사항 내부 감사에서는 일부 규칙만 검토한 relationship 의미 검토도 `OK`·`COMPLETED`가 될 수 있는 반면, 명세 검토는 미검토 규칙을 `UNGROUNDED`로 처리하는 상태 불일치를 찾았다. 또한 실질적 의미 선택을 묻는 질문이 표시돼도 답변이 비어 있으면 일부 피드백 게이트가 전진할 수 있다. 이 둘은 제품 결정을 놓칠 수 있으므로 별도 집중 수리가 필요하다.
- 배포 경계에서는 외부 서비스 URL이 설계 입력으로 수락된 후 placement에서 값이 사라지고 Terraform에 다시 필수 외부 endpoint 변수로 나타나는 중복 입력을 찾았다. 기존 집중 테스트가 이 변환을 재현했다. 공급자별 예외가 아니라 명시된 값을 보존하고 값이 없을 때만 늦은 입력을 요구하는 범용 수정 후보로 분류했으며, 후속 수정은 아래에 기록했다.

## 검증 경계 후속 수정

- 요구사항 relationship 검토에서 미검토 규칙이 남으면 더 이상 `OK`·`COMPLETED`가 되지 않는다. 부분 검토된 수리 후보 역시 `clean`으로 수용하지 않는다. 필수 의미 질문이 남아 있으면 빈 답변으로 명세 게이트를 통과하지 못한다. 관련 집중 테스트를 통과했다.
- 클래스의 actor-entry Boundary→Control 호출에서는 공개 반환형과 Control 반환형이 일치해야 하고, HTTP 진입점에서 표현할 수 없는 runtime·이전 호출 결과·다른 호출 출처는 클래스 단계에서 finding으로 돌려 협업 단위 수리 대상이 된다. 내부 Control/Entity 호출의 runtime 값은 유지한다. API 정규화도 같은 출처를 조용히 삭제하지 않고 거절하며, 204는 void 결과에만 쓴다. 동결 수강신청 클래스 체크포인트에서 새 규칙 finding은 0건이었다. 실제 OSS 모델의 동결 UC1 단일 API 제안은 `GET /course-offerings`와 200 응답을 냈고, 정규화 후 `CourseOfferingInfo` 배열 응답이 유지됐다. 이 한 건은 전체 API 단계 통과의 증거는 아니다.
- Arazzo 단순 조건은 실행기가 거부할 연쇄 비교식을 문서 검증에서 먼저 거절한다. 집중 검증에서 잘못된 문서는 HTTP 요청 전 `TEST_DEFECT`로 분류됐다. 전제조건 문구가 리터럴 선택에 전달되지만, 그 의미를 독립적으로 확인할 구조화 상태 효과 계약은 아직 없다. 이는 이번에 고친 문법 모순과 별개의 품질 공백이다.
- 이미 제공된 외부 endpoint 값은 배포 계획과 ResourcePlan으로 전달되고, 불필요한 두 번째 Terraform 필수 입력을 만들지 않는다. 내부 연결은 공급자 파생 주소를 유지한다. 직접 제공된 값은 셸 리터럴로 안전하게 출력하며, 민감 endpoint 값을 설계에 직접 담는 것은 정규화에서 거절한다. 집중 테스트를 통과했다.
- 위 결과는 경계별 집중 검사와 한 건의 API 실모델 호출이다. 수정된 경계의 합동 집중 회귀 13개도 통과했다. 새 수강신청 앱의 전체 설계→구현→테스팅 종단 성공을 뜻하지 않는다. 별도 source-binding 테스트 3개의 실패는 이번 HTTP projection 변경이 아니라 기존 테스트 기대와 현재 협업 실행 계약의 불일치였다. 바인딩을 매개변수별로 비교하고 실행 중인 조상 호출의 결과를 사용할 수 없음을 명시하도록 테스트를 고쳤다. 파일 전체 35건은 기본 pytest의 terminal 플러그인·PTY 종료 지연을 피하는 최소 플러그인 실행에서 exit code 0으로 통과했다.

## 후속 경계 확인

- 소형 앱 `dab1a159-acec-410f-a8f5-af9fe235e2cb`의 완료된 구현 job에 대응하는 동결 클래스 모델·PlantUML·시퀀스 모델·API 모델을 현재 제품 함수로 다시 투영했다. API endpoint 2개와 OpenAPI path 2개가 만들어졌고 API 검증 finding은 0건으로 `clean`이었다. 첫 검사에서 보인 클래스 누락 6건은 잘못된 클래스 산출물 입력을 고른 검사 착오였으며 제품 결함으로 분류하지 않는다.
- 수강신청 e1-aws의 UC1 클래스 모델에는 관련 interaction 2개가 있다. 그러나 저장된 구형 API 모델은 현재 제안 스키마의 필수 summary·응답 description을 채우지 않아, 이를 현재 제안으로 역변환하는 확인은 불가능했다. 구형 산출물 호환을 위한 코드 변경이나 전체 14개 interaction 재생성은 하지 않았다.
- 기존 앱에서 제공된 `start_design` 액션을 클래스/API 국소 재실행으로 오인해 한 번 제출했으나 요구사항부터 설계 전체를 시작하는 명령임을 확인하고 중단 요청했다. 명령 `48448aa2-e812-4b70-b28d-a8433d7c01af`는 `CANCELLED`이며 검증 근거로 사용하지 않는다. 이 확인은 Workspace의 새 종단 성공이나 구현·테스팅 통과를 뜻하지 않는다.

## 소형 앱 Testing 실제 재실행

- 계산기 앱 `887525c0-aa29-45d7-afeb-32b6b3699022`의 완료된 구현 job `494b5d7a2b99454d81bd74c6ebf911bf`에서 Workspace가 제공한 `start_testing` 액션 하나를 실행했다. 명령 `4e4bb591-752d-4b91-910e-bdb094513095`는 157.940초 후 `COMPLETED/PASS`였고 `validationSkipped=false`였다. 시나리오 응답 교체 기록은 0건이었다.
- 이전 실패의 원인은 동결 `caller_input`의 `requiredValueRef`가 문자열 path 입력의 리터럴 근거로 연결되지 않은 것이었다. 해당 출처가 현재 유스케이스의 target operation에 확인되는 경우에만 허용하도록 좁혔고, 다른 유스케이스의 동일 ref·`system_result`·resource identity 반례를 검사했다. 동일 근거의 OSS `openai/gpt-oss-120b` 소규모 그래프 호출과 투영 검증이 통과했다.
- 이 수정의 신규 focused 테스트는 `.venv`의 pytest 최소 플러그인 실행에서 exit code 0으로 통과했고, `dynamic_functional.py` 구문 검사와 전체 diff whitespace 검사도 통과했다.
- 실제 Testing에서 Static·IaC·Dynamic Functional 게이트가 모두 PASS했고, Arazzo workflow 1/1이 실행·통과했다. `GET /sum/3.5/2.7`은 HTTP 200과 응답 `6.2`를 반환했으며 상태코드·계약·의미 검사 모두 PASS했다. 자동 수리 시도 0건, blocking finding 0건이다. 이는 소형 앱의 결과이며 수강신청 앱 11개 workflow의 성공 증거는 아니다.

## 수강신청 앱 재개 전 소규모 확인

- 원본 앱 `8b6702dd-1560-423f-bf06-d2e755cdf932`의 최신 Testing 분기 `12886e34-c56e-4229-970e-408ad185086b`은 11 workflow 중 4 PASS·7 FAIL로 저장돼 있다. UC2/UC4/UC10은 생성 구현과 API 응답 계약 불일치, UC3/UC5/UC7/UC8은 계획 입력·선행 작업 근거 문제로 분리됐다. UC7은 HTTP 이전의 `offeringId=None`, UC8은 waitlist entry 생산자 미선택이므로 이 두 실패만으로 `waitlistEnabled` 런타임 결함을 주장할 수 없다. 해당 앱의 현재 명령에는 재실행 액션이 제공되지 않아 새 Testing을 제출하지 않았다.
- 좁은 OSS `openai/gpt-oss-120b` 검토는 UC7의 waitlist 활성화 조건과 관리 UC의 명시 효과를 비교해 생산자 없음으로 판정했고, 명시 setter가 있는 독립 도메인은 생산자 확립으로 구분했다. 첫 검토에서는 질문 필요 여부를 거짓으로 답했다. 생산·기본값·외부·파생 출처가 모두 없고 정책 선택 없이는 필수 동작을 정할 수 없다는 기준을 명시한 재검토에서는 수강신청 사례만 질문 필요로 바뀌고 setter·외부 상태·파생 상태 반례는 질문하지 않았다. 그러나 모델이 근거 없는 필드명을 질문 후보에 넣었으므로 이를 그대로 사용자에게 제시하지 않는다. 현재 제품 질문 경로도 UC 하나에 직접 연결된 요구사항만 인용하고 답변 효과도 그 UC에 제한돼, 소비 UC와 관리 UC를 잇는 선택지 질문은 아직 만들 수 없다.
- 별도 보존 TestingInput(분기 `5cb3e2fd-8c97-4b37-a067-49d04c59fbb8`, 요구사항 v2981·UC v2984·OpenAPI v2989)의 UC7 실제 제품 그래프 프롬프트(약 4.2만 자)를 OSS에 한 번 보낸 첫 결과는 검색 GET → 대기열 등록을 선택해, 읽기 전용 조회가 유일한 자원 생산자일 수 없다는 검증에 걸렸다. 제품의 기존 1회 correction 형식으로 같은 오류를 전달하자 강의 개설 생성 POST → 대기열 등록으로 바뀌었다. 이후 고정 입력 8개를 선택했고 투영·Arazzo 컴파일·OpenAPI 문서 검증까지 통과했다. HTTP는 실행하지 않았으며, 관리 API가 대기열 활성화 상태를 실제로 설정한다는 보장도 없으므로 UC7 Testing PASS로 취급하지 않는다.
- 같은 동결 입력의 UC8에서 생산자 선택 단계를 생략한 그래프-only 실험은 배열 첫 원소의 존재를 가정해 문서 검증에서 실패했다. 이 부분 실험은 전체 제품 경로의 실패 증거가 아니다. 이어 `_generate_candidate_workflow` 전체 경로를 한 번 실행하자 OSS 5회·15.44초에 outline `manageTerm → manageCourseOffering → joinWaitlist`, 생산자 선택 `cancelWaitlistEntry ← joinWaitlist.bodyValue`와 `joinWaitlist.offeringId ← manageCourseOffering.bodyOfferingId`를 만들었다. 최종 그래프는 `manageTerm → manageCourseOffering → joinWaitlist → viewWaitlistEntries → cancelWaitlistEntry`였고 컴파일·Arazzo/OpenAPI 문서 검증을 통과했다. HTTP 실행은 없으며 `waitlistEnabled` 설정·검증 근거도 없으므로 UC8 실제 Testing PASS로 승격하지 않는다.
- UC7도 전체 `_generate_candidate_workflow`를 별도로 1회 실행하자 OSS 3회·12.585초에 `manageTerm → manageCourseOffering → registerForCourse ×2 → joinWaitlist` 그래프를 만들고 컴파일·Arazzo/OpenAPI 문서 검증을 통과했다. 정원 2명으로 설정한 뒤 앞선 등록 둘이 같은 강의 개설 ID를 사용하도록 연결했다. 이 경로도 `waitlistEnabled`는 outline 문구에만 있고 강의 관리 요청·상태 효과·검증 기준에는 없으므로 RR9 조건의 실제 충족 증거는 아니다.
- UC5 전체 플래너는 OSS 8회·18.513초에 `manageTerm → manageCourseOffering → registerForCourse → viewRegistrationsAndSchedule → dropRegistration` 체인을 만들었다. 조회 결과는 고정 강의 ID로 필터해 등록 ID를 삭제에 연결했고 컴파일·Arazzo/OpenAPI 문서 검증을 통과했다. 반면 UC3은 OSS 10회·17.571초 후 graph 호출 전에 `No semantically valid finite collection identity and anchor pair was selected`로 중단됐다. 첫 번째 joint 선택은 강의 ID 앵커와 등록 목록의 강의 ID→등록 ID 대응을 선택했지만, 두 번째 선택이 `none/none`을 반환했다. 두 번째 후보의 정확한 수·타입은 확인하지 못했으므로 근거 부족과 모델 선택 오류 중 하나로 단정하지 않는다. 현재 재실행 근거에서는 UC3의 등록 목록 identity/anchor 선택이 남은 플래너 문제이며, 다른 세 UC의 계획 통과도 HTTP 성공은 아니다.

### 지속 상태를 언제 확정할 것인가

뒤 유스케이스의 분기·권한·적격성 판단에 영향을 주는 지속 상태는 요구사항에서 **무슨 뜻인지, 누가 바꿀 수 있는지, 기본 정책이 있는지**를 결정해야 한다. 클래스 설계는 그 결정을 Entity 상태 또는 명시적 파생식으로 표현하고, 생산 연산과 소비 연산의 근거 연결을 닫는다. 모든 DB 컬럼·임시값·HTTP 상태코드를 요구사항에서 미리 고정할 필요는 없다. 현재 `waitlistEnabled`는 읽는 조건이 있으나 설정 주체와 생산 경로가 비어 있으므로, 필드 하나를 추가하는 것으로는 해결되지 않는다. 시스템이 근거 있는 선택지 질문을 만든 뒤 설계/API에 전파해야 하며 Codex가 관리 연산에 임의로 설정 입력을 붙이지 않는다.

## 후속 소규모 검증: 출처 판정과 질문 생성 경계

- UC4의 필수 응답 연관값 `course`·`term`·`section`은 관리 연산의 정확한 요청 인자에 없고, 현재 생성 Java에서는 출처 없는 값이 `null`로 전달된다. OSS `openai/gpt-oss-120b`에 이를 검토시킨 두 번의 소규모 호출은 명시적 조회·파생·입력 반례는 구분했지만, 세 누락 필드를 포괄적인 “create request args” 문구만 보고 입력 출처가 있다고 잘못 판정했다. 따라서 이 모델 판정을 그대로 검증기나 자동 수리에 사용하지 않는다.
- 클래스 모델에는 매개변수·연산·호출·필드의 stable ref가 있지만, 연관 관계 endpoint와 연산의 저장/조회 효과를 필드 출처에 잇는 구조화된 ref가 없다. 현 모델만으로 완전한 필수 응답 생산자 행렬을 이름 매칭 없이 결정론적으로 계산할 수 없다. 관계 endpoint 및 상태 효과 출처의 구조화가 선행되어야 하며, 이후 부재가 확정된 경우에만 정책 선택지 질문으로 보낸다. DB 스키마 변경이나 현재 생성 앱의 직접 수정은 하지 않았다.
- UC7의 대기열 활성화 상태 질문을 위한 OSS 배치 호출은 실제 누락 사례 두 개를 질문 필요로, setter·기본값·외부 권한·파생 상태 반례 네 개를 질문 불필요로 구분했다. 하지만 두 실제 누락 사례 모두 관리 UC/RR 출처를 답에서 빠뜨리고 근거에 없는 필드명까지 지어냈다. 부재 판정 가능성은 관찰됐지만, 이 결과를 시스템 선택지 질문으로 제시하는 것은 아직 안전하지 않다.
- UC3의 두 번째 finite collection 선택에는 11개 매칭 필드와 13개 anchor가 있었다. 직접 OSS 선택 호출은 강의 개설 ID 쌍을 골랐지만 제품 전체 플래너는 `none`을 반환했다. 두 후보에 공통 `resourceRole`·`valueRef`·`identityObligationRef`가 없으므로, 이름 접미사 유사성으로 자동 선택하는 임시 수정은 폐기했다. 남은 과제는 안정된 동일 자원 근거를 설계/OpenAPI에서 후보 양쪽에 전달하는 것이다. 이번 확인은 UC3 그래프/HTTP 성공이 아니다.
- 추가 원본 추적에서 UC3의 `target_offering_id`와 UC2의 `course_offering_id`는 서로 다른 로컬 `valueRef`이고, 강의 개설 자원의 공통 identity obligation은 없다. 검색 응답의 `offeringId`에도 `valueRef`가 없다. 구형 동결 OpenAPI에는 새 `argumentProvenance`마저 없으므로 현재 체크포인트만으로 stable-ref 연결을 검증할 수 없다. 지금의 `cross_uc_values`도 생산자 결과를 추가할 수는 있지만 소비자↔생산자 동일성 edge는 만들지 않는다.
- 두 번째 고정-ID 질문 생성 OSS 호출에서는 UC7/RR9·관리 UC11/RR13을 입력 envelope에 고정하자 네 반례를 올바르게 건너뛰고 양쪽 UC에 미칠 효과를 담은 선택지를 제안했다. 그러나 두 선택지 중 하나가 출처에 없는 전역 기본 설정과 별도 관리 동작을 추가했다. ID·인용을 모델에게 다시 생성시키지 않는 방식은 효과가 있었지만, 선택지의 정책 범위를 검증하지 않고 사용자에게 공개해서는 안 된다.
- UC3 finite selector가 `none`으로 응답하면 같은 정확한 후보·anchor와 행 생성 근거를 간결하게 다시 제시하는 재선택을 최대 한 번만 허용했다. 자동 이름 매칭이나 자동 선택은 없다. 첫 선택이 유효하면 재호출하지 않고, 두 번째도 `none`이면 기존 오류로 남는다. 집중 테스트에서 세 경로를 확인했다. 동결 UC3에서 첫 `none`을 로컬 wrapper로 강제하고 **새 compact 프롬프트만** OSS에 보낸 한 번의 실제 호출은 1.706초에 목록에 존재하는 강의 개설 ID pair를 반환했고, 함수의 유한 후보·타입 검사를 통과했다. 전체 UC3 계획 및 HTTP Testing은 아직 재실행하지 않았다.
- UC4의 완전한 해결은 작은 클래스 연산 출처 필드 하나로 되지 않는다. 클래스 반환 타입에서 API의 중첩 `required` 응답 필드까지 가는 stable-ref 경로가 없고, 생성 mapper가 저장된 값도 별도로 `null`로 투영할 수 있기 때문이다. 최소 후속 경계는 class→API의 필수 응답 경로마다 stable field ref와 `{entityFieldRef|lookup|derived}` 출처를 갖는 `responseProjection` 계약이다. API가 필수 필드를 선언하기 전에 그 출처를 확인하고 구현 생성도 같은 계약을 써야 한다. 이 구조 없이 클래스 단계에서 완전한 생산자 검사를 했다고 주장하지 않는다.
- 대기열 정책 질문을 전체 명세 배치 뒤에 생성하는 시범 구현은 **제품 경로 실호출 실패로 되돌렸다**. 첫 전체 11-UC+독립 반례 inventory 호출은 응답 스키마가 후보 하나라서 LIB 명시 setter만 골랐다. 후보를 최대 5개로 넓힌 마지막 OSS 실호출(입력 8,651토큰·출력 3,682토큰·12.43초)은 LIB1/LIB2 loanable, UC2/UC10 registration period, UC2/UC11 capacity, UC1/UC11 published의 확립된 setter 4개만 반환했다. 인용·ID는 유효했지만 필요한 UC7/UC11 waitlist 부재를 아예 반환하지 않았다. 전체 inventory를 한 번에 검토하는 방식이 필수 질문을 놓친다는 실측 결과이므로, 이를 제품에 남기지 않는다. 다음 시도는 조건부 소비 상태를 먼저 작게 검색하고 해당 소비자·관리자 쌍의 전체 근거만 별도 검토하는 두 단계 경계가 필요하다. 자동 질문·답변·재개가 성공했다고 주장할 수 없다.
- UC3 재선택 변경을 포함한 `tests/test_functional_plan.py` 전체 집중 테스트는 최소 pytest 플러그인 실행에서 exit code 0으로 통과했다. `dynamic_functional.py` 구문 검사와 전체 diff whitespace 검사도 통과했다.
- UC4의 현재 API 정규화는 BCE의 non-Optional 필드를 required로 표시하지만 필드별 응답 출처를 저장하지 않는다. 생성 Controller는 `ObjectMapper.convertValue`로 구조 변환만 하고, 실제 연관 객체 구성은 구현 에이전트의 Service mapper에 맡긴다. 별도 OSS 실호출은 course/term/section의 출처 없음과 독립 lookup 반례는 맞췄지만, 실제 instructor ID→조회 출처도 없다고 잘못 판정했다. 따라서 이 판정으로 자동 수리 또는 질문을 하지 않는다.
- 전체 inventory 대신 두 단계 OSS 실험도 수행했다. 1단계(작은 UC/요구사항 요약)는 UC7과 정확한 RR9 조건 인용을 찾아냈다. 2단계(상세 소비자·관리자 후보 근거)는 UC7을 `unresolved`, 독립 LIB1/LIB2 명시 setter를 `explicit_setter`로 올바르게 분류했다. 그러나 UC7의 관리 UC ID를 `null`로 반환해 RR13/UC11을 질문의 두 번째 수정 대상으로 묶을 수 없었다. 따라서 이 결과도 시스템 질문으로 자동 연결하지 않았다. 후보 탐색과 부재 판정은 분리 효과가 있었으나, 관리 주체 선택은 아직 별도 근거 연결이 필요하다. 두 실호출은 각각 7.19초·3.32초였고 생성 앱/DB 수정은 없었다.

## 재개한 소규모 검증

- 같은 동결 수강신청 체크포인트에서 소비 UC7을 확정하고 관리 UC를 유한한 같은 체크포인트 후보 목록으로 제시한 OSS 실호출은 관리 UC11을 선택했다. UC7/RR9와 UC11/RR13의 인용이 실제 원문과 맞았고 상태 출처를 `unresolved`로 분류했다. 독립 도서관의 명시 setter 반례는 LIB2를 선택하며 `explicit_setter`로 구분했다. 이전 전체 inventory 호출에서 관리 UC가 `null`이었던 문제를 좁은 후보 검토로 분리할 수 있음을 보였지만, 아직 제품 질문·답변 경로의 성공 증거는 아니다.
- 현재 코드로 동결 UC3 전체 계획 생성을 한 번 재실행한 결과 OSS 3회·7.387초 후 `swapRegistration.path:currentRegistrationId`의 생산자 선택에서 먼저 실패했다. 상태 변경 후보에는 등록 ID 생산자가 없지만, 읽기 fallback에는 `viewRegistrationsAndSchedule.bodyRegistrationList0RegistrationId`가 있다. 이 fallback만 좁게 제시한 별도 OSS 호출은 해당 후보를 선택했다. 따라서 이전에 확인한 collection match/anchor 단계까지 이번 전체 계획은 도달하지 못했고, 전체 UC3 계획 PASS는 여전히 미확인이다.
- UC3의 읽기 fallback 입력은 전체 resource outline과 13개 범용 규칙 대신 관련 대상·유한 후보·생성 흐름·5개 규칙으로 줄였다. 실제 OSS 소규모 실험에서 UC3 등록 ID 읽기 후보를 선택하고 독립 청구서 ID→주문 ID 오연결은 거절했다. 관련·무관한 identity relation 및 읽기 전용 후보만 있는 경우까지 집중 테스트를 통과했고 `tests/test_functional_plan.py` 전체도 exit code 0이었다. 이후 전체 UC3 계획을 딱 한 번 실행했으나 검증용 wrapper의 stdout이 비어 결과를 회수하지 못했으므로 성공·실패를 주장하지 않는다.
- UC7 대기열 질문은 별도 실모델 소규모 호출에서 UC11 관리 후보와 정확한 RR9·RR13 인용을 골랐지만, 두 단계 제품 helper 실호출은 질문을 반환하지 못했다. 두 구조화 호출은 완료됐으나 중간 후보·검토 응답을 기록하지 않아 어느 경계에서 빠졌는지 알 수 없다. 시범 구현은 모두 되돌렸고, 기존 산출물이나 DB는 수정하지 않았다. 다음 시도에서는 코드 추가 전에 두 단계의 파싱된 응답과 출처 검증 결과를 한 실행에 함께 기록해야 한다.
- 표준 출력만 사용한 앞선 UC3 전체 계획 실험은 결과를 회수하지 못해 다시 계측했다. 이번에는 별도 임시 JSON에 시작·각 모델 응답·최종 상태를 원자적으로 기록하고, 동결 TestingInput에서 현재 제품 `_generate_candidate_workflow`를 한 번 실행했다. 29.191초·OSS 9회로 **UC3 계획 PASS**, compile PASS, Arazzo/OpenAPI 문서 검증 PASS였다. 현재 등록 ID는 상태변경 후보가 `unsupported`였지만 간결한 readback fallback에서 등록 목록을 선택했고, 강의 개설 ID anchor와 같은 배열 원소의 등록 ID를 묶는 유한 collection 선택도 통과했다. 최종 연산은 `manageTerm → manageCourseOffering → registerForCourse → viewRegistrationsAndSchedule → manageCourseOffering → swapRegistration`이다. 이는 계획·문서 검증이며 HTTP 실행이나 UC3 실제 Testing PASS는 아니다. 임시 결과 디렉터리는 읽은 뒤 정확한 경로만 제거했다.
- 요구사항 질문 실험은 단계별 파싱 응답을 원자 기록해 다시 확인했다. 소비 상태 검색은 UC7/RR9 조건을 정확히 반환했고 반환된 6개 소비 후보 모두 ID·요구사항 연결·인용 검증을 통과했다. 반면 여러 소비 UC를 한꺼번에 검토한 두 번째 호출은 UC7을 `explicit_setter`로 오분류하고 UC7 자신을 관리 UC로 골라 RR9만 인용했다. 독립 LIB1도 자기 자신과 IR1만 선택했다. 결정론적 검증이 질문을 내지 않은 것은 올바르며, 실패 위치는 소비 검색이 아니라 배치식 생산자/관리자 판정이다. 따라서 소비 UC별로 자기 자신을 제외한 유한 관리 후보를 제시하는 작은 검증을 다음 경계로 삼는다.
- 소비 UC별로 분리하고 자기 자신을 관리 후보에서 뺀 마지막 두 OSS 소규모 호출에서도 자동 질문 기준은 충족되지 않았다. UC7은 `unresolved`로 분류했지만 관리 UC가 `null`이고 RR9만 인용했다. 독립 LIB1은 LIB2와 정확한 IR1/IR2를 찾았지만 출처 종류를 `external`로 오분류했다. 이 방식의 프롬프트 추가 보정은 중단하고, 실제 사용자에게 정책/설정 주체를 선택지로 물어볼 수 있는 계약과 추적 근거를 별도 설계해야 한다. 제품 질문 기능은 도입하지 않았다.
- UC4를 위한 동작 변화 없는 첫 구조화 단계로, API 응답의 required 필드를 기존 BCE `fieldRefs` 경로로 열거하는 내부 `response_projection_context`를 추가했다. 현재 API 정규화·OpenAPI 출력을 변경하거나 출처가 있다고 추정하지 않는다. UC4 중첩 경로, 계산기 scalar root, Optional 제외, 이름 변경 안정성, 누락 ref 및 사용하지 않는 손상 선언 반례 등 집중 테스트 6건이 통과했다. 이 helper 자체는 필드 값을 생성하는 계약이 아니며 UC4 런타임 결함을 해결했다는 증거가 아니다.
- 새 context와 API 경계 집중 검사를 함께 실행해 exit code 0을 확인했다. 동결 UC4 설계에는 조회 Control의 `studentId` 출처만 있고 필수 `course`·`term`·`section`·`instructor` 응답값을 만드는 인자/lookup 선언은 없다. 유한 출처 후보를 준 OSS 실험에서는 `course=none`까지는 맞았지만 첫 응답이 부분 출력으로 끝났고, 더 짧은 두 번째 응답은 빈 내용이라 전체 결과를 구조적으로 검증할 수 없었다. 자동 source-map 생성에 사용하지 않는다. 현재 단계에서 확정할 수 있는 것은 **설계에 출처 선언이 없다**는 사실이며, 어떤 신규 입력/조회 정책을 택할지는 시스템 질문 또는 명시적 설계 수리 계약이 필요하다.

## 네 단계의 검증기 소유권과 공통 경계

현재 검증 규칙은 네 단계에 분산되어 있다. `app/validation.py`는 `Finding`, `ValidationReport`, `CheckSpec`/`run_checks` 등 결정론적 보고 어휘와 실행 보조를 제공하지만, 네 단계를 하나로 검증하는 통합 validator는 아니다.

| 단계 | 현재 소유 경로 | 검증 범위 |
| --- | --- | --- |
| 요구사항 | `app/requirements/modeling/validation.py`와 `specifications.py` 등의 산출물별 검사; `app/requirements/orchestration/feedback_gates.py`·`supervisor.py`가 질문·단계 진행을 결정 | 결정론 규칙과 별도의 LLM 의미 검토가 공존하며, 검토되지 않은 UC도 구분한다 |
| 설계 | `app/design/validation.py`가 산출물별 검증을 집계하고, `app/design/graphs/subgraphs.py`가 클래스·시퀀스·API 등의 개별 검사를 연결 | 공통 보고 형식에 산출물별 구조·의미 검사와 수리 흐름을 결합한다 |
| 구현 | `app/implementation/workflows/coordinator.py`가 작업별 검사 결과와 최종 완료·부합성 감사를 조정하며, `conformance.py`가 설계 부합성을 검사 | 소유 작업의 컴파일·집중 테스트와 최종 구현 산출물 감사를 구분한다 |
| 테스팅 | `app/testing/graphs/testing_graph.py`가 정적·동적 검사를 분기하고, `app/testing/service.py`가 실행 게이트 결과를 집계 | Arazzo 문서 검증, 정적/IaC 검사, 실제 HTTP 실행 결과가 별도 경로다 |

최신 제한된 OSS 실호출에서도 시스템 질문은 완성되지 않았다. UC7은 질문 대상 후보로 나왔지만 근거가 확인된 선택지는 없었다. 독립 라이브러리 사례의 명시적 setter는 질문을 건너뛰는 것으로 분류됐다. 별도 호출에서 공급한 UC7 제안 두 개는 제안으로 수락됐으나, 독립적인 기계 안전 승인 사례에서는 근거 없는 권한 주체를 만들어냈다. 따라서 그 정책 선택지는 안전하지 않으며, 제품 코드 변경이나 사용자 질문 완료로 기록하지 않는다.

후속 구조는 모든 규칙을 한 validator에 합치는 대신, 각 단계가 공통 stage-level entry/result 계약을 구현하도록 권고한다. 공통 계약은 단계 식별자, 입력 산출물·근거 참조, typed report와 checked/unexamined 규칙, 차단·수리 가능 상태를 전달하고, 실제 규칙과 실행기는 각 단계에 남긴다. 이는 설계 권고이며 현재 구현 완료를 뜻하지 않는다.
