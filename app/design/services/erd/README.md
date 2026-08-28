# ERD 수정과 논리 모델 투영

이 패키지는 ERD 피드백으로 독립된 BCE 사본을 수정하고, 수락된 Entity·field·relationship을
논리 table·column·FK·junction으로 결정론적으로 투영한다. 저장 원본은
`erd_bce_classes`이며 `erd_puml`은 언제든 다시 만들 수 있는 표현이다. LLM은 BCE만 수정하고
논리 모델이나 PlantUML을 직접 작성하지 않는다.

## 처리 경계

```text
저장 JSON
  → graph adapter의 BCEModel 검증
  → revise_erd_model (피드백이 있을 때만 structured LLM 1회)
  → BCEModel
  → project_logical_model
       ├─ build_entity_tables: Entity·field → table·column·지연된 1NF 목록
       ├─ order_for_mapping: 상속 우선 순서와 사상 불가 상속
       └─ map_relationship: relationship → FK·junction·relation·Unmapped
  → {Tables, Relations, Unmapped}
  → render_logical_model
  → erd_puml
```

## 공개 입력과 출력

ERD 수정 서비스는 graph state나 PlantUML이 아니라 typed BCE를 받는다.

```python
revise_erd_model(
    current_bce: BCEModel,
    feedback: str,
    scenario_text: str = "",
    targets: set[str] | None = None,
    *,
    proposal_call: ErdProposalCall | None = None,
) -> BCEModel
```

- `current_bce`는 클래스 단계에서 깊은 복사해 `erd_bce_classes`로 저장한 독립 모델이다.
- 빈 feedback은 LLM을 호출하지 않고 같은 typed 객체를 반환한다.
- 수정 결과의 Entity field와 DataType field는 기존 Java field 표기로 정규화한다.
- `proposal_call`은 structured adapter protocol이며 테스트와 상위 adapter가 호출을 대체할 때만
  사용한다. 운영 기본값은 공통 structured LLM adapter다.

논리 모델의 canonical 입구는 다음과 같다.

```python
project_logical_model(bce_model: BCEModel) -> dict[str, Any]
```

출력 shape는 기존 계약을 유지한다.

```json
{
  "Tables": [
    {
      "name": "Order",
      "primaryKey": ["order_id"],
      "keyOrigin": "surrogate",
      "columns": [],
      "uniqueTogether": [],
      "origin": {"kind": "class", "className": "Order"}
    }
  ],
  "Relations": [],
  "Unmapped": []
}
```

graph adapter만 raw `erd_bce_classes`를 `BCEModel.model_validate`로 읽고
`model_dump(by_alias=True)`로 되돌린다. 따라서 저장 키 `Classes`, `DataTypes`,
`Relationships`, `Collaborations`와 `erd_puml` 문자열 shape는 바뀌지 않는다. 논리 모델의
`Tables`, `Relations`, `Unmapped`는 결정론 투영 결과이며 BCE 저장 JSON에 섞지 않는다.

## Table·field 사상

`table_mapping.build_entity_tables`는 Entity 선언 순서대로 기본 table과 column을 만든다.

- 자연키는 선언된 identifier와 field 순서를 유지한다.
- identifier가 없으면 대리키를 추가하고 `keyOrigin=surrogate`로 출처를 남긴다.
- Entity 타입 field는 FK를 추측하지 않고 relationship 단계에 맡긴다.
- 다중값 field는 부모 키가 상속으로 확정된 뒤 만들 수 있도록 지연 목록에 남긴다.
- 이름 충돌은 조용히 개명하거나 버리지 않고 기존 detector가 판정할 정보를 보존한다.

이 단계는 다중도, FK 위치, junction 이름이나 relationship symbol을 결정하지 않는다.

## Relationship·FK·junction 사상

`relationship_mapping.map_relationship`은 이미 만들어진 table 집합과 관계 하나를 받아 새
table, relation, Unmapped를 반환한다.

- 1:N은 N쪽에 FK를 두고 참조되는 끝의 하한으로 mandatory를 정한다.
- N:M은 두 FK를 복합 기본키로 가진 junction을 만든다.
- 1:1은 선택 쪽에 unique FK를 둔다.
- Composition과 상속은 식별 관계로 표현한다.
- FK는 참조 table과 참조 column을 모두 기록한다.
- 다중도 누락, Entity 간 Dependency, 중복 junction/relationship, 필수 참조 순환은 값을
  추측하지 않고 기존 사유와 입력 순서로 `Unmapped`에 남긴다.

`projection.project_logical_model`은 상속을 먼저 처리한 뒤 나머지 입력 관계 순서, junction
추가 순서, 마지막 1NF child 순서와 첫 relation 위치를 유지한다. 같은 `BCEModel`이면 dict의
배열 순서와 이름까지 같다.

## PlantUML 투영과 호환 facade

`plantuml.render_logical_model`은 논리 모델을 기존 `entity "N" as N { ... }` 형태로만 그린다.
`Unmapped`는 실제 관계인 것처럼 그리지 않는다. table과 relation 순서, 빈 줄, 1NF provenance
주석과 crow-foot symbol은 하류 implementation parser도 소비하므로 byte-level 계약이다.

- `mapping.build_logical_model(dict)`는 이전 dict 입력 호출자의 호환 facade다.
- `reviser.revise_erd_classes(dict, ...)`는 이전 dict 수정 호출자의 호환 facade다.
- `plantuml.generate_erd_from_bce_json(dict)`는 저장 체크포인트·repository 재투영 경로를
  유지한다.

호환 facade는 canonical service/projection을 호출하며 별도 사상 규칙이나 prompt를 소유하지
않는다.

## 기준선 산출물 provenance

`tests/fixtures/erd_projection_golden.json`과 `erd_projection_golden.puml`은 분리 후 구현에서
새로 만든 기대값이 아니다. 리팩터링 부모 기준점 `bd17fce`의 기존 `mapping.py`와
`plantuml.py` blob을 체크아웃 없이 직접 실행해 캡처하고, 현재 typed 경계와 호환 facade를
그 결과에 비교한다. 기준 입력과 산출물의 canonical SHA-256은 다음과 같다.

- BCE 입력: `5e3fbf20c0034a4da72c5a4ef9cad9ca055ee283465dde9a14440481bed9fe21`
- logical JSON: `c37ffe3469f93d5ee2c9c5839faa3e1d7e1dc74d6754dce330694a4c9afc9b3b`
- PlantUML: `0732c352bdd8b0d64e8c753f65f84853368613755bc839556cd0989047c3c6a2`

기준점과 현재 브랜치 사이에서 공통 ERD helper와 `inheritance.py`에는 차이가 없음을 함께
확인했다. 따라서 golden은 table·field 및 relationship 분리 전의 정렬, 이름, `Unmapped`,
PlantUML 문자열 계약을 고정한다.

## 부작용과 LLM 호출 계약

- 외부 LLM 호출은 non-empty feedback의 `service.revise_erd_model`에서만 발생한다.
- 수정 요청 한 번은 기존 operation, reasoning, completion cap과 공통 schema repair 범위를
  유지한 structured 호출 한 번이다.
- projection, table mapping, relationship mapping, inheritance, validation, PlantUML rendering은
  순수 함수이며 네트워크, 설정, 저장소, graph state를 읽거나 쓰지 않는다.
- checkpoint 저장, feedback repair 예산과 semantic finding 비교는 graph가 소유한다.

## 금지 의존성

- ERD 서비스는 `app.design.graphs`, `ArchitectureState`, artifact repository를 import하지 않는다.
- ERD 서비스는 requirements 내부 state나 implementation service를 import하지 않는다.
- table mapping은 relationship mapping을 import하거나 FK·junction을 만들지 않는다.
- relationship mapping은 LLM, prompt, graph, repository를 import하지 않는다.
- projection과 renderer는 LLM 수정 서비스를 역참조하지 않는다.
- 테스트는 prompt literal, private helper 또는 내부 f-string에 결합하지 않는다.

## 실패 조건

- raw 저장 JSON이나 LLM proposal이 `BCEModel` 계약을 만족하지 않으면 경계에서 실패한다.
- structured 호출·schema repair가 실패하면 기존 모델을 임의 dict나 빈 placeholder로 바꾸지
  않고 예외를 전달한다.
- Entity 부재, identifier 불일치, table 이름 충돌과 `Unmapped`는 detector finding으로
  드러내며 projection이 임의로 이름·다중도·FK를 발명하지 않는다.
- 상속 순환·다중 상속과 필수 FK 순환은 무한 반복하거나 한 관계를 조용히 선택하지 않는다.
- renderer가 빈 논리 모델을 받으면 빈 문자열을 반환하며, 모델 결함은 semantic gate가
  `erd.has-entity` 등 기존 rule로 소유한다.
