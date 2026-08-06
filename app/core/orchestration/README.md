# 4단계 오케스트레이션

이 패키지는 멤버 소유 코드를 수정하지 않고 요구사항·설계·구현·테스팅 함수를 연결한다.
클라우드 작업은 별도 단계가 아니라 각 단계의 하위 작업이다.

## 실행

```python
from app.core.orchestration import RunRequest, run_batch, start_run, resume_run

result = run_batch(RunRequest(
    requirements=["Provide a REST API."],
    resource_constraints_text="Deploy on GCP in Seoul within 100 USD/month.",
))
```

- `start_run`: 사용자 입력이 필요하면 중단하는 interactive 실행
- `resume_run`: 중단된 실행에 답변을 전달
- `run_batch`: 동결된 입력으로 무인 실행
- `get_run`: SQLite에 저장된 실행 상태 조회

기존 `start_workflow`, `complete_design`, `complete_implementation` API는 제거했다.

## Provider

`RunRequest.providers`에서 각 하위 작업 구현을 `member`, `llm`, `builtin` 중 하나로
명시한다. 등록되지 않은 조합이나 선택한 provider의 실패는 즉시 실행 실패다. 다른
provider로 자동 전환하지 않는다.

기본 구성은 멤버 요구사항·설계, 멤버 구현 골격, LLM 수용 테스트 생성, 한 번의 LLM 업무
로직 완성, 결정론적 VM 선택, 한 번의 LLM Docker/VM Terraform 생성, 내장 애플리케이션
테스트다. 생성된 수용 테스트는 업무 로직 provider가 수정할 수 없다. 현재 K8s
중심 testing prototype은 VM 범위와 맞지 않아 기본 provider로 사용하지 않는다.

멤버 구현기가 미완성인 동안 비교실험은 `implementation_scaffold=llm`을 명시한다. 이
임시 provider는 고정된 Java 21/Spring Boot 빌드 골격과 LLM이 생성한 `src/main` 코드만
만들며, 멤버 provider 실패 시 자동으로 선택되지 않는다.

## 상태와 산출물

실행 상태는 표준 라이브러리 `sqlite3`로 `.easydep/orchestration/runs.sqlite3`에 저장한다.
사용자 산출물은 중복 없이 다음 위치에 기록한다.

```text
artifacts/runs/<run-id>/
├── manifest.json
├── 01-requirements/
├── 02-design/
├── 03-implementation/
└── 04-testing/
```

내부 testing은 생성 애플리케이션 테스트만 수행한다. Docker, OpenTofu, 업무 API와 코드
품질 평가는 EasyDep·CoT·MetaGPT에 동일하게 적용하는 외부 공통 평가기의 책임이다.
