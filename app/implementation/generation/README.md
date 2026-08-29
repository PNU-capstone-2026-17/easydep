# 구현 초기 파일 생성

이 디렉터리는 설계 단계에서 확정한 모델을, 구현 작업이 시작할 수 있는 파일 묶음으로
바꾼다. 여기서 만드는 코드는 완성된 업무 기능이 아니다. 클래스 이름, 필드, 메서드 인자와
반환 타입처럼 이미 설계에 기록된 내용만 옮기고, 실제 업무 처리는 뒤의 구현 작업이 채운다.

## Java 스캐폴더

`java_scaffold.py`는 PlantUML 문자열을 다시 읽지 않는다. `JavaScaffoldInput`이 다음 값을 먼저
검증한 다음 `render_java_scaffold()`가 Java 소스를 만든다.

- `bceModel`: Boundary, Control, Entity, 값 객체와 enum이 들어 있는 `BCEModel` JSON
- `sequenceModel`: 저장된 시퀀스 모델 JSON
- `apiModel`: 저장된 API 모델 JSON
- `erdBceModel`: ERD 단계에서 정리한 Entity 모델 JSON(선택 사항)
- `basePackage`, `javaVersion`, `applicationName`: Java 프로젝트의 기본 설정

반환값은 `dict[str, str]`이다. 키는 Java source root를 기준으로 한 상대 경로이고, 값은 UTF-8로
저장할 Java 소스다. 예를 들어 기본 패키지가 `com.example.orders`라면
`com/example/orders/bce/Order.java` 같은 키가 만들어진다. 호출하는 쪽에서 이 경로 앞에
`application/src/main/java`를 붙여 파일을 저장한다.

생성 결과는 다음 역할을 갖는다.

- 값 객체는 Java `record`, enum은 Java `enum`으로 만든다.
- Boundary와 Control은 메서드 모양을 선언하는 `interface`로 만든다.
- Entity는 설계에 있는 필드와 메서드를 가진 `class`로 만든다.
- 내용이 아직 없는 Entity 메서드는 컴파일할 수 있는 기본 반환만 넣는다. 메서드 동작을
  스캐폴더가 추측하지 않는다.
- 같은 typed 입력을 두 번 주면 파일 경로, 파일 순서와 내용이 모두 같아야 한다.

## 타입 이름 처리

설계 모델에는 사람이 자주 쓰는 `integer`, `decimal`, `datetime`, `list<T>`, `optional<T>` 같은
표현이 들어올 수 있다. 스캐폴더는 이를 각각 Java의 `int`, `BigDecimal`, `OffsetDateTime`,
`List<T>`, `Optional<T>`로 바꾸고 필요한 표준 라이브러리 import를 추가한다. 알 수 없는 이름은
업무 도메인의 사용자 정의 타입일 수 있으므로 임의로 다른 타입으로 바꾸지 않는다.

Java에서 사용할 수 없는 패키지나 타입 이름은 파일을 일부 만든 뒤 실패시키지 않고 입력 검증
단계에서 바로 거부한다. 따라서 호출자는 `ValidationError`를 사용자에게 입력 오류로 설명할 수
있다.

## 이 디렉터리에서 하지 않는 일

- PlantUML 분석
- 업무 로직 추측
- 생성 파일을 디스크에 직접 저장
- Gradle 실행이나 Java 컴파일
- React/TypeScript 화면 구현

파일 저장과 빌드는 구현 실행 흐름이 담당한다. 프론트엔드의 고정 초기 파일은
`frontend_scaffold.py`, 이후 프론트엔드 생성 작업은 `frontend.py`에서 다룬다.
