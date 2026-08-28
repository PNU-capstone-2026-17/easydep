# Implementation planning

`app.implementation.planning`은 설계 산출물과 측정된 클라우드 정보를 구현 작업과
후보 선택에 필요한 결정으로 투영한다. 코드를 생성하거나 배포를 실행하는 계층은
아니다.

## 계약

- **입력:** typed design artifact/`JobSpec`, 요구사항의 ResourceSpec, provider별
  Cloud KB 후보와 선택적 부하·SLO 측정값.
- **출력:** `ImplementationTask`, frontend contract, provider별 VM 후보와
  `capacity-floor/v1` 결과. 측정이 부족하면 임의의 수치 대신 `deferred`와 필요한
  질문/근거를 반환한다.
- **부수효과:** 계획·선택 계산은 파일, 네트워크, subprocess, LLM을 호출하지 않는
  결정론적 메모리 작업이다. Cloud KB의 번들 데이터는 읽기 전용으로 사용한다.
- **사용하면 안 되는 import:** `app.core` 레거시 경로와 설계 서비스 내부, 배포 실행기,
  orchestration graph를 import하지 않는다. 근거 없는 최소 용량이나 provider 간
  리소스 ID 동일성을 추정하지 않는다.
- **실패 조건:** 필수 typed 입력/측정값이 없거나 SLO를 위반하면 명시적인
  `deferred` 결과를 낸다. 후보가 없거나 리소스 계약·작업 식별자가 잘못되면
  검증 오류를 반환하며 조용히 후보를 만들어내지 않는다.
