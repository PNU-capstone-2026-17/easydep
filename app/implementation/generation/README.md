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
- Entity 필드만 보고 생성자나 getter/setter를 자동으로 만들지 않는다. 접근 메서드도 클래스
  설계에 operation으로 적힌 경우에만 한 번 생성한다.
- 내용이 아직 없는 Entity 메서드는 컴파일할 수 있는 기본 반환만 넣는다. 메서드 동작을
  스캐폴더가 추측하지 않는다.
- 타입 변환이 끝난 Java 메서드 이름과 인자 타입이 같은 경우에는 중복 signature로 거부한다.
- 같은 typed 입력을 두 번 주면 파일 경로, 파일 순서와 내용이 모두 같아야 한다.

## 타입 이름 처리

설계 모델에는 사람이 자주 쓰는 타입 이름이 들어올 수 있다. 스캐폴더는 의미가 분명한 작은
목록만 Java 타입으로 바꾼다.

- `string`, `integer`, `boolean`, `decimal`은 `String`, `Integer`, `Boolean`, `BigDecimal`
  로 바꾼다.
- `bytes`, `byte[]`, `bytes[]`는 `byte[]`로 바꾼다.
- `List<T>`와 `Optional<T>`는 안쪽 타입에도 같은 규칙을 적용한다.
- 같은 `BCEModel`에 선언된 class, record와 enum 이름은 그대로 사용한다.

그 밖의 이름은 별도의 추론 규칙으로 맞히지 않는다. 우선 컴파일 가능한 `Object`를 쓰고 바로
위에 `TODO(EasyDep)` 주석으로 원래 설계 타입을 남긴다. 이 표식은 뒤의 구현 작업에 타입을
보완해야 한다는 사실과 원문을 전달한다.

Java에서 사용할 수 없는 패키지나 타입 이름은 파일을 일부 만든 뒤 실패시키지 않고 입력 검증
단계에서 바로 거부한다. 따라서 호출자는 `ValidationError`를 사용자에게 입력 오류로 설명할 수
있다.

## 이 디렉터리에서 하지 않는 일

- PlantUML 분석
- 업무 로직 추측
- 클래스 모델에 없는 생성자·getter/setter 추가
- 정규식으로 임시 Java 타입 파일 생성
- 생성 파일을 디스크에 직접 저장
- Gradle 실행이나 Java 컴파일
- React/TypeScript 화면 구현

파일 저장과 빌드는 구현 실행 흐름이 담당한다. 프론트엔드의 고정 초기 파일은
`frontend_scaffold.py`, 이후 프론트엔드 생성 작업은 `frontend.py`에서 다룬다.

## Spring 실행 설정

`orchestrator.py`는 코딩 에이전트에게 맡길 이유가 없는 실행 설정도 함께 만든다.

- 운영 DB 주소·계정·비밀번호는 `SPRING_DATASOURCE_*` 환경 변수에서 읽는다.
- test profile은 MySQL 호환 모드의 메모리 H2를 사용한다.
- health endpoint는 `/healthz`로 노출한다.
- OpenAPI 또는 승인된 요구사항에 인증·인가가 명시된 경우에만 Spring Security 의존성과
  기본 HTTP 보안 설정을 만든다. 운영 계정 값은 `SPRING_SECURITY_USER_*` 환경 변수로 받는다.

이 설정으로 정상 기동하면 wiring 코딩 에이전트는 호출하지 않는다. 실제 build나 HTTP 검사에서
Bean 연결 오류가 확인된 경우에만 수리 작업이 활성화된다.

## Persistence 골격

`persistence_scaffold.py`는 `erdBceModel`에 확정된 Entity마다 다음 파일을 바로 만든다.

- JPA Entity: ERD 필드, 식별자, 기본 생성자와 필드 접근 메서드
- Spring Data Repository: Entity와 식별자 타입이 정해진 `JpaRepository`
- Flyway migration: 같은 필드와 식별자를 사용하는 최초 테이블

이 파일들은 같은 typed 모델에서 언제나 똑같이 만들어지므로 별도의 OpenHands 작업을 사용하지
않는다. null 허용 여부와 외래 키 소유자처럼 ERD에 없는 내용은 추측하지 않는다. 값 객체와
목록은 JSON column에 원래 타입 그대로 저장하고, 여러 식별자가 있으면 JPA 복합 키 파일도 함께
만든다. BCE Entity와 JPA Entity 사이에 필드 손실이 생기는 범용 mapper는 생성하지 않는다.

## HTTP Controller 골격

OpenAPI Generator가 만든 Java interface의 경로, annotation과 method signature는 그대로
사용한다. 그 위에 `apiModel.control_binding`에 적힌 Control과 생성자 주입을 연결한다.

API schema와 BCE 값 객체의 필드 이름·타입이 모두 대응하는 경우에만 요청과 성공 응답의 변환
본문을 함께 만든다. `UUID`나 날짜처럼 표준 변환 방법이 있는 값도 이 범위에 포함한다. 다음
경우에는 컴파일 가능한 `EASYDEP_CONTROLLER_BODY_REQUIRED:HTTP메서드:경로` 표식을 남긴다.

- API가 요구하는 필드가 BCE 결과에 없는 경우
- 설계 타입이 `Object`로 남은 경우
- BCE Entity처럼 생성·조회 방법이 업무 구현에 따라 달라지는 경우
- typed 모델만으로는 의미 있는 결과 변환을 정할 수 없는 경우

표식이 있는 method는 해당 기능의 OpenHands 작업이 실제 요구사항과 테스트를 보면서 완성한다.
공유 Controller에 다른 기능의 표식이 함께 있어도 현재 작업은 자신에게 배정된 HTTP 메서드와
경로만 확인한다. 모든 기능 작업이 끝나면 완료 검사에서 전체 표식이 사라졌는지 한 번 더 본다.
생성기는 누락 값을 `UNKNOWN`, `null` 또는 임의 ID로 채우지 않는다. HTTP Controller는 명시된
Control을 직접 호출하므로 API에만 존재하는 path/query 값도 중간 Boundary 객체에서 잃지 않는다.
Boundary interface 자체는 BCE 설계 계약으로 계속 생성하지만 별도의 중복 adapter는 만들지 않는다.
