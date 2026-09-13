# 최종보고서 추가 원고: 사용자 피드백 기반 산출물 수정

> 편집 메모: 이 문서는 기존 최종보고서의 DOCX 파일을 수정하지 않고, 보고서에 옮겨 넣을 수 있도록 작성한 추가 원고이다. 방법론 부분은 「멀티 AI 에이전트 기반 클라우드 네이티브 애플리케이션 개발 지원 방법」 장의 「기술 개요」 다음에, 사례 부분은 「사례 애플리케이션 생성 및 AWS 배포 사례」 장의 「시스템 입력」 다음에 배치하는 구성을 권장한다. 그림과 표 번호는 원본 보고서에 삽입할 때 다시 부여한다.
>
> 원문 근거: 사례에 사용한 전체 명령, 피드백 전후 산출물, PlantUML 원문과 렌더링 파일은 [사용자 피드백 사례 원본 증거 묶음](final-report-feedback-evidence/README.md)에 정리하였다. 질문과 선택지는 저장된 문자열을 번역하거나 다듬지 않고 그대로 인용하였다. 각 원본·렌더링 파일의 출처와 SHA-256 해시는 [manifest.json](final-report-feedback-evidence/manifest.json)에 기록하였다.

## 1. 방법론 장 추가 원고

### 사용자 피드백 기반 산출물 수정 및 단계 재개

제안하는 시스템은 요구사항 분석, 시스템 설계, 시스템 구현과 테스팅을 한 번에 끝까지 실행하지 않는다. 각 에이전트가 검토 가능한 산출물을 생성하면 작업을 일시 중단하고, 사용자가 결과를 확인한 뒤 다음 단계로 진행하거나 수정 의견을 전달하도록 한다. 현재까지 확정된 요구사항과 승인된 산출물만으로 다음 작업에 필요한 기능 동작, API와 같은 외부 인터페이스 또는 클라우드 배포 구성을 하나로 정할 수 없는 경우도 있다. 이런 항목은 에이전트가 임의로 결정하지 않고 질문과 선택지를 제시한다.

피드백은 시작한 주체에 따라 두 가지로 구분한다. **에이전트 요청형 피드백**은 필요한 입력이 없거나 둘 이상의 구현 가능한 해석이 남은 경우에 에이전트가 먼저 질문하는 방식이다. 최소 컴퓨팅 용량 질문과 UC1 검색 조건 선택이 이에 해당한다. **사용자 주도형 피드백**은 사용자가 단계별 산출물을 검토하고 먼저 수정 의견이나 후속 명령을 전달하는 방식이다. UC10 분리, UC3 연산 이름 수정과 테스팅 수리 위임이 이에 해당한다. 선택지와 자유 형식 입력은 별도의 피드백 유형이 아니라 사용자가 결정을 전달하는 형식이다.

이 문서에서 **사용자 의사결정 사항**은 현재 요구사항과 승인된 산출물에 둘 이상의 해석 또는 설정 후보가 남아 있으며, 어느 후보를 고르는지에 따라 기능 동작, 외부 인터페이스 또는 클라우드 배포 구성이 달라지는 항목을 뜻한다. 모델 응답의 스키마 오류, 미선언 타입과 실행 환경 오류는 사용자 의사결정 사항이 아니라 기술 실패로 구분한다. 기술 실패에는 오류 근거와 재시도 방법을 제시하고, 사용자의 선택만으로 해결된 것으로 처리하지 않는다.

사용자 입력은 다음 과정을 거쳐 반영된다.

1. 시스템은 현재 산출물과 실행 상태를 보존하고, 사용자가 선택할 수 있는 다음 행동을 공개한다.
2. 선택지 응답은 선택 ID에 연결된 결정 의미로 기록한다. 자유 형식 의견은 수정 대상, 변경 종류와 요청 효과로 정리한다.
3. 산출물 식별자와 요구사항 추적표(Requirements Traceability Matrix, RTM)를 이용하여 의미를 결정하는 기준 항목과 함께 수정해야 하는 후속 항목을 찾는다.
4. 수정 대상이 여러 가지로 해석되면 변경하지 않고 정확한 항목을 다시 질문한다. 다른 개발 단계의 산출물이나 식별자가 함께 바뀌는 경우에는 영향 범위를 표시하고 적용 확인을 받는다.
5. 확인된 범위만 수정 후보 생성에 사용한다.
6. 수정 후보의 스키마, 타입 참조, 다이어그램 호출 관계와 API 계약을 검사한다. 검사를 통과하면 새 버전으로 저장하고, 통과하지 못하면 일괄 변경을 저장하지 않는다.
7. 상위 산출물이 바뀌면 현재 추적 정보를 다시 계산하고 영향을 받은 다음 단계부터 재개한다.

이 방식은 개발 단계별 산출물의 담당 범위를 유지한다. 예를 들어 구현 에이전트가 유스케이스의 의미 공백을 발견하더라도 구현 코드에 정책을 임의로 추가하지 않는다. 해당 유스케이스를 담당하는 요구사항 분석 단계로 질문을 전달하고, 사용자가 의미를 결정한 뒤 요구사항 수정 명령을 실행한다. 이후 현재 버전의 추적 관계를 기준으로 설계 변경 범위를 다시 계산한다.

산출물은 기존 내용을 덮어쓰지 않고 버전 단위로 저장한다. 수정 계획에는 기준 산출물 버전과 추적 정보의 해시가 포함된다. 사용자가 검토하는 동안 기준 버전이 달라지면 이전 피드백을 그대로 적용하지 않는다. 변경 후보 전체가 검사를 통과한 경우에만 함께 저장하므로, 클래스 연산 이름만 바뀌고 연결된 시퀀스 호출은 이전 이름을 유지하는 것과 같은 부분 저장을 막는다.

### 피드백을 시작하는 두 가지 방식

| 구분 | 시작 조건 | 입력 방법 | 실제 사례 |
|---|---|---|---|
| 에이전트 요청형 | 승인된 입력과 산출물만으로 기능 동작·외부 인터페이스·배포 구성을 하나로 정할 수 없음 | 질문, 선택지 또는 자유 답변을 제시하고 사용자 응답까지 작업을 중단 | 최소 컴퓨팅 용량 질문, UC1 검색 조건 선택 |
| 사용자 주도형 | 사용자가 검토한 산출물이 원하는 결과와 다르거나 후속 처리를 직접 요청함 | 수정할 대상·원하는 변경·유지할 범위 또는 후속 API 명령을 사용자가 먼저 전달 | UC10 분리, UC3 연산 이름 수정, 테스팅 수리 위임 |

두 방식은 시작 지점만 다르다. 입력 이후에는 대상 확인, 영향 범위 계산, 필요 시 적용 확인, 수정 후보 검사와 새 버전 저장 순으로 처리한다.

## 2. 사례 장 추가 원고

### 수강신청 애플리케이션의 단계별 피드백 적용

피드백 사례는 수강신청 애플리케이션의 자연어 요구사항 16개를 Workspace API에 입력하여 얻었다. 사용한 입력 파일은 [e1-course-registration-aws.json](final-report-feedback-evidence/raw/input/e1-course-registration-aws.json)이다. 주 실행에서는 요구사항과 설계 산출물 수정, 배포 사양 선택과 구현 중 상위 명세 확인을 하나의 앱에서 수행하였다. 테스팅 사례는 같은 입력으로 수행된 별도 앱의 저장 결과를 사용하였다. 주 실행은 구현 단계에서 시작된 설계 변경이 검사에 통과하지 못하여 테스팅 단계에 도달하지 못했기 때문이다.

#### 요구사항 입력 단계: 최소 컴퓨팅 용량의 선택 유보(에이전트 요청형)

시스템은 요구사항을 정제한 뒤 다음 메시지와 이유를 저장하였다. 아래 영어 문장은 명령 결과의 원문이다.

> I refined and classified 16 requirements (14 functional and 2 non-functional). If known, provide either the minimum vCPU or minimum memory required by this workload.

> A sizing floor enables instance-spec selection. Without one, the system does not assume that the cheapest or smallest instance is sufficient.

에이전트가 제시한 동작 원문은 다음과 같다.

| `label` 원문 | `action` 원문 | `auto_selectable` 원문 |
|---|---|---|
| `Send answer` | `message` | `false` |
| `Skip suggestion and continue` | `advance` | `true` |

사례 실행은 `Skip suggestion and continue`를 선택하였다. 이 질문은 아직 VM 사양 산출물을 수정하는 단계가 아니므로 피드백 전후 배포 산출물은 존재하지 않는다. 질문, 이유, 선택지와 전체 명령은 [원문 추출](final-report-feedback-evidence/raw/choices/requirements-capacity-question-and-actions.json)과 [전체 명령](final-report-feedback-evidence/raw/commands/requirements-capacity-question.json)에 보존하였다.

#### 요구사항 분석 단계: 교수 유스케이스 분리(사용자 주도형)

최초 요구사항 분석 결과에서는 12개의 사용자 목표 유스케이스가 생성되었다. 수정 전 검토 화면에 저장된 메시지와 선택지 원문은 다음과 같다.

> I identified 12 user-goal use cases. Review: Refined requirements, Use cases. Send revision feedback, or continue to the next analysis stage.

| `label` 원문 | `action` 원문 |
|---|---|
| `Send revision feedback` | `message` |
| `Continue to next stage` | `advance` |

사용자는 `Send revision feedback` 경로로 다음 문장을 제출하였다. 이 문장은 저장된 `payload.text` 원문이다.

> Split UC10 into two separate use cases: one for reviewing the professor's assigned course offerings list and another for viewing the student roster of a specific course, without changing any other use cases.

전체 수정 전 산출물은 [usecase_spec v1](final-report-feedback-evidence/raw/artifacts/uc10-before-usecase-spec-v1.json), 전체 수정 후 산출물은 [usecase_spec v2](final-report-feedback-evidence/raw/artifacts/uc10-after-usecase-spec-v2.json)이다. v1의 UC10 이름은 `Review assigned course offerings and rosters`이고 유스케이스 수는 12개다. v2에서는 UC10 이름이 `Review assigned course offerings`로 바뀌고 `View student roster`가 UC13으로 추가되어 13개가 되었다. 두 항목은 RR11에 연결된다. ID를 기준으로 비교하면 나머지 11개 유스케이스의 값은 유지된다.

| 전체 산출물 | 피드백 전 | 피드백 후 |
|---|---|---|
| 유스케이스 모델 | [v1 전체 JSON](final-report-feedback-evidence/raw/artifacts/uc10-before-usecase-spec-v1.json) | [v2 전체 JSON](final-report-feedback-evidence/raw/artifacts/uc10-after-usecase-spec-v2.json) |
| 유스케이스 다이어그램 | 저장된 원본 없음 | [v1 PlantUML](final-report-feedback-evidence/sources/usecase-after-uc10-v1.puml), [SVG](final-report-feedback-evidence/renders/usecase-after-uc10-v1.svg), [PNG](final-report-feedback-evidence/renders/usecase-after-uc10-v1.png) |

UC10 피드백 전에 유스케이스 다이어그램은 생성되지 않았다. 저장된 유스케이스 다이어그램 v1은 UC10을 분리한 뒤 생성된 결과다. 따라서 수정 전 다이어그램을 새로 그려 전후 비교 자료로 사용하지 않는다.

#### 시스템 설계 단계: 원자적 수강 교환 연산의 이름 수정(사용자 주도형)

사용자가 제출한 피드백 원문은 다음과 같다.

> UC3 시퀀스의 performSwap 호출은 원자적 교환을 수행한다는 책임이 이름에 드러나지 않습니다. 이 호출을 swapRegistrationAtomically로 바꾸고 클래스 연산에도 같은 이름을 반영해 주세요. 다른 유스케이스는 변경하지 마세요.

시스템은 수정 권한을 가진 기준 항목을 하나로 정하지 못하고 다음 질문을 반환하였다.

> Exact links exist, but they do not identify one authoritative upstream target.

후보 원문은 `RegistrationControl`, `RegistrationControl::authorizeSwap(currentRegistrationId:String,newOfferingId:String)`, `RegistrationControl::performSwap(currentRegistrationId:String,newOfferingId:String)`, `RegistrationControl::validateEligibility(currentRegistrationId:String,newOfferingId:String)`, `StudentBoundary`, `StudentBoundary::swapCourseRegistration(currentRegistrationId:String,newOfferingId:String)`이었다. 이때 제시한 동작은 `Send answer` 하나였다.

사용자가 다시 제출한 답변 원문은 다음과 같다.

> Rename the operation RegistrationControl::performSwap(currentRegistrationId:String,newOfferingId:String) to swapRegistrationAtomically, keeping its parameters and return type, and update the UC3 sequence call that references this operation to use the new name.

시스템은 `class_diagram:RegistrationControl::performSwap(currentRegistrationId:String,newOfferingId:String)`을 기준 항목으로, `class_diagram:UC3::call:4`와 `sequence_diagram:UC3`를 후속 대상으로 표시하였다. 적용 확인 메시지와 선택지는 다음 원문으로 저장되었다.

> This revision changes another delivery stage or target identity. Confirm the displayed downstream scope before continuing.

| `label` 원문 | `action` 원문 |
|---|---|
| `Apply change` | `confirm_change` |
| `Dismiss change` | `dismiss_change` |

`Apply change`를 실행한 뒤 클래스 다이어그램과 시퀀스 다이어그램이 함께 새 버전으로 저장되었다. 클래스 전체 JSON에서 바뀐 값은 안정 식별자 `op_5e29d38d425377d9ad70eb89`를 가진 연산의 `name`, `operationId`와 UC3 협력 모델 호출의 `receiverOperationId`다. 시퀀스 전체 JSON에서는 `UC3::call:4`의 `label`과 연결된 클래스 다이어그램 해시가 바뀌었다.

| 전체 산출물 | 피드백 전 | 피드백 후 |
|---|---|---|
| 클래스 모델 | [v1 전체 JSON](final-report-feedback-evidence/raw/artifacts/uc3-before-class-diagram-v1.json) | [v2 전체 JSON](final-report-feedback-evidence/raw/artifacts/uc3-after-class-diagram-v2.json) |
| 클래스 다이어그램 | [PlantUML](final-report-feedback-evidence/sources/uc3-class-before-v1.puml), [SVG](final-report-feedback-evidence/renders/uc3-class-before-v1.svg), [PNG](final-report-feedback-evidence/renders/uc3-class-before-v1.png) | [PlantUML](final-report-feedback-evidence/sources/uc3-class-after-v2.puml), [SVG](final-report-feedback-evidence/renders/uc3-class-after-v2.svg), [PNG](final-report-feedback-evidence/renders/uc3-class-after-v2.png) |
| 시퀀스 모델 | [v1 전체 JSON](final-report-feedback-evidence/raw/artifacts/uc3-before-sequence-diagram-v1.json) | [v2 전체 JSON](final-report-feedback-evidence/raw/artifacts/uc3-after-sequence-diagram-v2.json) |
| UC3 시퀀스 다이어그램 | [PlantUML](final-report-feedback-evidence/sources/uc3-sequence-before-v1.puml), [SVG](final-report-feedback-evidence/renders/uc3-sequence-before-v1.svg), [PNG](final-report-feedback-evidence/renders/uc3-sequence-before-v1.png) | [PlantUML](final-report-feedback-evidence/sources/uc3-sequence-after-v2.puml), [SVG](final-report-feedback-evidence/renders/uc3-sequence-after-v2.svg), [PNG](final-report-feedback-evidence/renders/uc3-sequence-after-v2.png) |

수정 전 연산 이름은 `performSwap`, 수정 후 이름은 `swapRegistrationAtomically`다. 안정 식별자, 두 매개변수, `SwapResult` 반환형과 단계 참조는 유지되었다. 클래스 버전 목록과 시퀀스 버전 목록은 각각 [class_diagram 버전 원본](final-report-feedback-evidence/raw/version-indexes/class_diagram.json), [sequence_diagram 버전 원본](final-report-feedback-evidence/raw/version-indexes/sequence_diagram.json)에서 확인할 수 있다.

#### 배포 설계 단계: 용량과 비용을 비교한 VM 선택

배포 산출물 v1에는 `sizing` 필드가 없다. 이후 사용자가 `compute-1`에 `minVCpu: 2.0`, `minMemoryGiB: 4.0`을 입력하자 50개 후보가 저장되었다. 다음 표는 저장 순서상 앞의 다섯 후보이며, 값은 v2의 `sizing.guidance.computeUnits[0].candidates` 원문이다. 50개 전체는 수정 후 전체 JSON에 보존하였다.

| `sku` | `vCPU` | `memoryGiB` | `hourlyComputeUSD` | `monthlyComputeUSD` |
|---|---:|---:|---:|---:|
| `t3a.medium` | 2 | 4.0 | 0.046799998730421066 | 34.164 |
| `t3.medium` | 2 | 4.0 | 0.052000001072883606 | 37.96 |
| `t2.medium` | 2 | 4.0 | 0.0575999990105629 | 42.048 |
| `c5a.large` | 2 | 4.0 | 0.0860000029206276 | 62.78 |
| `t3a.large` | 2 | 8.0 | 0.09359999746084213 | 68.328 |

저장된 선택 원문은 `computeUnitId: compute-1`, `sku: t3a.medium`, `replicaCount: 1`, `replicationConfirmed: false`다. 비용 범위 원문은 다음과 같다.

> Compute on-demand list price only; excludes storage, databases, network, load balancers, taxes, support, and discounts.

| 전체 산출물 | 선택 전 | 선택 후 |
|---|---|---|
| 배포 모델 | [v1 전체 JSON](final-report-feedback-evidence/raw/artifacts/deployment-before-sizing-v1.json) | [v2 전체 JSON](final-report-feedback-evidence/raw/artifacts/deployment-after-sizing-v2.json) |
| Runtime 다이어그램 | [PlantUML](final-report-feedback-evidence/sources/deployment-runtime-before-v1.puml), [SVG](final-report-feedback-evidence/renders/deployment-runtime-before-v1.svg), [PNG](final-report-feedback-evidence/renders/deployment-runtime-before-v1.png) | [PlantUML](final-report-feedback-evidence/sources/deployment-runtime-after-v2.puml), [SVG](final-report-feedback-evidence/renders/deployment-runtime-after-v2.svg), [PNG](final-report-feedback-evidence/renders/deployment-runtime-after-v2.png) |
| Provisioning 다이어그램 | [PlantUML](final-report-feedback-evidence/sources/deployment-provisioning-before-v1.puml), [SVG](final-report-feedback-evidence/renders/deployment-provisioning-before-v1.svg), [PNG](final-report-feedback-evidence/renders/deployment-provisioning-before-v1.png) | [PlantUML](final-report-feedback-evidence/sources/deployment-provisioning-after-v2.puml), [SVG](final-report-feedback-evidence/renders/deployment-provisioning-after-v2.svg), [PNG](final-report-feedback-evidence/renders/deployment-provisioning-after-v2.png) |

배포 모델 v2에는 최소 용량, 선택 SKU, 복제 수, 비용 후보와 선택 근거가 추가되었다. 그러나 현재 다이어그램 변환은 이 sizing 정보를 그림에 표시하지 않는다. 그 결과 수정 전후 Runtime 원문과 이미지의 SHA-256은 서로 같고, Provisioning 원문과 이미지도 서로 같다. 이 사례는 구조화 산출물의 변경 사례이지 눈에 보이는 배포 다이어그램 변경 사례로 서술하지 않는다. 최소 용량 입력 전의 일시적인 조회 응답도 저장되지 않았으므로 새로 구성하지 않았다.

#### 구현 단계: 검색 조건의 의미를 사용자에게 다시 확인(에이전트 요청형)

구현을 시작하자 UC1의 `matching` 검색 기준이 명세에 없다는 질문이 저장되었다. 저장된 질문은 원문 자체가 줄임표로 끝나므로 생략된 부분을 복원하지 않았다.

> Implementation needs an upstream requirements or design decision: The search operation returns 'matching' published course offerings, but no search criteria are declared: the endpoint has no query parameters, the control operation has no parameters, and the use case does not specify what filters (e.g., term, course code, instructor) select the matching set. Implementations could return all published offerings or filter by caller-supplied criteria, producing different caller-visible behavior. Additionally, 'published' status is not represented on… Choose an option or provide another answer before retrying implementation.

에이전트가 제시한 선택지 원문은 다음과 같다. `요청 효과 원문`은 버튼을 선택했을 때 전송되는 `payload.text`다.

| `label` 원문 | `feedback_option_id` 원문 | `description` 원문 | 요청 효과 원문 |
|---|---|---|---|
| `Return all published offerings` | `return-all-published` | `The search lists every published course offering without caller-supplied filters; 'matching' means all published offerings.` | `Revise UC1 step 1/3 to state that the search returns all published course offerings with no filter criteria, and declare how an offering's published status is determined.` |
| `Filter by term and course code` | `filter-by-term-and-course` | `The caller supplies optional term and course code filters; the search returns published offerings matching them.` | `Revise UC1 to declare term and course code as caller-supplied search criteria, and update the endpoint and control operation signatures to carry them.` |
| `Filter by academic term` | `filter-by-term-only` | `The caller supplies a term; the search returns all published offerings in that term.` | `Revise UC1 to declare the academic term as the sole caller-supplied search criterion, and update the endpoint and control operation signatures to carry it.` |
| `Provide another answer` | 필드 없음 | 필드 없음 | `feedback_free_text: true`, `context.element_ref: use_case_spec:UC1` |

사례 실행은 `Filter by term and course code`를 선택하였다. 저장된 응답의 `payload.text`는 다음과 같다.

> Revise UC1 to declare term and course code as caller-supplied search criteria, and update the endpoint and control operation signatures to carry them.

시스템은 `use_case_spec:UC1`을 기준 항목으로 지정하고 API, 클래스 연산, 호출과 UC1 시퀀스를 후속 대상으로 제시하였다. 적용 확인 메시지는 `This revision changes another delivery stage or target identity. Confirm the displayed downstream scope before continuing.`이었고, 선택지는 `Apply change`와 `Dismiss change`였다.

| 전체 산출물 | 피드백 전 | 피드백 후 |
|---|---|---|
| UC1을 포함한 유스케이스 명세 전체 | [usecase_spec v3 전체 JSON](final-report-feedback-evidence/raw/artifacts/uc1-before-usecase-spec-v3.json) | [usecase_spec v4 전체 JSON](final-report-feedback-evidence/raw/artifacts/uc1-after-usecase-spec-v4.json) |
| 대응 유스케이스 다이어그램 | 새 버전 없음 | 새 버전 없음 |

v3과 v4 전체 JSON을 비교하면 UC1의 `trigger`, 주 시나리오 1단계, 성공 보장 문장과 `repair_history.episode_id`가 바뀌었다. 나머지 12개 유스케이스 명세는 유지되었다. 다만 후속 설계 변경은 수신 연산 서명 불일치와 미선언 반환 타입 때문에 검사에 통과하지 못했고, 새 클래스·API 버전은 저장되지 않았다. 따라서 UC1 명세 전후만 실제 산출물로 제시하며, 존재하지 않는 후속 설계 산출물을 만들지 않는다. API 버전이 하나뿐이라는 기록은 [api_spec 버전 원본](final-report-feedback-evidence/raw/version-indexes/api_spec.json)에 있다.

#### 테스팅 단계: 직접 호출한 자동 수리 위임(사용자 주도형)

별도 앱의 최초 테스트 결과가 실제로 제시한 메시지와 선택지는 다음과 같다.

> Testing found 1 blocking failure(s). The runtime environment must be restored before the same checks can continue.

| `label` 원문 | `action` 원문 |
|---|---|
| `Retry after environment recovery` | `retry_implementation` |

이 명령 결과에는 `delegate_repair` 선택지가 없다. 사례 실행의 자동 수리는 사용자가 Workspace API의 `delegate_repair` 명령을 직접 호출하여 시작하였다. 그러므로 테스팅 사례를 에이전트가 `delegate_repair` 버튼을 제시한 사례로 표현하지 않는다.

수리 전후 비교에는 저장된 두 명령 결과 전체를 사용한다. [첫 수리 명령 전체 결과](final-report-feedback-evidence/raw/commands/testing-first-repair-result.json)와 [최종 수리 명령 전체 결과](final-report-feedback-evidence/raw/commands/testing-final-repair-result.json)다. 최종 결과에는 수리 시도 3회, 수락 0건과 `TEST_PROFILE_DATA_UNAVAILABLE` 결함이 저장되어 있다. 동적 기능 테스트는 `GET /offerings`에서 HTTP 200을 받은 뒤 `GET /offerings/CS101-2023-Fall`에서 HTTP 404를 받았고, 최종 게이트는 `FAIL`로 남았다.

최종 수리 명령이 반환한 메시지와 후속 선택지는 다음 원문이다.

> Testing found 1 blocking failure(s). EasyDep classified the failures and will continue the matching automatic repair path.

| `label` 원문 | `action` 원문 |
|---|---|
| `Ask about this error` | `message` |
| `Rerun tests` | `start_testing` |

수락된 수정 후보가 없으므로 피드백 후 소스 코드 산출물은 존재하지 않는다. 시스템은 후보를 폐기했고 기존 산출물을 대체하지 않았다. 따라서 이 사례의 전후 자료는 실행 결과 전체이며, 소스 코드나 다이어그램 전후 자료를 새로 만들지 않는다. 현재 테스트 결과 조회 응답 전체는 [current-testing-result.json](final-report-feedback-evidence/raw/testing/current-testing-result.json)에 있다.

### 단계별 적용 결과 요약

| 개발 단계 | 피드백 또는 사용자 결정 | 전체 전후 근거 | 판정 |
|---|---|---|---|
| 요구사항 입력 | 최소 용량 입력을 뒤 단계로 유보 | [질문·선택지 전체](final-report-feedback-evidence/raw/commands/requirements-capacity-question.json), 이 시점의 전후 배포 산출물은 없음 | 결정 유보 |
| 요구사항 분석 | UC10의 담당 강좌 조회와 수강생 명단 조회 분리 | [v1](final-report-feedback-evidence/raw/artifacts/uc10-before-usecase-spec-v1.json) → [v2](final-report-feedback-evidence/raw/artifacts/uc10-after-usecase-spec-v2.json) | 반영 |
| 시스템 설계 | `performSwap`을 `swapRegistrationAtomically`로 변경 | 클래스 [v1](final-report-feedback-evidence/raw/artifacts/uc3-before-class-diagram-v1.json) → [v2](final-report-feedback-evidence/raw/artifacts/uc3-after-class-diagram-v2.json), 시퀀스 [v1](final-report-feedback-evidence/raw/artifacts/uc3-before-sequence-diagram-v1.json) → [v2](final-report-feedback-evidence/raw/artifacts/uc3-after-sequence-diagram-v2.json) | 반영 |
| 배포 설계 | 2 vCPU·4 GiB, `t3a.medium` 1대 선택 | [v1](final-report-feedback-evidence/raw/artifacts/deployment-before-sizing-v1.json) → [v2](final-report-feedback-evidence/raw/artifacts/deployment-after-sizing-v2.json) | 구조화 산출물에 반영, 그림은 동일 |
| 시스템 구현 | 검색 조건을 학기와 교과목 코드로 선택 | [usecase_spec v3](final-report-feedback-evidence/raw/artifacts/uc1-before-usecase-spec-v3.json) → [v4](final-report-feedback-evidence/raw/artifacts/uc1-after-usecase-spec-v4.json) | 요구사항 명세에 반영, 후속 설계는 미반영 |
| 테스팅 | `delegate_repair`를 API로 직접 호출 | [첫 수리 결과](final-report-feedback-evidence/raw/commands/testing-first-repair-result.json) → [최종 수리 결과](final-report-feedback-evidence/raw/commands/testing-final-repair-result.json) | 수리 미수렴, 대체 산출물 없음 |

이 사례에서 사용자 피드백은 질문에 답하는 경로와 산출물을 직접 고치는 경로로 시작되었다. 저장 결과에서는 UC10 모델, UC3 클래스·시퀀스와 UC1 명세의 새 버전이 확인된다. 배포 sizing은 구조화 모델에는 저장됐지만 그림에는 나타나지 않았고, UC1의 후속 설계 변경과 테스팅 자동 수리는 완료되지 않았다. 따라서 모든 피드백이 최종 구현과 테스트 성공까지 이어졌다고 일반화하지 않는다.

## 3. 원본 그림 파일 구성

보고서에는 다음 원본 기반 파일을 사용할 수 있다.

1. UC10 분리 후 유스케이스 다이어그램: [SVG](final-report-feedback-evidence/renders/usecase-after-uc10-v1.svg), [PNG](final-report-feedback-evidence/renders/usecase-after-uc10-v1.png). 수정 전 그림은 없다.
2. UC3 클래스 다이어그램 전체: 수정 전 [SVG](final-report-feedback-evidence/renders/uc3-class-before-v1.svg)·[PNG](final-report-feedback-evidence/renders/uc3-class-before-v1.png), 수정 후 [SVG](final-report-feedback-evidence/renders/uc3-class-after-v2.svg)·[PNG](final-report-feedback-evidence/renders/uc3-class-after-v2.png).
3. UC3 시퀀스 다이어그램 전체: 수정 전 [SVG](final-report-feedback-evidence/renders/uc3-sequence-before-v1.svg)·[PNG](final-report-feedback-evidence/renders/uc3-sequence-before-v1.png), 수정 후 [SVG](final-report-feedback-evidence/renders/uc3-sequence-after-v2.svg)·[PNG](final-report-feedback-evidence/renders/uc3-sequence-after-v2.png).
4. 배포 Runtime과 Provisioning 전체: [증거 묶음 목록](final-report-feedback-evidence/README.md)의 파일을 사용한다. 수정 전후 렌더링은 동일하다.

그림을 새로 그리거나 일부만 잘라 전후 산출물처럼 제시하지 않는다. 보고서 지면에서 축소하더라도 원본 파일 자체는 그대로 보존한다.

## 4. 실행 근거

### 주 실행: 요구사항·설계·배포·구현 피드백

- Workspace 앱 ID: `2b09c0e6-a14a-4f97-9066-77947e2b5ae9`
- 입력: [수강신청 요구사항 16개 원본](final-report-feedback-evidence/raw/input/e1-course-registration-aws.json)
- 앱 생성 및 최소 용량 질문 명령: `7e0a97c5-399e-468e-9732-23e82c3b41ef`
- UC10 수정 전 검토 명령: `07a919a3-ab32-40d6-a59a-168987123fa6`
- UC10 분리 피드백 명령: `24d71022-2599-4760-b0d7-936fba953a4b`
- UC3 설계 피드백 명령: `b794ef0f-212f-4696-ab51-157539f1f19d`
- UC3 기준 연산 선택 명령: `87f0d170-cc2a-4ba0-9bdd-c04ec9678f02`
- UC3 적용 확인 명령: `174612d5-216e-470d-89d3-f6ab2c7d44ce`
- 구현 시작 및 검색 조건 질문 명령: `026e1e8f-bff6-4616-b5c3-a04ac6702033`
- 구현 중 검색 조건 선택 명령: `8da14a2d-6380-4858-a159-d734eadbbad6`
- UC1 요구사항 변경 확인 명령: `3c8d5ccc-ebd3-4c2e-80af-feeae42ae159`

### 별도 실행: 테스팅 피드백

- Workspace 앱 ID: `6f9fd91d-0ad5-459c-b451-165aafe496fb`
- 입력: 주 실행과 동일한 수강신청 요구사항 16개
- 최초 테스트 명령: `55fb1796-964d-4cdc-b55a-56ed3bdb3310`
- 첫 자동 수리 위임 명령: `d9924032-2e98-4d86-aa02-18388644ecea`
- 최종 자동 수리 위임 명령: `9fe0cc3e-64d6-4a61-998e-0493505d8f94`
- 저장 결과: `FAIL` 1개, 수리 시도 3회, 수락된 변경 0건

각 명령의 전체 행과 API 응답은 [증거 묶음](final-report-feedback-evidence/README.md)에서 확인할 수 있다. 원본이 존재하지 않는 항목은 [manifest.json](final-report-feedback-evidence/manifest.json)의 `unavailableOriginals`에 기록하였다.
