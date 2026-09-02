# Testing 에이전트

Testing은 EasyDep이 만든 애플리케이션 산출물을 검사한다. 생성 테스트 코드와 LLM
프롬프트는 영어로 유지하고, 이 문서와 실행 결과 설명은 한국어로 작성한다.

## 입력과 실행 흐름

Implementation 작업이 완료되면 `TestingInput`에 산출물 version ID와 함께 요구사항,
use-case, OpenAPI, 최종 배포 bundle의 version/digest를 고정한다. bundle에는 ResourcePlan과
사용자용 deployment package 정보가 포함된다. Testing이 시작된 뒤에는 같은 앱에 새
요구사항이나 구현이 저장되어도 이 고정 입력을 바꾸지 않는다.

```text
고정된 TestingInput
  → 산출물 전체를 한 번 복원
  → deployment·IaC·deployment package 정적 gate
  → 실행된 앱을 대상으로 통합·E2E candidate 생성·검증
  → 실행된 테스트만 requirement coverage에 기록
```

backend 단위 테스트, 작은 통합 테스트와 frontend build는 Implementation이 코드를 작성하는
작업 안에서 실행하고 즉시 수리한다. Testing은 이를 반복하지 않고 여러 구성 요소를 함께 띄워야
확인할 수 있는 API 흐름과 사용자 DOM·JavaScript 흐름에 집중한다.

정적·동적 단계는 동일한 복원 폴더를 사용한다. `TestingInput`, `current_node`와 완료된 report는
현재 `workspace_commands.payload`에 함께 기록한다. 서버가 재시작되면 Workspace가 같은 command를
다시 실행하고 Testing은 저장된 검사 경계부터 이어 간다. 별도 Testing 작업 표나 내부 polling
thread는 사용하지 않는다.

## Gate 판정

각 검사는 `gateStatus`로 다음 네 상태를 사용한다.

| 상태 | 의미 |
| --- | --- |
| `PASS` | 검사를 실행했고 통과함 |
| `FAIL` | 검사를 실행했고 제품 또는 산출물 문제가 확인됨 |
| `INCONCLUSIVE` | 도구·환경·입력 문제로 판정할 수 없음 |
| `NOT_APPLICABLE` | 해당 검사 대상이 없음 |

기존 HTTP 호환을 위해 `status`의 `PASSED`, `FAILED`, `UNAVAILABLE`, `SKIPPED`도 당분간
함께 보낸다. 집계 기준은 `gateStatus`이며, 필수 도구가 `UNAVAILABLE`이거나 실행되지
않으면 전체 gate는 `INCONCLUSIVE`다. IaC나 deployment package가 설계에서 명시되지
않은 애플리케이션만 `NOT_APPLICABLE`로 표시한다.

배포 package가 있으면 다음을 검사한다.

- OpenTofu `fmt -check`, `init -backend=false`, `validate`
- `cloud-init schema`, `docker compose config`
- POSIX shell의 `bash -n`과 PowerShell parser
- README, compose, tofu, script의 필수 파일과 ResourcePlan 참조
- 실제 secret 값이나 private key가 package에 포함되지 않았는지

이 단계에서는 CSP 리소스를 만들거나 image를 push하지 않으며 `tofu apply`도 호출하지
않는다.

## Dynamic candidate와 실패 분류

코드는 요구사항·use-case·고정 OpenAPI에서 관련 candidate를 먼저 만들고, 최대 소수
candidate 묶음별로 테스트를 생성한다. 실행 전에 Python 문법, pytest 테스트 수,
assertion, all-skip/pass, OpenAPI path와 method를 검사한다. assertion이 없거나 잘못된
endpoint를 쓰는 candidate는 제품 성공으로 인정하지 않는다.

coverage는 pytest JSON report에서 실제로 수집·실행된 테스트의 requirement ID만
기록한다. 실행하지 않은 전체 요구사항을 coverage로 주장하지 않는다. 실패는 다음과
같이 분류하고 report의 `defect`와 `blocking_findings`에 repair route를 남긴다.

| 분류 | 수리 위치 | 테스트 보존 |
| --- | --- | --- |
| `TEST_DEFECT` | Testing에서 candidate 재생성 | 아니오 |
| `SUT_DEFECT` | 같은 candidate와 로그를 Implementation으로 전달 | 예 |
| `ENVIRONMENT_DEFECT` | 환경 복구 후 같은 검사 재실행 | 예 |
| `UPSTREAM_AMBIGUITY` | 요구사항 또는 설계 단계 | 예 |

제품 실패(`SUT_DEFECT`)에서는 `candidateCode`와 digest를 결과에 남겨 통과 조건을
약하게 만든 새 테스트로 바꾸지 않는다. 이전 repair history도 다음 작은 생성 요청에
전달해 같은 candidate와 전략을 반복하지 않는다.

## Runtime 격리

Dynamic runner는 `EASYDEP_TESTING_TOOLCHAIN_IMAGE`로 지정한 고정
`easydep-testing-toolchain` 이미지를 사용한다. 기본 구현 이미지와 layer를 공유하되 이
이미지에만 Playwright와 Chromium headless shell이 들어 있다. API 흐름은 httpx로 검사하고,
실제 DOM·JavaScript·event·routing이 필요한 E2E만 Playwright를 사용한다. screenshot이나
픽셀 비교는 수행하지 않으며 실행 중 `pip install`도 하지 않는다.

복원한 애플리케이션은 `/easydep-app:ro`로, 결과만 별도 임시 폴더에 `rw`로 mount한다.
실행마다 고유 Docker network를 만들고 `--read-only`, CPU·memory·process 수 제한과
timeout을 적용한다. runner는 `--rm`으로 종료되며 network와 임시 output도 성공·실패·
timeout 뒤에 정리한다. 저장소 전체를 쓰기 가능한 형태로 container에 연결하지 않는다.

## 실패와 수리

- 필수 source/deployment 산출물이 없거나 `implementation_job_id`가 다르면 작업을
  시작하지 않는다.
- 파일 digest가 다르면 손상된 snapshot으로 보고 중단한다.
- Docker build·health·toolchain 실행 불가는 `INCONCLUSIVE`이며 성공으로 표시하지
  않는다.
- static 설정 오류와 dynamic 제품 오류는 각각의 finding과 route를 보존한다.
- Testing은 `tofu plan -refresh=false`를 설정했을 때만 dry-run을 실행하며 실제
  `apply`는 수행하지 않는다.
