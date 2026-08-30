# 설계 공통 도구

이 디렉터리는 클래스·시퀀스·ERD·배포 설계가 함께 사용하는 작은 변환 도구를 모은다. 특정
다이어그램에만 적용되는 생성 규칙은 각 다이어그램 디렉터리에 두고, 여기에는 PlantUML 실행처럼
여러 단계가 똑같이 필요로 하는 기능만 둔다.

## PlantUML 이미지가 만들어지는 흐름

1. 설계 단계가 구조화된 모델을 PlantUML 문자열로 바꾼다.
2. 산출물 저장소가 모델을 MySQL에 저장한다.
3. 저장 직후 `app/artifact_images.py`가 SVG와 PNG를 요청한다.
4. `plantuml.py`의 `PlantUmlRenderer`는 서버 시작 때 한 번 띄운 PicoWeb JVM에 요청한다.
5. 렌더된 bytes는 PlantUML 내용의 SHA-256과 이미지 형식으로 메모리에 보관된다.
6. 이미지 HTTP API는 앱·단계·화면별 cache에서 결과를 바로 반환한다.

브라우저가 그림을 요청할 때마다 Java나 Docker container를 새로 실행하지 않는다. 클래스 생성
중간 preview도 PlantUML이 만들어진 시점에 SVG를 준비한다.

## 주요 입력과 출력

- 입력: UTF-8 PlantUML 문자열과 `svg` 또는 `png` 형식
- 출력: 브라우저에 그대로 보낼 이미지 `bytes`
- 기준 데이터: MySQL에 저장된 구조화 모델이다. 이미지 cache는 기준 데이터가 아니다.

같은 PlantUML 내용은 앱이나 화면이 달라도 같은 이미지 cache를 사용한다. 산출물에 피드백이
적용되면 route cache를 먼저 비우고 새 PlantUML로 교체한다.

## process와 파일

FastAPI가 시작될 때 로컬 loopback 주소에 PlantUML PicoWeb JVM 하나를 실행하고, FastAPI가
종료될 때 그 JVM만 종료한다. 포트는 사용 가능한 값을 자동으로 고르며 외부 네트워크에는
공개하지 않는다.

JAR 검색 순서는 다음과 같다.

1. `PLANTUML_JAR` 환경변수
2. 개발자 설치 위치 `~/.local/share/plantuml/plantuml.jar`
3. EasyDep Docker image의 `/opt/plantuml/plantuml.jar`

Docker image는 고정된 PlantUML image에서 JAR를 복사하고 Graphviz와 한글 font를 함께 설치한다.
개발·테스트 환경에 JAR가 없을 때만 기존 단발 Docker 명령을 호환 경로로 사용한다.

## 실패와 재개

이미지 렌더링 실패는 이미 저장한 구조화 모델을 되돌리지 않는다. 원인은 server log에 남고,
다음 이미지 요청에서 해당 stage만 다시 cache에 준비한다. JVM이 예상치 못하게 종료되면 현재
호출은 실패로 반환하고 다음 호출에서 새 JVM을 시작한다.

처음 읽을 파일은 다음 두 개다.

- `plantuml.py`: PicoWeb process, PlantUML URL 변환과 내용별 이미지 cache
- `app/artifact_images.py`: 앱·stage·시퀀스 유스케이스별 route cache
