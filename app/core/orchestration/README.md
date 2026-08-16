# 4단계 오케스트레이션

이 패키지는 멤버 소유 코드를 수정하지 않고 요구사항·설계·구현·테스팅 함수를 연결한다.
클라우드 작업은 별도 단계가 아니라 각 단계의 하위 작업이다.

## 실행

```python
from app.core.orchestration import (
    RunRequest,
    retry_failed_run,
    run_batch,
    start_run,
    resume_run,
)

result = run_batch(RunRequest(
    requirements=["Provide a REST API."],
    resource_constraints_text="Deploy on GCP in Seoul within 100 USD/month.",
))
```

- `start_run`: 사용자 입력이 필요하면 중단하는 interactive 실행
- `resume_run`: 중단된 실행에 답변을 전달
- `run_batch`: 동결된 입력으로 무인 실행
- `get_run`: SQLite에 저장된 실행 상태 조회
- `retry_failed_run`: 구현·테스트 실패 run의 완료 체크포인트를 유지하고 실패 작업부터 재개

기존 `start_workflow`, `complete_design`, `complete_implementation` API는 제거했다.

## Provider

`RunRequest.providers`에서 각 하위 작업 구현을 `member`, `llm`, `builtin` 중 하나로
명시한다. 등록되지 않은 조합이나 선택한 provider의 실패는 즉시 실행 실패다. 다른
provider로 자동 전환하지 않는다.

기본 구성은 멤버 요구사항·설계, 멤버 구현 골격, LLM 수용 테스트 생성, 한 번의 LLM 업무
로직 완성, 결정론적 VM 선택, 한 번의 LLM Docker/VM Terraform 생성, 내장 애플리케이션
테스트다. 생성된 수용 테스트는 업무 로직 provider가 수정할 수 없다. 현재 K8s
중심 testing prototype은 VM 범위와 맞지 않아 기본 provider로 사용하지 않는다.

비교실험은 멤버 구현 provider를 사용한다. `--approve-member-implementation`을 명시한
실행만 현재 run의 외부 전송을 일괄 승인하고 OpenHands workflow를 실행한다. 승인이 없으면
`MEMBER-APPROVAL-REQUIRED`로 멈추며 임시 LLM으로 우회하지 않는다. 멤버 workflow가
`COMPLETE`이면 acceptance/logic 임시 LLM 호출은 0회다. 구현된 planner를 모두 수행한 뒤
`NEEDS_PLANNER`로 남은 경우에만 임시 provider가 공백을 보완하며, `FAILED`와
`NEEDS_INPUT`은 그대로 실패한다. Docker와 VM Terraform은 계속 전용 VM delivery 단계가
소유한다.

## 상태와 산출물

실행 상태는 표준 라이브러리 `sqlite3`로 `.easydep/orchestration/runs.sqlite3`에 저장한다.
사용자 산출물은 중복 없이 다음 위치에 기록한다.

```text
artifacts/runs/<run-id>/
├── manifest.json
├── timing-summary.json
├── 01-requirements/
├── 02-design/
├── 03-implementation/
└── 04-testing/
```

내부 testing은 생성 애플리케이션 테스트만 수행한다. Docker, OpenTofu, 업무 API와 코드
품질 평가는 EasyDep·CoT·MetaGPT에 동일하게 적용하는 외부 공통 평가기의 책임이다.

`timing-summary.json`은 단계 벽시계와 요구사항·설계 LLM 호출, IaC 생성·수정·HCL 검사·
공급자 초기화·검증을 각각 내림차순으로 기록한다. 병렬 하위 작업은 시간이 겹칠 수 있으므로
하위 작업 시간을 단순 합산해 전체 실행 시간이나 임계 경로로 해석하지 않는다. 실패 실행도
중단 직전까지의 시간 사건을 보존한다.

## 개발 단계 실패 재개

개발 중 실패는 새 run으로 처음부터 다시 실행하지 않는다. 정리된 작업공간은 원본 run의
`03-implementation/application`에서 복원하고, 완료된 구현 하위 작업은 건너뛴 뒤 실패한
작업부터 재개한다. 테스트 실패도 이전 시도 결과를 보존한 채 새 시도를 추가한다.

재개 결과는 원본을 덮어쓰지 않고 다음 위치에 기록한다.

```text
artifacts/runs/<run-id>/repairs/attempt-<n>/
```

각 repair 매니페스트에는 `developmentRepair=true`, `parentRunId`, `retryHistory`가 들어간다.
따라서 개발 개입 결과를 독립 본실험 반복으로 집계해서는 안 된다. 경로 안전 검사 같은 보안
불변식은 재개나 절제 조건에서도 비활성화하지 않는다.

## 앱·클라우드 일관성 경계

- 앱 계약 검증은 생성 소스·설정·빌드에서 관찰한 사실을 조합한다. 데이터베이스 드라이버 존재뿐 아니라 ORM과 데이터베이스 조합에 필요한 런타임 설정도 검사한다. 규칙은 사례 ID가 아니라 확장 가능한 기술 조합 레지스트리로 관리한다.
- 사용자가 `resource_constraints_text`에 CSP를 하나 명시하면 그 값이 요구사항 분석기의 추론보다 우선한다. 분석 결과가 다른 CSP를 가리키면 불일치 이력을 남기고 명시된 CSP로 VM 선택과 Terraform 생성을 통일한다.
- 입력이 둘 이상의 CSP를 동시에 배포 대상으로 명시하면 임의 선택하지 않고 실패시킨다. 생성 Terraform은 고정 provider source·version과 다른 CSP 리소스 접두사 혼입 여부를 공급자 초기화 전에 검사한다.

## 현재 의존성 모델의 입력 범위

이 표는 capability 전체 분류가 아니라 현재 실행 코드가 DepKB 의존성 계산에 실제로 소비하는
입력만 기술한다. 동적 capability 이름을 만들 수 있다는 사실과 그 이름을 DepKB가 해석할 수
있다는 사실은 구분한다.

| 입력 | 현재 결과 | 범위 |
|---|---|---|
| Docker-on-VM 시스템 범위 | `vm` 시작 리소스 | 지원 범위의 기본 전제 |
| 승인된 `persistent-block-storage` stable ID | 값에 따라 `disk` 또는 `no_disk` | 자유로운 need 이름과 독립적으로 모델링됨 |
| 승인된 `load-balanced-ingress` stable ID | CSP별 HTTP 부하분산 실현 | 명시적으로 모델링됨 |
| `https-load-balanced-ingress` stable ID | 요구사항 분류에는 남지만 Docker-on-VM 생성 범위에서는 미지원 | HTTP로 자동 확장하지 않음 |
| 승인된 필수 고가용성 요구 | CSP별 관리형 VM 그룹 + `loadBalancer` 앵커 | 사전 선호 질문 없이 필수 요구 근거가 있을 때만 적용 |
| 그 밖의 승인된 동적 capability | 앵커 없음 | DepKB 미모델링, IaC 에이전트에는 요구사항으로만 전달 |

설계 결과의 `dependency_coverage.modeledInputs`는 이번 실행에서 실제 해석한 입력과 결과를,
`unmodeledAcceptedNeeds`는 승인됐지만 DepKB가 해석하지 않은 capability 이름을 기록한다. 후자는
요구사항 누락이라는 뜻은 아니지만 DepKB가 충족 근거를 제공했다는 주장에는 포함할 수 없다.

`deployment_needs`의 자유로운 key는 사람이 읽는 표현으로 유지한다. 별도의
`dependencyCapabilityIds`만 현재 근거가 있는 stable ID를 담으며, 반복 LLM 표본이 모두 같은
ID에 동의할 때만 하류로 전달한다. stable ID와 하이픈·밑줄만 다른 canonical key도 같은
합의 규칙 아래 받을 수 있다. 그 밖에는 stable ID의 토큰이 key·role·근거에 모두 나타나는
후보만 연결하고, 더 구체적인 후보 하나를 고를 수 없으면 미모델링으로 남긴다. 접미사나
동의어를 임의 별칭으로 등록하지 않는다. 기존 CapabilityContract/v1 산출물의
`persistent_storage` key는 저장된 개발 실행을 재생할 수 있도록 영속 스토리지에 한해 마이그레이션
입력으로 받는다. 새 capability를 이 예외 목록에 계속 추가하지 않는다.
