# Requirements resources

`app.requirements.resources`는 사용자가 제공하는 `RESOURCE_SPEC`의 모양과 질문,
application/cloud 일관성 계약을 소유한다. 질문을 생성하는 에이전트나 클라우드
지식베이스의 사실을 소유하지 않는다.

## 계약

- **입력:** JSON Schema에 맞춰 작성된 ResourceSpec 사전, application/runtime 사실,
  cloud capability와 binding, CSP와 workload 맥락,
  `input_registry`가 선언한 질문·근거·소비자 목록.
- **출력:** 스키마 필드·타입·enum 조회, 누락/권고/맥락 질문(`Ask`), gap 목록,
  그리고 빈 목록이 성공을 뜻하는 결정론적 검증 오류 목록.
- **부수효과:** 순수 조회와 검증만 수행한다. 캐시는 스키마를 반복해서 읽지 않기
  위한 프로세스 내부 캐시일 뿐 파일·네트워크·LLM 호출을 만들지 않는다.
- **금지 의존성:** `app.design`·`app.implementation`·requirements agent의 상태,
  프롬프트, 실행기를 import하지 않는다. 타입과 필수 여부를 이 README나 Python에
  복제하지 말고 `resource_spec.schema.json`을 단일 원천으로 사용한다.
- **실패 조건:** 알 수 없는 필드/타입, 필수값 누락, 잘못된 enum·object 모양,
  소비자나 근거가 없는 질문, 또는 스키마와 레지스트리의 불일치가 발견되면
  호출자가 고칠 수 있는 오류 목록을 반환한다.

`input_registry`는 무엇을 물을지와 왜 필요한지를, `cloud_contract`는 값의 기계적
모양을 담당한다. 둘을 합치거나 에이전트 단계에서 별도 계약을 만들지 않는다.

`input_registry`의 일부 `Basis(CODE, "app/core/...")` 값은 기존 저장 JSON과 checkpoint의
감사 식별자를 보존하기 위한 레거시 provenance ID다. 실행 가능한 import나 현재 파일 링크가
아니며, canonical Python 경로를 이 문자열로부터 유도하지 않는다.
