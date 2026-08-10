# EasyDep 문서 안내

문서마다 역할을 하나만 부여한다. 같은 수치나 결론을 여러 문서에 복사하지 않고 아래 원문으로 연결한다.

| 우선순위 | 문서 | 역할 |
|---|---|---|
| 1 | [연구 배경](research.md) | 이미 승인된 연구 목적과 배경. 수정하지 않는다. |
| 2 | [현재 시스템 상태](current-system-status.md) | 현재 구현 범위, 검증된 기능, 미완료 작업의 기준점 |
| 3 | [2026-08-10 연구 결과](research-results-20260810.md) | 완료된 DepKB 효과 측정과 한계의 결과 원문 |
| 4 | [주장-증거 연결표](../evaluation/research_protocol/reports/research-claim-evidence-matrix-20260809.md) | 연구 주장별 근거 파일과 주장 한계 |
| 5 | [졸업과제 작업 기준](project-baseline.md) | 범위와 다음 작업 순서만 요약한 운영 기준 |

클라우드 지식이 없는 독자는 먼저 [클라우드 분석과 리소스 추천 안내](cloud-resource-guidance.md)를
읽으면 리소스 의존성, 앱 용량 측정, 앱–클라우드 충돌, VM 추천이 어떻게 연결되는지 한 번에 볼 수 있다.
리소스별 역할과 AWS·Azure·GCP의 전체 관계가 필요하면
[Docker-on-VM 리소스 의존성 분석 결과](vm-resource-dependency-results.md)를 이어서 읽는다.

## 설계 문서

- [시스템 아키텍처](ARCHITECTURE.md)
- [앱-클라우드 계약](app-cloud-contract-design.md)
- [구현 에이전트](implementation-agent.md)
- [구현 검증 효율화](implementation-validation-efficiency.md)
- [멤버 Linux runner 결정](member-linux-runner-decision.md)
- [VM 확장 범위](cloud-native-extension.md)
- [오케스트레이션과 체크포인트](../app/core/orchestration/README.md)
- [리소스 의존성 연구 프로토콜](../evaluation/research_protocol/README.md)
- [구성요소 projection](../evaluation/research_protocol/reports/component-projections.md)

## 평가와 실행

- [비교 실험 계획](comparison-experiment-plan.md)
- [테스트 애플리케이션 프로파일](test-application-profiles.md)
- [DepKB 효과 평가 상세](depkb-effect-evaluation-20260810.md)
- [실험 계약](../evaluation/experiment-contract.md)
- [평가 실행 안내](../evaluation/README.md)
- [개발·실험 의사결정 기록](../evaluation/research_protocol/reports/development-log.md)
- [입력 안내](../inputs/README.md)
- [산출물 안내](../artifacts/README.md)

P1~P3은 리소스 모델의 근거가 아니라 종단 통합 점검용 사례다. DepKB 효과는 동일 입력의 `full`/`no-depkb` 비교 결과로만 해석한다.

완료된 계획과 과거 설계는 [문서 보관소](archive/README.md)와 [연구 프로토콜 보관소](../evaluation/research_protocol/archive/README.md)에 둔다. 보관 문서는 현재 동작이나 결과의 근거로 사용하지 않는다.

## 유지 원칙

- 현재 상태 수치는 `current-system-status.md`, 완료된 측정치는 결과 보고서에만 기록한다.
- 계획이 완료되거나 전제가 바뀌면 삭제하지 않고 보관소로 옮긴다.
- 실패와 의사결정 이력은 `development-log.md`에 보존하되 현재 상태를 중복 서술하지 않는다.
- 사람용 문서는 한국어로 작성하고 시스템이 요구하는 식별자와 스키마 필드만 영어로 유지한다.
