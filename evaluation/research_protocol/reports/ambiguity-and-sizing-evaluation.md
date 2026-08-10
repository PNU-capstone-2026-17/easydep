# 질문·보류와 비용·성능 추천 평가

이 문서는 클라우드 구성 요소 절제실험과 별도로 수행하는 두 평가의 경계를 정의한다. 두 결과를 P1~P3 종단 점수와 합쳐 하나의 원인 효과로 해석하지 않는다.

## 1. 질문·보류 정책 평가

`CapabilityContract/v1`은 다음 순서로 판단한다.

1. 논리적으로 불가능한 요구는 `abstained`로 보류한다.
2. Docker-on-VM 연구 범위 밖의 요구는 `abstained`로 보류한다.
3. 원문 근거가 없거나 provider·region·security·availability·scale·budget처럼 realization을 바꾸는 정보가 미정이면 `needsQuestion`으로 질문한다.
4. 원문에 명시되고 근거 span이 유효한 제약은 `accepted`로 수락한다.
5. 추론된 capability는 개발 자료로 보정된 신뢰도가 동결 임계값 이상일 때만 자동 수락한다.

현재 개발 캠페인에는 독립 검토할 inferred proposal이 0개다. 따라서 숫자를 임의로 정하지 않고 자동 수락을 비활성화했다.

- 목표 정밀도: `0.90`
- Wilson 95% 하한: `0.80`
- 현재 `acceptThreshold`: `null`
- 의미: 명시적 근거가 없는 inferred capability는 전부 질문으로 보낸다.

이는 “신뢰도가 높다”는 LLM 자기보고를 임계값으로 쓰는 방식이 아니다. 추후 inferred 개발 사례가 생기면 두 검토자의 판정, Cohen's kappa, 불일치 조정 결과로 isotonic calibration을 수행하고 위 두 조건을 만족하는 임계값만 동결한다.

정책 수준 개발 사례는 [`protocols/ambiguity-cases.json`](../protocols/ambiguity-cases.json)에 있다. provider·region 누락, 성능 목표만 있고 용량 하한이 없는 경우, 근거 없는 영속성 추론, HA·예산 충돌, 영속성 요구 충돌, 범위 밖 관리형 플랫폼을 구분한다.

```powershell
python -m evaluation.research_protocol.commands.evaluate_ambiguity
```

보고 지표는 coverage, selective risk, 질문 recall, hard-abstention recall, 세 가지 disposition 정확도다. 이 평가는 입력 특징을 이미 구조화한 **정책 단위 평가**이므로, 자연어에서 모호성을 올바르게 추출했다는 종단 증거로 사용하지 않는다. 그 주장은 별도의 LLM 추출 campaign과 대화 재개 실험이 필요하다.

## 2. 비용·성능 추천 평가

VM 추천은 다음 범위로 제한한다.

- provider와 정확한 region
- x86_64
- VM당 최소 vCPU·메모리
- 최소 VM 수
- 온디맨드 VM compute list price
- 지속 부하에서 알려진 burst·구세대 경고

스토리지, 네트워크 송신, Load Balancer, 세금, 약정 할인은 예산 판정에 포함하지 않는다. 따라서 결과 이름도 전체 인프라 비용이 아니라 `monthlyComputeListPriceUsd`다. 최소 용량 근거가 없으면 가장 작은 VM을 임의 추천하지 않고 `missing_capacity_floor`로 보류한다.

평가 oracle은 다음 고정 스냅샷의 SHA-256과 결합돼 있다.

- `app/core/cloudkb/data/tumblebug-cost.json.gz`
- `app/core/cloudkb/data/tumblebug-perf.json.gz`

scorer는 실행할 때 실제 파일 해시를 다시 계산하며 하나라도 다르면 결과 생성을 거부한다. 정상 선택 3건, HA 예산 불가능 1건, 용량 누락 1건, 정확한 가격 리전 부재 1건을 평가한다.

이 평가는 고정 카탈로그에서 선택 규칙이 제대로 동작하는지를 증명한다. 실제 애플리케이션의 처리량이나 지연시간을 VM 사양만으로 보장하지 않는다. 비용·성능에 관한 종단 주장은 별도의 부하 시험과 실제 청구·가격 시점 기록이 있을 때만 가능하다.

## 3. 연구 주장 경계

| 증거 | 허용되는 주장 | 허용되지 않는 주장 |
|---|---|---|
| 정책 사례 통과 | 구조화된 모호성에 대해 수락·질문·보류 규칙이 구분된다 | 자연어 모호성을 LLM이 항상 정확히 찾는다 |
| VM 선택 사례 통과 | 고정 카탈로그에서 용량·compute 예산·성능 경고 규칙을 지킨다 | 전체 클라우드 비용 또는 실제 처리량을 보장한다 |
| paired component 실험 | DepKB가 구성 요소·관계 누락에 미치는 효과를 추정한다 | 비용 추천이나 질문 정책의 효과까지 설명한다 |
| 실제 배포·기능 gate | 생성 가능성과 앱 기능 성공을 각각 관측한다 | 관측하지 않은 CSP 서비스로 일반화한다 |
