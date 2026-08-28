# API 명세 서비스

이 패키지는 수락된 유스케이스·BCE·시퀀스 계약을 얕은 API 모델로 제안하고, 그 모델을
결정론적으로 정규화·검증한 뒤 OpenAPI 3.1 문서로 투영한다. LLM이 OpenAPI 문서를 직접
작성하거나 수정하지 않으며, 저장되는 `api_spec_model`이 의미의 원본이다.

## 처리 경계

```text
유스케이스 명세 + BCEModel + SequenceCollection
  → ApiSpecModel proposal
  → deterministic normalization
  → normalized ApiSpecModel
  → graph adapter의 observational model validation + 기존 semantic gate
  → accepted ApiSpecModel
  → deterministic OpenAPI 3.1 projection
```

- `models.py`: 저장 JSON과 같은 `ApiSpecModel` 계열 typed 계약
- `prompts.py`: 생성·수정용 LLM 지침과 메시지 조립
- `normalization.py`: BCE parameter·return 계약에 따른 기계적 누락 보완
- `validation.py`: schema, 참조, HTTP-to-Control binding의 결정론 검사
- `service.py`: typed proposal·revision과 normalize 조율
- `projection.py`: 수락 모델을 OpenAPI 3.1 JSON으로 투영
- `legacy.py`: 이전 PlantUML 입력 호출자를 typed 입력으로 연결하는 격리 adapter
- `extractor.py`, `reviser.py`, `openapi.py`: 기존 import 경로를 유지하는 호환 facade

## 입력과 출력

canonical 생성 경계는 다음 세 입력만 받는다.

```python
generate_api_spec_model(
    scenario_text: str,
    bce_model: BCEModel,
    sequence_model: SequenceCollection,
) -> ApiSpecModel
```

- `scenario_text`는 유스케이스와 행위 근거다.
- `BCEModel`은 Boundary·Control·Entity, 정확한 operation parameter와 return type의 원본이다.
- `SequenceCollection`은 actor 진입과 Boundary→Control 호출, 유스케이스별 순서의 원본이다.

수정 경계도 현재 `ApiSpecModel`과 동일한 typed 설계 문맥을 받는다. 출력은 정규화된
`ApiSpecModel`이다. graph adapter만 `model_validate`로 체크포인트의 raw dict를 읽고,
typed validation report를 관측한 뒤 `model_dump`로 기존 `Endpoints`, `Schemas`, snake_case
필드 shape를 저장한다. repair를 결정하는 finding 집합은 기존 semantic detector와 동일하다.

`projection.py`의 `build_openapi_from_model`은 같은 모델에 항상 같은 JSON을 반환한다. 외부
계약인 `openapi`, `info`, `paths`, `components.schemas`와 `x-easydep-control` shape를 유지하며,
RTM·deployment·implementation은 이 결과와 저장 모델을 기존 방식으로 소비한다.

## 제안·정규화·검증·투영

1. proposal은 endpoint, schema, traceability와 Control binding만 구조화 응답으로 받는다.
2. normalization은 선택된 Control의 정확한 parameter·return 계약으로 query/body/response의
   기계적 누락을 보완한다. 새 endpoint나 유스케이스 의미를 발명하지 않는다.
3. validation은 path parameter, schema 참조, operation ID, Control argument와 sequence
   call의 일치를 검사하고 모델을 바꾸지 않는 report를 반환한다.
4. graph adapter는 이 report를 observational check로 실행한다. typed 규칙이 기존 detector의
   유스케이스 범위·분해 경로 판정보다 엄격해 호출 수를 늘리지 않도록 repair finding에는
   합치지 않는다. 기존 semantic finding이 있으면 기존 bounded repair가 API revision
   service만 다시 호출한다. 별도 repair loop를 추가하거나 BCE·sequence를 이 패키지에서
   수정하지 않는다.
5. graph가 수락한 모델만 OpenAPI로 투영한다. projection 실패를 LLM 출력으로 덮지 않는다.

## Legacy PlantUML adapter

이전 `extract_api_spec_model(scenario_text, class_puml, sequence_puml, ...)` 호출은 호환 facade와
`legacy.py`에만 남는다. PlantUML parsing은 이전 체크포인트·호출자를 위한 fallback이며,
canonical `service.py`, `normalization.py`, `validation.py`가 사용하는 기본 입력이 아니다.
typed 모델이 함께 제공되면 그것을 계약 원본으로 사용하고 PlantUML에서 parameter나 return
type을 다시 추론하지 않는다.

## 부작용

- LLM 호출은 `service.py`의 proposal/revision에서만 발생한다.
- normalization, validation, projection은 순수 함수이며 네트워크, 저장소, graph state,
  전역 체크포인트를 읽거나 쓰지 않는다.
- persistence, raw state 검증, observational validation 실행, JSON dump, stage checkpoint와
  feedback cascade는 graph adapter가 소유한다.

## 금지 의존성

- API 서비스는 `app.design.graphs`, artifact repository, `ArchitectureState`를 import하지 않는다.
- API 서비스는 requirements 내부 state나 implementation service를 import하지 않는다.
- `service.py`는 클래스·시퀀스 PlantUML 문자열을 canonical 설계 입력으로 받지 않는다.
- projection은 LLM, prompt, 설정 또는 legacy parser에 의존하지 않는다.
- downstream 소비자를 위해 OpenAPI나 저장 JSON에 새 private 필드를 노출하지 않는다.

## 실패 조건

- 입력 `BCEModel`, `SequenceCollection` 또는 proposal이 Pydantic 계약을 만족하지 않으면 실패한다.
- 존재하지 않는 schema, path parameter 누락·중복 operation ID, 빈 operation 집합은 수락하지 않는다.
- endpoint가 실제 Control operation과 모든 parameter를 연결하지 못하거나 sequence에서 호출을
  찾지 못하면 typed report에 남긴다. 이 report는 현재 관측 전용이며 repair 범위를 넓히지 않는다.
- graph의 기존 bounded repair 뒤에도 finding이 남으면 실패를 반환하며 빈 placeholder endpoint,
  fabricated class/use-case ID 또는 느슨한 `Object` 계약으로 통과시키지 않는다. API service는
  자체 repair loop를 추가해 호출 수나 repair 범위를 넓히지 않는다.
