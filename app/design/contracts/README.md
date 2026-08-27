# Design contracts

이 패키지는 downstream 단계가 사용할 수 있는 설계 산출물의 공개 검증 경계다.

- **입력:** graph adapter에서 검증·저장된 typed 설계 JSON.
- **출력:** 검증 성공, 결정론적으로 구성한 ResourcePlan/runtime binding/PUML 투영,
  또는 계약 위반을 설명하는 `ValueError`.
- **부수효과:** 파일·네트워크·LLM·저장소를 사용하지 않는다.
- **금지 의존성:** orchestration, repository, implementation 내부를 import하지 않는다.
- **실패 조건:** schema version, ID·참조·digest 또는 provider 구조가 설계 계약과 다르면
  즉시 실패한다. downstream을 위해 값을 보정하거나 추정하지 않는다.
