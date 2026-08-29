# EasyDep 제품 경로 실행기

이 디렉터리의 제품 경로 실행기는 프론트엔드와 같은 공개 HTTP API로 EasyDep을 한 번
실행한다. 요구사항·설계·구현 단계의 Python 함수를 직접 호출하지 않는다.

## 무엇을 하는가

실행 순서는 프론트엔드 Workspace 화면과 같다.

1. `POST /api/workspace/apps`로 요구사항을 입력한다.
2. `GET /api/workspace/apps/{app_id}`로 현재 command를 확인한다.
3. 프론트엔드 자동 모드와 같은 조건으로 다음 버튼을 고른다.
4. `POST /api/workspace/apps/{app_id}/commands`로 그 버튼 동작을 보낸다.
5. `/events`의 공개 이벤트를 읽으며 완료를 기다린다.
6. 완료되면 `GET /api/apps/{app_id}`의 산출물 응답을 그대로 저장한다.

질문 답변, 변경 확인, 실패 재실행처럼 사람의 판단이 필요한 상태에서는 임의로 답을
만들지 않고 멈춘다. 이때 앱 ID, 단계, command ID, 상태와 이벤트 위치가 출력되므로
브라우저에서 같은 앱을 열어 확인할 수 있다.

## 실행 방법

EasyDep API 서버를 먼저 실행한 뒤 다음 명령을 사용한다. 한글 요구사항과 JSON 출력을
안전하게 처리하려면 Windows에서도 `-X utf8`을 붙인다.

```powershell
python -X utf8 -m evaluation.easydep.product `
  --base-url http://127.0.0.1:8000 `
  --message "주문과 결제를 처리하는 서비스를 만들어 주세요." `
  --stop-after testing `
  --output C:\temp\easydep-product-run.json
```

긴 요구사항은 UTF-8 파일로 전달할 수 있다.

```powershell
python -X utf8 -m evaluation.easydep.product `
  --message-file C:\temp\requirements.txt `
  --output C:\temp\easydep-product-run.json
```

`--stop-after`에는 `requirements`, `design`, `implementation`, `testing` 중 하나를 넣는다.
기본값은 `testing`이다.

## 의도적으로 포함하지 않은 기능

이 실행기는 다음 기능을 제공하지 않는다.

- 요구사항 묶음과 반복 횟수를 관리하는 profile
- 별도의 holdout 입력 잠금
- 실행 설정을 비교하는 manifest와 자동 재개
- LLM 이벤트를 재귀적으로 분석하는 계측
- 성공률, p50, p95 같은 통계 집계
- 산출물 파일별 digest 재검증

필요하면 실행기가 저장한 원시 Workspace·이벤트·산출물 JSON을 별도 분석 프로그램에
입력한다. HTTP 실행 경로와 평가 기준을 분리하면 제품 사용 흐름이 바뀌어도 수정할 곳이
작아진다.

## 파일 구성

- `product_scenario.py`: 공개 HTTP transport, 프론트엔드와 같은 자동 동작, 실행 loop
- `product/cli.py`: 요구사항 한 건을 실행하고 결과 JSON을 저장하는 명령행 도구
- `tests/test_product_scenario_runner.py`: 자동 동작과 공개 API 실행 순서 검증
- `tests/test_product_evaluation.py`: 실제 URL 모양과 CLI 출력 검증

다른 평가 디렉터리의 요구사항·클라우드 리소스 자료는 필요할 때 사람이 선택해 이 실행기의
`--message-file`에 전달한다. 실행기 자체가 자료의 공개 범위나 반복 정책을 관리하지 않는다.
