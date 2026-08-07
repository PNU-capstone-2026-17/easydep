# EasyDep 문서 안내

문서의 현재 기준점은 `current-system-status.md`이다. 구현과 문서가 다를 경우 실행 코드와
이 문서를 우선하고, 불일치는 별도 변경으로 바로잡는다.

## 시작점

| 문서 | 역할 |
|---|---|
| [현재 시스템 상태](current-system-status.md) | 현재 범위, 검증된 기능, 부족한 점과 다음 순서 |
| [HTTP API](api.md) | 외부 API 계약과 예시 |
| [요구사항 아키텍처](ARCHITECTURE.md) | 요구사항 분석 그래프와 검증 방식 |
| [구현 에이전트](implementation-agent.md) | 구현 worker, 단계, 승인 및 복구 방식 |
| [VM 확장 범위](cloud-native-extension.md) | Docker-on-VM 설계 범위와 결정 |

## 실험 문서

| 문서 | 역할 |
|---|---|
| [비교실험 계획](comparison-experiment-plan.md) | 비교군, 사례 및 평가 지표 |
| [테스트 앱 프로필](test-application-profiles.md) | P1·P2·P3와 홀드아웃 사례 |
| [실험 계약](../evaluation/experiment-contract.md) | 실행기 공통 입력·출력과 적격성 조건 |
| [파일럿 결과](../evaluation/pilot-results.md) | 완료된 종단 파일럿 근거 |

## 연구 및 세부 설계

- [연구 목표 원문](research.md)
- [오케스트레이션 계약](../app/core/orchestration/README.md)
- [클라우드 지식베이스 문서](../app/core/cloudkb/document/README.md)
- [평가 실행 안내](../evaluation/README.md)
- [입력 사례](../inputs/README.md)
- [산출물 안내](../artifacts/README.md)

## 이력 문서

`archive/`는 이미 끝난 계획, 병합 당시 판단, 현재 범위에서 제외된 Kubernetes 경로 같은
역사 기록이다. 현재 구현의 근거로 사용하지 않는다. 자세한 목록은
[archive 안내](archive/README.md)를 참고한다.

## 유지 원칙

- 현재 상태를 중복 서술하지 않고 `current-system-status.md`로 연결한다.
- 계획 문서는 완료되거나 전제가 바뀌면 `archive/`로 이동한다.
- 실험 결과는 계획 문서가 아니라 `evaluation/` 아래에 기록한다.
- API와 실행 명령은 가능한 한 코드에서 직접 확인하고 갱신한다.
- 새 문서를 추가하면 이 색인에 역할과 기준 상태를 함께 기록한다.
