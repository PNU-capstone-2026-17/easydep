# 근거 우선 클라우드 리소스 및 에이전트 연구

현재 구현 상태는 [현재 시스템 상태](../../docs/current-system-status.md), 완료된 최신 측정은 [2026-08-10 연구 결과](../../docs/research-results-20260810.md)를 따른다. 이 문서는 방법과 실행 계약을 설명하며 최신 결과 수치를 중복 기록하지 않는다. 완료된 계획과 파일럿 문서는 [보관소](archive/README.md)에 둔다.

## 코드와 산출물 경계

- `core/`: 경로, JSON·subprocess, 스냅숏, provider 도구 등 여러 실행기가 공유하는 코드
- `commands/`: `build_*`, `run_*`, `measure_*`, `evaluate_*`, `analyze_*` 직접 실행 진입점
- `definitions/`: 실행 전에 확정되는 근거 모델·실험 정의 JSON
- `measurements/`: 날짜별 원시 관측값과 파일럿 결과 JSON
- `reports/`: 현재 결과 해석·타당성 감사·실행 지침 문서
- `protocols/`: 현재 실행기가 읽는 사례·설정 JSON
- `archive/`: 완료된 계획과 현재 실행 경로에서 제외한 일회성 파일럿

실행기끼리 다른 실행기의 밑줄(`_`) 함수에 의존하지 않는다. 재사용할 로직은 위 공용 모듈의 공개 함수로 올리고, 사례별 경로·리소스·oracle은 JSON 입력에서만 받는다.

이 디렉터리는 확인적 연구의 경계다. 과거 DepKB 주장, Native v1 인벤토리,
`neutral-layer` v1·v2·v3 탐색 결과와 `evaluation/dependency_audit` 증거 카드는 이 프로토콜로
재측정되기 전까지 탐색 자료로만 취급한다. 완료된 `neutral-layer` 탐색 코드는 작업 트리에
중복 보관하지 않으며 Git 커밋 `b4a2b4d`에서 확인할 수 있다.

연구의 중심은 멀티 에이전트 개발 지원에 둔다. 전체 노력의 30%는 리소스 모델의
기반 확립에, 70%는 요구사항에서 IaC까지의 멀티 에이전트 생성·검증 평가에 배분한다.

앱 구현과 클라우드 환경의 교차 불일치는
[애플리케이션–클라우드 계약 설계](../../docs/app-cloud-contract-design.md)에 따라 앱 실행,
클라우드 능력, 배포 결합의 세 계약으로 분리한다. 클라우드 리소스 게이트와 실제 앱 기능
게이트는 서로 대체하지 않는다.

## 독립 API 추적성 경계

여러 에이전트가 같은 중간 OpenAPI를 공유하면 잘못된 계약에도 서로 일관되게 맞출 수 있다.
따라서 요구사항 문장에 `request`, `payload`, `response`와 함께 명시된 `field(s)` 목록은
결정적으로 추출해 OpenAPI request/response 스키마와 별도로 대조한다. 이 검사는 암시된 필드명,
동의어 또는 도메인 관습을 추론하지 않는다. 명시 필드가 누락되면 cloud enrichment 단계가
실패하고 구현으로 진행하지 않는다.

필드 목록은 자유 문장 전체를 정규식으로 수집하지 않고 식별자가 쉼표와 `and`로 연결된
구문만 소비한다. `when`, `supporting`, `using` 등 후속 기능 설명은 필드로 취급하지 않는다.
방향은 `accept`/`request`와 `return`/`response`처럼 비대칭 의미가 분명한 표지만 사용하며,
요청과 응답 모두에 쓰일 수 있는 `payload` 자체는 방향 근거로 사용하지 않는다.

이 게이트는 최종 사례 oracle을 읽지 않으며 개발·holdout 모두 같은 알고리즘을 사용한다.
최종 컨테이너 기능 oracle도 그대로 유지한다. 전자는 요구사항에서 설계로 전달되는 계약의
상관 오류를 조기에 막고, 후자는 실제 실행 응답을 독립적으로 판정한다.

명시 필드가 누락되면 구조화 API 모델과 게이트 진단을 API 설계 에이전트에 한 번만 반환한다.
수정된 구조화 모델에서 OpenAPI를 결정적으로 다시 렌더하고 같은 추적성 게이트를 재실행한다.
두 번째에도 누락되면 구현으로 진행하지 않는다. 수정된 `design_result` 전체와 LLM 호출 수를
하류 단계·실험 기록에 전달하며, 최종 기능 oracle은 수정 프롬프트에 노출하지 않는다.

## IaC 공급자 검증 피드백 경계

IaC 에이전트의 HCL 구문 검사만으로는 현재 공급자 플러그인이 지원하지 않는 리소스·데이터
소스를 검출할 수 없다. EasyDep 실험군은 생성 파일을 승격하기 전에 격리 임시 디렉터리에서
OpenTofu 또는 Terraform의 `init -backend=false`와 `validate`를 실행한다. 검증 실패 시 실제
도구 진단과 최초 입력·전체 생성 파일을 같은 IaC 에이전트에 한 번만 돌려주고, 수정된 전체
파일 묶음을 HCL 및 공급자 스키마로 다시 검증한다. 두 번째 검증도 실패하면 구현 단계를
실패 처리하며 부분 파일은 애플리케이션 작업공간에 승격하지 않는다.

같은 1회 제한은 HCL 파싱 및 중복 resource/data/variable/output/module 선언에도 적용한다.
HCL 사전검사와 공급자 스키마 검사는 하나의 수정 예산을 공유하므로 단계마다 반복 수정하거나
통과할 때까지 재시도하지 않는다.

이 내부 피드백은 멀티 에이전트 시스템의 개입으로 기록하고 LLM 호출 수에 포함한다. 성능
평가에서는 별도의 외부 평가기가 같은 IaC를 다시 검증한다. 따라서 내부 검증 통과를 최종
정답으로 간주하지 않으며, 특정 벤더 리소스명을 치환하는 사후 정제 규칙도 사용하지 않는다.

## 실행 검열과 산출물 실패의 분리

명시적인 LLM 요청 시간 초과(`Request timed out`)는 산출물의 구문·기능·IaC 실패와 구분해
`executionStatus=censored`, `censorReason=llmResponseCompletionTimeout`으로 기록한다.
비스트리밍 호출의 완료 timeout은 TTFT나 엔드포인트 전체 장애를 증명하지 않는다. 검열 건은 예정
실행 수와 검열률에는 포함하지만 시스템 실패 수와 성능 분모에서는 제외한다. 반면 생성된
코드의 컴파일 timeout, 테스트 timeout, 내부 검증 실패는 시스템 산출물 결과이므로 검열로
바꾸지 않는다. 저장된 개발 인덱스도 재실행 없이 같은 결정 규칙으로 보정할 수 있다.

설계 구조화 출력은 스트리밍으로 수신하되 최종 결과는 기존과 같은 Pydantic 스키마로
검증한다. `responseEstablishedSeconds`, `firstEventSeconds`, `ttftSeconds`,
`firstContentSeconds`, `maxInterEventSeconds`, 전체 `elapsedSeconds`를 구분해 기록한다.
TTFT가 짧아도 최종 JSON이 완성되지 않으면 응답 완료 실패이며, 반대로 비스트리밍 완료
시간만 있는 과거 결과에서 TTFT를 추정하지 않는다.

개발 실행에서는 주 LLM 호출이 120초를 넘길 때 짧은 스트리밍 probe를 한 번 병렬 실행한다.
probe가 HTTP 429를 반환한 경우에만 직접적인 속도제한 신호로 기록한다. probe가 빠르게
완료되면 엔드포인트 전체 장애가 아닌 요청별 완료 지연의 근거로 사용한다. probe 실패가
timeout이나 전송 오류인 경우에는 속도제한으로 단정하지 않는다. 추가 호출이 결과에 영향을 줄
수 있으므로 홀드아웃과 확증 실행에서는 이 진단을 사용하지 않는다.

## 단계·하위 작업 시간 계측

모든 오케스트레이션 `StepResult.metrics.timing`은 UTC `startedAt`, `finishedAt`과 단조시계
기반 `elapsedSeconds`를 기록한다. 설계 단계는 여기에 구조화 응답 스키마별 LLM 이벤트를
호출 순서대로 추가하며, 각 이벤트에는 성공/실패, 오류 유형, 클라이언트·벽시계 제한을
포함한다. 따라서 `design.architecture` 총시간 안에서 클래스, 시퀀스, API, 배포 모델 중
어디가 병목인지 분리할 수 있다.

IaC 전달 단계는 최초 생성, HCL 사전검사, 제한된 수정 호출을 각각 이벤트로 남긴다.
OpenTofu/Terraform의 `init`과 `validate` 보고서에도 같은 UTC 시각과 소요시간을 기록한다.
외부 평가의 생성·평가·전체 시간은 manifest의 기존 필드로 유지한다. 분석에서는 총시간과
하위 이벤트 합의 차이를 오케스트레이션·직렬화·파일 I/O 오버헤드로 별도 보고하며, 서로
다른 시계를 단순 합산해 CPU 시간이라고 해석하지 않는다.

## 동결 순서

1. 개발용 요구사항 말뭉치와 하위 소비자에서 capability를 도출한다.
2. 사전 정의한 리소스 유형 없이 Native 원시 관측을 수집한다. P1~P3 문장에서
   추적한 결정 앵커는 스키마 탐색의 시작점일 뿐 최소 리소스 목록이나 정답이 아니다.
   결정 앵커가 속한 공식 서비스 모델·계열의 전체 연산을 모집단으로 유지하고,
   앵커 밖은 CSP·서비스 계열·관측
   채널별 20%(최소 5개, 최대 20개) 고정 난수 표본으로 누락을 검사한다.
3. 공식 근거 판정 규칙으로 경계·관계를 자동 판정하고, 충돌하는 고영향 예외만 사람이
   제시된 원문을 대조한다. 빈 전수 검토 양식은 감사 자료이지 동결 필수조건이 아니다.
4. 개발 세트 정답만 사용해 capability 신뢰도 정책을 보정하고 동결한다.
5. CSP/API 버전, 가설, 사례, oracle, 코드, 비용 정책과 해시를 동결한다.
6. 서로 독립적인 세 세션에서 확인 실험을 실행한다. 5단계까지 holdout 정답은 숨긴다.

공식 수명주기 근거로 경계 모델을 재생성하려면 다음을 실행한다.

```powershell
python -m evaluation.research_protocol.commands.build_evidence_models
```

동결된 공급자 근거 모델에서 실제 에이전트가 읽는 의존관계 뷰를 재생성하려면 다음을 실행한다. 이 산출물에는 과거 `claims.json`의 탐색적 주장이 들어가지 않는다.

```powershell
python -m evaluation.research_protocol.commands.build_runtime_dependencies
```

Capability 판정 정책은 두 검토자의 개발 세트 JSONL을 준비한 뒤 다음 명령으로 만든다.
입력에 `split=holdout` 또는 `origin=explicit`인 항목이 하나라도 있으면 명령이 실패한다.
명시된 제약은 원문 근거 게이트로 판정하므로 추론 capability의 임계값 보정에 섞지 않는다.

개발 캠페인에 inferred 제안이 0개라면 수치를 추정하지 않고 다음 명령으로 모든 추론을
질문으로 보내는 정책을 동결한다. 이후 개발 캠페인에 inferred 제안이 생기면 이 명령은
실패하며 반드시 두 검토자의 라벨로 다시 보정해야 한다.

```powershell
python -m evaluation.calibrate_capabilities `
  app/requirements/knowledge/capability-threshold.json `
  --no-inferred-proposals evaluation/research_protocol/definitions/capability-proposals.json `
  --version development-no-inference-v1
```

개발 입력만 사용한 독립 검토 패킷은 다음 명령으로 생성한다. `--reviewers`는 판정이 비어
있는 두 양식만 만들며 사람의 판단을 대신하지 않는다.

```powershell
python -m evaluation.research_protocol.commands.capability_packet `
  evaluation/research_protocol/definitions/capability-proposals.json `
  --reviewers reviewer-a reviewer-b
```

```powershell
python -m evaluation.calibrate_capabilities labels.jsonl `
  app/requirements/knowledge/capability-threshold.json --version development-v1
```

45분 측정 창은 첫 변경 API 요청부터 시작한다. 정리에는 별도의 60분 안전 창을
부여한다. 검열된 관측을 대상 시스템의 실패로 바꾸지 않는다. Holdout에서 예상하지
못한 요소가 나오면 먼저 동결 모델의 coverage 실패로 기록하고, 이후 적응 실행에서
fallback을 평가하더라도 1차 결과를 수정하지 않는다.

## 근거 승인 기준

CSP 사실은 버전과 위치가 고정된 공식 출처로 우선 승인한다. 공식 문서가 필수성을
명시하지 않을 때만 사전등록한 제거–실패–복구 실험의 3회 일관된 결과를 사용한다.
서로 충돌하는 고영향 근거만 사람이 조건과 API 버전의 동일성을 검토한다. 배포 정책은 사실과 별도 산출물로 관리한다.
Terraform과 중립 모델은 교차 검증에 사용하지만 CSP 필수 동작의 단독 근거로 삼지 않는다.

확인적 실행 전에는 다음 읽기 전용 점검이 `ready=true`를 반환해야 한다. 세 CSP의
공식 근거로 동결된 Native v2 모델, 동결된 결정 앵커, 보정된 capability 정책, 완결된
provider projection, 필요한 의존성 개입 결과가 하나라도 없거나 해시가 달라진 상태를
조용히 통과시키지 않는다. 대용량 공식 원본은
작업공간에 보존하지 않고, 동결 모델 안의 출처 버전·내용 해시로 다시 가져올 수 있게 한다.

중립 모델의 후보는 세 CSP 공식 수명주기·매뉴얼과 대조한 뒤에만 경계와 의존성 판정에
사용한다. 원문 URL뿐 아니라 API 버전, 조회일, 문서 절, 한국어 판독문을 묶은 지문을
동결하며, 공식 문서가 필수라고 명시하지 않은 참조 관계는 `candidate`로 남긴다.

의존성 개입 실험은 `dependency-experiment-plan.json`을 따른다. 제어면 생성 성공과
VM·컨테이너 기동, `/readyz`, 대표 업무 요청을 별도 관측하므로 “리소스는 만들어졌지만
앱은 작동하지 않는” 결과를 배포 성공으로 잘못 집계하지 않는다.
실제 GCP 반복 절차와 결과 위치는 `reports/GCP-기능-개입-실행서.md`에 고정되어 있다.

```powershell
python -m evaluation.research_protocol.commands.readiness
```
