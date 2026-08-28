# 클래스 설계 코드 규칙

이 패키지는 저장되는 계약을 둘러싼 타입 경계로 클래스 설계를 다룬다. 아래 규칙은 클래스
인벤토리, 연산, 협업, 서비스, 시퀀스 투영을 서로 독립적으로 테스트할 수 있게 한다.

## 경계

- 원시 유스케이스 입력은 `build_scenario_index`로 한 번만 정규화한다. 클래스 설계 내부
  단계는 중첩된 원시 딕셔너리 대신 변경할 수 없는 `ScenarioIndex`를 받는다.
- `class_diagram.inventory`는 전역 BCE 인벤토리를 소유한다. 클래스 다이어그램의 연산은
  실행 조각 계약을, 협업은 호출 순서와 값의 출처를 소유하며, `class_diagram.service`는
  공개 조율 경계다. `sequence_diagram.projection`은 수락된 모델의 결정론적 소비자다.
- 구현 도우미는 소유자 안에서 비공개로 유지한다. 다른 모듈과 테스트는 소유자의 공개 함수,
  타입 스키마 또는 서비스 경계를 사용한다.

## 타입 모델과 직렬화

- 저장소나 어댑터 경계마다 클래스 다이어그램 산출물은 `BCEModel`, 시퀀스 산출물은
  `SequenceCollection`으로 검증한다.
- 클래스 모델 JSON 계약(`Classes`, `DataTypes`, `Collaborations`, `operationId`)에는
  `model_dump(by_alias=True)`를 사용한다. 디코딩한 JSON은 렌더러나 하류 어댑터에 넘기기
  전에 다시 검증한다.
- 시퀀스 JSON은 `SequenceCollection`의 필드 이름을 따른다. PlantUML은 모델에서 만든
  표시 형식이며 수정의 기준 데이터로 사용하지 않는다.
- 투영기에 호환성용 재구성을 추가하지 않는다. 레거시 데이터는 명시적인 로드 경계에서
  처리하고, 이후에는 정규화된 타입 모델만 전달한다.

## 체크포인트와 테스트

- 체크포인트에는 구조화된 클래스·시퀀스 모델을 저장하고, 그 모델에서 렌더 산출물을 만든다.
  재개 경로는 수락된 모델을 보존하며 누락되었거나 유효하지 않은 수정 대상만 다시 검증한다.
- 계약 형태, 왕복 직렬화, 식별자·참조 무결성, 결정론적 시퀀스 투영을 테스트한다. 프롬프트
  문구나 비공개 도우미 이름을 단정하지 말고 작은 `ScenarioIndex`, `BCEModel`,
  `SequenceCollection` 값으로 테스트한다.

## 문서와 주석

- `services/README.md`는 전체 설계 서비스 지도를, 각 산출물 README는 그 디렉터리의 파일,
  입출력, LLM 호출, 검증·repair와 실패 조건을 설명한다.
- README의 JSON은 계약을 보여 주는 작은 합성 예제다. system prompt 전문은 복제하지 않고
  실제 상수와 응답 Pydantic schema를 기준 구현으로 연결한다.
- 모듈 docstring은 책임, 입력, 출력, 부작용과 사용하면 안 되는 import를 설명한다. 공개 함수는
  Google-style `Args`, `Returns`, `Raises`, `Notes`를 사용한다.
- 주요 함수 내부에는 후보 축소 → 외부 호출 → 정규화 → 검사 → 국소 repair의 단계 전환을
  주석으로 표시한다. 대입이나 반복문의 문법을 그대로 읽어 주는 주석은 남기지 않는다.
- provenance, 실행 순서, include/extend 삽입처럼 코드만으로 이유가 드러나지 않는 규칙에는
  정상·실패 예제를 둔다.
- prompt, schema, operation 이름, validation rule 또는 repair 예산을 바꾸면 같은 커밋에서
  해당 README를 갱신한다.
