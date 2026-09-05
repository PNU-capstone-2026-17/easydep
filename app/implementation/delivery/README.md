# Implementation delivery

`app.implementation.delivery`는 이미 선택된 ResourcePlan과 provider primitive를
실행 가능한 Docker·Terraform/OpenTofu 산출물로 렌더링하고, 배포 전 연결 관계를
검증한다. 클라우드 공급자 선택이나 요구사항 해석은 담당하지 않는다.

## 계약

- **입력:** schema-version이 있는 ResourcePlan, provider별 primitive와 binding,
  배포 설정 및 필요한 application contract.
- **출력:** provider별 IaC 텍스트/파일, delivery bundle, OpenTofu 검사 결과와
  검증 결과. 선택된 ResourcePlan이 있으면 `application/deployment/tofu/`에 OpenTofu,
  cloud-init 예시, Compose, 비밀값 없는 env 예시와 대화형 `easydep.ps1`도 함께
  만든다. 렌더러는 입력의 선택을 그대로 실현하고 새 리소스 타입을 추론하지
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
생성된 README에 따라 CSP에 로그인한 뒤 `.\easydep.ps1` 하나를 실행한다. 사용자는
`배포/재개`와 `삭제`만 고르며, 환경 검사, 누락 입력 질문, registry 준비, image push,
plan 확인과 health 검사는 스크립트 내부에서 순서대로 실행된다. registry를 먼저 만든 뒤
정확한 image digest를 본 plan에 전달하므로 "registry가 아직 없어서 image를 push할 수
없는" 순환이 생기지 않는다.

사설 배포는 공개 health URL이 없으므로 내부 네트워크에서 별도로 확인해야 한다. 정적
검사는 실제 자원을 만들지 않으며, 실제 배포 성공과 별도로 기록한다.
