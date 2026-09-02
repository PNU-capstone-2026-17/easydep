# 설계 공개 계약

이 패키지는 downstream 단계가 사용할 수 있는 설계 산출물의 공개 검증 경계다.

- **입력:** graph adapter에서 검증·저장된 typed 설계 JSON.
- **출력:** 검증 성공, 결정론적으로 구성한 ResourcePlan/runtime binding/PUML 투영,
  또는 계약 위반을 설명하는 `ValueError`.
- **부수효과:** 파일·네트워크·LLM·저장소를 사용하지 않는다.
- **사용하면 안 되는 import:** orchestration, repository, implementation 내부를 import하지 않는다.
- **실패 조건:** schema version, ID·참조·digest 또는 provider 구조가 설계 계약과 다르면
  즉시 실패한다. downstream을 위해 값을 보정하거나 추정하지 않는다.

`api_spec.py`는 API 제안과 저장 endpoint의 Pydantic 모델을 소유한다. API 설계 서비스와
구현 단계가 같은 `ApiSpecModel`을 사용하므로, 구현 코드가 설계 서비스 내부 모듈을 import하지
않는다. `erd.py`는 검증된 BCE 모델을 논리 데이터 모델로 바꾸는 순수 함수를 공개한다.
`deployment.py`는 배포 bundle과 runtime binding의 공개 함수를 제공한다.
