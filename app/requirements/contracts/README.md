# 요구사항 contract 경계

`app.requirements.contracts`는 HTTP 입력과 단계 상태가 공유하는 typed shape만
소유한다. 요청을 실행하거나 단계 순서를 결정하지 않는다.

## 입력

- `request.py`는 신규 분석, 세션 재개, 구조화 피드백, 클라우드·자원
  제약 입력을 Pydantic 모델로 받는다.
- `state.py`는 각 단계가 읽고 추가하는 `AgentState`와 item `TypedDict`를
  정의한다.
- 단계 함수의 실행 입출력 계약은 상류 없는
  `app.requirements.common.state_contract`의 `StateContract`를 사용한다.

## 출력

- `AnalyzeRequest` 및 중첩 input model의 검증된 값
- `AgentState`, `RequirementItem`, `ActorItem`, `UseCaseItem`, `UseCaseSpecItem`의
  정적 타입 계약
- 기존 `app.requirements.schemas` 및 `app.requirements.agent.state` import가
  재노출하는 동일한 class 객체

## 부수효과

모델 검증·정규화 외의 부수효과가 없다. LLM·네트워크·파일·세션·
repository를 호출하지 않는다. `TypedDict`는 실행 검증을 하지 않으므로
단계 호출 시점의 키 존재 검사는 `StateContract`가 담당한다.

## 사용하면 안 되는 import

- agent 단계 구현과 graph·runner·supervisor
- runtime structured LLM·telemetry
- HTTP endpoint·session store·artifact repository
- design·implementation·workspace 반대 참조

## 실패 조건

- Pydantic 요청 필드의 타입·값 제약을 만족하지 못하면 validation
  error를 발생시킨다.
- 단계가 선언한 상류 키가 없으면 `MissingUpstreamState`, 선언한
  출력 키를 내지 않으면 `BrokenStageOutput`으로 즉시 실패한다.
- 빈 값과 키 부재를 같게 보지 않는다. 빈 산출물은 통과할 수 있지만
  키 부재는 배선 오류다.
