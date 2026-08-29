# Implementation delivery

`app.implementation.delivery`는 이미 선택된 ResourcePlan과 provider primitive를
실행 가능한 Docker·Terraform/OpenTofu 산출물로 렌더링하고, 배포 전 연결 관계를
검증한다. 클라우드 공급자 선택이나 요구사항 해석은 담당하지 않는다.

## 계약

- **입력:** schema-version이 있는 ResourcePlan, provider별 primitive와 binding,
  배포 설정 및 필요한 application contract.
- **출력:** provider별 IaC 텍스트/파일, delivery bundle, Terraform 검사 결과와
  검증 결과. 렌더러는 입력의 선택을 그대로 실현하고 새 리소스 타입을 추론하지
  않는다.
- **부수효과:** 순수 renderer/validator는 메모리에서 동작한다. delivery CLI가
  명시적으로 호출될 때만 작업 디렉터리와 산출물을 쓰고 Terraform/OpenTofu를
  별도 프로세스로 실행한다.
- **사용하면 안 되는 import:** `app.core` 레거시 경로, 요구사항 agent의 실행 상태, LLM 호출,
  설계 서비스 내부 모듈에 의존하지 않는다. 선택되지 않은 provider나 숨은 기본값을
  산출물에 추가하지 않는다.
- **실패 조건:** plan schema/primitive가 없거나 연결 정보가 중복·불일치하고,
  provider가 지원되지 않거나 생성된 HCL을 검사할 수 없으면 진단과 함께
  중단한다. 도구 실행 실패·시간 초과도 성공으로 포장하지 않는다.
