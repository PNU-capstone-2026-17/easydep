# VM 리소스 의존성 평가

이 평가는 전체 애플리케이션 품질과 분리하여 `depkb`의 직접 효과를 측정한다. 세 CSP에서
두 variant에 동일한 시작 리소스를 주고 프로비저닝 의무 리소스와 관계를 완성하게 한다. 요구사항·설계·
구현 LLM을 재실행하지 않으므로 그 변동을 KB 효과로 잘못 세지 않는다.

```powershell
python -m evaluation.easydep.cloud_resources.run aws-public-load-balancer --variant full
python -m evaluation.easydep.cloud_resources.run aws-public-load-balancer --variant no-cloud-kb
python -m evaluation.easydep.cloud_resources.score artifacts/runs/<run-id>
```

`no-cloud-kb`는 시작 리소스만 유지하고 의존성 조회를 실제로 건너뛴다. 이 실험의
`cloud-plan.json` 채점은 EasyDep 전체판과 KB 제거판 사이의 **구성요소 분석에만** 사용한다.
CoT와 MetaGPT에는 이 내부 형식을 요구하지 않는다. 시스템 간 종단 비교는 각 방식의
소스코드·Dockerfile·IaC와 선택적 배포 매니페스트를 대상으로 별도로 수행한다.

`gold.json`은 제품 출력이나 `claims.json`에서 생성하지 않는다. 2026-08-07에 대화 맥락을
전달받지 않은 AI clean-room 검토자가 `review_packet.json`과 그 안의 CSP 공식 문서만으로
재판정했다. 동결 파일의 `independenceStatus`는 `independently-reviewed`다. 이는 시스템
출력으로 정답을 만든 순환을 차단하지만 인간 전문가 검토와 동의어는 아니다.

독립 검토자는 `review_packet.json`만 전달받는다. 이 파일에는 정답이 없고 공식 CSP 문서,
질문, 허용된 정규화 자원명만 있다. 검토자가 각 사례의 `mandatoryNodes`,
`mandatoryRelations`, `rationale`, `reviewerId`, `reviewedAt`을 채운 복사본을 반환하면 다음처럼
구조와 완전성을 검증해 동결한다. 또한 시스템 출력을 보지 않았다는
`independenceAttestation=true` 확인이 필요하다.

```powershell
python -m evaluation.easydep.cloud_resources.review_gold completed-review.json
```

동결 결과에는 검토자·시각과 원본 검토 패킷 SHA-256이 기록된다. 실제 검토 응답은
`completed-review.cleanroom.json`에 보존한다. 논문에는 검토자가 AI였음과 검토자가 지적한
직접 관계 해석의 모호성을 함께 공개해야 한다. 가능하면 제출 전 인간 검토로 교차 확인한다.

현재 결정론적 제거 실험의 수치와 해석 범위는 [`results.md`](results.md)에 기록한다.

같은 위치의 VM 선택 실험은 연쇄 의존 자원 계산이 아니라 용량·가격·성능 지식의 직접 효과를
검사한다. 세 CSP 정상 선택과 불가능·정보 부족 사례를 함께 실행한다.

```powershell
python -m evaluation.easydep.cloud_resources.select select-aws-steady-small --variant full
python -m evaluation.easydep.cloud_resources.select select-aws-steady-small --variant no-vm-knowledge
python -m evaluation.easydep.cloud_resources.score_selection artifacts/runs/<run-id>
```

정답 파일에는 사용한 cost/perf 스냅샷 SHA-256과 판정 절차를 동결했다. 이 결과 역시
결정론적 구성요소 제거 검사이며, LLM 대조군보다 우수하다는 종단 증거로 사용하지 않는다.
