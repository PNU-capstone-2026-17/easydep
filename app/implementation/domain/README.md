# 구현 도메인 모델

이 디렉터리는 구현 단계 여러 모듈이 함께 읽는 작은 Python 타입을 둔다.

## 입력과 출력

- `JobSpec`은 한 구현 Job에 고정된 typed 설계 파일 경로와 실행 설정을 담는다.
- `ImplementationIR`은 `bceModel`, `apiModel`, `erdBceModel`에서 구현에 필요한 클래스,
  endpoint, persistence 대상과 대표 HTTP 시나리오만 골라 담는다.
- `CommandEvidence`는 실제 build·test 명령과 종료 코드, 출력과 걸린 시간을 담는다.
- `artifact_layout.py`는 DB에 종류별로 저장된 파일을 실행 가능한 앱 폴더로 합칠 때의
  경로만 정의한다. Testing 임시 폴더와 사용자가 받는 ZIP이 이 규칙을 함께 사용한다.

`implementation_ir.py`는 PlantUML이나 OpenAPI 표시 문자열을 다시 해석하지 않는다. 표시
형식이 바뀌어도 저장된 typed 설계가 같으면 같은 구현 계획을 만든다.

## 이 디렉터리에서 하지 않는 일

- 파일 생성과 source 수정
- LLM 또는 외부 도구 호출
- workflow 상태 변경
- 화면용 다이어그램 렌더링

입력 JSON을 읽을 수 없거나 필수 class·endpoint가 없으면 빈 내용을 추측해서 채우지 않고,
상위 검증 단계가 명확한 오류를 반환하도록 한다.
