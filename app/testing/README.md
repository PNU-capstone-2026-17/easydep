# Testing 에이전트

Testing은 Implementation이 남긴 고정 `TestingInput`으로 애플리케이션을 한 번 복원하고,
동적 기능 검사와 배포 정적 검사를 실행한다. 구현 단계에서 끝난 단위 테스트와 frontend build는
반복하지 않는다.

## 동적 기능 검사

기능 테스트의 단일 계획 형식은 Arazzo v1.1.0 JSON이다.

```text
requirements / use cases / OpenAPI / RTM hints
  → workflow 후보 투영
  → LLM이 Arazzo Workflow Object 작성
  → 공식 Arazzo schema와 EasyDep 실행 profile 검증
  → frozen OpenAPI와 target URL로 workflow 실행
  → workflow/step/criterion 결과와 trace evidence 기록
```

문서 envelope, source, 버전과 stable workflow ID는 코드가 결정한다. LLM은 존재하는 OpenAPI
`operationId`만 사용하여 step 순서, Runtime Expression과 근거 있는 `successCriteria`를
작성한다. RTM과 sequence는 후보 순서와 조사 근거를 제공하지만 실행 operation을 제한하는
allowlist가 아니다. Arazzo 실행 의미는 표준 필드에만 두며 `x-easydep-trace`는 요구사항,
유스케이스와 evidence reference만 보존한다.

공식 schema를 통과한 문서는 다시 EasyDep 실행 profile로 검사한다. 현재 profile은 frozen local
OpenAPI, 동기 HTTP와 local workflow, workflow input, step output, Runtime Expression,
`successCriteria`, `dependsOn`, bounded retry와 goto를 지원한다. 원격 source, AsyncAPI,
임의 코드 실행, 무제한 retry와 순환 control flow는 HTTP 요청 전에 거부한다.

실행기는 기존 OpenAPI 요청 직렬화와 response schema 검증 primitive를 재사용한다. 모든 요청은
Testing이 실행한 앱의 `target_url`로 고정한다. workflow input과 요청 leaf는 OpenAPI의
`const`, `enum`, `example`, `default`를 먼저 사용하고, 결정할 수 없는 값만 LLM에 한 번
묻는다. 실제 사용한 `workflowInputs`와 `inputValues`는 Arazzo 문서 밖에 저장하여 재실행 때
동일한 값을 쓴다.

HTTP 도달과 OpenAPI response 검증은 contract 결과다. 요구사항에서 직접 근거를 얻은
`successCriteria`가 통과한 경우만 semantic coverage로 인정한다. criterion이 없으면 workflow가
통과해도 관련 요구사항은 `unverifiedIds`에 남긴다.

실패 action이 지정한 cleanup은 원래 workflow의 성공·실패와 별도로 기록한다. 응답 schema 오류와
transport 오류에서도 실행 가능한 cleanup workflow를 시도한다. cleanup 실패는 최초 실패를
덮어쓰지 않는다.

## 결과와 재개

`candidatePlan`에는 canonical Arazzo 문서만 저장한다. 실행 데이터는 다음 sibling 필드로
분리한다.

- `workflowInputs`, `inputValues`: 실제 사용한 고정 입력
- `workflows`: workflow 문서, step 결과와 trace
- `failedWorkflowId`, `failedStepId`: 첫 차단 지점
- `pendingWorkflowIds`, `reusedWorkflowIds`: 재개와 재사용 범위
- `planDigest`, `candidateDigest`: 문서와 입력 식별값

같은 구현을 선택 수리할 때에는 동일 문서·입력의 PASS workflow를 재사용하고 실패 workflow를
먼저 실행한다. 구현 파일이 바뀌면 이전 PASS workflow도 회귀 확인을 위해 다시 실행한다. 예전
자체 `FunctionalTestPlan` checkpoint는 해석하지 않으며 새 Testing 실행에서 Arazzo 계획을
다시 만든다. 별도 DB table이나 migration은 없다.

동적 실패에는 workflow, step, operation, 실패 criterion, 요청·응답과 애플리케이션 로그 위치를
남긴다. RTM으로 얻은 source file은 구현 수리의 조사 힌트이고 수정 범위 hard limit가 아니다.

- `TEST_DEFECT`: Arazzo 문서·expression·입력 문제
- `SUT_DEFECT`: 검증된 criterion 또는 OpenAPI contract를 실제 응답이 위반
- `ENVIRONMENT_DEFECT`: container, transport 또는 tool 문제
- `UPSTREAM_AMBIGUITY`: acceptance, operation 순서나 복구 계약의 근거 부족

Implementation으로 돌아간 동적 수리는 같은 Arazzo workflow와 입력을 `run_task_check`에서
재실행한다. 통과하면 바깥 Testing 단계가 동일 checkpoint에서 나머지 workflow와 gate를 이어서
확인한다. 정적 수리도 처음 실패한 Trivy, package 또는 OpenTofu gate만 같은 방식으로 다시
검증한다.

동적 검사가 차단되면 static, package와 IaC gate는 `DEFERRED/NOT_APPLICABLE`로 남겨 원인을
가리지 않는다. 동적 검사가 통과하면 공용 toolchain으로 남은 gate를 실행한다. 입력과 관련 파일
digest가 같은 미선택 gate만 이전 PASS 결과를 재사용한다.

## 코드 경계

- `schemas/arazzo.py`: 공식 schema와 EasyDep 실행 profile 검증
- `utils/arazzo_planner.py`: frozen 산출물을 workflow authoring context로 투영
- `utils/arazzo_executor.py`: Arazzo orchestration, expression, criterion과 cleanup
- `utils/functional_executor.py`: 재사용 가능한 OpenAPI/HTTP primitive
- `nodes/dynamic_functional.py`: 계획 생성, workflow 실행과 report 집계
- `service.py`: checkpoint, selective rerun, repair evidence와 공개 결과
- `repair_check.py`: Implementation 수리 공간에서 동일 gate 재검증

LLM 연결은 다른 단계와 동일하게 `app.llm_connection`을 사용한다. API key는 요청에만 전달하고
Arazzo 문서, 결과와 로그에는 저장하지 않는다.
