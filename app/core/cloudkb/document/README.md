# Cloud KB 문서 안내

이 디렉터리에는 현재 연구와 구현의 기준이 되는 문서만 둔다.

1. [`research.md`](research.md): 과제 목표와 현재 실증 범위
2. [`dependency-analysis.md`](dependency-analysis.md): 리소스 의존성의 정의·판정 기준·근거
3. [`vm-scope.md`](vm-scope.md): AWS·Azure·GCP Docker-on-VM 연구 범위
4. [`vm-resource-selection.md`](vm-resource-selection.md): VM 용량·가격·성능 선택 기준
5. [`evaluation-protocol.md`](evaluation-protocol.md): 대조실험과 효과 측정 방법
6. [`terminology-ledger.md`](terminology-ledger.md): 용어 출처·조작적 정의·금지 해석
7. [`resource-coverage.md`](resource-coverage.md): 현재 자원 어휘의 충족 범위와 미측정 후보

실행 상태와 채점기의 세부 불변식은 `evaluation/experiment-contract.md`에 둔다. 연구 질문과
지표는 이 디렉터리의 평가 프로토콜이 기준이고, 실행 계약은 이를 구현하는 하위 규칙이다.

`archive/`는 조사 과정과 폐기된 설계를 보존하는 비권위 자료다. 논문이나 구현의 현재 기준으로 인용하지
않으며, 필요한 주장은 위 다섯 문서에서 원출처와 함께 다시 확인한다.

문서와 코드가 충돌하면 이를 숨기지 않는다. 각 문서의 **현재 구현 상태** 절에 차이를
기록하고, 코드 또는 데이터가 범위를 만족할 때만 완료로 표시한다.
