# Implementation delivery

`app.implementation.delivery`는 이미 선택된 ResourcePlan과 provider primitive를
실행 가능한 Docker·Terraform/OpenTofu 산출물로 렌더링하고, 배포 전 연결 관계를
검증한다. 클라우드 공급자 선택이나 요구사항 해석은 담당하지 않는다.

## 계약

- **입력:** schema-version이 있는 ResourcePlan, provider별 primitive와 binding,
  배포 설정 및 필요한 application contract.
- **출력:** provider별 IaC 텍스트/파일, delivery bundle, OpenTofu 검사 결과와
  검증 결과. 선택된 ResourcePlan이 있으면 `application/deployment/tofu/`에 OpenTofu,
  cloud-init 예시, Compose, 비밀값 없는 env 예시와 PowerShell/POSIX 실행 script도
  함께 만든다. 렌더러는 입력의 선택을 그대로 실현하고 새 리소스 타입을 추론하지
  않는다.
- **부수효과:** 순수 renderer/validator는 메모리에서 동작한다. delivery CLI가
  명시적으로 호출될 때만 작업 디렉터리와 산출물을 쓰고 OpenTofu를
  별도 프로세스로 실행한다.
- **사용하면 안 되는 import:** `app.core` 레거시 경로, 요구사항 agent의 실행 상태, LLM 호출,
  설계 서비스 내부 모듈에 의존하지 않는다. 선택되지 않은 provider나 숨은 기본값을
  산출물에 추가하지 않는다.
- **실패 조건:** plan schema/primitive가 없거나 연결 정보가 중복·불일치하고,
  provider가 지원되지 않거나 생성된 HCL·cloud-init·Compose·실행 script가 올바르지
  않으면 진단과 함께 중단한다. 설치되지 않은 검사 도구는 `INCONCLUSIVE`로 구분하고,
  실제 도구 실패·시간 초과는 성공으로 포장하지 않는다.

배포 package는 EasyDep 서버가 `apply`하거나 비밀값을 보관하는 기능이 아니다. 사용자는
생성된 README 순서대로 CSP 인증, image push, plan·deploy·verify·destroy를 직접 실행한다.
