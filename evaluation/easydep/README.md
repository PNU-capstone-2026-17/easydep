# EasyDep 평가

EasyDep의 각 개발 단계를 개별적으로 평가한다.

- `requirements/`: 요구사항 분석 결과를 평가하는 개발·홀드아웃 입력, 정답과 채점기
- `product_scenario.py`: 브라우저와 같은 공개 HTTP API로 앱 생성부터 테스트까지 실행하는 도구
- `product/`: 서로 다른 요구사항을 공개 제품 경로로 반복 실행하고 결과를 집계하는 도구

설계·구현·테스팅 단계의 독립적인 평가 도구가 준비되면 이 디렉터리 아래에 같은 수준으로
추가한다. 일반 실행 산출물은 `artifacts/runs/<run-id>/`에, 제품 반복 평가 manifest는
`<output>/<profile>/<run-id>/manifest.json`에 저장된다. 기본 `output`은
`artifacts/product-evaluation`이다.

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

## 여러 요구사항으로 반복 평가하기

`product/`는 `ProductScenarioRunner`를 그대로 사용한다. 즉, 요구사항·설계·구현 함수를 평가
코드에서 직접 부르지 않는다. 앱을 만드는 순간부터 화면이 사용하는 Workspace API를 거치므로
프론트엔드에서 가능한 흐름과 평가에서 가능한 흐름이 서로 달라지는 문제를 찾을 수 있다.

### 입력 목록

`product/catalog.json`에는 11개 입력의 ID, 업무 유형, development/holdout 구분과 기존 입력 파일
경로가 있다. 기존 파일의 `classified` 목록은 내부 단계에 바로 전달하지 않는다. 평가 도구가 각
요구사항을 하나의 일반 사용자 메시지로 합친 다음 `POST /api/workspace/apps`에 보낸다. 따라서
요구사항 첫 분류와 확인 질문도 실제 제품과 똑같이 실행된다.

- development 8개: 작은 API, 주문·결제, 알림, IoT, 노트, 쇼핑몰, 배차, 대규모 전자상거래
- holdout 3개: 물류, 파트너 보고, 고가용성 원격 진료

holdout은 구현 방향이나 prompt를 고르는 자료가 아니다. 설정과 알고리즘을 development 입력으로
확정한 뒤 마지막 확인에만 사용한다. 명령에도 이 구분을 넣어, 별도 확인 옵션 없이는 holdout이
실행되지 않게 했다. `quick`, `stability`, `full`은 profile을 먼저 고른 뒤
해당 development 원문만 읽는다. 카탈로그에 holdout 파일 경로가 있어도 이 실행들은
holdout 원문을 열지 않는다.

### 실행 종류

| profile | 입력과 반복 | 완료 지점 | 주된 용도 |
| --- | --- | --- | --- |
| `quick` | development 8개 × 1회 | 설계 완료 | 일반 변경 뒤 빠른 회귀 확인 |
| `stability` | development 8개 × 3회 | 설계 완료 | LLM 결과 차이와 완주율 비교 |
| `full` | 대표 development 4개 × 1회 | Testing 완료 | 생성 코드와 실제 도구까지 확인 |
| `holdout` | holdout 3개 × 1회 | Testing 완료 | 설정 확정 뒤 최종 확인 |

설계 완료 지점에는 deployment 산출물도 포함된다. 모든 최초 실행에는 서로 다른 run ID가 생긴다.
`stability`도 같은 앱을 세 번 쓰지 않고 각각 새 앱에서 시작하므로 우연히 남은 상태나 cache가
결과를 섞지 않는다.

서버를 먼저 실행한 뒤 다음처럼 profile 하나를 명령 한 번으로 실행한다. 외부 LLM을 사용하는
명령이므로 EasyDep 작업 지침에 따라 처음부터 네트워크 권한이 있는 환경에서 실행한다.

```powershell
python -X utf8 -m evaluation.easydep.product run `
  --profile quick `
  --base-url http://127.0.0.1:8000 `
  --provider nvidia-nim `
  --model meta/llama-3.3-70b-instruct `
  --settings-json artifacts/product-settings.json `
  --output artifacts/product-evaluation
```

안정성 실행은 `--profile stability`, 실제 구현과 Testing까지 확인하려면 `--profile full`로
바꾼다. holdout은 설정을 확정한 뒤에만 다음처럼 명시적으로 연다.

```powershell
python -X utf8 -m evaluation.easydep.product run `
  --profile holdout `
  --allow-holdout-after-settings-lock `
  --provider nvidia-nim `
  --model meta/llama-3.3-70b-instruct `
  --settings-json artifacts/product-settings.json
```

### manifest에 남는 내용

각 실행은 `<output>/<profile>/<run-id>/manifest.json`에 저장된다. 실행을 시작하기 전에 먼저
`RUNNING` manifest를 만들고, 정상 종료나 예외가 발생하면 같은 파일을 갱신한다.

- 입력 ID·원본 경로·입력 SHA-256, Git commit, provider, model과 설정 digest
- 위 조건의 출처가 `cli-user-provided-labels`이고 서버 검증값은 아니라는 표시
- 단계별 시작·종료 시각과 상태 변화, Workspace event와 선택한 화면 action
- 논리 LLM 작업 수와 실제 HTTP 요청 수, 입력·출력·전체 token
- schema repair, 의미 repair, 단계 사이 repair 전달 횟수
- cache hit/miss/bypass/single-flight와 provider 429/5xx/timeout
- 최종 단계, 최초 실패 단계와 이유, 구현·Testing job ID
- 구조화 산출물과 생성 파일의 버전·digest·파일 검증 수

`--provider`, `--model`, `--settings-json`은 평가 CLI가 서버 설정을 바꾸는
옵션이 아니다. 실행자가 이미 서버에 적용했다고 판단한 조건을 manifest에
남기는 비교용 label이다. 현재 공개 API는 서버의 전체 LLM 설정을 반환하지 않으므로,
평가 CLI는 이 label이 실제 서버 설정과 같은지 검증하지 못한다. 비교 전에 서버를
같은 설정으로 시작했는지 실행 명령과 서버 로그로 따로 확인해야 한다.

일부 단계가 LLM timing이나 token을 공개 응답에 포함하지 않으면 평가 도구가 0으로 기록하지
않는다. 값은 `null`로 두고 `measuredUnavailable`에 이유를 남긴다. 0은 실제 호출이나 repair가
없었다는 뜻이고, `null`은 측정할 자료가 없었다는 뜻이므로 서로 다르다.

### 실패한 실행 이어서 실행하기

실패 manifest의 `resumeRecord`에는 앱 ID, 마지막 command, event cursor, 현재 단계와 작업 ID가
있다. 다음 명령은 새 앱을 만들지 않고 공개 Workspace snapshot을 다시 읽어 같은 run ID에
결과를 이어 쓴다. 현재 공개 command가 실패 상태라면 화면에 노출된
`retry_requirements`, `retry_design`, `rerun_implementation`, `start_testing` 중 해당 버튼을
누른다. 요구사항부터 새로 시작하는 별도 평가 경로를 만들지는 않는다. 요구사항 재실행도 저장된
checkpoint가 있을 때만 실패한 위치에서 이어진다.

```powershell
python -X utf8 -m evaluation.easydep.product resume `
  --manifest artifacts/product-evaluation/quick/<run-id>/manifest.json `
  --base-url http://127.0.0.1:8000 `
  --provider nvidia-nim `
  --model meta/llama-3.3-70b-instruct `
  --settings-json artifacts/product-settings.json
```

재개할 때 입력 digest, provider, model, 설정 digest가 달라지면 중단한다. 서버의 현재 단계가
저장된 실패 단계보다 과거로 돌아간 경우도 중단한다. 이 검사 덕분에 다른 조건의 실행을 같은
결과처럼 합치거나 이미 완료한 공개 단계를 처음부터 다시 실행하지 않는다.

### 결과 집계하기

```powershell
python -X utf8 -m evaluation.easydep.product report `
  --input artifacts/product-evaluation `
  --output artifacts/product-evaluation/report.json
```

집계에는 완주율, 최초 실패 단계별 건수, 전체 시간과 token의 p50/p95, repair 중앙값, 단계별
시간, cache와 provider 오류가 들어간다. 실패한 실행도 시간이나 token이 측정됐다면 분포에
포함한다. 값이 없는 실행 수와 이유는 `unavailableCount`, `measuredUnavailableReasons`로 따로
보이므로 성공한 실행만 골라 평균을 좋게 보이게 하지 않는다. 재개로 같은 단계가
여러 번 실행됐다면 해당 run 안에서 시간을 먼저 더한 뒤 하나의 표본으로 사용한다.
목표 단계까지 한 번도 도달하지 못했다면 그 단계도 `sampleCount: 0`과 전체
`unavailableCount`로 보고서에 나온다.

한 보고서는 profile, 목표 단계, Git commit, 설정 digest, model/provider label,
development/holdout 구분이 같은 manifest만 합친다. 조건이 다른 파일이 들어 오면
명령이 오류를 내고 중단한다. 조건별로 입력 폴더를 나누어 보고서를 각각 만들어야
한다. 보고서의 `provenance`와 각 `runs` 항목에 이 비교 조건이 남는다.
