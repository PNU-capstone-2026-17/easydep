# Cloud KB 문서 지도와 정리 상태

> 갱신일: 2026-08-04

## 현재 기준

1. 연구 목표는 [`research.md`](research.md)를 따른다.
2. 시스템 범위와 단계 계약은 [`docs/cloud-native-extension.md`](../../../../docs/cloud-native-extension.md)를 따른다.
3. Cloud KB의 현재 패키지 판단은 [`../README.md`](../README.md)를 따른다.
4. `archive/`와 아래 보류 문서는 현재 구현 지시가 아니라 과거 근거 자료다.

## 활성 문서

| 문서 | 역할 |
|---|---|
| `research.md` | 과제 원문 |
| `README.md` | 이 디렉터리의 문서 상태와 탐색 순서 |
| `../../../../docs/cloud-native-extension.md` | 제품 범위와 단계별 책임 |
| `../../../../docs/comparison-experiment-plan.md` | 비교실험 기준 |
| `../../../../docs/test-application-profiles.md` | 실험 애플리케이션 |

## 보류 문서

다음 문서는 유용한 조사 내용을 포함하지만 현재 VM 범위와 맞는지 재검증되지 않았다.

| 문서 | 보류 이유 |
|---|---|
| `kb-book.md` | graphkb, 다수 CSP, 관리형 서비스와 과거 에이전트 경로가 혼재 |
| `constraint-derivation.md` | 기존 요구사항 계약과 현재 최소 `cloudContext` 대조 필요 |
| `dependency-model.md` | graphkb·depkb 전환 전후의 개념과 결과가 혼재 |
| `infra-planning-api.md` | 현재 `ResourcePlan` 계약과 대조 필요 |
| `resource-catalog.md` | K8s·VPN·관리형 서비스 제거 후 VM 카탈로그로 축소 필요 |

보류 문서는 새 구현의 기준으로 사용하지 않는다. 필요한 근거만 현재 문서나 코드 테스트로 옮긴 뒤 아카이브한다.

## 현재 패키지

제품 경로에는 `depkb`, `capacitykb`, `sizingkb`, `costkb`, `perfkb`, `kbcommon`만 유지한다. 에이전트 연결과 배포 산출물 조립은 Cloud KB 밖에서 새로 구성한다.

다음 단계는 유지 패키지 내부의 관리형 서비스와 타 CSP 분기를 제거하고 VM 전용 공개 API를 고정하는 것이다.

## 삭제 기준

다음 조건을 모두 만족한 항목만 삭제한다.

- 제품 코드에서 참조되지 않음
- 활성 테스트와 실험 프로필에서 사용되지 않음
- 연구 근거로 필요한 원본 또는 측정 결과가 아카이브됨
- 삭제 후 Cloud KB 테스트가 통과함

레거시 패키지·테스트·범위 밖 데이터는 Git 이력으로 복구할 수 있다.
