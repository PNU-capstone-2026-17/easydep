# 이력 문서

이 디렉터리의 문서는 과거 구현과 의사결정을 보존하기 위한 기록이다. 현재 시스템의 사용법,
지원 범위 또는 작업 우선순위를 판단하는 근거로 사용하지 않는다.

| 문서 | 보관 이유 |
|---|---|
| `handoff-2026-07-20.md` | 초기 MySQL 저장소와 에이전트 병합 인수인계 기록 |
| `requirements-agent-initial-deployment.md` | 요구사항 단계가 마일스톤 1이던 시기의 minikube·AKS 배포 문서 |
| `kubernetes-deployment-file-generation.md` | 현재 Docker-on-VM 범위에서 제외된 Kubernetes 생성 경로 |
| `agent-sdk-merge-plan.md` | 2026-07-25에 완료된 저장소 병합 계획 |
| `requirements-agent-improvements.md` | 이후 구조 변경 전에 작성된 개선 후보 목록 |
| `csp-neutral-minimum-dependencies.md` | 중립 관계 설명을 현재 토폴로지·3사 의존성 기준 문서에 통합 |
| `vm-resource-dependency-results.md` | 프로비저닝·런타임 표를 현재 토폴로지·3사 의존성 기준 문서에 통합 |
| `agent-cloud-guardrails-and-evaluation.md` | 상품 카탈로그 API 중심의 과거 사례·평가안 |
| `implementation-validation-efficiency.md` | 구현 검증 병목에 대한 완료된 일회성 결정 |
| `member-linux-runner-decision.md` | Linux runner 경계에 대한 완료된 일회성 결정 |
| `interim-report-20260810.md` | 2026-08-10 시점의 중간 보고서 |
| `api.md` | HTTP 계약은 실행 중인 FastAPI `/docs`와 route 구현으로 대체 |
| `ARCHITECTURE.md` | 요구사항 분석기 상세 구조를 현재 시스템 상태 문서에 요약 |
| `implementation-agent.md` | 구현 실행 경계와 핵심 API를 현재 시스템 상태 문서에 통합 |
| `app-cloud-contract-design.md` | 계약 의미와 부분수정 원칙을 토폴로지·검증 문서에 통합 |
| `cloud-resource-guidance.md` | 비전공자 안내 내용을 토폴로지·CSP 의존성 절에 통합 |
| `csp-resource-dependencies.md` | CSP별 전체 표를 논리 배포 토폴로지 문서 9절에 통합 |
| `deployment-topology-capacity-validation-plan.md` | 채택된 토폴로지·VM 추천·검증 계획을 두 기준 문서에 통합 |
| `project-baseline.md` | 범위·중단 기준을 현재 상태와 평가 문서에 통합 |
| `test-application-profiles.md` | P1~P3은 회귀 사례로 동결하고 새 E1·E2는 평가 문서에 정의 |
| `research-results-20260810.md` | 2026-08-10 시점 결과를 현재 평가 문서에 요약 |
| `depkb-effect-evaluation-20260810.md` | 상세 원시 해석을 보관하고 핵심 결과만 평가 문서에 통합 |
| `cloud-native-extension.md` | 과거 링크 호환 안내의 역할이 끝나 보관 |
| `resource-plan-experiment-reflection.md` | 2026-08-15~17 E1~E3 반영 판단과 3사 IaC 실험 결과를 기록한 시점 보고서 |
| `managed-l4-ingress-experiment-20260817.md` | 2026-08-17 AWS·Azure·GCP 관리형 L4 실험의 완료 결과 |

현재 기준은 상위의 [문서 안내](../README.md), [현재 시스템 상태](../current-system-status.md),
[논리 배포 토폴로지와 CSP 의존성](../logical-deployment-topology-decisions.md),
[비교평가 계획과 현재 결과](../comparison-experiment-plan.md)를 따른다.
