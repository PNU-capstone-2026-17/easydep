# API 명세 서비스

이 패키지는 승인된 유스케이스와 클래스 상호작용을 HTTP API와 OpenAPI 3.1 문서로 바꾼다.
LLM은 HTTP 설계에 필요한 선택만 제안하고, 이미 클래스 모델에 있는 실행 정보는 코드가
채운다. 저장되는 `api_spec_model`이 API 편집과 재개의 기준 데이터다.

## 처리 흐름

```text
유스케이스 명세 + BCEModel.Collaborations
  → LLM이 ApiSpecProposal 제안
  → 코드가 Control 연결·타입·응답·추적 정보 계산
  → ApiSpecModel 차단 검사
  → OpenAPI 3.1 JSON 생성
```

- `app.design.contracts.api_spec`: 작은 LLM 응답인 `ApiSpecProposal`과 저장 모델인
  `ApiSpecModel`의 공개 타입 계약
- `prompts.py`: 생성·수정 지침과 LLM 입력 조립
- `normalization.py`: 승인된 Boundary→Control 호출과 HTTP 제안을 결합
- `service.py`: 생성·수정 LLM 호출과 정규화 순서 관리
- `projection.py`: 승인 모델을 OpenAPI 3.1 JSON으로 변환

## LLM과 코드의 역할

LLM은 제공된 `interaction_id`마다 다음 HTTP 표현을 고른다.

- path와 HTTP method
- path/query/body 입력 위치
- operation ID와 설명
- 성공·실패 status와 HTTP 전용 schema

코드는 `BCEModel.Collaborations`와 Control operation 선언에서 다음 값을 계산한다.

- endpoint가 호출하는 Control 클래스와 메서드
- HTTP 입력과 Control parameter의 연결
- 빠진 요청 body schema와 그 필드 타입
- 성공 응답 타입과 배열 여부
- Boundary·Control 및 유스케이스 추적 정보
- status에 대응하는 결과 이름

따라서 LLM이 같은 연결 정보를 다시 추측하거나, 서로 모순되는 타입을 한 응답 안에 작성할
필요가 없다. 수정할 때도 저장 모델에서 코드 생성 필드를 뺀 `ApiSpecProposal`만 LLM에 보낸다.

## 입력과 출력

```python
generate_api_spec_model(
    scenario_text: str,
    bce_model: BCEModel,
) -> ApiSpecModel
```

시퀀스 다이어그램은 클래스의 승인된 `Collaborations`에서 코드로 만든 결과이므로 API 생성
입력으로 다시 받지 않는다. `build_openapi_from_model`은 같은 `ApiSpecModel`에서 항상 같은
OpenAPI JSON을 만든다. 하류의 구현 단계가 사용하는 `x-easydep-control`도 여기서 생성된다.

## 검사와 수리

graph adapter는 저장 모델의 타입을 확인한 뒤 `api_spec_findings`를 실제 차단 검사로 한 번만
실행한다. 별도의 관찰용 validator로 같은 규칙을 다시 검사하지 않는다. 문제가 있으면 기존
설계 수리 흐름이 현재 HTTP 제안과 수리 이력을 LLM에 전달한다. API 서비스 안에 별도 수리
루프는 두지 않는다.

## 의존 관계와 부작용

- LLM 호출은 `service.py`에서만 발생한다.
- 정규화와 OpenAPI 변환은 네트워크나 저장소를 사용하지 않는다.
- API 서비스는 graph state, artifact repository, requirements 내부 state를 import하지 않는다.
- PlantUML 문자열과 이전의 느슨한 API payload는 지원하지 않는다.

## 실패 조건

- 입력 BCE 모델이나 LLM proposal이 Pydantic 계약을 만족하지 않는다.
- proposal의 `interaction_id`가 승인된 Boundary→Control 상호작용에 없다.
- Control parameter에 대응하는 HTTP 입력을 하나로 정할 수 없다.
- schema 참조, path parameter, operation ID 또는 OpenAPI 결과가 유효하지 않다.
