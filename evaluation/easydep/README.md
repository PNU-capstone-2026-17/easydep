# EasyDep 평가

EasyDep의 각 개발 단계를 개별적으로 평가한다.

- `requirements/`: 요구사항 분석 결과를 평가하는 개발·홀드아웃 입력, 정답과 채점기
- `product_scenario.py`: 브라우저와 같은 공개 HTTP API로 앱 생성부터 테스트까지 실행하는 도구

설계·구현·테스팅 단계의 독립적인 평가 도구가 준비되면 이 디렉터리 아래에 같은 수준으로
추가한다. 일반 실행과 평가 실행 모두 `artifacts/runs/<run-id>/`에 저장하고 manifest로 구분한다.

## 실제 사용자 경로로 전체 실행하기

`ProductScenarioRunner`는 평가를 위해 단계 내부 함수를 따로 부르지 않는다. 브라우저가 사용하는
Workspace API에 앱을 만들고, command 상태와 event를 조회한 뒤 화면에 표시된 다음 버튼을
선택한다. 따라서 이 실행기가 성공하면 단순히 요구사항 함수 하나가 성공한 것이 아니라 다음
연결 경로가 함께 동작했다는 뜻이다.

```text
앱 생성
  → 요구사항 질문에 답변하거나 LLM 수리 버튼 선택
  → 설계 시작과 완료 확인
  → 구현 시작과 전송 승인
  → 테스트 시작과 필요한 LLM 수리
  → 구조화 산출물 버전 조회
  → 생성 파일 목록과 파일 내용의 SHA-256 확인
```

실제 로컬 서버에 연결하는 가장 작은 예시는 다음과 같다.

```python
from evaluation.easydep.product_scenario import (
    AutoActionPolicy,
    HttpProductScenarioTransport,
    ProductScenarioRunner,
)


def answer_question(command):
    # 실제 평가에서는 입력 세트에 준비된 답을 반환한다. 알 수 없는 질문이라면 None을
    # 반환해야 하며, 자동 정책이 임의로 답을 만들지 않는다.
    return "AWS 서울 리전을 사용하고 월 예산은 100달러입니다."


runner = ProductScenarioRunner(
    HttpProductScenarioTransport("http://127.0.0.1:8000"),
    policy=AutoActionPolicy(answer_question),
    timeout_seconds=7200,
)
result = runner.run("소규모 온라인 주문 서비스를 만들어 주세요.")
print(result.app_id, result.implementation_job_id, result.testing_job_id)
```

### 자동 정책이 하는 일

`AutoActionPolicy`는 자동 모드 전용 backend를 사용하지 않는다. 현재 command 공개 응답을 보고
화면에 이미 있는 `advance`, `delegate_repair`, `approve_implementation`, `start_testing` 등의
버튼 중 하나를 대신 누른다. 사용자의 판단이 필요한 질문은 `question_answer` callback에서 답을
받은 경우에만 `message` command로 보낸다. 답이 없으면 실행을 멈추므로 평가 도구가 요구사항을
임의로 만들지 않는다.

화면에는 실패한 단계의 재실행, 과거 단계로 돌아가기, 직접 수정 지시 입력 같은 버튼도 보인다.
이 선택지는 `public_actions`에 포함되지만 자동 정책은 임의로 누르지 않는다. 특히 일반적인
`message` 입력란을 질문 답변으로 오인하지 않고, 공개 응답에 실제 질문 정보가 있을 때만
`question_answer`의 값을 전송한다.

실행 중 생성된 command ID와 마지막 event cursor는 계속 기억한다. 구현과 테스트에서는 각각의
job ID도 함께 기록한다. 최종 파일을 조회할 때 파일 snapshot의 `implementation_job_id`가 이번
실행의 ID와 다르면 성공으로 처리하지 않는다. 이 검사는 같은 앱에서 구현을 여러 번 실행했을 때
테스트 결과와 최신 파일을 잘못 섞는 문제를 찾는 데 사용한다.

### 실행이 멈췄을 때

시간이 초과되면 `ProductScenarioTimeout`, 사용자의 답이 필요하면
`ProductScenarioNeedsInput`, command나 산출물 확인이 실패하면 `ProductScenarioFailed`가
발생한다. 세 예외에는 모두 `report`가 있으며 다음 값이 들어 있다.

- `app_id`: 이어서 조회할 앱
- `last_command_id`: 마지막으로 확인한 command
- `current_stage`: 멈춘 단계
- `event_cursor`: 마지막으로 읽은 event 번호
- `implementation_job_id`, `testing_job_id`: 구현·테스트 작업 번호
- `artifact_versions`: 멈춘 시점에 공개 API에서 확인할 수 있었던 산출물 버전

`report.as_dict()`를 실행 결과 파일에 저장하면 서버 로그만으로는 알기 어려운 재개 위치를 남길
수 있다. 다시 실행할 때에는 이 값과 서버의 Workspace snapshot이 같은 앱과 작업을 가리키는지
먼저 확인한다.

## 테스트 작성 원칙

`tests/test_product_scenario_runner.py`는 실제 HTTP 응답과 같은 dict를 반환하는 fake transport를
사용한다. prompt 문장, 단계의 private helper, Python 소스 문자열을 검사하지 않는다. 검증 대상은
공개 command 순서, event cursor, timeout 보고서, 구현 작업과 파일 버전의 출처가 일치하는지이다.
실제 서버와 fake 모두 `ProductScenarioTransport`를 구현하므로 테스트 전용 단계 실행 경로는 없다.
