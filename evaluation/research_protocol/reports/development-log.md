# 개발 실험 기록

## 2026-08-08 P1-GCP 세 기준군 자원 파일럿

- 조건: 개발 세트 `P1-gcp`, 반복 1, `openai/gpt-oss-120b`, seed 42,
  EasyDep·CoT·MetaGPT를 같은 일정에서 직렬 실행, 작업별 30분 제한, Docker와 OpenTofu
  평가 활성화
- 실행 인덱스: `artifacts/runs/experiment-development-P1-gcp-r1.json`
- 전체 벽시계 시간: 약 16분 43초. 개별 시간은 MetaGPT 349.606초, CoT 196.888초,
  EasyDep 400.144초였다.
- CoT: 생성·평가 완료, 실험 적격, 컨테이너 기능 oracle 2/2 통과, 의미 검사율 0.9.
- MetaGPT: 생성·평가 자체는 종료했지만 Markdown fence가 소스 파일에 섞였고 구현 계약과
  Terraform 및 Docker 빌드가 실패해 실험 부적격이었다. 이 결과를 환경 검열로 바꾸지 않는다.
- EasyDep: OpenTofu 검증, 컨테이너 기동, `/health`, 길이·온도 변환 업무 요청 2/2와 모든
  적용 가능한 capability·의존성 검사를 통과했다. 다만 파이프라인 내부 테스트 단계가 이미
  삭제된 `app/implementation/tools/gradle/gradlew.bat`를 참조해 생성 실패로 기록됐다.
- 원인 판정: Gradle 도구를 Docker로 전환한 과거 변경과 테스트 어댑터 사이의 시스템 회귀다.
  동일 산출물을 고정 `gradle:8.14.2-jdk21` 컨테이너로 재실행해 테스트 파일 2개와 Gradle
  작업 4개가 모두 성공함을 확인했다. 원본 인덱스는 감사 가능성을 위해 고치지 않는다.
- 정리: 파일럿 소유 평가 컨테이너와 임시 이미지는 평가기에서 삭제됐다. 종료 후 보인 두 개의
  중지 컨테이너는 수주 전 생성된 사용자 자원이라 건드리지 않았다. 여유 디스크는 5 GiB
  기준을 넘었다.
- 해석 제한: 1개 사례·1개 반복의 개발 파일럿이므로 실험군 효과 크기나 우월성을 주장하지
  않는다. 실행 환경·정리·판정 경계 검증에만 사용한다.

### Gradle 회귀 수정 후 전체 경로 재실행

- 별도 인덱스 `artifacts/runs/experiment-development-easydep-full-P1-gcp-r1.json`으로
  EasyDep 한 건을 새로 생성·평가했다. 총 420.538초가 걸렸다.
- 내부 테스트 단계는 고정 `gradle:8.14.2-jdk21` 컨테이너에 정상 진입했다. 따라서 삭제된
  wrapper를 찾던 시스템 회귀가 제거됐음을 확인했다.
- 이번 새 모델 산출물은 `GlobalExceptionHandler.java`의 잘못된 `@Override` 때문에
  `compileJava`에서 실패했다. 외부 컨테이너 평가도 같은 소스 컴파일 결함으로 실패했으며,
  OpenTofu 검증과 적용 가능한 의미 검사는 통과했다.
- 이 실패는 도구 미가용이나 환경 검열이 아니라 생성 코드 결함으로 유지한다. 앞선 산출물의
  성공 재검증과 이번 새 산출물의 실패는 서로 다른 표본이므로 합치거나 대체하지 않는다.
- 실행 종료 후 해당 `.easydep/orchestration/workspaces/<run-id>` 작업공간, Gradle 임시
  컨테이너, `easydep-evaluation:*` 이미지는 남지 않았다.

### 생성 규칙 보완 후 반복 2

- 별도 인덱스
  `artifacts/runs/experiment-development-easydep-full-metagpt-standard-P1-gcp-r2.json`에서
  EasyDep와 MetaGPT를 반복 2로 직렬 실행했다. 전체 시간은 약 9분 42초였다.
- MetaGPT는 파일 내부 Markdown 금지 규칙을 받았지만 8개 파일을 계속 오염시켰고,
  Terraform과 Docker 빌드도 실패했다. 기준선의 실제 실패로 유지하며 사후 정제하지 않는다.
- EasyDep scaffold는 변환 로직까지 이미 구현했지만 logic 단계가 명시적 빈 `files`를 반환하자
  오케스트레이터가 이를 응답 누락과 동일하게 실패 처리했다. 보존된 source에는 meter↔centimeter,
  celsius↔fahrenheit 변환과 예외 처리가 구현되어 있었다.
- 결정: logic 응답에서 `files` 키 누락·비객체는 실패로 유지하되, 명시적 빈 객체는
  `noChanges=true`로 허용한다. 정확성은 다음 단계의 불변 acceptance test와 Docker Gradle
  빌드가 판정한다. 이는 생성 코드를 보정하는 규칙이 아니라 멀티 에이전트 단계 간 no-op 계약을
  명시하는 변경이다.

## P1-AWS 개발 실행: 명시적 API 필드 추적성 경계 보정

- `easydep-full:P1-aws:r1`은 설계 단계에서 중단되었으며 구현·평가는 실행되지 않았다.
- 요구사항 에이전트가 응답 필드 절과 지원 단위 설명을 한 문장으로 합친
  `fields result and unit, supporting ...` 표현을 만들었다. 초기 추출기는 쉼표 뒤 설명까지
  필드명으로 읽어 `supporting`, `temperature`, `celsius` 등을 누락 필드로 잘못 판정했다.
- 명시적 필드 절이 후속 분사형 설명(`supporting`, `including`, `using` 등) 앞에서 끝나도록
  경계를 보수적으로 제한하고 회귀 테스트를 추가했다.
- 수정 규칙을 보존된 동일 요구사항·OpenAPI에 다시 적용한 결과 누락 필드는 0개였다.
  따라서 이 실행은 시스템 성능 실패 표본이 아니라 개발 중 계측기 결함 발견 사례로 분류한다.
- 수정 후 `easydep-full:P1-aws:r2`에서는 필드 목록 경계는 정상화됐지만, `returns a JSON
  payload containing fields result and unit`의 `payload`를 요청 방향 표지로 오인해 다시 설계
  단계에서 중단됐다. `payload`는 요청·응답 양쪽에 쓰이는 중립어이므로 방향 근거에서 제외하고,
  `accepts`와 `returns`처럼 방향이 명시된 동사만 사용하도록 보정했다. 이 실행도 같은 이유로
  성능 표본에서 제외한다.
- 두 실행 뒤 파일럿 소유 컨테이너와 `easydep-evaluation:*` 이미지는 남지 않았다.

### P1-AWS 반복 3: 앱 기능 성공과 IaC 공급자 스키마 실패

- `easydep-full:P1-aws:r3`는 609.623초에 생성과 외부 평가를 마쳤다. 컨테이너 빌드·기동,
  `/health`, 독립 업무 요청 2건은 모두 통과했고 capability/금지 리소스 검사는 적용 가능한
  8건 전부 통과했다.
- OpenTofu의 AWS 공급자 6.58.0은 생성된 `data "aws_subnet_ids" "default"`를 지원하지 않아
  `validate`가 실패했고, 최종 `experimentEligible`은 `false`였다. 인덱스의 `status=completed`는
  평가 절차가 종료됐다는 뜻일 뿐 산출물 성공을 뜻하지 않는다.
- 같은 보존 IaC에 새 격리 검증기를 적용해 `init`, `validate` 순서와 동일 오류를 재현했다.
- 결정: 특정 AWS 데이터 소스를 코드로 치환하지 않는다. 모든 CSP에 공통인 공급자 스키마
  검증을 IaC 승격 전에 실행하고, 실제 진단을 IaC 에이전트에 한 번만 반환한 뒤 재검증한다.
  최종 외부 평가기는 독립적으로 유지하며 내부 수정 호출도 LLM 호출 수에 포함한다.

### 공급자 검증 개입 후 P1-AWS 동일 셀 재실행

- 동결된 개발 split이 반복 1~3만 허용하므로 반복 수를 임의로 늘리지 않았다. 기존 인덱스를
  덮어쓰지 않도록 커밋 `6b0a7cd` 전용 아티팩트 루트에서 P1-AWS 반복 3 셀을 다시 실행했고,
  실제 run 산출물은 고유 ID `easydep-full-p1-aws-20260808T023241Z-6991da`로 보존했다.
- 781.156초에 전체 평가가 끝났다. 내부 `init`·`validate`, 독립 외부 OpenTofu 검증,
  컨테이너 빌드·기동·health, 업무 요청 2건과 의미 검사 9건이 모두 통과해
  `experimentEligible=true`, 의미 검사율 1.0이었다.
- 생성 IaC는 VPC, 인터넷 게이트웨이, 서브넷, 라우팅 테이블·연결, 보안 그룹, AMI 조회,
  VM을 명시적으로 선언했다. 이전의 미지원 `aws_subnet_ids` 데이터 소스는 없었다.
- 이 표본의 최초 IaC가 이미 공급자 검증을 통과해 수정 피드백은 발동하지 않았고 단계
  `llm_calls`는 1이었다. 따라서 실제 LLM 수정 성공을 이 표본으로 주장하지 않으며,
  1회 호출 제한·재검증·실패본 미승격은 결정적 단위 테스트의 근거로만 유지한다.
- 종료 후 실행 작업공간과 실험 소유 컨테이너·이미지는 남지 않았다.

### P2-Azure 반복 1: 필드 목록 자유문장 과잉 추출

- 커밋 `259624b` 전용 아티팩트 루트에서 `easydep-full:P2-azure:r1`을 실행했으나 설계
  단계에서 중단됐다. 요구사항 에이전트의 `fields title and content when creating a note`에서
  초기 추출기가 `creating`, `note`, `when`까지 요청 필드로 오인했다.
- `when`만 예외로 추가하지 않고, 필드 절에서 식별자가 쉼표와 `and`로 연결된 목록만 소비하는
  작은 문법으로 교체했다. 후속 설명이나 예약 경계어가 나오면 목록을 종료한다.
- 회귀 테스트 7건이 통과했고, 보존된 P1-AWS와 P2-Azure 요구사항·OpenAPI에 재적용한 결과
  두 사례 모두 실제 누락 필드는 0개였다. 이 실행은 계측기 결함으로 성능 표본에서 제외한다.
- 실제 run ID는 `easydep-full-p2-azure-20260808T024821Z-35300e`이며 실행 작업공간은 남지 않았다.

### 필드 파서 보정 후 P2-Azure 재실행: LLM endpoint 검열

- 커밋 `0570a31` 전용 경로에서 같은 P2-Azure 반복 1 셀을 재실행했으나 378.322초 뒤 설계
  LLM 호출이 `Request timed out`으로 종료됐다. 구현·IaC·컨테이너 평가는 시작되지 않았다.
- 이는 생성된 산출물의 정확성 실패가 아니므로 당시에는 `llmEndpointTimeout` 검열로
  분류했다. 아래 직접 지연 측정 뒤 이 이름이 원인을 과도하게 단정한다는 사실을 확인했다.
  집계기는 검열 건을 일반 실패 수에서 제외하며, 명시적 요청 timeout이 아닌 컴파일·테스트
  timeout은 계속 시스템 결과로 취급한다.
- 기존 인덱스를 재실행 없이 같은 규칙으로 보정하는 경로와 회귀 테스트를 추가했다.
- 검열 대체 실행도 381.653초 뒤 동일한 설계 요청 timeout으로 끝났다. 두 번 연속 같은
  조건이므로 단발 네트워크 지연으로 보지 않고, 설계 공통 배관의 클라이언트 120초·벽시계
  150초 제한이 P2 구조화 응답에 부족한 실행 설정 문제로 판정했다.
- 설계 호출 제한을 각각 300초·330초로 늘리되 전체 작업 제한 1800초는 유지했다. 세 값은
  환경 점검 결과와 프로토콜에 기록해 실행마다 확인 가능하게 했다.
- 확장된 제한으로 수행한 두 번째 대체 실행도 528.169초 뒤 같은 endpoint timeout으로
  검열됐다. 따라서 추가 반복이나 제한 증가는 중단하고, 후속 개발에서는 어느 구조화 설계
  스키마 호출이 지연되는지 단계 식별 계측을 먼저 보완한다.
- 이 과정에서 사용자 지정 인덱스 루트와 중앙 EasyDep run 루트가 다를 때 실패 run 탐색과
  resume·집계가 인덱스 루트만 조회하는 결함을 발견했다. 두 루트를 안전하게 해석하도록
  수정하고, 사용자 지정 인덱스에서도 중앙 run의 외부 평가 지표를 읽는 회귀 테스트를 추가했다.
- 확장 제한 실행의 실제 run ID는 `easydep-full-p2-azure-20260808T032050Z-a9ade9`이며
  실행 작업공간은 남지 않았다.
- 다음 실행부터 구조화 LLM 경계 오류에 `BCEExtractionResult`, `SequenceModel`,
  `ApiSpecModel`, `DeploymentModel` 같은 응답 스키마명을 포함한다. timeout 분류는 유지하면서
  어느 설계 산출물에서 지연됐는지를 추가 반복 없이 식별하기 위한 계측이다.
- 경로 수정 후 기존 P1-AWS 커스텀 인덱스를 재집계해 의미 검사율 1.0, 컨테이너 기능 통과율
  1.0, 부정확 클라우드 주장 0건, 구현 완결 1건이 요약에 실제 반영됨을 확인했다.

## P1-Azure 반복 1: HCL 중복 선언과 피드백 경계 일반화

- 커밋 `7bb777c` 전용 경로에서 P1-Azure 반복 1을 실행했다. 설계는 timeout 없이 완료됐지만
  IaC가 `public_ip_address` output을 두 번 선언해 HCL 사전검사에서 구현 단계가 중단됐다.
  실제 run ID는 `easydep-full-p1-azure-20260808T033829Z-a114c4`다.
- 부분 산출물 외부 평가는 완료됐지만 IaC·컨테이너가 실패했고 `experimentEligible=false`였다.
  의미 검사의 6/11 통과는 부분 산출물 진단일 뿐 성공 근거로 사용하지 않는다.
- 특정 output을 삭제하는 보정 대신, 공급자 스키마에만 적용하던 1회 제한 피드백을 HCL
  파싱·중복 선언 검사에도 일반화했다. HCL과 공급자 검증은 수정 예산 1회를 공유하며,
  수정본이 다시 실패하면 승격하지 않는다.
- 관련 오케스트레이션 테스트 35건이 통과했으며, HCL 중복 수정 성공과 1회 후 실패 중단을
  각각 검증했다.
- HCL 피드백 개입 후 같은 셀을 커밋 `3f29cc3` 전용 경로에서 재실행했으나, 이번 표본은
  설계 OpenAPI가 명시 응답 필드 `unit` 대신 `targetUnit`을 생성해 추적성 게이트에서
  중단됐다. 요구사항 원문과 OpenAPI를 대조해 파서 과잉 추출이 아닌 실제 계약 누락임을
  확인했다. run ID는 `easydep-full-p1-azure-20260808T034627Z-85679a`다.
- 특정 필드명을 코드로 치환하지 않고, 누락 진단과 구조화 API 모델을 API 설계 수정기에
  한 번만 반환하도록 설계 경계를 확장했다. 수정 모델에서 OpenAPI를 다시 렌더하고 동일
  게이트를 통과한 경우에만 수정된 `design_result`를 구현 단계로 전달한다.
- 이 내부 수정은 최종 기능 oracle을 읽지 않으며 LLM 호출 수와 수정 여부를 단계 지표로
  기록한다. 관련 추적성·오케스트레이션 테스트 24건이 통과했다.
- API 추적성 피드백 개입 후 커밋 `65aaed1` 전용 경로에서 재실행한 표본은 설계 게이트를
  통과했지만 IaC HCL 파싱 중 Lark `UnexpectedToken` 예외가 검사 결과로 변환되지 않아
  구현 단계에서 중단됐다. run ID는 `easydep-full-p1-azure-20260808T035422Z-bfb60d`이며
  부분 외부 평가의 `experimentEligible`은 `false`였다.
- HCL 파서 라이브러리의 버전별 예외 유형을 모두 사전검사 진단으로 변환해 같은 1회 수정
  루프가 처리하도록 보완했다. 파서 예외→수정→승격 경로를 별도 회귀 테스트로 고정했다.
- 병목 분석을 위해 모든 오케스트레이션 하위 단계에 UTC 시작·종료와 단조시계 소요시간을,
  설계에는 구조화 스키마별 LLM 이벤트를, IaC에는 생성·HCL·수정 및 `init`·`validate`
  명령별 시간을 기록하도록 계측했다. 이 계측 이후 결과만 세부 병목 분석의 주 근거로 쓴다.
- 커밋 `cae35a9` 계측으로 다시 실행한 run ID는
  `easydep-full-p1-azure-20260808T042458Z-04426d`다. Azure IaC는 최초 공급자 검증 실패를
  1회 수정한 뒤 통과했고, 외부 의미 검사와 컨테이너 기능 검사는 모두 1.0이었다. 다만 내부
  인수 테스트가 Spring Boot 3.3에 없는 이전 `LocalServerPort` 패키지를 import하여 컴파일에
  실패했으므로 종단 생성 성공으로 세지 않는다.
- 단계 벽시계는 VM 전달 194.29초, 요구사항 120.51초, 설계 107.25초, 테스트 36.33초였다.
  VM 전달에서는 공급자 `init` 두 번이 각각 54.59초와 80.56초로 총 135.15초를 차지했다.
  요구사항은 19개 LLM 호출의 합계가 132.56초였지만 일부 병렬 실행 때문에 벽시계는
  120.51초였다. 따라서 호출시간 합과 단계시간을 같은 값으로 취급하지 않는다.
- 반복 다운로드와 공급자 버전 소실을 함께 막기 위해 직렬 실행 전용 OpenTofu 플러그인
  캐시를 추가하고, 성공한 `.terraform.lock.hcl`을 IaC 산출물에 승격하도록 했다. 검증 결과는
  lock SHA-256과 공급자 선택 버전을 기록한다. 캐시는 `.easydep/cache/opentofu/plugins` 한
  경로에만 두며 Git 산출물에는 포함하지 않는다.
- Spring Boot 3.3 공식 문서의 패키지 계약을 테스트 생성 프롬프트에 명시했다.
  근거는 [Spring Boot 3.3의 임의 포트 테스트 예제](https://docs.spring.io/spring-boot/3.3/how-to/webserver.html)이며,
  이 변경은 최종 기능 oracle을 노출하지 않는다.
- 커밋 `9a07d35` 회귀 실행의 run ID는
  `easydep-full-p1-azure-20260808T044216Z-43b6a9`다. 생성·내부 테스트·공급자 검증·외부
  기능 2/2·의미 검사 11/11이 모두 통과해 P1-Azure의 첫 유효 종단 성공으로 판정했다.
  수정 후 공급자 `init`은 80.56초에서 23.19초로 줄었고, 캐시는 249,000,251바이트를
  사용했다. 이 한 표본은 속도 개선의 효과크기 주장이 아니라 병목 제거가 작동한다는 개발
  확인으로만 사용한다.
- 이 실행에서 `azurerm_network_interface_security_group_association`이 평가기에서
  `unmapped`로 남았다. 공식 AzureRM 문서는 이 타입을 NIC와 NSG 사이의 association을
  관리하는 리소스로 정의하고 두 ID를 모두 필수 인자로 둔다. 따라서 이를 NIC나 방화벽
  자체로 축약하지 않고, 중립 관계 `securityAssociation`을 구체화하는 공급자 리소스로
  매핑한다. 근거는 [AzureRM 공급자 공식 리소스 문서](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/network_interface_security_group_association)다.

## P2-Azure 검열 실행과 시간 요약

- 커밋 `aa8cd6c` 전용 경로의 P2-Azure 실행은
  `easydep-full-p2-azure-20260808T045845Z-29c07a`에서 설계 중단됐다. 요구사항 분석은
  155.29초에 완료됐지만 `ApiSpecModel` 호출이 300.11초에서 endpoint timeout이 됐다.
  실행기는 당시 이를 `llmEndpointTimeout` 검열로 분류했고 외부 평가는 실행하지 않았다.
- 같은 실패가 반복된 상태에서 재실행으로 성공 표본을 고르는 것은 선택 편향을 만들므로 추가
  재시도하지 않는다. 이는 P2 리소스 모델 실패 근거도, 성공 근거도 아니다.
- 각 실행 폴더에 `timing-summary.json`을 자동 생성해 단계 벽시계와 하위 LLM·IaC 작업을
  내림차순으로 제공한다. 병렬 LLM 호출 시간은 서로 겹칠 수 있으므로 합계를 임계 경로로
  해석하지 말라는 기계 판독 필드도 함께 둔다.

## LLM timeout 직접 재측정과 분류 정정

- 기존 설계 호출은 비스트리밍 `chat.completions.parse()`였으므로 시작부터 완성 응답까지의
  시간만 관측했다. 이 경로의 300초 `APITimeoutError`만으로 TTFT가 300초였거나 엔드포인트
  전체가 느렸다고 판단할 수 없다.
- 같은 모델·엔드포인트를 2026-08-08에 직접 측정했다. 단순 스트리밍 요청은 첫 이벤트
  3.443653초·전체 3.730863초, 전체 `ApiSpecModel` JSON Schema를 건 스트리밍 요청은 첫
  이벤트 2.587444초·전체 6.120654초였다. 같은 스키마의 비스트리밍 `parse()`도
  9.975516초에 완료됐다. 키·URL·응답 본문은 기록하지 않았다.
- 결론: 현재 엔드포인트와 구조화 출력 기능은 정상 응답하며 과거 표본은 특정 요청의 완성
  지연 또는 일시적 정지로만 판정할 수 있다. 향후 검열명은 원인을 단정하지 않는
  `llmResponseCompletionTimeout`으로 바꾸고, 기존 타이밍 사건에는 TTFT 미관측을 명시한다.
- 실패 당시 P2 전체 설계 체크포인트의 재전송은 별도 egress 승인이 필요해 실행하지 않았다.
  따라서 과거 요청을 현재 시점에 완전히 재현했다는 주장도 하지 않는다.
- 구조화 스트리밍 응답을 다시 측정한 결과 첫 이벤트 1.520174초, 첫 출력 1.525535초,
  전체 6.651610초였고 최종 JSON은 `ApiSpecModel` 검증을 통과했다. 응답 본문은 저장하지
  않고 콘텐츠 1,500자와 추론 1,368자라는 크기만 기록했다.
- 설계 공통 LLM 경계를 구조화 스트리밍으로 전환했다. 각 호출은 HTTP 응답 성립, 첫 SSE
  이벤트, 첫 출력 토큰, 첫 JSON 콘텐츠, 최대 이벤트 간격, 완료시간을 따로 기록하고 마지막에
  기존과 같은 Pydantic 스키마를 검증한다. 비민감 최소 API 설계 입력으로 운영 경계를 직접
  호출해 endpoint 1개를 가진 유효 모델이 반환됨을 확인했다.
- P2-GCP 개발 셀은 이 진단 요청이 들어온 시점에 의도적으로 중단했다. 자식 프로세스 4개를
  종료하고 잔존 컨테이너가 없음을 확인했으며, 해당 부분 실행은 어떤 성능·성공률 통계에도
  포함하지 않는다.

### no-op 계약 보완 후 EasyDep 반복 3

- 별도 인덱스 `artifacts/runs/experiment-development-easydep-full-P1-gcp-r3.json`으로
  EasyDep 반복 3을 실행했다. 총 499.576초가 걸렸다.
- logic 단계는 명시적 빈 변경을 받아들였고, 생성·내부 Docker Gradle 테스트·OpenTofu
  검증까지 모두 완료됐다. 이로써 no-op 계약과 도구 실행 경로는 작동함을 확인했다.
- 컨테이너는 기동되고 `/health`가 성공했지만 두 업무 요청 모두 HTTP 200 응답의 필드가
  `convertedValue`, `targetUnit`이라 고정 oracle의 `result`, `unit` 계약과 달라 실패했다.
  의미 검사 자체는 1.0이었지만 앱 기능 실패 때문에 실험 부적격으로 유지했다.
- 상류 OpenAPI도 이미 `convertedValue`, `targetUnit`을 사용했고 acceptance test는 그
  상류 계약과 scaffold에 맞춰 통과했다. 이는 여러 에이전트가 같은 잘못된 중간 계약에
  일관되게 맞추는 상관 오류이며, 내부 테스트 통과만으로 사용자 요구 충족을 증명할 수 없음을
  보여 준다.
- 결정: 다음 개발 보완은 최종 oracle을 생성 프롬프트에 노출하는 방식이 아니라, 요구사항에
  명시된 API 필드와 OpenAPI 스키마 사이의 독립 추적성 검증을 설계 단계 경계에 추가하는 것이다.
  기능 oracle은 계속 별도 최종 판정기로 유지한다.
- 구현 검증: 일반적인 `request/payload/response + field(s)` 명시절만 추출하는 결정적 게이트를
  추가했다. 반복 3의 보존 요구사항·OpenAPI에 적용했을 때 최종 oracle을 읽지 않고도
  `FR1:response:result`, `FR1:response:unit` 누락을 검출했다.
- 종료 후 실행 작업공간, Gradle 임시 컨테이너와 `easydep-evaluation:*` 이미지는 남지 않았다.

## 2026-08-08 지식 접근 API 적격성 점검

- 대상: 저장소 `.env`에 동결된 모델과 API 엔드포인트. 키·기본 URL·응답 본문은 기록하지
  않았다.
- 도구 없는 최소 Responses 요청은 성공했다. 사용량은 입력 68토큰, 출력 16토큰이며 응답
  항목 유형은 `reasoning`이었다.
- 같은 모델·엔드포인트에 함수 도구를 강제한 두 요청은 모두 HTTP 400으로 거부됐다. 하나는
  특정 함수 선택 형식, 다른 하나는 `tool_choice=required` 형식이었다.
- 판정: Responses API 자체는 사용할 수 있지만 현재 설정에서는 함수 호출 적격성이 입증되지
  않았다. Remote MCP는 공개 HTTPS 읽기 전용 서버가 없어 점검하지 않았다.
- 결정: 지식 접근 4군 프로토콜은 `development`로 유지한다. 현재 텍스트 전용 호출을 함수
  도구나 MCP로 가장하지 않으며, 이 점검 결과를 에이전트 성능 결과로 사용하지 않는다.
- 다음 조건: 동일 모델·동일 엔드포인트에서 함수 호출과 Remote MCP가 모두 성공하고 호출
  기록·토큰·바이트 측정기가 준비된 뒤에만 4군 확인 실험을 동결한다.

## 2026-08-08 P1-GCP 파일럿

- 조건: `easydep-full`, `openai/gpt-oss-120b`, seed 42, 개발 세트, 반복 2,
  외부 Docker/OpenTofu 실행 제외
- 실행 ID: `easydep-full-p1-gcp-20260807T171719Z-2cc928`
- 생성 결과: 요구사항·설계·구현은 생성했으나 생성 애플리케이션 테스트 실패로 전체
  파이프라인은 실패했다. 이 결과를 성공으로 재분류하지 않는다.
- 보존 구현 평가: 필수 파일은 완전했고, 고정 oracle의 적용 가능한 10개 클라우드
  capability·의존성 검사는 모두 통과했다. 외부 도구를 생략했으므로
  `experimentEligible=false`다.
- 발견한 계측 결함: 생성 실패 시 보존된 구현을 공통 평가기로 평가하지 않았으며,
  5개 capability 표본의 의미가 같아도 키가 다르면 중복 제안으로 집계했다.
- 조치: 생성 상태와 평가 상태를 분리한 부분 산출물 평가를 추가하고, 같은 요구사항 ID와
  포함 관계의 원문 근거를 가진 제안을 하나의 증거 군집으로 합쳤다.

수정 후 동일한 stateless conversion 개발 입력에서 capability 단계만 다시 실행했다.
5개 seed의 결과는 5개 증거 군집으로 합쳐졌고 각 군집의 출현율은 1.0이었다. 이는 개발
확인 결과이며 holdout 또는 확인적 성능 주장에 사용하지 않는다.

## 2026-08-08 Capability 개발 캠페인

- 코드 커밋: `ca54bed3697a706e908a000a1e5fc019db40b643`
- 조건: 개발 입력 4개, 입력별 독립 seed 5개, `openai/gpt-oss-120b`, temperature 0
- 결과: 근거 군집 41개, explicit 41개, inferred 0개, 자동 판정은 accepted 40개와
  needsQuestion 1개였다.
- 결정: inferred 표본이 없는 상태에서 isotonic 임계값이나 Wilson 하한을 추정하지
  않았다. 대신 새 inferred 제안은 모두 질문으로 보내는 보수 정책을 패킷 SHA-256과
  함께 `development-no-inference-v1`로 동결했다.
- 제한: 두 검토 양식은 비어 있으며, 41개 explicit 제안의 정확도·누락 평가는 실제 두
  검토자가 독립적으로 완료해야 한다. 이 검토 전에는 에이전트 성능 주장을 확정하지 않는다.
## 2026-08-08 P2-Azure 스트리밍 계측 재실행

- 사용자 승인에 따라 `baf5283` 코드 상태에서 P2-Azure 반복 1을 다시 실행했다. 샌드박스 내부의
  첫 시도는 요구사항 단계에서 11.784초 만에 `Connection error`로 끝났으므로 모델 실패가 아닌
  실행 환경의 네트워크 차단으로 분리했다. 네트워크 접근이 가능한 동일 명령의 유효 실행 ID는
  `easydep-full-p2-azure-20260808T055155Z-dbe562`이다.
- 유효 실행은 496.353초가 걸렸으며 요구사항 146.913초, 설계 218.205초, VM 전달 89.972초가
  주요 구간이었다. 설계의 구조화 호출은 모두 완료됐다. 관측 TTFT 범위는 0.297~1.432초였고,
  과거 `ApiSpecModel`의 300초 완료 지연은 재현되지 않았다. 특히 이번 `ApiSpecModel`은 응답 연결
  0.201초, TTFT 0.297초, 첫 JSON 내용 7.056초, 전체 완료 14.023초였다. 따라서 과거 사건을
  엔드포인트 TTFT 장애로 해석하지 않는다.
- 생성은 구현 단계에서 실패했다. 최초 Terraform의 HCL 오류를 한 번 수정한 뒤 AzureRM 공급자
  검증까지 진행했으나, 수정본이 NIC의 제거된 `network_security_group_id` 인자를 사용했고
  `azurerm_managed_disk`의 필수 `create_option`을 누락했다. 이는 리소스 생성 가능성 게이트의 실패다.
- 생성 실패 산출물에 대한 부분 평가는 의미 점수 0.25였으며 HTTPS와 영속 데이터가 실패하고,
  애플리케이션 포트·VM 수·디스크 크기·마운트 경로는 확인 불가였다. 공급자 검증 실패로 컨테이너
  기능 검사는 수행할 수 없었으므로 애플리케이션 기능 성공의 근거도 없다.
- 종료 뒤 `easydep.evaluation=true` 컨테이너와 해당 실험의 Python·Java·Gradle·OpenTofu·Terraform
  프로세스가 남지 않았음을 확인했다.

## 2026-08-08 P2 영속성·Azure 공급자 계약 보완

- P2-GCP에서 생성 소스가 Jakarta Persistence와 Spring Data JPA를 사용하면서 빌드에는 해당
  의존성이 없었던 결함을 개발 세트 개입으로 보완했다. 승인된 `persistent_storage` 요구가
  있을 때만 `spring-boot-starter-data-jpa`와 로컬 기능 검사용 H2 런타임을 결정적으로 추가한다.
  미확정 요구에는 추가하지 않아 질문·보류 정책을 우회하지 않는다.
- P2-Azure 실패를 공급자별 예외 문자열 수정으로 처리하지 않고, IaC 에이전트 입력에 고정
  공급자 호환성 계약을 추가했다. NIC와 NSG는 별도
  `azurerm_network_interface_security_group_association`으로 연결하고, 새 데이터 디스크의
  `azurerm_managed_disk.create_option`은 `Empty`로 지정하도록 한다.
- 근거는 HashiCorp의 [NIC-NSG 연결 리소스 공식 문서](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/network_interface_security_group_association)와
  [Managed Disk 공식 문서](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/managed_disk)다.
  후자는 `create_option`을 필수 인자로 정의하고 빈 디스크 예제에서 `Empty`를 사용한다. 실제
  수용 여부는 프로젝트가 고정한 AzureRM 5.0.1 공급자 스키마의 `tofu validate`로 별도 판정한다.
- 관련 오케스트레이션 회귀시험 37건과 Ruff 검사가 통과했다. 이 결과는 프롬프트 입력 계약과
  빌드 의존성 선택을 검증할 뿐, 아직 P2의 재시작 후 데이터 보존 성공을 입증하지 않는다.

## 2026-08-08 P2-Azure 공급자 계약 보완 후 개발 게이트

- 커밋 `6b50505`의 full 조건으로 실행한 ID는
  `easydep-full-p2-azure-20260808T061709Z-d0143d`이며 총 564.696초 뒤 구현 단계에서
  중단됐다. 요구사항 267.055초, 설계 194.273초, VM 전달 28.325초였고 요구사항의
  `RelationshipModel` 98.119초가 가장 긴 관측 하위 작업이었다.
- 직접 원인은 Azure 스키마가 아니라 IaC 출력 봉투였다. 생성기가 허용되지 않은
  `cloudinit.cfg` 파일명을 반환했고 안전 파일 검사가 이를 거부했다. 의미 부분 평가는 0.4167이며
  IaC와 컨테이너 게이트는 모두 실패했으므로 Azure 계약 보완의 효과를 판정할 수 없다.
- 파일 확장자 허용 범위를 결과에 맞춰 넓히지 않았다. 대신 안전 경로·확장자·봉투 형식 오류도
  HCL·공급자 오류와 같은 최대 1회 수정 피드백 예산을 사용하도록 일반화했다. 봉투 수정 뒤
  HCL 또는 공급자 오류가 남아도 두 번째 수정을 허용하지 않는다. `no-verification` 조건에서도
  경로 안전 검사는 연구 처치가 아닌 보안 불변식이므로 비활성화하지 않는다.
- 관련 회귀시험 72건과 Ruff 검사가 통과했고, 종료 뒤 평가 컨테이너와 해당 실행의 자식
  프로세스가 남지 않았음을 확인했다.

### 출력 봉투 수정 후 재실행

- 커밋 `4c1119c`의 실행 ID `easydep-full-p2-azure-20260808T063014Z-6ced9a`는
  640.499초 뒤 AzureRM 공급자 검증에서 중단됐다. 출력 봉투 수정은 수행됐지만 같은 1회 수정
  예산 안에서 만들어진 Terraform이 `azurerm_linux_virtual_machine` 내부에 지원되지 않는
  `data_disk` 블록을 사용했다. 두 번째 수정은 호출하지 않았다.
- [HashiCorp의 VM 데이터 디스크 연결 공식 문서](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/virtual_machine_data_disk_attachment)는
  관리 디스크 연결을 별도 `azurerm_virtual_machine_data_disk_attachment`로 표현하고
  `managed_disk_id`, `virtual_machine_id`, `lun`, `caching`을 요구한다. 이 공급자 근거를
  호환성 계약에 추가하고 Linux VM 내부 `data_disk` 블록을 금지했다.
- 이 반복도 공급자 검증에서 실패했으므로 리소스 생성 가능성과 애플리케이션 기능을 모두
  입증하지 못하며, 개발 개입 이력으로만 사용한다.

### Azure 리소스 게이트 통과와 애플리케이션 게이트 실패

- 커밋 `78509b6`의 실행 ID `easydep-full-p2-azure-20260808T064324Z-72f07e`는
  AzureRM 공급자 검증과 의미 검사 12/12를 모두 통과했다. 이로써 현재 P2-Azure 산출물은
  리소스 생성 가능성 게이트를 처음 통과했지만 실제 apply 성공을 뜻하지는 않는다.
- 전체 실행은 937.017초였고 내부 테스트 단계에서 174.171초 뒤 실패했다. Java 소스와 테스트는
  컴파일됐으나 Spring 컨텍스트 초기화가 실패해 `/health`와 CRUD 테스트 2개가 모두 실행되지
  못했고, 외부 컨테이너 기능 게이트도 실패했다.
- 직접 원인은 생성 YAML이 `jdbc:sqlite:/var/lib/notes/notes.db`, `org.sqlite.JDBC`,
  `org.hibernate.dialect.SQLiteDialect`를 선택한 반면 결정적 빌드는 H2만 포함한 저장 엔진
  불일치다. 특정 실행의 SQLite 의존성을 뒤늦게 추가하지 않고, P2의 파일 기반 보존 런타임을
  `jdbc:h2:file:/var/lib/notes/notes`로 고정한다. 시스템 소유 `application.properties`가 URL,
  H2 드라이버, H2 방언과 DDL 갱신을 함께 지정하고 `EASYDEP_DATASOURCE_URL`로 외부 대체를
  허용한다.
- 이 결과는 리소스 게이트 성공과 기능 게이트 실패를 분리해야 한다는 연구 설계를 실제로
  보여준다. 기능 성공과 재시작 후 데이터 보존은 다음 실행 전까지 미입증 상태다.

### 동일 run 체크포인트 수정과 P2 재시작 보존 게이트

- 위 실패를 새 run으로 처음부터 생성하지 않았다. 실행 ID
  `easydep-full-p2-azure-20260808T064324Z-72f07e`의 구현 산출물을 복원하고, 완료된 요구사항·
  설계·구현 하위 작업은 건너뛴 채 `testing.application`만 다시 실행했다. 최초 테스트 시도
  174.171초와 수정 시도 197.344초를 모두 보존했으며, 수정 결과는 원본이 아닌
  `repairs/attempt-1`에 기록했다. 이 결과는 독립 반복으로 집계하지 않는다.
- H2 파일 데이터소스 정합성을 고친 뒤 내부 Gradle 테스트, 외부 컨테이너 상태 검사와 CRUD,
  AzureRM 공급자 검사, 의미 검사 12/12가 통과했다. 그러나 기존 공통 평가기는 한 컨테이너
  수명 안의 POST→GET만 검사했으므로 이 시점에도 재시작 보존은 미입증이었다.
- P2 오라클에 별도 `persistenceAcceptance`를 추가했다. 평가기는 `/var/lib/notes`에 임시 Docker
  명명 볼륨을 연결하고 데이터를 쓴 뒤 정상 종료하며, 같은 볼륨으로 새 컨테이너를 만들어
  다시 읽는다. 일반 기능과 재시작 보존은 별도 결과로 기록한다.
- 최초 평가기 구현은 `docker rm --force`로 첫 컨테이너를 즉시 종료했다. 쓰기는 성공했지만
  재생성 뒤 빈 목록이 나왔으며, 이는 정상 재시작이 아니라 강제 종료 내구성을 측정한 평가기
  결함으로 분류했다. `docker stop --time 30` 뒤 제거하도록 고친 세 번째 평가에서 재시작 전
  쓰기, 정상 종료, 두 번째 컨테이너 상태 검사와 조회가 모두 통과했고, 조회된 값은
  `restart-proof`/`must-survive`였다.
- 최종 개발 평가 파일은 `repairs/attempt-1/persistence-evaluation-v3.json`이며
  `experimentEligible=true`, IaC·일반 기능·재시작 보존이 모두 `passed`다. 평가에 사용한
  컨테이너·명명 볼륨·이미지의 제거도 각각 `passed`로 확인했다. 이 개발 중 평가기 변경으로
  오라클 해시를 다시 계산했으며, 확인적 실행 전의 두 suite가 같은 새 해시를 참조한다.

## 2026-08-08 단일 변수 구성요소 투영 후보

- 종단 P1~P3를 구성요소 효과의 근거로 재사용하지 않고, 영속 스토리지·다중 VM 부하분산·
  HTTPS 종단의 세 쌍을 별도 개발 후보로 정의했다. 각 쌍은 같은 애플리케이션·CSP·리전·seed를
  유지하고 control과 treatment만 바꾼다.
- 기존 중립 `loadBalancer` 한 노드로는 공급자 구현 차이를 보존할 수 없음을 공식 문서에서
  재확인했다. 특히 Azure HTTPS는 L4 Azure Load Balancer가 아니라 Application Gateway의
  listener, certificate, routing rule, backend pool과 HTTP settings를 사용한다. 이들은
  Terraform에서 하나의 `azurerm_application_gateway` 안의 중첩 블록이다. 반면 GCP는
  forwarding rule, HTTPS proxy, URL map, backend service, instance group, health check와
  certificate가 여러 최상위 리소스로 분리된다.
- `component-projections.json`은 각 중립 변화에 대해 공급자별 최상위 리소스·데이터 소스·
  중첩 블록·게스트 설정을 구분하고 일대일·일대다·다대다 관계를 보존한다. AWS EBS의 동일
  가용 영역 제약과 세 CSP 모두의 디스크 연결·게스트 마운트 분리도 포함했다.
- 근거는 AWS·Azure·GCP 공식 설명서와 각 공급자가 공동 유지하거나 공식로 표시된 HashiCorp
  Terraform Registry 문서로 제한했다. 이 파일은 아직 `development-candidate`이며, 고정
  공급자 버전의 최소 구성 검증과 독립 검토 전에는 골드 오라클로 승격하지 않는다.

### 고정 공급자 스키마 감사

- 첫 감사 시도는 공급자 다운로드 전에 0.513초 만에 실패했다. 감사기가 생성한 단일 행 HCL에
  `source`와 `version`의 구분자가 없었고 Azure `features` 중첩 블록도 한 줄에 놓인 harness
  결함이었다. 결과는 `provider-schema-audit-attempt1-invalid-hcl.json`에 보존했으며 공급자
  부적합이나 네트워크 실패로 집계하지 않는다.
- 생성 HCL을 다중 행 블록으로 수정한 뒤 실제 고정 버전 AWS 5.100.0, AzureRM 5.0.1,
  Google 5.45.2를 직렬로 초기화하고 `tofu providers schema -json`을 검사했다. 전체
  벽시계는 249.632초였다.
- AWS 8개, Azure 12개, GCP 10개의 최상위 리소스·데이터 소스·중첩 블록 검사가 모두
  통과했고 실패는 0개였다. 게스트 파일시스템 포맷·마운트는 공급자 스키마 밖의 별도 기능
  계약이므로 이 숫자에 포함하지 않는다. 원시 결과는
  `measurements/2026-08-development/provider-schema-audit-20260808.json`에 보존한다.
- 종료 뒤 시스템 임시 경로에 `easydep-schema-*` 디렉터리가 남지 않았음을 확인했다. 이번
  실행은 공급자별 시간을 남기지 못했으므로 감사기에 명령별·공급자별·전체 단조 시계 계측을
  추가했다. 다음 실행부터 다운로드/초기화와 스키마 읽기 병목을 분리한다.
- 이 감사는 타입과 중첩 블록이 해당 공급자 버전에 존재함을 증명하지만 필수 인자와 관계가
  함께 유효함을 아직 증명하지 않는다. 따라서 상태는 골드가 아니라 개발 후보로 유지하며,
  다음 단계에서 최소 Terraform 구성의 `tofu validate`를 수행한다.

### AWS 최소 관계 fixture

- AWS 5.100.0 fixture에서 별도 EBS 볼륨과 `aws_volume_attachment`가 같은 영역의 VM을
  참조하도록 구성했다. 두 영역의 VM, ALB, 두 subnet, target group, 두 backend membership,
  상태 검사, HTTP listener와 ACM 인증서를 참조하는 HTTPS listener도 한 구성에서 연결했다.
- 실제 `plan/apply`는 실행하지 않았고 격리 임시 디렉터리에서 `fmt`, `init`, `validate`만
  수행했다. `validate.valid=true`, 오류 0개, 경고 0개로 통과했다.
- 전체 129.748초 중 형식 검사는 0.082초, 공급자 초기화는 99.843초, 검증은 29.486초였다.
  이 작업의 병목은 LLM이나 클라우드 API가 아니라 공급자 다운로드·초기화였다. 종료 뒤
  `easydep-fixture-*` 임시 디렉터리가 남지 않았음을 확인했다.
- 이 결과는 리소스 참조와 공급자 필수 스키마 조합을 증명하지만 AMI·ACM 인증서가 실제
  계정과 리전에 존재하거나 생성이 성공함을 증명하지 않는다. 실제 환경 전제와 생성 가능성,
  애플리케이션 기능은 이후 게이트에서 별도로 측정한다.

### 제한된 고정 공급자 캐시와 Azure 최소 관계 fixture

- 반복되는 공급자 다운로드가 병목이므로 사용자 제안에 따라
  `.easydep/provider-plugin-cache`를 연구 감사 전용 캐시로 추가했다. 허용 목록은 AWS
  5.100.0, AzureRM 5.0.1, Google 5.45.2뿐이며 감사 작업은 직렬로 실행한다. 허용 목록 밖의
  provider/version 디렉터리가 발견되면 자동 삭제하지 않고 다음 감사를 거부한다. 캐시는
  Git에 포함하지 않는다.
- Azure 첫 fixture 감사는 38.000초 뒤 실패했다. 초기화 34.701초는 통과했지만 두 VM에 넣은
  가짜 SSH 공개키가 디코딩되지 않아 검증이 3.161초 만에 오류 2개를 반환했다. 이는 공급자
  관계가 아니라 fixture 입력 결함이며
  `provider-fixture-audit-azure-attempt1-invalid-ssh.json`에 보존했다.
- 배포하지 않는 감사 전용 민감 암호 변수를 사용하도록 fixture를 고친 뒤 AzureRM 5.0.1에서
  다시 검증했다. 관리 디스크와 VM 연결, Application Gateway 전용 subnet, public frontend,
  HTTP/HTTPS listener, 중첩 인증서, 두 routing rule, backend settings, 상태 probe, backend
  pool과 두 NIC membership이 한 구성에서 연결된다.
- 수정 결과는 `validate.valid=true`, 오류 0개, 경고 0개다. 전체 68.917초 중 형식 검사는
  0.089초, 캐시를 채운 초기화는 60.807초, 검증은 7.889초였다. 캐시에는 AzureRM 5.0.1만
  249,000,251바이트로 보존됐고 격리 작업 디렉터리는 남지 않았다. 이후 같은 버전 감사부터
  다운로드를 반복하지 않는다.
- fixture의 PFX와 관리자 암호는 정적 스키마 검사용 비배포 값이다. 실제 인증서 유효성,
  가용 영역 지원, 리소스 생성과 애플리케이션 기능 성공의 근거로 사용하지 않는다.

### GCP 최소 관계 fixture와 3사 캐시 완성

- Google 5.45.2 fixture는 별도 persistent disk와 attachment, 두 영역 VM과 두 zonal instance
  group, health check, backend service, URL map, HTTP/HTTPS proxy, 관리 인증서와 두 global
  forwarding rule을 실제 참조로 연결했다. `validate.valid=true`, 오류 0개, 경고 0개다.
- GCP 감사 전체는 36.384초였으며 형식 검사 0.095초, 캐시를 채운 초기화 32.365초, 검증
  3.876초였다. 캐시는 AzureRM과 Google 두 버전을 포함해 355,370,088바이트가 됐다.
- 캐시 도입 전에 검증했던 AWS 5.100.0도 동일 fixture로 다시 검증해 전용 캐시에 보존했다.
  재검증은 `valid=true`로 통과했고 전체 219.683초 중 초기화·캐시 적재 206.585초, 검증
  12.473초였다.
- 최종 전용 캐시는 AWS 5.100.0, AzureRM 5.0.1, Google 5.45.2만 포함하며 크기는
  1,075,354,443바이트다. 예상하지 않은 버전은 0개이고 C: 여유 공간은 감사 직후
  11,911,700,480바이트였다. 새 버전을 자동 축적하지 않으며 버전 교체나 연구 종료 시
  `.easydep/provider-plugin-cache` 전체를 정리 대상으로 삼는다.
- 세 fixture 감사 모두 실제 `plan/apply`를 하지 않았고 종료 뒤 `easydep-fixture-*` 임시
  디렉터리가 남지 않았다. 이 결과는 공급자 관계의 정적 구성 유효성을 높이지만 실제 생성과
  기능 작동을 대신하지 않는다.

## 2026-08-08 체크포인트 재개와 앱–클라우드 경계 재결정

### 같은 run 재개와 LLM 장애 오판 수정

- P2-Azure 실행 `easydep-full-ps-treatment-azure-20260808T085013Z-9d5397`은 새 run을
  만들지 않고 `--retry-failed-checkpoints`로 같은 `runId`의 실패 체크포인트를 재개했다.
  완료된 요구사항·설계 단계는 다시 생성하지 않았다.
- 샌드박스 안 재시도 두 건은 LLM 호출이 약 3.4초와 2.0초에 `Connection error`로 끝났다.
  허용된 경로에서 같은 endpoint를 직접 측정하자 TTFT 3.66초, 전체 완료 3.93초였다. 따라서
  endpoint timeout 판단을 철회하고 실행 환경 egress 차단으로 재분류했다. 호출 환경과 TTFT,
  응답 완료, 스트림 간격을 구분해 기록한다.

### 고정 공급자 오프라인 mirror와 시간 병목

- 재개 실행이 잘못된 AWS 6.58.0을 내려받아 캐시를 약 908MB 늘린 뒤, 런타임도 고정 버전
  AWS 5.100.0, AzureRM 5.0.1, Google 5.45.2만 허용하도록 변경했다. provider source/version을
  다운로드 전에 검사하고 로컬 filesystem mirror 외 설치를 차단한다. 잘못 들어온 버전은
  제거했다.
- Azure fixture의 오프라인 검증은 init 27.545초, validate 31.777초였다. 다운로드를 없애도
  provider 시작과 스키마 검증 비용은 남으므로 둘을 분리 측정한다. 전용 캐시는 약 1.08GB이며
  연구 종료나 고정 버전 변경 때 정리한다.

### 리소스 게이트 뒤 앱 빌드 실패와 오버피팅 판정

- 같은 run의 후속 재개는 최초 IaC provider 오류를 한 번 수정한 뒤 AzureRM 정적 검증을
  통과하고 testing까지 진행했다. 이는 정적 구성 가능성만 확인하며 실제 `apply`나 앱 기능
  성공은 아니다.
- 생성 소스는 Jakarta Persistence와 SQLite를 사용했지만 빌드는 그 의존성을 제공하지 않아
  컴파일에 실패했다. `persistent_storage`이면 SQLite/JPA 설정을 넣는 보완은 P2에는 유용하나
  `영속 볼륨 = DB = JPA = SQLite`라는 잘못된 결합을 범용 로직에 넣으므로 오버피팅으로
  판정했다.
- SQLite는 P2의 고정 앱 워크로드로 유지하되 클라우드 영속 능력에서 추론하지 않는다.
  앱 엔진·프레임워크·경로는 `ApplicationRuntimeContract`, VM·볼륨·네트워크 능력은
  `CloudCapabilityContract`, 포트·경로·상태검사 연결은 `DeploymentBindingContract`로
  분리한다. 상세 내용은 [앱–클라우드 계약 설계](../../../docs/app-cloud-contract-design.md)에
  기록했다.

### 로컬 자원 정리 결정

- 과거 OpenTofu 캐시 약 1.28GB와 Docker 빌드 캐시를 정리했다. Docker VHDX는 13.66GiB에서
  9.42GiB로 줄었고 C: 여유 공간은 약 4.04GiB에서 8.28GiB로 회복됐다. 저장소의 고정 Gradle
  Wrapper를 사용하므로 임시 Gradle 이미지와 volume도 제거했다.
- 공급자 고정 캐시는 재현성과 반복 시간을 위해 유지한다. 실험은 직렬 실행하고 임시 작업공간,
  컨테이너, 클라우드 리소스는 종료 시 정리하며 정리 실패도 별도 결과로 기록한다.

### 계약 모델의 출처 표기 보완

- `ApplicationRuntimeContract`, `CloudCapabilityContract`, `DeploymentBindingContract`라는
  명칭과 스키마가 TOSCA·OAM 등에 이미 존재하는 것처럼 오해될 위험을 확인했다. 세 계약은
  모두 EasyDep 내부 연구 제안이며 외부 표준 호환 모델이 아니다.
- 외부 개념과의 관계를 `adopted`, `adapted`, `hypothesis`로 나누고 필드 수준 추적표를
  추가했다. 직접 대응 근거가 없는 DB 일관성, 상태–HA 규칙, 부분 복구와 해시 무효화는
  `hypothesis`로 표시해 절제·오류 주입 실험으로 검증한다. 새 필드도 근거가 등록되기 전에는
  기본적으로 `hypothesis`로 취급한다.

## 2026-08-08 앱–클라우드 계약 최소 구현

- 스키마 변경 자체에 오버피팅되지 않도록 세 계약의 안정 코어를 `id`, 자유 `kind`,
  `attributes`, 근거 참조, `extensions`로 제한했다. 알 수 없는 앱 fact와 과거
  `deployment_needs` capability가 손실 없이 통과하는 회귀시험을 추가했다.
- 기술별 지식은 스키마 열거형이 아니라 별도 탐지 규칙으로 분리했다. 현재 Java/Spring
  평가 경로의 JPA, SQLite, H2 신호만 등록했으며 이 규칙은 표준 사실이 아닌 연구 가설이다.
- scaffold는 더 이상 `persistent_storage`나 `embedded_database_support`를 보고 JPA/H2/
  SQLite를 선택하지 않는다. 생성된 앱 소스·설정에서 실제 사용 API를 관측한 뒤 그에 필요한
  빌드 의존성을 구성한다. logic 작업 뒤에도 계약을 재관측해 후속 변경으로 계약이 낡는 것을
  막는다.
- 테스트기는 `jdbc:sqlite` 문자열을 검색해 고정 `/tmp/easydep-notes.db`를 주입하던 규칙을
  제거했다. 앱 계약의 환경변수 template이 있을 때만 실행별 임시 디렉터리를 제공하고 종료와
  함께 정리한다.
- `APP-DEP-001`, `APP-DB-001`, `BIND-PORT-001`, `BIND-STORAGE-001` 합성 검사와 기존
  오케스트레이션·테스트를 포함한 최종 회귀시험 72건이 통과했다. 자유 attributes가 빌드
  스크립트로 투영될 때는 configuration 허용 목록과 dependency 좌표 문법을 검사하며, 테스트
  환경 주입은 `EASYDEP_` 소유 namespace만 허용한다. 아직 design에서
  binding을 자동 완성하거나 진단별 부분 복구를 수행하지 않으므로 P2 종단 성공 근거로
  해석하지 않는다.

## 2026-08-08 binding 계획과 진단 기반 부분 복구 연결

- 앱 계약에서 관측한 HTTP 포트를 cloud backend port로, 파일 기반 영속 데이터의 상위 경로를
  cloud mount target으로 선택하는 identity binding 계획기를 추가했다. 이는 DB 종류를 보지
  않으며 `runtime.storage`와 수락된 영속 cloud capability가 함께 있을 때만 mount binding을
  만든다. logic 수정 뒤에는 과거 `planned.*` 항목을 버리고 다시 계산한다.
- VM 전달 입력에 세 계약과 선택된 `applicationPort`, `applicationMountPath`를 추가했다.
  Dockerfile의 `EXPOSE`도 고정 8080이 아니라 앱 계약 포트를 사용한다. 아직 생성 Terraform을
  역파싱해 모든 LB·방화벽·mount 값을 대조하지는 않으므로 IaC binding 완전 검증 근거는 아니다.
  기존 Dockerfile이 계약 포트를 노출하지 않으면 덮어쓰지 않고 `BIND-PORT-001`로 실패시킨다.
- testing 출력에서 컴파일 의존성 오류와 DB driver/dialect 오류를 각각 `APP-DEP-001`,
  `APP-DB-001`로 분류한다. testing 실패 진단이 앱 오류이면 logic부터, binding/cloud 오류이면
  VM 전달부터 같은 `runId`에서 재실행한다. 앞서 성공한 scaffold·수용 테스트는 유지하고 수정
  지점 이후의 하류 결과만 무효화한다.
- 일반 기능 실패는 소유 작업을 단정할 근거가 없어 testing만 다시 실행한다. 구현 단계 자체의
  실패도 별도 역방향 회송 없이 현재 실패 작업부터 재개한다. retry history에는 원 진단,
  `repairOwner`, `invalidatedSteps`를 보존한다.
- 핵심 binding·복구 시험 66건과 cloud/capability/실험 실행기 회귀시험 46건, 합계 112건이
  통과했다. 이 결과는 단계 선택과 계약 전달의 결정성을 검증하지만 P2의 실제 앱·클라우드
  기능 성공을 아직 증명하지 않는다.

## 2026-08-08 생성 IaC binding 보수적 역검사

- 생성 입력에 계약을 전달하는 것만으로는 LLM이 이를 실제 HCL과 부트스트랩에 반영했는지 알
  수 없어, 생성 파일에서 backend/container port와 mount target을 다시 관측하는 게이트를
  추가했다. 이 게이트는 AWS/Azure/GCP 리소스 타입이나 P2 경로를 규칙에 사용하지 않는다.
- `backend_port`, `target_port`, `container_port`, backend·health·probe 문맥의 `port`, Docker
  publish container port만 앱 포트의 강한 증거로 취급한다. 따라서 외부 TLS listener 443을
  앱 포트 충돌로 세지 않는다. mount는 실행 명령의 대상 경로만 관측한다.
- 리터럴이 계약과 충돌하거나 필수 mount 명령이 없으면 구조화된 binding 진단으로 한 번의 IaC
  수정 피드백을 수행한다. 변수·template 값은 추측해 실패시키지 않고 unresolved로 보존한다.
  health·TLS·방화벽은 합법적인 topology가 여러 가지라 현재 자동 실패 범위에서 제외했다.
- 벤더 이름을 전혀 모르는 합성 HCL, listener/backend 포트 분리, 동적 값 보류, mount 누락·동적
  경로와 1회 수정 피드백 시험 6건이 통과했다. 이전 회귀 112건과 합쳐 118건이며, 이는 정적
  관측 게이트의 동작 근거이지 실제 `apply` 및 앱 기능 성공 근거는 아니다.
# 2026-08-08 대안 토폴로지 선택 의미론

| 하위 작업 | 벽시계 시간 | 결과와 병목 |
|---|---:|---|
| 기존 사영·계약 검색 | 약 3초 | `capabilityRealizations` 골격은 존재하지만 적용 조건·판정·선택 추적이 없음을 확인 |
| 표준·논문·벤더 근거 검색 | 약 7초 | TOSCA 2.0, CAMP, W3C PROV, NIST SP 500-307, MCDA 연구와 3사 공식 부하분산 문서 확보 |
| 최소 모델·문서 구현 | 약 44초 | 임의 가중치 없이 후보·제약·실행 가능성·관측·선택 정책 참조만 구현 |
| 신규 단위 테스트 | 4.6초 | 플러그인 자동 로드를 끈 격리 실행에서 4건 통과 |
| 관련 회귀 테스트 | 본문 약 12개 통과, 러너는 60초 종료 제한 초과 | 테스트 본문은 모두 통과했으나 종료 단계에서 Python 자식 프로세스 2개가 남음 |
| 잔류 프로세스 정리 | 약 5초 | 이번 검증에서 시작된 PID 5072, 16380만 종료 |

최초 결합 검증은 결과 출력 없이 120초 제한에 걸렸다. 이를 모델 연산의 타임아웃으로
분류하지 않고 테스트 수집·종료 지연으로 분리했다. 신규 파일 import는 약 12초 안에 완료됐고
Ruff 검사는 통과했다. 후속 실험 실행기는 테스트 본문 완료 시각과 프로세스 종료 시각을 별도
단계로 기록해야 한다.
# 2026-08-08 졸업과제 기준선 및 작업공간 정리

- 클라우드 모델 확장을 중단하고 구현·증거·미완료 항목을 `docs/project-baseline.md` 한 장으로
  정리했다. `research.md`는 수정하지 않았다.
- 런타임에서 참조되지 않던 대안 토폴로지 선택 모듈과 전용 시험·문서를 제거했다. 설계 이력은
  Git 커밋에 남으며 현재 연구 경로에서는 사용하지 않는다.
- 루트 `.pytest-*`, 정적 검사 캐시와 `.easydep`의 테스트 전용 임시 경로를 정리 대상으로
  확인했다. 총 111개 대상, 15,129개 파일, 약 21MiB였다. 대부분 제거했으나 권한이 잠긴
  `.pytest-azure-attachment`와 `.easydep/pytest-design-timeout`은 우회하지 않고 남겼다.
- 최초 회귀 실행은 시스템 임시 폴더 `pytest-of-projw`의 접근 거부로 `tmp_path` setup이
  실패했다. 이를 코드 실패로 분류하지 않았다. 저장소 내부 전용 `--basetemp`로 재실행한
  핵심 오케스트레이션·계약 회귀시험 89건은 22.9초에 모두 통과했다.
# 2026-08-08 P2 개발 실행과 실행 로그 보완

- P2-AWS 개발 실행은 요구사항 분석을 206.622초에 완료했으나 설계 단계의 서로 다른 두 LLM
  호출이 각각 330초 벽시계 제한에 도달했다. 따라서 이 결과는 클라우드 모델 또는 IaC 실패가
  아니라 `llmResponseCompletionTimeout` 검열로 기록하며, 앱·IaC·클라우드 기능 성공 여부는
  판정하지 않는다.
- 이후 개발 실행의 LLM 클라이언트 제한은 600초, LLM 벽시계 제한은 660초, 작업 전체 제한은
  1800초로 완화했다. 제한시간 단축보다 실제 병목 구간의 식별을 우선한다.
- P2-GCP는 새 표본을 만들지 않고 기존 run
  `easydep-full-p2-gcp-20260808T053454Z-bba68a`를 세 차례 부분 재개했다. 첫 재개는 testing만,
  두 번째와 세 번째 재개는 진단에 따라 `implementation.logic`, `implementation.vm_selection`,
  `implementation.vm_delivery`, testing만 다시 실행했다. 요구사항과 설계는 재사용했다.
- P2-GCP의 오류는 차례로 JPA 의존성 누락(`APP-DEP-001`), Hibernate dialect 클래스 불일치
  (`APP-DB-001`), 생성 코드의 일반 컴파일 오류로 이동했다. 세 번째 재개에서도 종단 성공에는
  이르지 못했으므로 P2-GCP는 실패한 개발 관찰로 유지하며 추가 사례별 패치는 중단한다.
- 세 번째 재개의 작업자 전체 시간은 694.926초였다. 기록된 생성·평가 시간은 294.365초이고,
  실제 하위 작업은 logic 12.614초, VM 선택 0.001초, VM 전달 47.711초, 앱 테스트 24.229초였다.
  나머지 약 400초는 작업자 시작과 첫 단계 사이의 준비 구간이다. 기존 계측이 이 시간을
  설명하지 못했으므로 생성 시작·종료와 평가 시작·종료 이벤트를 추가했다.
- 작업자는 stdout/stderr를 `worker-logs/*.log`에 즉시 기록한다. 로그에는 작업과 LLM 호출의
  시작·종료, 상태, 경과시간만 남기며 프롬프트·응답·인증정보는 남기지 않는다.
- 제어 프로세스가 중단되어 인덱스가 `running`으로 남아도 명시된 `resumeRunId`를 보존하도록
  수정했다. 아티팩트 복사에서는 테스트용 `.easydep-test-*` 디렉터리를 제외한다. 이 두 결함은
  재개 실패를 새 실행으로 오인하거나 Windows 접근 거부를 일으킬 수 있었으며 회귀시험을
  추가했다.
# 2026-08-08 작업자 준비 구간 병목 재측정

- P2-GCP 세 번째 재개에서 작업자 시작과 첫 하위 작업 사이에 약 400초의 공백이 관측되어,
  LLM·클라우드를 호출하지 않는 로컬 측정으로 후보를 분리했다.
- `evaluation.experiment` import는 15.478초, SQLite 상태 읽기는 0.043초, 약 397KB 상태의
  요구사항·설계·구현 계약 검증은 각각 0.001초 미만, 22개·27KB 애플리케이션 체크포인트
  복원은 2.867초였다. 어느 항목에서도 400초 지연은 재현되지 않았다.
- 구현 잠금은 기다리는 방식이 아니라 이미 점유됐으면 즉시 실패하는 비차단 잠금이므로 장시간
  잠금 대기도 원인에서 제외했다. 당시 중복 작업자 종료 과정과 Windows 자원 경합이 있었으므로
  해당 400초를 정상 실행 비용으로 일반화하지 않는다.
- 후속 실행에서 원인을 잃지 않도록 체크포인트 읽기·복원·실행, 실행 상태·아티팩트 저장의
  시작·종료 이벤트와 경과시간을 추가했다. 작업자 시작부터 `generationStarted`까지는 Python
  프로세스 및 모듈 초기화 구간으로 해석한다.
# 2026-08-09 P3-AWS 개발 실행과 동시 엔드포인트 probe

- P3-AWS 개발 실행 `easydep-full-p3-aws-20260808T145324Z-e9df62`은 요구사항 분석을
  244.740초에 완료했다. 작업자 시작부터 생성 시작까지는 13.526초로, 별도 import 측정과
  일치했으며 이전 P2의 약 400초 공백은 재현되지 않았다.
- 설계 단계에서 두 BCE 추출은 각각 13.920초와 10.679초에 완료됐지만 `SequenceModel`은
  660.017초에 벽시계 제한에 도달했다. 전체 생성 시간은 956.337초이며 실행 결과는
  `llmResponseCompletionTimeout` 검열이다. 구현·IaC·앱 기능 평가는 실행되지 않았다.
- 장기 호출이 진행 중인 2026-08-09 00:07 KST에 동일 모델·엔드포인트로 짧은 스트리밍
  probe를 보냈다. 응답 연결 1.526초, 첫 이벤트 1.553초, TTFT 1.565초, 전체 완료
  1.826초로 정상이었다. 따라서 이 사건은 엔드포인트 전체 속도제한이나 전면 장애가 아니라
  특정 구조화 요청의 응답 완료 지연으로 분류한다.
- 후속 개발 실행은 LLM 호출이 120초를 넘기면 독립적인 짧은 probe를 한 번 실행한다. probe의
  HTTP 429 여부, TTFT, 완료시간과 원래 작업명을 기록하며 응답 본문과 인증정보는 기록하지
  않는다. probe 자체가 처치에 영향을 줄 수 있으므로 홀드아웃·확증 실행에서는 비활성화한다.
# 2026-08-09 P3-Azure Sequence 단독 재현

- P3-Azure 실행 `easydep-full-p3-azure-20260808T151744Z-f65bd2`도 요구사항 분석을
  194.436초에 완료한 뒤 `SequenceModel` 단일 호출이 660.015초 제한에 도달했다. 120초 시점의
  자동 probe는 첫 이벤트 0.761초, 전체 0.954초, HTTP 429 없음으로 정상 응답했다.
- 설계 체크포인트는 `gen_sequence_diagram` 직전이었고 Sequence 결과는 없었다. AWS와 Azure의
  Sequence 입력은 각각 유스케이스 본문 11,320자와 10,638자, 클래스 다이어그램 1,088자와
  928자였다. Sequence 스키마는 1,220자이며 SDK 자동 재시도는 0회다.
- 기존 스트리밍 구조화 호출에는 completion 토큰 상한이 없었다. 사용자의 별도 전송 승인 후
  같은 Azure 체크포인트 입력을 `max_completion_tokens=8192`, 180초 제한으로 한 번만 호출하자
  30.707초에 완료됐다. 참가자 0개·메시지 26개여서 의미 검증은 여전히 필요하지만, 무제한
  completion이 장기 완료 지연의 원인 후보라는 재현 근거를 얻었다.
- 사례별 Sequence 내용을 하드코딩하지 않고 `LLM_MAX_COMPLETION_TOKENS`가 설정됐을 때만 모든
  설계 구조화 호출에 동일 상한을 전달하도록 했다. 다음 검증은 새 run이 아니라 같은 Azure
  설계 체크포인트에서 수행한다.
# 2026-08-09 P3-Azure 설계 복구 후 scaffold 경계

- completion 상한을 적용해 같은 Azure run의 설계 단계를 재개하자 Sequence는 33.109초,
  API 모델은 13.712초, 배포 모델은 9.319초에 완료됐고 설계 단계 전체가 통과했다. 요구사항은
  재사용했지만 설계 내부의 BCE 추출 두 건은 다시 실행되어 하위 체크포인트 재사용은 불완전했다.
- 다음 실패는 scaffold가 생성한 `src/main/resources/products.json`을 파일 확장자 허용 목록이
  거부한 것이었다. 경로는 기존 정책이 허용한다고 설명하는 `src/main` 내부이고 JSON은 일반적인
  운영 리소스이므로, 특정 상품 사례가 아니라 정책 구현 불일치로 판정해 `.json`을 허용했다.
  `src/test` 등 비운영 경로 차단은 그대로 유지했다.
# 2026-08-09 구현 첫 작업 실패 재개 경계

- JSON 운영 리소스 허용 후 같은 Azure run을 구현 단계에서 재개하려 했으나, scaffold가
  작업공간을 만들기 전에 실패한 상태라 `run_root`가 없다는 이유로 즉시 중단됐다.
- 완료된 구현 하위 작업이 하나도 없는 구현 단계 실패는 복원할 작업공간 자체가 없다. 이 경우
  요구사항·설계를 유지하고 scaffold부터 다시 실행하도록 했다. 반대로 완료된 구현 작업이
  있거나 testing 단계라면 기존 작업공간 없이는 안전하게 이어갈 수 없으므로 계속 실패시킨다.
# 2026-08-09 실패한 scaffold 작업공간 정리

- 구현 재개 허용 후에는 최초 scaffold 실패가 남긴 작업공간 때문에 `Run workspace already
  exists`가 발생했다. 실패 결과에는 `run_root`가 없으므로 이 디렉터리는 재사용 가능한
  체크포인트가 아니라 부분 기록이다.
- scaffold가 새 작업공간을 만든 호출에서 예외가 발생하면 그 작업공간을 즉시 제거하도록 했다.
  과거 실행처럼 잔여물이 이미 있는 경우에도 완료된 구현 하위 작업이 하나도 없을 때만 정확한
  run ID 작업공간을 제거한다. 기존 `run_root`를 받아 수정 중인 작업공간과 완료된 구현
  체크포인트는 삭제하지 않는다.
# 2026-08-09 운영 리소스 확장자 정책 통일

- 작업공간 정리 후 Azure 구현 재개에서 scaffold와 acceptance test는 각각 16.350초와
  11.483초에 통과했지만 logic 편집기가 같은 `products.json`을 거부했다.
- scaffold와 logic이 서로 다른 운영 파일 확장자 목록을 가진 것이 원인이었다. 두 경로가
  `.java`, `.kt`, `.yaml`, `.yml`, `.properties`, `.json`의 공통 허용 목록을 사용하도록
  통일했다. 경로는 계속 `src/main` 아래로 제한하며 테스트·빌드·인프라 파일은 허용하지 않는다.
# 2026-08-09 P3-Azure 개발 실행 종료

- 공통 운영 리소스 정책 적용 후 같은 run을 재개하자 checkpoint 복원은 0.041초, logic은
  4.314초, VM 선택은 0.001초 미만에 완료됐다. 완료된 scaffold와 acceptance test는 다시
  실행하지 않았다.
- VM 전달은 생성 31.121초, HCL 사전검사 0.506초, 내장 수리 14.285초, 수리 후 provider init
  17.493초, validate 3.091초 순으로 진행됐다. 수리 후에도 Application Gateway backend pool의
  set 인덱싱과 `azurerm_linux_virtual_machine`의 `zones` 대신 `zone`을 써야 하는 오류가 남았다.
- 이는 실행기·경계 정책 결함이 아니라 생성 Terraform이 Provider 스키마를 만족하지 못한 실제
  실험 실패다. P3-Azure를 통과시키기 위한 HCL 사례별 패치는 추가하지 않고 실패 관찰로
  종료한다. 실제 `apply`는 수행되지 않았으므로 정리할 클라우드 리소스도 생성되지 않았다.
# 2026-08-09 P3-GCP scaffold 장기 호출 중단

- P3-GCP는 요구사항 224.697초와 설계 208.497초를 완료했다. Sequence는 의미 검증에 따라
  49.544초와 33.072초 두 번 실행됐으며 둘 다 completion 상한 안에서 정상 종료됐다.
- scaffold 비스트리밍 단일 호출은 10분을 넘겨도 반환하지 않았다. 같은 시간대 probe는 응답
  연결 1.734초, TTFT 1.763초, 전체 2.005초, HTTP 429 없음으로 정상이었다. 정보가 더
  늘지 않아 해당 작업자만 종료하고 체크포인트를 보존했다.
- scaffold·acceptance test·logic provider도 SDK 재시도는 0회였지만 completion 상한을 전달하지
  않았다. `LLM_MAX_COMPLETION_TOKENS` 설정을 세 구현 provider에 동일하게 전달하도록 통일했다.
  기본값을 코드에 고정하지 않으며 이번 개발 재개에서는 8192를 사용한다.
# 2026-08-09 P3-GCP 개발 실행 종료와 체크포인트 한계

- completion 상한 적용 전 scaffold 단일 호출을 10분 넘게 관찰한 뒤 작업자만 종료했다. 이후
  같은 run ID를 재개하려 했지만 RunStore에 해당 run이 없어 `Unknown orchestration run`으로
  즉시 실패했다.
- 설계 전용 LangGraph 체크포인트는 `gen_sequence_diagram` 등 내부 노드를 저장하지만, 상위
  4단계 오케스트레이터 상태는 정상 반환 뒤 `_finish`에서 저장된다. 따라서 프로세스가 단계
  도중 강제 종료되면 요구사항·설계가 완료됐어도 상위 run을 복구할 수 없다.
- P3-GCP는 요구사항·설계 완료와 scaffold 응답 완료 검열까지만 유효한 개발 관찰로 남긴다.
  상태를 수작업 합성하거나 새 run을 성공할 때까지 반복하지 않는다. 단계별 영속 체크포인트는
  비교실험 전에 해결해야 할 실행기 기반 결함으로 다음 작업에 넘긴다. 실제 cloud `apply`는
  수행되지 않아 정리할 클라우드 리소스는 없다.

# 2026-08-09 상위 오케스트레이션 영속 체크포인트 보완

- 기존에는 네 단계 그래프가 모두 반환된 뒤에만 RunStore를 저장했다. 이 때문에 P3-GCP처럼 프로세스가 하위 작업 도중 종료되면 이미 완료한 요구사항·설계 결과도 같은 run ID로 복구할 수 없었다.
- run 시작 직후 초기 상태를 저장하고, 각 단계 결과와 설계·구현의 하위 작업 성공 직후 상태를 같은 RunStore 레코드에 갱신하도록 변경했다. 산출물 전체 복사는 최종 처리에만 유지하여 체크포인트 기록 자체가 새로운 병목이 되지 않게 했다.
- 명시적인 재시도 호출은 `failed`뿐 아니라 `running`으로 남은 중단 상태도 받을 수 있게 했다. 단, 이는 이전 작업 프로세스가 종료됐음을 실행 관리자가 확인한 뒤에만 사용하는 복구 경로다.
- 설계의 아키텍처 작업이 완료된 뒤 클라우드 보강 작업에서 프로세스가 중단되는 상황을 로컬에서 재현했다. 같은 run ID를 복구했을 때 요구사항과 완료된 아키텍처 작업은 다시 호출되지 않았고, 중단된 클라우드 보강 작업부터 실행되어 전체 과정이 완료됐다.
- `tests/test_modular_orchestration.py` 38개 테스트가 통과했다. 이 검증은 외부 LLM이나 클라우드 리소스를 사용하지 않았다.

# 2026-08-09 PS-control-Azure 체크포인트 파일럿

- 성분 실험의 영속 저장 대조 사례 `PS-control-azure`, `easydep-full`, 반복 1을 개발 파일럿으로 실행했다. 첫 시도는 샌드박스 네트워크 차단으로 요구사항 단계에서 6.864초 만에 실패했으며 429나 모델 지연이 아니었다. 외부 LLM 전송 승인을 받은 뒤 같은 run ID `easydep-full-ps-control-azure-20260808T163719Z-68f843`를 재개했다.
- 재개 실행에서 요구사항 162.638초, 설계 아키텍처 135.003초, scaffold 23.285초, acceptance test 생성 14.053초, logic 2.963초, VM delivery 73.833초가 걸렸다. LLM 단일 호출은 관찰 범위에서 2.046~37.074초였고 429나 stall probe 발동은 없었다.
- 단계·하위 작업 체크포인트 저장은 0.008~0.032초였으며 병목이 아니었다. 요구사항·설계·구현의 완료 작업이 같은 run에서 재사용되는 것도 로그로 확인했다.
- 최초 앱 테스트는 생성 Java가 `org.sqlite.SQLiteDataSource`를 직접 import하지만 Gradle이 SQLite JDBC를 `runtimeOnly`로 둬 컴파일에 실패했다. 직접 참조되는 SQLite·H2 드라이버를 compile 및 runtime classpath에 모두 포함하는 `implementation` 규칙으로 일반화해 수정했고 관련 계약·오케스트레이션 테스트 47개가 통과했다.
- 수정 뒤 보존 앱은 컴파일을 통과했지만 Hibernate가 SQLite dialect를 결정하지 못해 기능 테스트 2개가 실패했다. 같은 복구 시도의 VM delivery는 Azure 사례에 AWS Terraform을 생성했고, AWS provider의 폐기된 `aws_subnet_ids` data source 때문에 provider 검증에서 차단됐다.
- 위 두 실패는 앱 런타임 계약과 CSP 일관성 검증의 별도 관찰로 남긴다. 성공할 때까지 LLM을 반복하거나 SQLite/Azure 사례 전용 패치를 추가하지 않았다. Docker 평가와 cloud `apply`는 수행하지 않았으므로 생성된 클라우드 리소스는 없다.

# 2026-08-09 앱 런타임·CSP 불일치의 일반 검증 규칙

- SQLite 사례 전용 수정 대신 데이터베이스 표식, ORM 표식, 필수 런타임 설정 표식을 조합하는 확장형 규칙 레지스트리를 추가했다. 현재 관찰된 SQLite+JPA 조합은 특정 dialect 클래스가 아니라 `hibernate.dialect` 또는 `database-platform` 설정의 존재를 요구하므로 사용자 정의 dialect도 허용한다. 누락은 `APP-DB-002`로 조기에 보고한다.
- CSP는 사례 ID나 리전 이름으로 추측하지 않는다. 원문 클라우드 제약에서 AWS·Azure·GCP 명시를 탐지하고, 하나가 명시되면 요구사항 분석 결과보다 우선하여 VM 선택과 Terraform 생성에 같은 목표를 전달한다. 분석값과 다르면 `providerAnalysisMismatch`에 두 값을 남긴다.
- 원문이 둘 이상의 CSP를 배포 대상으로 명시하면 임의 선택하지 않고 실패한다. 이후 기존 provider source·version 및 외부 CSP 리소스 접두사 검증이 선택된 목표를 기준으로 동작한다.
- 사용자 정의 dialect 허용 반례와 다중 CSP 거부 반례를 포함해 앱 계약, VM delivery, 오케스트레이션 테스트 75개가 통과했다. 외부 LLM, Docker, cloud `apply`는 사용하지 않았다.

# 2026-08-09 PS-control-Azure 성분 파일럿 한 쌍

- 세 CSP 각각에서 원문 목표가 분석 결과보다 우선하는지와 외부 CSP 리소스 혼입을 차단하는지 대칭 반례를 추가했다. 앱 계약·VM delivery·오케스트레이션 테스트 80개와 정적 검사가 통과했다.
- 새 index에서 `PS-control-azure`, 반복 1의 `easydep-full`과 `easydep-no-depkb`를 각각 새 run으로 한 번 실행했다. Docker와 cloud `apply`는 수행하지 않았다. 두 조건 모두 실패하여 이 한 쌍으로 DepKB 효과를 추정하지 않는다.
- `easydep-no-depkb` run `easydep-no-depkb-ps-control-azure-20260808T170819Z-8230c7`은 요구사항 147.707초와 설계 162.769초를 완료한 뒤 scaffold 15.906초에서 SQLite+JPA dialect 설정 누락 `APP-DB-002`로 조기 차단됐다. 전체 생성 시간은 328.095초였다.
- `easydep-full` run `easydep-full-ps-control-azure-20260808T171359Z-ed1054`은 요구사항 153.991초, 설계 118.042초, VM delivery 52.359초를 거쳐 AzureRM 5.0.1 리소스만 포함한 Terraform을 생성했다. 이전 Azure→AWS 혼입은 재현되지 않았다. 앱 테스트는 잘못된 `org.hibernate.dialect.SQLiteDialect` 설정으로 2개가 실패했고 전체 생성 시간은 391.491초였다.
- 대조 입력은 VM·컨테이너 교체 시 데이터 손실을 허용하고 영속 앱 데이터 디스크를 만들지 말라고 명시했지만, `full` Terraform에는 `azurerm_managed_disk`와 attachment가 생성됐다. 이는 생성 성공 여부와 별개로 부정 요구가 capability 판단 또는 vendor projection까지 보존되지 않은 불일치다.
- 위 결과에 맞춰 사례 전용 SQLite dialect나 Azure 디스크 억제 규칙을 추가하지 않았다. 다음 수정 단위는 기술명이 아니라 긍정·부정·미지정 요구의 극성을 capability 계약과 projection이 끝까지 보존하는 일반 불변식이다.

# 2026-08-09 영속성 제약과 디스크 의존성의 극성 분리

- `full` 요구사항 산출물을 다시 확인하니 `persistent_storage.required: true`와
  `metadata.persistent_application_disk: false`가 함께 기록돼 있었다. 전자는 제약이 필수라는
  뜻이고 후자는 영속 앱 디스크를 금지한다는 값이다. 후속 코드가 `required`만 보고 디스크를
  선택해 제약 강도와 Boolean 값의 극성을 혼동했다.
- 논리 애플리케이션 모델의 `database` 노드도 클라우드 블록 디스크의 근거로 사용되고 있었다.
  데이터베이스 구현은 인메모리·임시 파일·외부 관리형 서비스 등 여러 합법적 실현이 가능하므로,
  이 연결을 제거하고 승인된 영속성 capability만 디스크 의존성 선택의 입력으로 제한했다.
- 현재 지원하는 영속성 capability 해석을 한 함수로 모아 설계 앵커와 IaC 생성 프롬프트가 같은
  결정을 사용하게 했다. 명시적인 `persistent_application_disk: false`는 필수 제약이어도 디스크를
  선택하지 않는다. 값이 없는 기존의 승인된 필수 영속성 요구는 이전 동작을 유지한다.
- 이는 `PS-control-azure`라는 사례명이나 Azure 리소스명을 참조하지 않는 계층 불변식 수정이다.
  관련 요구사항 계약·클라우드 설계·VM 전달·오케스트레이션·앱 계약 테스트 93개와 정적 검사가
  통과했다. 외부 LLM, Docker, cloud `apply`는 사용하지 않았다.

# 2026-08-09 의존성 모델 입력 지원 범위 가시화

- 실행 경로를 역추적한 결과, 동적으로 생성되는 `deployment_needs` 중 DepKB 설계가 이름과
  의미를 직접 해석하는 것은 현재 `persistent_storage` 하나였다. `multiZone`은 별도
  `RESOURCE_SPEC` 입력이고, `https_ingress`·`availability_requirement` 등은 IaC 에이전트에
  전달되지만 DepKB 의존성 앵커에는 연결되지 않았다.
- 전체 capability 어휘를 새로 정의하지 않고, 설계 결과에 이번 실행에서 해석한 입력과
  `unmodeledAcceptedNeeds`를 기록하도록 했다. 질문 대기 capability는 승인되지 않았으므로
  미모델링 목록에도 넣지 않는다.
- IaC 에이전트에도 이 범위 정보를 전달해 미모델링 요구를 DepKB가 충족하거나 입증했다고
  주장하지 않도록 했다. 미모델링 요구 자체는 기존처럼 원래 `deploymentNeeds`를 통해 처리한다.
- `multiZone → loadBalancer`는 현재 구현된 선택 규칙으로 명시하되 보편적 필연성으로 격상하지
  않았다. 이 가시화 결과를 이후 최소 보완 범위 선정의 관찰 자료로 사용한다.

# 2026-08-09 CNA 합성 사례 출처 감사 경계

- PURE는 일반 요구사항 문서이므로 CNA 배포 capability의 대표 사례 조사에서 제외했다.
  기존 CNA 앱 기능과 3사 공식 문서·중립 교차표·Provider 검증 자료를 합성 입력의 근거로
  사용하고, LLM은 근거 카드의 자연어 표현만 담당하도록 범위를 정정했다.
- 기존 세 component 축은 모든 CSP에서 공식 벤더 출처, projection 구성 요소·관계,
  통제·처치 사례와 동일한 앱 기능 oracle을 갖췄는지 자동 감사하도록 했다. 리소스 생성
  가능성과 앱 기능 성공은 계속 별도 조건이다.
- 현재 18개 사례에는 합성 당시 모델·프롬프트 해시·seed·근거 카드 해시가 없다. 이를 숨기지
  않고 개발 파일럿에는 사용할 수 있지만 재현 가능한 합성 코퍼스에는 부적격으로 판정한다.
  과거 계보를 추측해서 채우거나 사례를 조용히 재작성하지 않았다.
- 최초 자동 감사에서 HTTPS 축의 AWS 근거가 Terraform Registry뿐임을 발견했다. AWS 공식
  Application Load Balancer 문서에서 HTTPS listener가 서버 인증서와 보안 정책을 요구하고
  TLS를 종료한다는 근거를 확인해 projection 출처에 추가했다. 감사 조건을 낮춰 통과시키지
  않았다.

# 2026-08-09 요구사항 capability와 벤더 projection 연결

- 자유로운 `deployment_needs` key를 DepKB 식별자로 사용하던 결합을 분리했다. 요구사항
  산출물은 기존 key를 표시 이름으로 유지하면서, 현재 근거가 닫힌
  `persistent-block-storage`, `load-balanced-ingress`, `https-load-balanced-ingress`만
  `dependencyCapabilityIds`로 별도 전달한다.
- stable ID는 한 번의 LLM 출력만으로 채택하지 않는다. 동일 근거로 묶인 반복 표본이 모두
  같은 ID를 낸 경우에만 보존하며, 불일치는 미모델링으로 남긴다. 지원 목록 밖 ID도 버린다.
- 런타임 provider projection이 HTTP 부하분산에도 인증서를 포함하던 오류를 수정했다. 3사
  모두 HTTP 실현과 HTTPS·인증서 결합 실현을 분리했고, GCP에는 기존 근거에 있던 HTTP target
  proxy 대응을 추가했다. 일반 HTTPS를 부하분산으로 추론하지 않는다.
- 과거 CapabilityContract/v1 개발 산출물 재생을 위해 `persistent_storage` key만 제한적인
  마이그레이션 입력으로 유지한다. 새 의미는 이 예외에 추가하지 않는다.
- 변경된 projection 해시로 의존성 개입 manifest를 결정론적으로 재생성했다. 기존 GCP 개입
  결과는 수정하거나 재실행하지 않았으며 준비도 검사는 `ready=true`, blocker 0개를 반환했다.

# 2026-08-09 세 capability의 LLM·projection 실측

- 명시적 금지를 일반 사용자 요구사항으로 모델링하지 말라는 사용자 지적에 따라, 금지용
  stable-ID 해석을 일반화하지 않았다. 최종 측정은 기존 treatment 원문의 긍정 capability
  문장만 선택했고 새 문장을 만들지 않았다.
- 현재 설정의 capability 표본 수가 예상한 3이 아니라 5여서 live 실행당 호출은 15회였다.
  최초 live 154.598초, 전체 요구사항 확인 live 174.733초, 최종 긍정 capability live
  73.021초가 걸렸다. 명령 출력에서 429나 외부 오류는 관찰되지 않았다.
- 첫 live는 세 의미를 canonical 동적 key로 추출했지만 ID 필드를 비워 0/3·0/9였다. 하이픈과
  밑줄만 다른 canonical key를 stable ID로 인정하되, 반복 5표본이 모두 같은 의미에 동의해야
  한다는 규칙을 추가했다. 저장 결과의 오프라인 재생은 3/3·9/9였다.
- 최종 live에서 HTTP·HTTPS 부하분산은 stable ID 추출과 3사 projection이 모두 통과했다.
  영속성은 의미·20 GiB·마운트 경로를 맞혔지만 `persistent_storage_notes`라는 변형 key와 빈
  ID를 반환해 실패했다. 사례별 별칭을 추가하거나 성공할 때까지 재호출하지 않았다.
- capability 없음 baseline은 세 CSP 모두 VM만 선택해 통과했다. 이 측정은 LLM 추출과 로컬
  DepKB projection만 사용했으며 Docker, Provider 실행, cloud `apply`는 수행하지 않았다.

# 2026-08-09 열린 need의 제한적 capability 연결

- 영속성 실패 뒤 사례별 `persistent_storage_notes` 별칭을 추가하지 않았다. 엔티티 연결의
  후보·NIL 구조와 selective classification의 거부 원칙, TOSCA requirement–capability 분리를
  참고하되 별도 학습 모델이나 임베딩 저장소는 도입하지 않았다.
- stable ID 자체의 토큰이 key·role·근거 span에 모두 존재하는 후보만 만들고, 포함 관계로 더
  구체적인 후보가 하나일 때만 선택한다. 0개 또는 서로 비포함인 복수 후보는 미모델링으로
  남기며 반복 LLM 표본의 전원 동의 규칙도 유지한다.
- 최종 positive live 출력을 추가 LLM 없이 재생한 결과 영속성까지 연결돼 추출 3/3,
  CSP projection 9/9가 됐다. 이는 동일 출력에 대한 개발 재생이며 독립 live 성공으로 집계하지
  않는다. 동의어 사전·사례 ID·추가 호출은 사용하지 않았다.

# 2026-08-09 제한적 연결의 독립 LLM 확인

- 커밋 `5ae3ccc`의 코드를 변경하지 않고 긍정 capability 세 축을 새 LLM 출력으로 다시
  측정했다. 각 축 5회, 총 15회 호출에서 stable ID 추출은 3/3, AWS·Azure·GCP 사영은
  9/9가 통과했다. 셀별 시간은 영속 블록 스토리지 21.646초, HTTP 부하분산 24.478초,
  HTTPS 부하분산 30.397초였고 전체 측정은 76.584초였다.
- 최초 샌드박스 실행은 15회 모두 `APIConnectionError`로 degraded되어 0/3·0/9를 냈다.
  직후 같은 환경의 단발 probe도 0.729초에 연결 오류가 났지만, 승인된 외부 네트워크에서는
  TTFT 1.331초, 전체 1.568초에 정상 완료됐다. 따라서 최초 실행은 모델·속도제한 결과가
  아니라 실행 환경의 네트워크 검열로 분류하고 성능 집계에서 제외한다.
- 독립 측정은 LLM capability 추출과 로컬 provider projection만 확인했다. Terraform
  provider 실행, Docker 기능 검사, cloud `apply`는 수행하지 않았으며 생성·정리할 클라우드
  리소스도 없다. 다음 게이트는 기존 앱–클라우드 계약의 합성 불일치 검사를 고정한 뒤 실제
  종단 사례에서 앱 기능과 클라우드 생성 가능성을 분리해 관찰하는 것이다.
- 앱 내부 의존성, 앱 포트·영속 경로와 cloud binding, 생성 IaC의 포트·mount 관측, 세 CSP
  projection을 함께 다루는 기존 합성 검사 27개를 재실행해 모두 통과했다. 이는 결정적
  불일치 판정기의 회귀 기준이며 실제 앱 빌드·기능이나 cloud 생성 성공을 대신하지 않는다.

# 2026-08-09 PS-control-Azure 앱 기능 재검증

- 최신 `easydep-full` 실패 체크포인트의 앱만 복원했다. 요구사항·설계·구현 LLM은 다시
  호출하지 않았고, 기존 Gradle 캐시로 테스트를 실행했다.
- 정적 검사기는 dialect 설정의 존재만 보고 통과했지만 실제 테스트 2개는 모두
  `org.hibernate.dialect.SQLiteDialect`를 classpath에서 찾지 못해 실패했다. Hibernate 6.6
  공식 dialect 문서는 SQLite를 community dialect로 분류하며 별도
  `hibernate-community-dialects` 모듈이 필요하다고 명시한다.
- 이 관찰을 사례 ID 분기로 처리하지 않고, 설정한 ORM 확장 클래스와 제공 모듈의 결합을
  교체 가능한 데이터베이스–dialect 규칙으로 검사한다. 잘못된 알려진 클래스는
  `APP-DB-003`, 공식 community 클래스만 설정하고 제공 모듈이 없으면 `APP-DEP-001`로
  빌드 전에 차단한다. 새 진단도 기존 부분 복구 경계에 따라 logic과 하류 작업만 무효화한다.
- 첫 부분 재개는 logic LLM을 호출했지만 이전 구조화 진단을 프롬프트에 전달하지 않아 변경
  없이 `APP-DB-003`에서 다시 멈췄다. 실패한 하위 작업을 재시도할 때 그 작업의 직전 진단만
  `repairFeedback`으로 전달하도록 일반화했다. 완료 작업은 재실행하지 않으며
  `no-verification` 절제 조건에서는 이 피드백을 전달하지 않는다.
- 구조화 피드백을 받은 두 번째 logic 호출은 설정을 사용자 정의 dialect로 바꾸고 구현
  소스를 생성했지만 검증기는 다시 같은 진단을 냈다. 원인은 생성 소스가 아니라 이전 Gradle
  실행이 남긴 `build/resources/main` 복사본까지 관찰한 것이었다. 앱 계약 관찰 범위를
  `src/main`으로 제한해 테스트·빌드 산출물의 오래된 문자열이 현재 계약 사실을 오염시키지
  않게 했다.
- 오염을 제거한 현재 소스는 정적 앱 계약을 통과했지만 Gradle `compileJava`에서 실패했다.
  생성된 사용자 정의 dialect가 현재 Hibernate API에 없는 `registerColumnType`을 호출한 것이
  원인이다. 이는 리소스 생성 가능성과 별개인 앱 기능 실패이며, 정적 설정–모듈 결합 검사만으로
  임의 확장 구현의 API 호환성까지 보장할 수 없음을 보여준다. 성공할 때까지 LLM을 반복하거나
  해당 메서드를 사례별로 치환하지 않고 체크포인트와 컴파일 증거를 보존했다. cloud `apply`는
  수행하지 않았다.
- 동적 실패를 다음 복구 입력으로 사용할 때에는 테스트 로그 전체가 아니라 진단 코드와 마지막
  2,000자의 실행 증거만 retry history에 보존한다. 테스트 실패가 logic 소유 진단이면 완료된
  상류 작업을 유지한 채 이 증거를 다음 logic 호출에 전달한다. 피드백이 있을 때만 수정 의무를
  명시하며 `no-verification`에서는 전달하지 않는다.
- 기존 체크포인트를 다시 재개했지만, 저장된 마지막 상태가 동적 컴파일 실패가 아니라 과거
  `APP-DB-003` 정적 실패였기 때문에 logic은 그 오래된 진단을 다시 처리했다. 8.213초 뒤 설정을
  존재하지 않는 core dialect로 되돌려 같은 `APP-DB-003`이 세 번째 반복됐다. 성공할 때까지
  호출하지 않고 중단했다. 세 repair attempt는 각각 보존했으며, 동적 증거 전달 효과는 이력이
  섞이지 않은 새 개발 실행에서 평가해야 한다. cloud `apply`는 수행하지 않았다.

# 2026-08-09 새 PS-control-Azure 실행의 설계 단일 호출 검열

- 별도 실험 인덱스에서 새 `easydep-full` 실행을 시작했다. 요구사항은 150.521초에 완료됐고
  설계의 BCE 추출 두 호출은 각각 15.926초와 8.365초에 완료됐다.
- 이어진 `SequenceModel`만 330.005초 wall timeout으로 실패했다. 같은 지연 중 자동 probe는
  첫 이벤트 0.549초, 전체 0.717초에 정상 완료됐고 429가 아니었으므로 엔드포인트 전반의
  속도제한이 아니라 해당 구조화 응답의 완료 정지로 분류했다.
- 내부 설계 체크포인트에는 `gen_sequence_diagram`이 다음 노드로 남고 BCE 결과가 보존돼
  있었다. 상위 provider가 빈 실패 산출물만 보고 설계를 처음부터 시작하지 않도록, pending
  내부 노드가 있으면 새 입력 없이 그 노드만 재실행하는 경로를 추가했다.
- 같은 run을 재개하자 요구사항과 BCE를 반복하지 않고 설계가 127.005초에 완료됐으며 cloud
  enrichment는 0.001초 미만, scaffold는 18.384초가 걸렸다. acceptance test는
  `src/test/resources/clean-db.sql`을 제안했지만 Java/Kotlin만 허용하던 쓰기 경계에서 차단됐다.
- 테스트 리소스는 실행 스크립트를 허용하지 않고 `src/test/resources` 아래의 SQL·JSON·YAML·
  properties·CSV·TXT 같은 선언적 확장자만 허용한다. 생산 소스·빌드·인프라 경계는 그대로다.
- acceptance 재개는 15.119초에 통과했고 logic 3.606초, VM 선택 0.001초 미만, VM 전달
  54.959초 뒤 앱 컴파일에서 `JdbcTemplate`·`RowMapper` 제공 의존성이 없어 `APP-DEP-001`로
  테스트 단계에서 실패했다.
- 이 동적 증거의 마지막 2,000자를 logic에 전달해 재개하자 logic만 13.034초에 수정 완료됐다.
  요구사항·설계·scaffold·acceptance test는 반복하지 않았다. 따라서 동적 실패 → 소유 하위
  작업 복구 경로는 fresh 실행에서 실제 작동함을 확인했다.
- 하류 VM 전달은 고정 provider 캐시에 없는 `hashicorp/template`을 생성해 provider schema
  검증에서 차단됐다. 임의 provider를 다운로드하거나 캐시에 추가하지 않았다.
- 복구 앱을 별도로 테스트하자 컴파일과 health 검사는 통과했지만 POST `/notes`가 500을 반환해
  업무 기능 검사는 실패했다. acceptance test가 Spring 컨텍스트 생성 뒤 DB 파일을 삭제하는
  순서와 앱의 테이블 초기화 시점이 충돌했을 가능성이 있으나, 테스트나 앱을 사례별로 고치지
  않고 앱 기능 실패로 보존했다. Terraform `apply`는 수행하지 않았다.
# 2026-08-09 PS-control-Azure 앱과 테스트 픽스처의 독립 진단

- 실패 직후의 `notes-test.db`는 크기가 0바이트이고 테이블이 없었지만, 앱이 통상 사용한 DB에는
  `notes`와 `sqlite_sequence` 테이블이 존재했다. 생성된 테스트는 Spring 컨텍스트가 저장소를
  초기화한 뒤 각 테스트 직전에 DB 파일을 삭제하고 있었으므로, 테스트 픽스처가 앱이 만든
  스키마를 제거하는 순서 문제로 판단했다.
- 동일 앱의 `bootJar`를 별도 임시 DB와 18080 포트로 실행해 독립 확인했다. `/health`는 200,
  POST `/notes`는 201, 이어진 GET `/notes`는 방금 생성한 노트를 포함해 200을 반환했다. 따라서
  이 관찰에서 앱 로직은 동작했고, 종단 테스트 실패의 소유 작업은 acceptance test이다.
- 최초 준비 확인은 Windows PowerShell의 웹 응답 파싱 방식 때문에 실제 기동을 놓쳤고, 첫 POST는
  셸의 JSON 인용 문제로 400을 만들었다. `-UseBasicParsing`과 PowerShell 객체 JSON 직렬화로
  계측 오류를 제거한 결과만 위 판정에 사용했다. 임시 Java 프로세스, DB와 로그는 확인 후 정리했다.
# 2026-08-09 고정 Provider 경계 보완과 재개 실행 오염

- 제한된 로컬 캐시에는 선택 CSP의 고정 Provider만 둔다는 실험 계약을 IaC 사전 검증에도
  적용했다. `required_providers`뿐 아니라 모든 `resource`와 `data` 블록의 형식이 선택 CSP
  네임스페이스인지 검사한다. 따라서 선언 없이 암묵적으로 보조 Provider를 요구하는 구성도
  초기화 전에 차단하며, `templatefile()` 같은 Terraform/OpenTofu 언어 내장 함수는 허용한다.
  AWS·Azure·GCP 대칭 테스트와 내장 함수 허용 테스트를 포함한 관련 테스트 36개가 통과했다.
- 기존 run을 명시적으로 재개한 첫 worker는 실패했던 `implementation.vm_delivery`만 실행해
  38.572초에 완료했고, 이어진 `testing.application`이 39.336초 뒤 실패했다. Provider 초기화와
  검증이 모두 통과했으므로 `hashicorp/template` 의존 문제는 선택 Provider 경계와 한 차례의
  제한된 수리로 해소됐음을 확인했다. cloud `apply`는 수행하지 않았다.
- 다만 실행 셸이 5초 후 종료됐다고 보고한 뒤에도 첫 controller/worker가 계속 실행 중이었다.
  이를 종료된 것으로 오인해 같은 run을 다시 재개하면서 두 worker가 겹쳤고, 먼저 끝난 worker의
  작업공간 정리가 후발 worker의 application 디렉터리를 제거했다. 후발 결과의
  `Generated application repository is absent`와 최종 실험 인덱스는 동시 실행으로 오염됐으므로
  성능·성공률 근거에서 제외한다. 첫 worker의 단계별 로그 중 겹치기 전에 끝난 VM delivery 결과만
  진단 근거로 보존하며, 동일 run의 추가 재시도는 중단했다.
# 2026-08-09 중복 실행 차단과 사용자 지정 하위 작업 복구

- 같은 run의 start·resume·retry 전체를 SHA-256 기반 run별 파일 잠금으로 감쌌다. 서로 다른
  run은 실행할 수 있지만 동일 run의 두 번째 실행은 상태를 읽거나 작업공간을 복원하기 전에
  즉시 거부한다. 기존 구현 단계의 전역 자원 잠금은 유지한다. orchestration과 experiment 관련
  회귀 테스트 80개가 통과했다.
- 진단 문자열만으로 테스트와 앱의 소유권을 자동 추측하지 않고, 구현 또는 테스트 실패 뒤
  운영자가 기존 구현 하위 작업 중 하나를 수리 소유자로 지정할 수 있게 했다. 지정된 작업과
  그 이후 작업만 무효화하며, 운영자 사유는 2,000자 이내 수리 증거로 전달한다. 허용 목록 밖
  작업은 거부한다.
- 동시 실행으로 최신 application 스냅샷이 없는 경우에도 자동으로 과거 결과를 섞지 않는다.
  운영자가 수리 소유자를 명시한 경우에만 요청 시점보다 앞선 가장 최근의 존재하는 불변
  application 스냅샷을 복원한다. 해당 복원과 사용자 지정 분기의 회귀 테스트 45개가 통과했다.
- PS-control-Azure의 기존 run에서 독립 앱 프로브 결과를 근거로
  `implementation.acceptance_tests`를 수리 소유자로 지정했다. 요구사항·설계·scaffold는
  재실행하지 않았고 acceptance test 23.701초, logic 5.166초, VM 선택 0.001초, VM delivery
  40.439초, 앱 테스트 37.886초를 거쳐 총 125.6초에 완료됐다. VM delivery의 Provider init은
  2.957초, validate는 3.256초였으며 한 차례 제한 수리 뒤 모두 통과했다.
- P2-Azure는 기존 run `easydep-full-p2-azure-20260808T064324Z-72f07e`가 이미 완료 상태이고,
  내부 테스트·IaC 검사·일반 기능·정상 재시작 뒤 영속성 및 정리까지 통과한 기록이 있으므로
  다시 실행하지 않았다. 개발 중 수정 실행을 새 독립 반복으로 중복 집계하지 않는 원칙을
  유지했다. 이번 작업에서도 cloud `apply`는 수행하지 않았다.

## 2026-08-09 구성요소 파일럿과 측정 교정

- PS-control Azure는 acceptance 하위 작업부터 재개해 125.6초에 완료했다. 요구사항·설계·scaffold는 다시 실행하지 않았다.
- TLS-treatment Azure의 scaffold가 20분 제한에 도달했지만 즉시 수행한 엔드포인트 probe는 TTFT 2.336초, 전체 2.579초, 429 없음이었다. 동일 산출물에서 포트 추론 정규식의 과도한 backtracking을 재현했고, 줄 단위 파서로 교체한 뒤 0.009405초가 됐다.
- TLS-treatment의 VM delivery는 Azure Application Gateway provider schema 오류 두 종류에서 실패했다. 사례별 Terraform 패치를 더하지 않고 실패 증거로 보존했다.
- LB-control Azure의 full과 no-depkb는 각각 VM delivery만 재실행해 완료했다. 그러나 동일 사례·seed에서도 앱 파일 해시가 달라 종단 결과를 DepKB 효과로 비교하지 않기로 했다.
- 저장된 동일 LLM 출력으로 고정입력 투영 절제를 추가했다. 9/9 provider cell의 입력 해시가 같았고 modeled outcome은 full 9, no-depkb 0, realization은 full 6, no-depkb 0이었다. 이는 투영기 처치 충실도이며 생성·기능 성공 증거가 아니다.
- 감사에서 mount 경로 하드코딩, 관계 cardinality/constraint 과대 채점, 과거 attempt 체크포인트 혼합 가능성을 확인했다. mount는 생성 경로를 관측하도록 바꾸고, 관계는 `observed-unverified`, 제약은 별도 gate로 유지했다. 과거 checkpoint fallback은 runId·appId·완료 단계·application digest가 일치해야 복원한다.
- 상세 결과와 후속 범위는 `component-pilot-results-20260809.md`, `undergraduate-research-plan.md`에 기록했다. `docs/research.md`는 수정하지 않았다.

### 근거 기반 dependency expectation 파생

- component oracle의 3사×6 profile 빈 `requiredDependencies` 표를 삭제했다. 사례별 edge를 수기로 입력하지 않고 profile의 `componentDeltas`와 provider를 키로 `component-projections.json`에서 파생한다.
- 파생 결과는 정적 Terraform reference, cardinality, constraint의 세 gate로 나뉜다. 정적 reference만 합격·실패에 포함하고 나머지는 `requires-separate-gate`로 남긴다.
- 고정 provider fixture에서 정적 reference는 AWS 9/9, Azure 11/11, GCP 11/11이 관측됐다. 관계 하나의 pair를 제거한 반례 시험에서는 해당 dependency가 실패했다.
- CNA 감사 결과는 `eligibleForDependencyStructureMeasurement=true`, `eligibleForCardinalityOrConstraintClaim=false`다. 따라서 다음 생성 실험에서는 정적 의존성 누락을 측정할 수 있지만 cardinality나 런타임 제약 충족을 함께 주장하지 않는다.
- 보존된 실패 PS-treatment Azure attempt 4를 재채점하자 manifest의 Azure 요청과 AWS Terraform이 불일치했다. 독립 provider boundary가 failed였고 Azure 정적 dependency reference는 0/2였다. 이 반례는 새 평가기가 실제 생성 오류를 잡는다는 개발 증거이며 기존 P2-Azure 성공 실행과 별개다.

## 2026-08-09 앱–클라우드 validator 고정입력 절제

- 문서에만 있던 `no-consistency-validator` variant를 실제 orchestration payload에 추가했다. scaffold, logic, VM delivery가 같은 flag를 소비하며 DepKB와 repair feedback은 유지한다.
- VM delivery의 IaC binding 및 기존 Dockerfile port 검증도 같은 flag 경계에 포함했다. 결과 metrics와 preflight에 validator 활성 여부를 기록한다.
- Gradle, Spring Data JPA, Hibernate, Spring Boot, Docker 공식 문서를 근거로 build/runtime dependency, runtime integration, port, storage path의 mismatch/control 8건을 고정했다.
- 동일 입력 절제에서 full은 mismatch 4/4를 조기 탐지하고 control 0/4 오탐, 수정 소유 작업 4/4 일치였다. no-validator의 조기 탐지는 0/4였다. LLM 호출과 cloud apply는 없었고 evaluator 내부 시간은 약 0.065초였다.
- 이는 validator 구성요소와 variant 처치 충실도 증거다. 실제 수정 실행, 오수정률, downstream 기능 성공은 측정하지 않았으므로 결과에 `functionalSuccessMeasured=false`, `repairExecutionMeasured=false`로 보존했다.

## 2026-08-09 자연어 질문–응답 부분 재개

- PURE FR/NFR 자료는 CNA 요구사항 코퍼스가 아니므로 실제 요구사항 경로의 기본 예시 샘플링을
  `none`으로 바꿨다. 비교실험은 기존 sampler를 명시적으로 켤 수 있다. 선택적 Excel reader와
  Windows 콘솔 인코딩 오류가 실제 분석을 막지 않도록 했다.
- 같은 자연어 입력을 plain graph에서 실행했을 때 39.799초, LLM 8회에 완료됐지만 질문은 사용자에게
  나오지 않았다. 오케스트레이션 어댑터가 피드백 게이트를 항상 꺼 둔 것이 원인이었다. 대화형 실행만
  gated graph를 사용하고 배치는 기존 plain graph를 유지하도록 분리했다.
- 실제 gated checkpoint에서 provider·region·월 예산 필수 질문과 용량·트래픽·다중 영역 권고 질문을
  확인했다. Azure, Korea Central, 월 100 USD 답변 뒤 관련 제약 구조화만 재개했으며 LLM 1회,
  4.638초, 전체 7.139초가 걸렸다. 최종 계약은 유효했고 필수 질문·거절은 0건, 상류 배포 필요사항은
  보존됐다.
- stall probe가 `.env`를 읽지 못해 0.000159초 설정 오류를 엔드포인트 실패처럼 출력하던 문제를
  고쳤다. 수정 후 TTFT 2.000초, 전체 2.161초, HTTP 429 없음이었다.
- 관련 회귀 테스트 73개와 ruff가 통과했다. 앱 기능 성공과 cloud apply는 이 파일럿에서 측정하지
  않았다. `docs/research.md`는 수정하지 않았다.

## 2026-08-09 용량 질문·HA 상태 충돌·VM 반영

- provider·region·예산을 고정하고 용량 하한만 비운 자연어 입력에서 최소 vCPU/메모리 질문을
  확인했다. `minVCpu=2` 답변 뒤에는 제약 구조화 LLM 1회만 실행돼 7.851초에 계약이 갱신됐다.
- 로컬 파일 저장과 AZ 장애 생존을 함께 적은 자연어 입력은 두 요구와 `multiZone=true`를 모두
  추출했다. 실제 scaffold는 Java 파일 I/O와 `/data/records.db`를 만들었지만 JDBC 전용 관측기가
  놓쳤다. 파일 I/O API와 외부설정 절대 경로가 함께 있을 때만 node-filesystem 상태로 관측하도록
  일반화했고, 로그 경로만 있는 control은 제외했다.
- `BIND-STATE-HA-001`은 자동 수정하지 않고 상태 외부화/복제 또는 가용성 요구 재검토를 사용자에게
  묻는다. 실제 외부화 수정 1회가 설명만 shared file로 바꾸고 로컬 파일을 유지하자 재검증이 다시
  `needs_input`으로 차단했다. 실패한 수정은 임시 앱 snapshot에서 되돌려 원래 checkpoint 파일을
  보존한다. 성공적 해소는 아직 측정되지 않았다.
- VM 추천값을 프롬프트에 넣는 데서 끝나지 않고 AWS instance_type, Azure size, GCP machine_type과
  변수 기본값을 HCL AST로 관측하는 gate를 추가했다. 실제 고정 카탈로그 추천 3건은 모두 IaC 반영을
  통과했고 Azure Korea Central 가격 공백은 보류했다. provider validate와 cloud apply는 수행하지 않았다.
- `docs/research.md`는 수정하지 않았다.
## 2026-08-09 사용자 요구 수정에 따른 상위 단계 재개

- `BIND-STATE-HA-001`에서 사용자가 가용성 요구 수정을 선택해도 구현 코드가 요구를 임의로
  낮추지 않도록 했다.
- 응답에는 수정 후의 **전체 활성 요구사항**이 필요하다. 누락, 빈 값, 기존 요구와 동일한 값은
  거부한다.
- 원 요구사항과 수정본, 사용자 선택, 시각을 `requirementRevisionHistory`에 보존한다.
- 같은 run ID를 유지하면서 requirements, design, implementation, testing만 무효화한다. 이는
  구현 실패의 무조건 재실행이 아니라 사용자가 상위 입력을 바꾼 경우의 추적 가능한 재계산이다.
- 이전 구현 작업공간은 불변 artifact가 저장된 뒤 제거하여 서로 다른 요구 버전의 파일이 섞이지
  않게 했다.
- `tests/test_modular_orchestration.py` 57개와 Ruff 검사를 통과했다.
## 2026-08-09 동일 생성 앱 스냅샷 validator 파일럿

- 완료 artifact를 두 arm에 복제하고 tree SHA-256으로 동일 입력을 확인하는 러너를 추가했다.
- 과거 `completed` P2 artifact가 현재 계약 검증과 Gradle 테스트를 통과하지 않아 저장 사례의
  기준에서 제외했다. manifest 상태를 현재 유효성의 대리값으로 사용하지 않는다.
- 현재 유효한 동일 P1 앱에서 dependency, port, storage target 세 경계를 변형했다.
- full은 3/3을 downstream 전에 탐지했다. no-validator의 Gradle 테스트는 dependency만
  27.963초에 실패했고 port와 storage는 각각 45.085초, 37.793초에 통과했다.
- 호스트 mount와 컨테이너 target 관측을 분리했다. 서로 다른 합법 경로를 동일해야 한다고
  가정하지 않으며 앱 접근 경로는 컨테이너 target에 대응한다.
- LLM 수정과 실제 cloud 기능은 아직 측정하지 않았다고 결과에 명시했다.
## 2026-08-09 소유 하위 작업 LLM 수정 파일럿

- 동일 변형 snapshot에서 진단 소유 하위 작업만 실행하는 격리 러너를 추가했다.
- dependency는 `implementation.logic` 1회, 8.426초에 해소됐고 수정 파일은 실험용 Java 파일
  하나였다. HTTP acceptance test는 41.000초에 통과했다.
- port는 `implementation.vm_delivery` 생성+수정 2회, 78.454초에 해소됐고 HTTP acceptance
  test는 47.758초에 통과했다. provider init 35.173초가 가장 큰 하위 병목이었다.
- storage target의 초기 실행은 생성+수정 뒤에도 `BIND-STORAGE-001`로 안전 실패했다. 이후
  validator의 일반 계약인 container target=`applicationMountPath`를 생성 지침에도 명시했다.
  호스트 source는 다른 경로를 허용한다. 재실행은 65.256초에 Azure provider·binding을 통과했고
  HTTP acceptance test도 45.277초에 통과했다.
- 재실행된 VM delivery가 새 파일과 이전 infra 파일을 섞는 버그를 발견했다. 검증된 소유 파일
  집합을 staging에서 교체하고 실패 시 복원하도록 수정했으며 `.terraform` cache도 제거한다.
- 상위 requirements/design 실행은 세 사례 모두 0회다. 실제 cloud apply와 live HTTP는 하지 않았다.

## 2026-08-09 실제 cloud 증거 감사와 최소 실행 결정

- GCP backend service↔backend group 개입 세 반복은 control 기능 성공, 관계 제거 뒤 기능 실패,
  복원 뒤 기능 성공, cleanup verified와 residual 0을 이미 보존한다. LB·HTTPS 실제 기능을 다시
  실행하지 않는다.
- 3사 native replication은 벤더 리소스 관계 근거로만 사용하며 EasyDep 앱 종단 성공으로
  해석하지 않는다.
- 읽기 전용 CLI로 실험 접두사를 확인했다. AWS ap-northeast-2의 VM·volume·ELBv2, Azure resource
  group, GCP의 개입 관련 9종 리소스가 모두 0이었다.
- 새 실제 apply의 유일한 후보는 Azure 영속 저장 1셀이다. 현재 static·provider·앱 test는
  통과했지만 container image와 SSH/TLS 실행 변수가 아직 결합되지 않아 apply는 시작하지 않았다.
- 임시 registry를 범용 subsystem으로 만들지 않고, 한 이미지 digest와 비커밋 실행 변수를 준비한
  뒤 apply·ready·업무 기능·restart 영속성·destroy·residual을 분리 측정하는 범위만 허용한다.

## 2026-08-09 영속 볼륨 멱등성 및 후보 보존 점검

- 실제 apply 직전 기존 Azure P2 cloud-init을 다시 읽어 매 부팅마다 `mkfs.ext4 -F`를 실행하는 결함을
  발견했다. 리소스 생성과 attachment가 성공해도 VM 교체 시 앱 데이터를 지울 수 있으므로 실제 apply를
  중단했다.
- 특정 SQLite나 `/var/lib/notes`에 결합하지 않고, 계약된 영속 볼륨에서 명백한 무조건 `mkfs`를
  `BIND-STORAGE-DESTRUCTIVE-INIT`으로 진단하고 VM delivery에 귀속했다. 생성 지침에는 파일시스템이
  없을 때만 포맷하는 멱등 초기화 규칙을 추가했다.
- 같은 앱 스냅샷에서 VM delivery만 다시 실행한 첫 측정은 진단·provider validate·앱 테스트를 모두
  통과했다. VM delivery는 56.050초, 전체는 88.776초였고 상위 단계 실행은 0회였다.
- 성공 후보를 해시 경로에 보존하도록 하네스를 추가한 뒤의 두 실행은 각각 Terraform template 내부
  셸 변수 경계와 고정 provider 선언 누락으로 실패했다. provider gate가 승격을 차단했으며 후보는
  보존되지 않았다. 성공할 때까지 재시도하면 선택 편향이 생기므로 추가 LLM 호출을 중단했다.
- 로컬 Docker는 현재 config 접근 및 daemon 권한 문제로 이미지 생성·게시 입력으로 사용할 수 없었다.
  정확한 IaC 스냅샷과 불변 이미지 digest가 함께 준비되기 전에 자원 생성만 측정하지 않는다.

## 2026-08-09 실제 P2 앱·배포 경계 분리 확인

- Docker daemon은 권한을 받아 정상 사용 가능했고 기존 ACR 로그인도 확인했지만, 후보 검증 전에는
  registry에 이미지를 게시하지 않았다.
- 복구 전 P2 앱은 Docker build가 성공했어도 `org.sqlite.JDBC` 누락으로 컨테이너가 4초 안에 종료했다.
  MockMvc 통과가 패키징된 런타임 성공을 대신하지 못한다는 직접 증거다.
- `repairs/attempt-1`의 H2 file 복구 앱은 로컬에서 health·Notes POST/GET·컨테이너 재시작 후 레코드
  보존을 29.407초에 통과했다. 컨테이너와 볼륨은 즉시 제거했다.
- 이 실제 P2 앱에서 VM delivery만 66.891초 실행해 멱등 포맷과 provider validate를 통과한 해시 후보를
  보존했다. 하지만 사후 파일 감사에서 `lsblk | ... | head -n 1` 장치 선택을 발견했다.
- 순서가 보장되지 않은 첫 블록 장치 선택을 `BIND-STORAGE-DEVICE-AMBIGUOUS`로 차단하고 VM delivery에
  귀속했다. 안정적인 provider 장치 식별자와 attachment 가시성에 대한 제한적 대기를 생성 계약에
  추가했다. 후보는 apply하지 않았고 두 로컬 이미지 태그도 제거했다.
- 새 장치 계약 적용 후 실제 P2 VM delivery 후속 실행은 80.380초에 provider validate 실패로 끝났다.
  `templatefile` map의 `container_port`와 템플릿의 `application_port` 이름이 불일치했다. 추가 LLM
  재시도는 성공 표본 선택 편향을 피하기 위해 중단했고 cloud apply와 registry push는 수행하지 않았다.

## 2026-08-09 HA–노드 상태 충돌의 같은 실행 요구사항 수정 파일럿

- Java/Spring 앱 종류나 고정 데이터베이스에 의존하지 않도록 `/records`, 임의 상태 경로
  `/var/lib/app-state`, Azure 단일 개발 사례로 자연어 요구사항부터 실행했다. 초기 요구사항의
  노드 로컬 영속 상태와 영역 장애 허용 충돌은 `BIND-STATE-HA-001`로 탐지됐다.
- 사용자가 HA를 완화하는 요구사항 수정은 같은 외부 run에 기록됐지만, 내부 요구사항 그래프가 최초
  thread checkpoint를 재사용해 명시적인 단일 영역 요구에도 `multiZone=true`를 남겼다. 요구사항과
  설계 내부 session ID를 요구사항 revision별로 격리하고 회귀시험 64건을 통과시켰다.
- 기존 저장 상태를 수동 변조하지 않고 같은 run에서 더 명시적인 두 번째 정정을 제출했다. 새 revision의
  첫 호출과 실패 단계 재시도는 각각 2.850초, 2.735초 뒤 `APIConnectionError`로 끝났다. 즉 무제한
  재시도나 장시간 내부 루프가 아니었다.
- 직후 독립 단순 endpoint probe도 1.117초에 같은 `APIConnectionError`가 발생했고 HTTP 응답이나
  첫 이벤트는 없었다. 현재 run은 요구사항 실패 checkpoint에 보존했으며 endpoint 연결이 복구되기
  전에는 추가 호출하지 않는다.
- 이 파일럿은 cloud apply를 수행하지 않았고 실제 자원을 만들지 않았다. 따라서 현재 증거는 충돌 탐지,
  요구사항 revision 격리, 실패 단계 보존까지만 지지하며 최종 앱 기능 성공이나 Azure 배포 성공을
  주장하지 않는다.
- 정적 평가 경계 보완을 끝낸 뒤 endpoint 회복 여부를 단순 probe로 한 번 재확인했지만 1.335초에
  `APIConnectionError`가 다시 발생했다. HTTP 응답과 첫 이벤트는 없었으며 HA run 재시도는 수행하지
  않았다.
- 동일 probe를 승인된 외부 네트워크 경계에서 실행하자 연결 1.885초, TTFT 1.917초, 전체 2.156초에
  정상 완료됐다. 앞선 연결 오류를 endpoint 장애가 아니라 제한된 실행환경 egress로 재분류했다.
- 같은 run의 revision-2 요구사항 실패 단계부터 재개해 요구사항·설계·구현·테스트를 완료했다. 610초
  호출은 단발 LLM 지연이 아니었다. 요구사항과 설계의 다수 구조화 호출, VM delivery 55.394초,
  앱 테스트 57.922초가 누적됐으며 가장 긴 단일 LLM 사건은 SequenceModel 40.546초였다.
- provider init/validate, Docker build, HTTP 업무 기능, 컨테이너 재시작 영속성은 통과했고 생성한
  container·volume·image cleanup도 모두 통과했다. 그러나 Terraform에는 요구된 영속 디스크와
  `/var/lib/app-state` mount가 없었다. 의미 평가는 `persistentData=false`, `volumeMount=unknown`으로
  실패했으므로 이 run을 종단 성공으로 분류하지 않는다.
- 원인은 합성된 capability ID `node_filesystem_storage`가 고정 별칭 목록에 없어서 persistent 의미가
  계약 planner에서 탈락한 것이다. 특정 이름을 추가하지 않고 `applicationState.durability=persistent`
  의미로 판정하고, 명시적 storage intent 경로를 관측된 상위 경로보다 우선하도록 수정했다.
- 공통 evaluator도 provider와 앱 기능만 통과하면 semantic 실패가 있어도 `experimentEligible=true`가
  되던 문제를 고쳤다. 불변 artifact 재평가에서는 failed 1, unknown 1로 적격 `false`이며 Docker
  cleanup은 다시 모두 통과했다. 이 결과는 충돌 탐지와 배선 결함 발견 증거이지 성공률 표본이 아니다.

## 2026-08-09 cloud 후보와 로컬 자원 최종 점검

- Azure P2의 마지막 해시 후보는 장치 식별 불변식 위반으로 이미 제외됐고, 이후 실행은 template 변수
  불일치로 provider validate에서 차단됐다. 현재 `.easydep/research-candidates`에는 승격 후보가 없으며
  외부 VM이 pull할 불변 image digest도 없다. 성공 출력이 나올 때까지 LLM을 반복하지 않고 apply를
  시작하지 않았다.
- 읽기 전용 CLI에서 AWS 서울 리전의 `easydep` 태그 자원, Azure의 EasyDep 이름 resource group,
  GCP의 EasyDep 이름 project가 모두 0이었다. 이 조회는 실험 식별 규약 범위의 cleanup 확인이며
  계정 전체의 무관·미태그 자원 부재를 주장하지 않는다.
- EasyDep 이름의 로컬 Docker container, volume, image도 모두 0이었다. 남은 Python 프로세스 두 개는
  VS Code Black formatter 언어 서버로 확인되어 종료하지 않았다.
- `.easydep/provider-plugin-cache` 약 1.0GB는 고정 provider 재검증 시간을 줄이는 제한 캐시이고,
  `.easydep/models` 약 418MB와 checkpoint 약 97MB는 현재 개발 입력이므로 삭제하지 않았다.

## 2026-08-09 전체 완료 감사

- 전체 회귀시험 첫 실행에서 공통 모듈의 상위 계층 역참조, 교정된 component oracle의 suite hash,
  과거 neutral-layer v2 freeze와 활성 평가기 hash 차이 등 4건을 발견했다.
- stall probe 구현을 `requirements/common`으로 이동하고 기존 `app.core` 경로는 호환 wrapper로 남겨
  계층 역참조를 제거했다. component suite hash는 현재 oracle digest로 갱신했다.
- 이미 실행된 neutral-layer v2의 freeze manifest는 현재 평가기 hash로 덮어쓰지 않았다. 과거 동결
  실험과 활성 평가기가 달라졌음을 readiness가 실패로 검출하는 상태를 올바른 회귀 조건으로 확정했다.
- 최종 전체 suite는 수집 1,289개 중 1,240개 통과, 환경 조건이 명시된 49개 skip, subtest 6개
  통과였다. pytest의 timeout marker와 TypedDict 오수집 경고도 정리했다.
- 구현 완료와 후속 논문 평가를 분리한 `completion-audit-20260809.md`를 작성했다. 실제 Azure P2
  종단 성공, 반복 통계, 멀티 에이전트 단독 인과효과는 완료 주장에 포함하지 않는다.
## 2026-08-09 멤버 OpenHands 구현 workflow 오케스트레이터 연결

- 비교실험이 임시 `implementation_scaffold=llm`을 강제하던 설정을 제거하고 기본 멤버
  provider를 사용하도록 바꿨다. 멤버 소스는 수정하지 않았으며 오케스트레이터 worker가
  공개된 생성·계획·`run_workflow_to_completion` 경계를 호출한다.
- `--approve-member-implementation`을 명시한 실험만 현재 run의 외부 전송을 일괄 승인한다.
  승인이 없으면 `MEMBER-APPROVAL-REQUIRED`로 멈추고 임시 LLM으로 우회하지 않는다.
- 멤버 workflow가 `COMPLETE`이면 임시 acceptance/logic LLM은 각각 0회 호출된다. 구현된
  planner를 모두 수행한 뒤 `NEEDS_PLANNER`인 경우에만 기존 임시 provider가 남은 앱 공백을
  보완한다. `FAILED`와 `NEEDS_INPUT`은 그대로 중단한다.
- 메인 오케스트레이터의 `API_KEY`는 worker 프로세스 안에서만 `LLM_API_KEY`로 연결한다.
  키 값은 로그와 산출물에 기록하지 않는다. `.env`를 읽은 호환성 점검에서 Python, OpenHands
  SDK, tools와 API key 네 조건이 모두 true였다.
- 멤버가 앱 구현과 내부 단위·통합 검증을 소유하고, Docker와 VM Terraform은 기존 전용
  VM delivery 단계가 계속 소유한다. 최종 builtin testing과 공통 외부 평가기는 멤버 내부
  검증을 대체하지 않고 합성 결과를 독립적으로 재검증한다.

### 실제 P1-AWS 연결 스모크

- 새 P1-AWS 개발 run에서 requirements 201.169초 뒤 최초 design 실패가 발생했고, 고정 복구
  예산 1회로 같은 run의 design만 재실행해 205.286초에 통과했다. requirements는 재실행하지
  않았다. 이어 `implementation.scaffold`에서 실제 멤버 worker가 호출됐다.
- 첫 멤버 preflight는 고정 OpenAPI Generator 7.24.0 JAR 부재를 보고했다. 저장소의 공식
  `bootstrap-implementation-tools.ps1`을 실행해 명시된 SHA-256을 검증한 JAR, lockfile 기반
  npm 도구와 Gradle 8.14.2를 준비했다.
- 같은 run의 implementation 체크포인트만 재개하자 멤버 생성기는 Docker Desktop 비실행으로
  중단됐다. 멤버 구현은 pinned JAR를 요구하지만 실제 BCE/OpenAPI 생성 명령은 각각
  `node:20`과 태그가 고정되지 않은 `openapitools/openapi-generator-cli` Docker 이미지를
  사용한다. 이는 현재 오케스트레이터 연결 문제가 아니라 멤버 도구 실행 경계의 불일치다.
- Docker를 켜서 mutable 이미지를 받아 성공시키거나 오케스트레이터에 명령 shim을 추가하지
  않았다. 전자는 재현 가능한 연구 실행이 아니고 후자는 멤버 구현을 우회하는 즉발성 패치이기
  때문이다. 실제 OpenHands task 실행 증거는 이 도구 경계가 멤버 쪽에서 고정된 뒤 확보해야
  한다.
