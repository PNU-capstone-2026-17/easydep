# Cloud KB 문서 안내

이 디렉터리에는 현재 연구와 구현의 기준이 되는 문서만 둔다.

1. [`research.md`](research.md): 과제 목표와 현재 실증 범위
2. [`../../../../docs/logical-deployment-topology-decisions.md`](../../../../docs/logical-deployment-topology-decisions.md): Docker-on-VM 범위, 배포 다이어그램과 리소스 의존성의 통합 기준
3. [`vm-resource-selection.md`](vm-resource-selection.md): VM 용량·가격·성능 선택 기준
4. [`evaluation-protocol.md`](evaluation-protocol.md): 대조실험과 효과 측정 방법
5. [`terminology-ledger.md`](terminology-ledger.md): 용어 출처·조작적 정의·금지 해석
6. [`resource-coverage.md`](resource-coverage.md): 현재 자원 어휘의 충족 범위와 미측정 후보

실행 상태와 채점기의 세부 불변식은 `evaluation/experiment-contract.md`에 둔다. 연구 질문과
지표는 이 디렉터리의 평가 프로토콜이 기준이고, 실행 계약은 이를 구현하는 하위 규칙이다.

`archive/`는 조사 과정과 폐기된 설계를 보존하는 비권위 자료다. 논문이나 구현의 현재 기준으로 인용하지
않으며, 필요한 주장은 위 문서들에서 원출처와 함께 다시 확인한다.

문서와 코드가 충돌하면 이를 숨기지 않는다. 각 문서의 **현재 구현 상태** 절에 차이를
기록하고, 코드 또는 데이터가 범위를 만족할 때만 완료로 표시한다.
