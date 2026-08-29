# 처음 읽는 사람을 위한 코드 탐색 순서

EasyDep는 여러 단계와 비동기 작업을 포함하므로 한 파일에서 전체 흐름을 볼 수 없다. 아래
순서는 “사용자가 버튼을 눌렀을 때 어디로 가는가”를 기준으로 가장 짧은 경로를 제시한다.

## 1. 서버가 무엇을 연결하는지 본다

`server.py`에서 startup과 router 등록을 확인한다. 여기에는 업무 규칙이 없고 DB, worker와
각 HTTP 경계를 조립하는 코드만 있어야 한다.

## 2. UI 명령이 단계 호출로 바뀌는 과정을 본다

1. `frontend/src/lib/api.ts`
2. `app/workspace/api.py`
3. `app/workspace/service.py`
4. `app/workspace/repository.py`

`api.py`는 입력 검증, `service.py`는 action과 단계 연결, `repository.py`는 command/event
저장을 맡는다. 세 책임을 한 함수에서 처리하지 않는 것이 중요하다.

## 3. 한 단계만 골라 끝까지 따라간다

처음에는 요구사항 또는 설계 하나만 선택한다. 예를 들어 클래스 설계는 다음 순서다.

```text
app/design/service.py
  → app/design/graphs/design_graph.py
  → app/design/services/class_diagram/service.py
  → inventory.py → operations.py → collaboration.py
  → validation/ → plantuml.py
```

LLM에 보내는 payload, LLM proposal, normalize를 거친 값, validator finding, 모든 검사를
통과한 accepted model을 구분해서 본다. 모두 Python dict처럼 보여도 검증 수준이 다르다.

## 4. 저장과 재개를 마지막에 본다

정상 실행 흐름을 이해한 다음 `app/repositories`, `app/db`, 각 단계의 session과 checkpoint 코드를
본다. 저장 코드를 먼저 읽으면 업무 상태와 DB 행 상태를 혼동하기 쉽다.

## 주석을 읽고 쓰는 방법

좋은 주석은 다음 질문에 답한다.

- 왜 여기에서 검증해야 하는가?
- 왜 전체가 아니라 이 범위만 다시 실행하는가?
- 어떤 값이 외부 계약이어서 모양을 유지해야 하는가?
- 실패했을 때 어느 중간 저장 지점부터 이어서 실행할 수 있는가?

함수 이름을 한국어로 다시 읽어 주는 주석은 피한다. 대신 코드만 보고 알기 어려운 경계,
순서, 부작용과 선택 이유를 설명한다.
