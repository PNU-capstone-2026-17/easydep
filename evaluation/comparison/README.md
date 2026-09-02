# EasyDep · MetaGPT · ChatDev 비교 테스트 자동화

이 도구는 세 프레임워크에 같은 요구사항과 검증 조건을 적용하고, 실행부터 JSON/Markdown
보고서 생성까지 반복합니다. 하나의 가중 합산값을 만들지 않습니다. 요구사항, 제약조건,
검증 게이트, 추적성을 각각 `충족 개수 / 전체 개수`로 보여주고 토큰·시간·LLM 호출 수는
별도의 원시 수치로 남깁니다.

## 가장 빠른 확인

저장소 루트에서 다음 명령을 실행합니다. 이 예제는 외부 API를 호출하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation.comparison validate evaluation/comparison/examples/smoke-manifest.json
.\.venv\Scripts\python.exe -X utf8 -m evaluation.comparison run evaluation/comparison/examples/smoke-manifest.json
```

결과는 다음 위치에 생깁니다.

- `artifacts/comparison/comparison-smoke-test/comparison.md`: 사람이 읽는 비교표
- `artifacts/comparison/comparison-smoke-test/comparison.json`: 분석·시각화용 전체 데이터
- `<실험>/<대상>/run-NNN/`: 실행별 stdout, stderr, 판정 JSON

예제는 의도적으로 필수 파일 하나를 만들지 않으므로 구현 요구사항이 `1/2 (50.0%)`로
표시됩니다. 실패나 시간 초과도 반복 실행의 전체 개수에서 빠지지 않습니다.

## 무엇을 어떻게 계산하는가

| 결과 | 분자 | 분모 | 판정 기준 |
|---|---|---|---|
| 구현 요구사항 | 연결된 게이트가 모두 통과한 요구사항 수 | 전체 필수 요구사항 수 | 게이트가 없으면 구현으로 간주하지 않음 |
| 충족 제약조건 | 연결된 게이트가 모두 통과한 제약조건 수 | 전체 제약조건 수 | 리전·예산·금지 서비스 등을 독립 검증 |
| 통과 필수 게이트 | 통과한 필수 게이트 수 | 전체 필수 게이트 수 | 빌드, API, 동시성, 영속성 등 |
| 완전 추적성 | 필수 증거 단계가 모두 연결된 요구사항 수 | 전체 요구사항 수 | 연결된 파일이 실제로 존재해야 함 |
| 성공 실행 | 완료되고 모든 필수 게이트를 통과한 실행 수 | 계획한 전체 반복 수 | 실패·시간 초과 포함 |

구현 여부와 추적성은 다릅니다. 코드 파일을 요구사항에 연결했더라도 동작 게이트가 실패하면
구현 요구사항으로 세지 않습니다. 반대로 동작이 통과해도 설계·API·코드·테스트 증거가
연결되지 않았다면 추적성은 미충족입니다.

토큰, LLM 호출, 실행 시간, `총 토큰 / 구현 요구사항 수`는 효율을 해석하기 위한 값일 뿐
품질 결과와 합산하지 않습니다. 토큰을 확인할 수 없으면 0이 아니라 `null`과 `미수집`으로
표시합니다. 중앙값은 전체 실행과 성공 실행을 나누어 제공하므로 실패를 숨기지 않습니다.

## 실제 비교 준비 순서

1. 동일한 요구사항 입력과 클라우드 제약을 고정합니다.
2. 모든 대상에서 모델, API 엔드포인트, 온도, 최대 토큰, 시간 제한, 반복 횟수를 같게 맞춥니다.
3. 각 대상용 래퍼가 실행 후 `subject-result.json`을 만들도록 합니다.
4. 요구사항과 제약조건을 독립 게이트에 연결합니다.
5. manifest를 `validate`한 뒤 `run`합니다.
6. `comparison.md`의 비율과 실패 상세를 먼저 보고, 토큰·시간은 같은 성공 수준끼리 비교합니다.

MetaGPT와 ChatDev는 현재 고정 설치 스크립트를 사용할 수 있습니다.

```powershell
./evaluation/baselines/setup_metagpt.ps1
./evaluation/baselines/setup_chatdev.ps1
```

설치와 실제 LLM 실행은 외부 네트워크 및 각 공급자의 API 키가 필요합니다.

## manifest 작성

최소 구조는 다음과 같습니다.

```json
{
  "schemaVersion": "easydep-comparison-manifest/v1",
  "experimentId": "course-registration-v1",
  "repetitions": 3,
  "outputRoot": "artifacts/comparison",
  "requirements": [
    {
      "id": "FR-01",
      "text": "학생은 강좌를 조회할 수 있다.",
      "verificationGates": ["business-api"],
      "evidenceStages": ["design", "api", "code", "test"]
    }
  ],
  "constraints": [
    {
      "id": "CLOUD-01",
      "text": "AWS 서울 리전에 배포한다.",
      "verificationGates": ["terraform-policy"]
    }
  ],
  "gates": [],
  "arms": []
}
```

요구사항 ID에는 한 가지 검증 책임만 둡니다. 예를 들어 “조회·등록·취소”를 하나의 ID로
묶으면 일부만 구현됐을 때 정확한 개수를 낼 수 없으므로 세 개로 나눕니다.

### 지원 게이트

파일 존재 확인:

```json
{
  "id": "generated-artifacts",
  "kind": "fileExists",
  "paths": ["{workspace}/pom.xml", "{workspace}/src/main/java"]
}
```

명령 실행:

```json
{
  "id": "build-and-test",
  "kind": "command",
  "command": ["mvnw.cmd", "test"],
  "cwd": "{workspace}",
  "timeoutSeconds": 900,
  "expectedExitCodes": [0]
}
```

HTTP 업무·동시성 오라클:

```json
{
  "id": "business-api",
  "kind": "httpOracle",
  "oraclePath": "../baselines/course-registration-cases/business-oracle.json",
  "baseUrl": "{base_url}"
}
```

`httpOracle`은 순차 `request`와 실제 동시 `concurrentRequests`를 지원합니다. 비교 대상의
`subject-result.json`에서 `metadata.baseUrl`을 제공하면 `{base_url}`로 사용할 수 있습니다.
기존 수강신청 오라클은 기본 흐름, 중복 등록 거부, 마지막 좌석 초과 판매 방지 등을 검사합니다.

한 HTTP 오라클에 여러 업무 검증이 있으면 요구사항을 오라클 전체가 아니라 개별 phase에
연결할 수 있습니다. 예를 들어 강좌 조회 요구사항에는
`"verificationGates": ["business-api#course-catalog"]`, 중복 등록 요구사항에는
`"verificationGates": ["business-api#duplicate-enrollment-rejected"]`를 사용합니다. 오라클은
한 번만 실행되지만 구현 요구사항 개수는 각 phase 결과로 따로 계산됩니다. `#` 뒤 ID가
없거나 실패하면 해당 요구사항은 구현된 것으로 세지 않습니다.

게이트의 `required` 기본값은 `true`입니다. 탐색용 검사를 `false`로 둘 수 있지만, 어떤
요구사항이나 제약조건의 `verificationGates`에 연결했다면 그 항목의 구현 판정에는 그대로
반영됩니다.

### 비교 대상 실행 명령

각 `arm.command`는 셸 문자열이 아니라 인자 배열입니다. 실행기는 셸 확장 없이 명령을 실행해
인용 차이를 줄이고, 제한 시간을 넘기면 해당 실행을 `timeout`으로 보존합니다.
manifest의 프레임워크 이름과 고정 버전이 결과 파일의 값과 다르면 해당 실행은 실패로
분류합니다. 실패 래퍼가 결과 파일을 남긴 경우에는 토큰 사용량을 버리지 않고 실패 실행에
보존합니다.

```json
{
  "id": "chatdev",
  "framework": "ChatDev",
  "frameworkVersion": "1.1.6-bcab157",
  "command": ["{python}", "-X", "utf8", "scripts/run_chatdev_arm.py", "--run-dir", "{run_dir}"],
  "resultPath": "{run_dir}/subject-result.json",
  "timeoutSeconds": 7200
}
```

사용 가능한 템플릿 변수는 `{python}`, `{repository}`, `{manifest_dir}`, `{run_dir}`,
`{arm_id}`, `{experiment_id}`, `{repetition}`입니다. 게이트에는 추가로 `{workspace}`와
`subject-result.json`의 단순 `metadata` 값이 제공됩니다.

## 비교 대상이 남겨야 하는 결과

각 래퍼는 성공 여부와 관계없이 가능하면 다음 파일을 작성합니다.

```json
{
  "schemaVersion": "easydep-comparison-subject-result/v1",
  "framework": "ChatDev",
  "frameworkVersion": "1.1.6-bcab157",
  "status": "completed",
  "workspace": "C:/generated/course-registration",
  "usage": {
    "inputTokens": 12000,
    "outputTokens": 3400,
    "totalTokens": 15400,
    "llmCalls": 18,
    "missingUsageCalls": 0,
    "source": "chatdev-log-provider-usage"
  },
  "requirementEvidence": {
    "FR-01": {
      "design": ["docs/design.md"],
      "api": ["openapi.json"],
      "code": ["src/CourseController.java"],
      "test": ["tests/course-api.http"]
    }
  },
  "metadata": {"baseUrl": "http://127.0.0.1:18080"}
}
```

증거 경로는 `workspace` 기준 상대 경로 또는 절대 경로입니다. 문자열만 적는 것으로 끝나지
않고 평가 시 파일 존재를 확인합니다.

## 토큰 어댑터 사용

ChatDev 실행 로그 변환:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation.comparison.adapters.chatdev `
  --log C:/temp/chatdev.log `
  --workspace C:/generated/chatdev-app `
  --evidence C:/temp/chatdev-evidence.json `
  --output C:/temp/run/subject-result.json
```

MetaGPT는 실행 종료 시 저장한 CostManager JSON을 우선 사용합니다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation.comparison.adapters.metagpt `
  --cost-manager-json C:/temp/metagpt-cost.json `
  --workspace C:/generated/metagpt-app `
  --evidence C:/temp/metagpt-evidence.json `
  --output C:/temp/run/subject-result.json
```

CostManager JSON을 저장할 수 없는 실행은 `--log`로 대체할 수 있습니다. 어댑터는
`prompt_tokens`와 `completion_tokens`를 합산합니다. 프레임워크 내부의 오래된 가격표로
계산한 비용은 수집하지 않습니다.

EasyDep 공개 제품 실행 결과 변환:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation.comparison.adapters.easydep `
  --product-result C:/temp/easydep-product.json `
  --usage C:/temp/easydep-langsmith-usage.json `
  --workspace C:/generated/easydep-app `
  --evidence C:/temp/easydep-evidence.json `
  --output C:/temp/run/subject-result.json
```

실제 arm 래퍼는 다음 세 단계를 한 프로세스 안에서 수행하면 됩니다: 프레임워크 실행,
사용량·증거 변환, 생성 앱 기동과 `metadata.baseUrl` 기록. 비교 실행기는 그 다음 동일한
게이트를 적용합니다. API 키나 원문 프롬프트는 결과 파일과 로그에 기록하지 마세요.

## 공정한 비교 체크리스트

- 동일 요구사항 원문과 클라우드 제약을 사용했는가
- 동일 모델·엔드포인트·온도·토큰 한도·시간 한도를 사용했는가
- 동일한 독립 게이트를 사후 적용했는가
- 실패·시간 초과·사용량 미수집을 결과에서 제거하지 않았는가
- 각 프레임워크 버전과 실행 환경을 고정했는가
- 사람 또는 LLM의 주관 판정은 자동 게이트와 분리했는가

LLM 판정은 문서 명료성처럼 결정적 검사가 어려운 보조 분석에만 사용하고, 필수 요구사항
구현 개수의 기본 근거로 사용하지 않는 것을 권장합니다.
