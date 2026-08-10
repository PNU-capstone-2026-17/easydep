# 구성요소 비교 파일럿 기록 (2026-08-09)

## 목적과 범위

영속 스토리지의 AWS control/treatment 한 쌍을 `easydep-full`과
`easydep-no-depkb`에서 각 1회 실행했다. 이는 확인 실험이 아니라 처치 분리, 체크포인트,
측정 경계를 점검하는 개발 파일럿이다. Docker, Terraform 검증, 실제 클라우드 생성은
실행하지 않았다.

## 실행 결과

네 셀 모두 생성 단계에서 실패하여 쌍별 효과는 계산하지 않았다.

| 조건 | 마지막 단계 | 관찰 결과 |
|---|---|---|
| no-depkb control | implementation.scaffold | `APP-DB-003` |
| full treatment | design.architecture | 출력 상한에서 Sequence JSON 미완성 |
| full control | implementation.scaffold | `APP-DB-002` |
| no-depkb treatment | implementation.scaffold | `APP-DB-003` |

따라서 이 실행으로 DepKB 효과, 구조적 참조 회수율 또는 기능 성공률을 주장할 수 없다.
실험 집계기도 네 관찰을 모두 제외하고 완전한 쌍을 0개로 기록했다.

## 구조화 LLM 지연 관찰

공통 스트림 계측을 보완하여 응답 본문을 저장하지 않고 첫·마지막 이벤트 시각,
이벤트 수, reasoning/content 문자 수와 종료 이유 유무를 기록했다. 재현된
`BCEExtractionResult` timeout은 첫 이벤트 2.31초, 마지막 이벤트 330.00초,
최대 이벤트 간격 1.12초, 52,850개 이벤트, content 358,982자였으며
`finish_reason`은 없었다. 동시에 실행한 endpoint probe는 TTFT 0.87초이고 429가
아니었다. 이 관찰은 속도제한이나 스트림 정지가 아니라 종료 신호 없이 출력이 계속된
호출임을 지지한다.

개발 재개에서는 모든 구조화 호출에 기존 지원 상한 8,192 토큰을 적용했다. 비정상 장기
출력은 막았지만 한 Sequence 응답이 잘린 JSON으로 종료됐다. 따라서 이 값은 확인 실험의
동결 설정으로 채택하지 않고, 출력 크기 분포와 스키마별 유효 완료율을 보고 정해야 한다.

## 체크포인트 복구 관찰

requirements와 design 실패는 해당 단계에서 재개됐다. 반면 `APP-DB-002/003` 구현
실패를 일반 checkpoint retry로 반복하면 repair owner가 지정되지 않아 같은 진단이
재현됐다. 명시적으로 `implementation.logic` 수리를 요청한 실행도 LLM 호출 전에
기존 workspace 충돌로 실패했다.

현재 구현에서는 scaffold가 파일을 만든 뒤 일관성 검증에서 실패할 수 있다. 그러나
logic부터 무효화할 때 완료된 이전 단계의 output만 보존하므로, 실패한 scaffold가 남긴
`run_root`는 사라지고 기존 workspace만 남는다. 또한 scaffold 프롬프트는
`repair_feedback`을 소비하지 않는다. 이는 SQLite 전용 문제가 아니라 수정 소유 작업과
실패 보고 작업이 다를 때 발생하는 단계 계약 문제다.

## 다음 결정

사례별 dialect 예외를 추가하지 않는다. 먼저 다음 두 대안을 회귀 사례로 비교한다.

1. scaffold는 생성만 담당하고 앱 일관성 판정과 수리는 logic 이후에 수행한다.
2. 검증 실패한 scaffold의 부분 산출물을 명시적인 검증 완료 전 체크포인트로 표현하고,
   logic 수리 단계가 그 산출물을 안전하게 인계받는다.

선택 기준은 상류 단계 재실행 회피, 실패 산출물 혼합 방지, repair feedback의 실제 소비,
최종 앱 기능 성공이다. 이 단계 계약이 정리되기 전에는 나머지 capability 매트릭스를
확대하지 않는다.

## 부분 산출물 인계 보완 결과

진단이 지정한 수리 작업이 실패 보고 작업보다 뒤에 있을 때, 다음 조건을 모두 만족하는
부분 산출물만 수리 단계로 인계하도록 보완했다.

- `run_root`가 해당 run의 표준 orchestration workspace와 정확히 일치한다.
- workspace 안에 application 디렉터리가 실제로 존재한다.
- 실패 진단이 기존 repair routing에 등록되어 있다.
- 같은 단계의 과거 결과가 여러 개이면 최신 결과 하나만 사용한다.

인계 사실과 원래 진단 코드는 `partialOutputHandoffs`와 단계의 `repairHandoff` metrics에
남긴다. 인계는 최종 성공을 의미하지 않으며, 수리 작업 이후의 일관성 검증과 테스트를
다시 통과해야 한다.

기존 no-depkb control의 `APP-DB-003` 체크포인트에 적용한 결과 scaffold를 다시 호출하지
않고 `implementation.logic`이 진단 피드백을 받아 실행됐으며, 기존 앱 계약 실패 지점을
통과해 `implementation.vm_delivery`까지 진행했다. 이후 Terraform의 AWS provider 선언
계약 실패에서 멈췄다. 따라서 앱 계약 수리와 클라우드 전달 실패를 서로 다른 단계 결과로
분리할 수 있게 됐다. 오케스트레이션 회귀시험 67건과 실험 실행기 시험 38건이 통과했다.

## 고정 provider 계약 보완 결과

검증기는 provider source와 버전을 정확히 요구했지만 기존 생성 입력은 source만 전달했다.
고정 provider 버전은 LLM이 선택하는 토폴로지가 아니라 실험 실행 환경의 재현성 정책이므로,
provider 선언이 없는 경우 시스템이 `easydep-provider.tf`를 결정론적으로 추가하도록
보완했다. 기존 선언이 잘못된 경우에는 덮어쓰지 않고 검증 실패를 유지하며, 시스템 관리
파일명을 생성기가 사용한 경우도 거부한다. AWS·Azure·GCP가 같은 고정 계약 테이블과
경로를 사용한다.

기존 no-depkb control에서 VM delivery만 재실행한 결과 LLM 생성 15.46초, HCL preflight
0.014초, OpenTofu provider init 52.91초, validate 9.83초가 걸렸다. 추가 LLM 수리 없이
고정 AWS 5.100.0 cache 정책, lock selection, provider validate와 앱–IaC binding 검증을
통과했다. 이후 실행은 testing 단계의 `APP-DEP-001` 컴파일 실패에서 멈췄으므로 provider
계약 실패와 애플리케이션 구현 실패가 분리됐다. provider 관련 회귀시험 53건이 통과했다.

이 관찰에서 VM delivery의 가장 큰 로컬 병목은 LLM 호출이 아니라 고정 cache를 사용하는
OpenTofu init 52.91초였다. 이를 실제 클라우드 지연이나 provider 다운로드 시간으로
해석하지 않는다.

## 애플리케이션 컴파일 부분 복구 결과

provider 계약 보완 후 실행은 testing에서 Hibernate API와 맞지 않는 사용자 정의
`SQLiteDialect`의 컴파일 오류로 실패했다. testing이 보존한 `APP-DEP-001`과 제한된
stderr 증거를 기존 repair routing에 따라 `implementation.logic`에 전달했다. requirements,
design, scaffold는 재실행하지 않고 logic, VM selection, VM delivery와 testing만 다시
수행했으며 269.2초 뒤 같은 run이 완료됐다.

완료 attempt snapshot을 대상으로 한 정적 평가에서는 애플리케이션 소스, 테스트, 빌드,
Dockerfile과 IaC 구조가 존재했고 AWS VM·보안 그룹·VPC 참조가 관측됐다. control 요구와
같이 별도 영속 디스크, 로드밸런서와 HTTPS 구성은 생성되지 않았다. Docker 및 외부 도구를
제외한 개발 평가이므로 이 결과는 `experimentEligible=false`이며, paired 효과 계산에는
사용하지 않는다. 이 실행은 부분 복구 경로의 종단 동작 증거로만 사용한다.

## 자원 안전

실험 run 산출물은 전체 약 84MB였다. 디스크 안전 게이트가 실행을 중단했을 때 별도로
남아 있던 8월 7일 이전 Terraform provider 임시파일 68개(6,051,827,386바이트)를
삭제해 여유 공간을 약 8.49GB로 회복했다. Docker VHDX와 Gradle 캐시는 변경하지 않았다.

## 신규 4셀 개발 파일럿

부분 복구 과정에서 얻은 산출물을 재사용하지 않고, AWS 영속 스토리지의 control/treatment를
`easydep-full`과 `easydep-no-depkb`에서 각각 한 번씩 새로 실행했다. 네 셀은 모두 생성
단계에서 실패했고 `experimentEligible=false`였으므로 DepKB 효과나 paired contrast는
계산하지 않는다.

| 조건 | 실행 시간 | 마지막 단계와 제외 사유 |
|---|---:|---|
| no-depkb control | 587.282초 | IaC 수리 뒤 `aws_ami` data 선언 없이 참조가 남아 provider schema 검증 실패 |
| full treatment | 359.570초 | 설계 Sequence JSON이 8,192 completion-token 한도에서 잘려 검증 실패 |
| full control | 354.496초 | SQLite/JPA 사용에 필요한 지원 Hibernate dialect가 없어 구현 검증 실패 |
| no-depkb treatment | 482.838초 | SQLite/JPA 사용에 필요한 지원 Hibernate dialect가 없어 구현 검증 실패 |

full-treatment 설계 호출은 `finish_reason=length`, content 10,304자, reasoning 12,834자,
8,166개 스트림 이벤트를 기록했다. 첫 이벤트는 0.460초, 첫 content는 18.837초에 왔고
최대 이벤트 간격은 0.268초였으므로 엔드포인트 정지나 속도제한이 아니라 completion 예산
소진으로 분류한다. 저장된 오류 발췌에는 `UC1` 반복과 문자열 중간 EOF가 있어 응답은 유효한
완성 JSON이 아니다. 다만 더 큰 한도에서 정상 완료될지 반복 출력만 늘어날지는 아직 알 수
없으므로, 이 실행 결과를 변경하지 않은 채 실패한 설계 체크포인트만 16,384 토큰으로 한 번
진단 재생해야 한다.

나머지 세 실패도 사례별 예외를 추가해 우회하지 않는다. 두 SQLite/JPA 실패는 동일한 일반
앱-클라우드 계약 검증기가 서로 다른 arm에서 일관되게 탐지한 결과이고, IaC 실패는 선언과
참조의 구조적 무결성 검증 결과다. 후속 판단은 단일 진단 재생과 기존 repair routing으로 해당
소유 작업만 수정할 수 있는지에 근거한다.

## 근본 원인 분석에 따른 실행 경계 보완

기존 component 셀은 최초 검증 실패에서 끝나므로 복구형 EasyDep의 종단 성공이 아니라
`first-pass success`만 측정했다. 실험 러너에 셀별 고정 복구 예산을 추가했다. 복구는 새 run을
만들지 않고 동일 run의 진단 소유 작업과 그 하류만 재실행하며, 실행 전에 정한 횟수를 다 쓰면
성공 여부와 관계없이 중단한다. 결과에는 `initialGenerationStatus`, `checkpointRepairBudget`,
`checkpointRepairsUsed`, `recoveredGeneration`을 분리해 성공 샘플 선택을 막는다. 기본 예산은
0이므로 과거 실험 의미는 바뀌지 않는다.

구조화 LLM 실패의 토큰 상한 적절성은 원문 전체를 저장하지 않고 평가한다. 실험 세션에서
`LLM_FAILURE_RESPONSE_SAMPLE_CHARS`를 명시한 경우에만 검증 실패 content의 앞·뒤 표본을 각각
최대 4,096자와 전체 SHA-256 지문으로 기록한다. reasoning, prompt, 정상 응답 원문은 기록하지
않는다. 기존 이벤트 수·content/reasoning 문자 수·종료 이유와 이 표본을 함께 사용해 정상 장문
절단, 반복 붕괴, 단순 JSON 문법 오류를 구분한다.

16,384 토큰 진단 재생에서도 SequenceModel은 107.657초 뒤 `finish_reason=length`로
종료했다. content는 23,544자, reasoning은 15,144자였고 응답 끝 표본은 `"UC1"`만
반복했다. 따라서 8,192 한도에서 유효한 장문 응답이 잘렸다는 가설은 기각하고, 의미가 같은
추적 참조를 무한히 추가하는 생성 붕괴로 판정한다. 토큰 한도를 더 높이지 않는다.

`use_case_ids`는 동일 ID를 여러 번 넣어도 의미가 늘지 않는 집합형 추적 참조다. 이 의미를
런타임 검증으로 표현하고, 프롬프트에도 각 ID를 한 번만 포함하도록 명시했다. JSON Schema의
표준 `uniqueItems`도 시험했지만 현재 엔드포인트 문법 구현이 이 키를 지원하지 않아 400으로
거부했으므로 사용하지 않는다. 이는 `UC1`이나 PS 사례를 답으로 하드코딩한 것이 아니라 모든
시퀀스 메시지 추적 참조에 적용되는 불변식이며, 엔드포인트가 지원하는 스키마 부분집합도 함께
보존한 결정이다.

지원되는 스키마 부분집합으로 되돌린 뒤 동일 체크포인트를 8,192 토큰에서 재생하자 두
SequenceModel 호출은 각각 17.904초와 23.825초, `finish_reason=stop`으로 완료했다. content는
각각 2,259자와 7,284자였고 설계 단계 전체가 통과했다. 따라서 현 입력의 적정 상한은 최소한
8,192로 충분하며, 상한 증설 대신 집합형 참조의 의미를 명시하는 것이 실제 원인에 대응했다.

같은 run은 scaffold, acceptance-tests 생성, logic, VM selection, VM delivery까지 이어졌고
마지막 애플리케이션 컴파일에서 `GlobalExceptionHandler`의 잘못된 `@Override`로 실패했다.
현재 testing은 이를 `APPLICATION_TESTS_FAILED`라는 일반 진단으로만 남겨 생성 파일의 소유
하위 작업을 자동 식별하지 못한다. 이 상태에서 복구 횟수만 늘리면 testing만 반복할 수 있으므로
수동으로 logic을 지정해 성공 사례를 만드는 것은 중단한다. 다음 보완은 오류 문자열별 예외가
아니라 컴파일 실패 파일을 구현 작업의 산출물 소유권 기록과 연결해 repair owner를 정하는
일반 경로여야 한다.

## 구현 에이전트와 외부 테스트 경계

현재 component·종단 실험의 EasyDep arm은 `implementation_scaffold=llm`을 명시하므로
OpenHands 기반 멤버 구현 workflow를 실행하지 않는다. 관측된 Java 컴파일 실패는 임시 LLM
scaffold와 후속 LLM logic을 합친 애플리케이션을 독립 builtin testing이 처음 컴파일한
결과다. 이를 OpenHands 구현 에이전트의 단위·통합 테스트 실패로 해석하지 않는다.

OpenHands 내부 검증과 오케스트레이터의 최종 testing은 목적이 다르다. 전자는 소유 작업을
수행하면서 수정하는 내부 루프이고, 후자는 여러 구현 산출물이 합쳐진 뒤 독립적으로 확인하는
최종 gate다. 다만 현재 `MemberScaffoldProvider` 연결은 멤버 workflow를 계획만 하고
`verification.compile=false`로 두며, OpenHands 작업을 끝까지 실행하지 않는다. 따라서 임시
LLM 경로의 성공을 의도한 멀티에이전트 구현 경로의 성공으로 대체해 주장할 수 없다.

외부 testing의 컴파일 실패 라우팅은 오류 문구별 답안을 쓰지 않는다. 컴파일러가 보고한
저장소 내부 파일을 구현 결과의 `scaffold_files`, `acceptance_tests`, `files`와 대조하고 최신
작성자 우선으로 소유 하위 작업을 정한다. 일치하지 않으면 일반 실패를 유지해 추측하지 않는다.
이 경로는 독립 최종 gate에서 발견한 결함을 돌려보내는 기능이며 OpenHands 내부 검증을
대체하지 않는다.

PS 쌍에서 SQLite·JPA 생성 실패가 cloud component 효과를 차단한 것은 별도 실험 경계 문제다.
따라서 현재의 종단 셀은 시스템 통합 벤치마크로만 유지한다. DepKB의 구성요소 효과를 추정할
때는 기능 검증을 이미 통과한 동일 애플리케이션 스냅샷을 두 arm에 공유하고 클라우드 계획과
IaC 생성 이후만 변화시키는 실행을 사용해야 한다. 이 분리는 SQLite를 다른 특정 저장 기술로
바꾸는 패치가 아니라 앱 생성 변동성을 처치 밖으로 이동하는 방법론적 통제다.
