# EasyDep 문서 안내

사람이 계속 갱신하는 기준 문서는 세 개다. 과거 계획·상세 보고·대체된 설계는
[보관소](archive/README.md)에 둔다.

| 순서 | 기준 문서 | 내용 |
|---:|---|---|
| 1 | [현재 시스템 상태](current-system-status.md) | 4단계 구조, 구현됨·미구현, 실행 경계와 다음 작업 |
| 2 | [배포 다이어그램과 리소스 의존성 통합 기준](logical-deployment-topology-decisions.md) | 범위, 토폴로지, ResourcePlan, 멀티 AZ·VM 그룹·LB, CSP 생성 의존성, 앱·guest 바인딩, 셋업과 검증 |
| 3 | [비교평가 계획과 현재 결과](comparison-experiment-plan.md) | E1·E2·D1, 기준군, 공통 gate, 실행 규모와 주장 한계 |

[연구 배경](research.md)은 이미 승인된 진실 원천으로 보존하며 수정하지 않는다.

코드와 함께 갱신되는 실행 상세는 문서 수에 포함하지 않는다.

- [클래스·시퀀스 설계 생성 로직](class-design-pipeline.md): 현재 생성 계약과 단계별 책임
- [상호작용 설계 개선과 LLM 호출 최적화](interaction-design-improvements.md): 남은 개선,
  호출 운영 원칙과 측정 항목
- `app/core/orchestration/README.md`: 오케스트레이터 명령과 provider 계약
- `evaluation/README.md`: 평가 실행 명령
- `evaluation/experiment-contract.md`: 실패·검열 분류
- FastAPI `/docs`: 현재 HTTP API 계약

완료된 E1~E3 및 관리형 L4 실험 결과는 [보관 문서](archive/README.md)에서 확인한다.

## 유지 원칙

- 현재 구현 상태와 다음 작업은 상태 문서에만 기록한다.
- 배포 모델·CSP 관계·검증 규칙은 토폴로지 문서에만 기록한다.
- 사례·비교군·측정 결과와 주장 한계는 평가 문서에만 기록한다.
- 특정 시점 보고서와 완료된 결정은 `archive/`로 이동하고 다시 갱신하지 않는다.
- 사람용 문서는 한국어로 쓰고 시스템 식별자와 필드 이름만 영어로 유지한다.
