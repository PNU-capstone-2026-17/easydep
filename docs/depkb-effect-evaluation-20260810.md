# DepKB 효과 검증 결과

이 문서는 승인된 `research.md`를 수정하지 않고, Docker-on-Linux-VM 범위에서 DepKB가 제공하는
효과를 세 층으로 나눠 측정한 결과를 보고한다. 측정일은 2026년 8월 10일이다.

## 연구 질문

1. 근거 기반 평가기가 필수 구조 참조의 누락을 선택적으로 검출하는가?
2. 동일 앱과 동일 요구분석 출력에서 DepKB를 제공하면 LLM의 IaC delivery와 구조 참조 완결성이
   개선되는가?
3. 모델에 포함된 의존 관계 하나가 실제 cloud 앱 기능에 필요한가?

세 질문의 증거를 합산 점수 하나로 만들지 않는다. 정적 누락 검출, 생성 결과, 실제 기능은 서로
대신할 수 없는 별도 층이다.

## 1. 고정 입력 생성 절제

### 설계

- 축: 영속 블록 저장소, 다중 VM 부하분산, HTTPS 종료
- CSP: AWS, Azure, GCP
- arm: `full`, `no-depkb`
- 조건: treatment만 사용
- 반복: 3회
- 총 셀: 3 × 3 × 2 × 3 = 54

각 capability 안에서 두 arm은 같은 애플리케이션 tree SHA-256과 같은 저장된 `deploymentNeeds`를
사용했다. 영속 저장소는 notes 앱, LB와 TLS는 immutable product 앱을 사용했다. capability 간 앱은
같지 않지만 각 paired 비교 안에서는 동일하다. 요구사항·설계·앱 생성 LLM 호출은 0회이며 VM
delivery만 다시 실행했다. 설계 입력은 앱과 Docker image만 포함하는 중립 다이어그램으로 고정해
과거 P2 토폴로지가 LB/TLS에 누출되지 않게 했다.

세 반복 모두 config SHA-256이 같고, 각 반복의 동일 앱 입력 18/18과 축별 고정 앱 테스트 3/3을
통과했다. 실제 cloud apply는 수행하지 않았다.

### 주 결과

`delivery 완료`와 `의존 참조 완결`을 분리했다. 의존 참조 완결은 VM delivery가 완료되고 해당
provider·capability의 모든 근거 기반 `componentDependencyReference`가 통과한 경우다.

| 지표 | full | no-depkb | 차이 |
|---|---:|---:|---:|
| VM delivery 완료 | 20/27 (74.1%) | 15/27 (55.6%) | +18.5%p |
| 의존 참조 완결 | 14/27 (51.9%) | 8/27 (29.6%) | +22.2%p |
| 구조 참조 macro recall | 0.677 | 0.503 | +0.173 |
| 불필요 개념 gate 실패 | 4/27 | 5/27 | -1건 |

paired delivery는 full 승 7, no-depkb 승 2, 동률 18이었다. 의존 참조 완결은 full 승 7,
no-depkb 승 1, 동률 19였다. discordant pair의 정확 부호검정 보조값은 각각 0.180과 0.070이다.
표본이 개발 사례에 군집되어 있어 이 값을 모집단 유의성 검정으로 해석하지 않는다.

### capability별 결과

| Capability | delivery full/no-depkb | 의존 완결 full/no-depkb | 평균 참조 recall 차이 |
|---|---:|---:|---:|
| 영속 저장소 | 8/9 · 8/9 | 8/9 · 8/9 | 0.000 |
| 다중 VM LB | 5/9 · 2/9 | 5/9 · 0/9 | +0.393 |
| HTTPS 종료 | 7/9 · 5/9 | 1/9 · 0/9 | +0.127 |

영속 저장소에서는 요구사항 자체가 attachment 관계를 충분히 명시해 DepKB 추가 효과가 없었다.
LB에서는 가장 큰 양의 차이가 나타났다. TLS는 delivery가 일부 개선됐지만 LB와 TLS의 전체 참조를
모두 만족한 셀이 full에서도 1/9뿐이어서 실용적 완성도는 낮다.

### CSP별 결과

| CSP | delivery full/no-depkb | 의존 완결 full/no-depkb | 평균 참조 recall 차이 |
|---|---:|---:|---:|
| AWS | 8/9 · 7/9 | 5/9 · 3/9 | +0.038 |
| Azure | 5/9 · 3/9 | 3/9 · 3/9 | +0.198 |
| GCP | 7/9 · 5/9 | 6/9 · 2/9 | +0.284 |

효과는 CSP에서도 균일하지 않았다. 특히 Azure의 의존 완결 수는 같았고, GCP에서 가장 큰 평균
참조 차이가 나타났다.

### 반복 안정성

| 반복 | delivery full/no-depkb | 의존 완결 full/no-depkb | 평균 참조 차이 |
|---|---:|---:|---:|
| 1 | 7/9 · 6/9 | 5/9 · 3/9 | +0.129 |
| 2 | 8/9 · 4/9 | 6/9 · 2/9 | +0.423 |
| 3 | 5/9 · 5/9 | 3/9 · 3/9 | -0.032 |

세 번째 반복에서는 차이가 사라지거나 반대로 나타났다. 따라서 평균적인 양의 개발 효과는
관측됐지만 안정적·일반적인 향상으로 주장할 수 없다. 원시 결과와 결정론적 집계는 다음 파일이다.

- `artifacts/confirmatory/component-fixed-treatment-r1-20260810.json`
- `artifacts/confirmatory/component-fixed-treatment-r2-20260810.json`
- `artifacts/confirmatory/component-fixed-treatment-r3-20260810.json`
- `artifacts/confirmatory/component-fixed-treatment-summary-20260810.json`

### 실패 모드 분리

54셀을 전달 단계와 구조 관측 단계로 다시 분리했다. 전달에 실패한 셀에서 평가기가 후속 check를
실패로 채운 값은 실제 의존 누락으로 세지 않았다. IaC 전달이 완료된 셀에서만 구조 참조 실패를
관측값으로 취급했다.

| 결과 층 | 셀 수 |
|---|---:|
| 전달 완료·의존 참조 완결 | 22 |
| 전달 완료·의존 참조 불완전 | 13 |
| 전달 실패 | 19 |

전달 실패 19건 중 18건은 provider schema 검증 단계에서 발생했고 1건은 현재 로그만으로 단계를
더 분류할 수 없었다. TLS의 경우 AWS 두 arm 모두 `httpsListener→certificate`가 완료된 세 반복에서
계속 누락됐다. AWS full은 `backendMembership→backendGroup`과 `backendMembership→vm`도 세 번
계속 누락됐다. 반면 Azure/GCP TLS 일부는 구조를 관측하기 전 provider schema 검증에서 끝났다.
따라서 TLS 저성공률을 하나의 DepKB 효과나 하나의 근본 원인으로 설명하지 않는다.

실제 cloud preflight 대조 후보는 수기로 고르지 않고 다음 조건으로 도출했다.

1. 같은 반복·capability·CSP의 두 arm이 모두 provider 검증까지 완료됐다.
2. 한 arm만 근거 기반 구조 참조가 완결됐다.
3. 두 조건을 만족하지 않으면 구조 차이를 가진 paired cloud 후보에서 제외한다.

이 조건을 만족한 것은 GCP LB 반복 1과 AWS LB 반복 2의 두 쌍뿐이다. 이 판정은 cloud 실행 준비
완료가 아니라 입력 정합성·plan·apply·기능 gate를 차례로 적용할 후보라는 뜻이다. 실제 IaC
preflight에서 두 no-depkb 산출물은 HTTP 요구와 달리 HTTPS 인증서 입력을 추가한 사실도 확인됐다.
full 산출물의 VM 수는 Terraform `count`를 정적 평가기가 해석하지 못해 추가 plan 확인이 필요하다.
전체 27쌍 중 14쌍은 한쪽
이상이 전달 실패, 7쌍은 구조 완결 동률, 4쌍은 둘 다 구조 불완전이었다. TLS를 성공할 때까지
재생성하거나 특정 오류에 맞춰 수정하지 않고, 현재는 기능 paired 검증 부적격 결과로 보존한다.
결정론적 분석 결과는
`artifacts/confirmatory/component-fixed-failure-analysis-20260810.json`이다.

### Cloud preflight와 원본 apply

두 구조 대조 후보에 동일한 입력 정책을 적용했다. provider별 프로젝트·리전, 최소 VM 유형과
container image 위치는 실행환경 입력으로 제공했지만, HTTP 요구에 없던 인증서 입력은 보충하지
않았다. `init`, `validate`, `plan -refresh=false`를 순차 실행했으며 이 단계에서는 cloud 리소스를
생성하지 않았다.

| 셀 | init·validate | plan | 판정 |
|---|---|---|---|
| GCP LB r1 full | 통과 | 실패 | 일부 GCP 리소스의 project binding 누락 |
| GCP LB r1 no-depkb | 통과 | 실패 | 요구하지 않은 TLS 인증서·키가 필수 입력 |
| AWS LB r2 full | 통과 | 통과 | 원본 apply 승격 |
| AWS LB r2 no-depkb | 통과 | 실패 | 요구하지 않은 ACM 인증서 ARN이 필수 입력 |

유일하게 plan을 통과한 AWS full만 원본 그대로 실제 apply했다. 기존 고정 이름·태그와 일치하는
AWS 리소스가 0개인지 먼저 확인했다. apply는 25.4초 뒤 다음 두 독립 원인으로 실패했다.

- Application Load Balancer에 한 AZ의 subnet 하나만 전달되어 AWS의 최소 2개 AZ 제약을 위반했다.
- 생성물이 고정한 AMI가 `ap-northeast-2`에 존재하지 않아 VM 두 대 생성이 거부됐다.

기능용 앱 이미지를 공개 registry에 배포하지 않고 provisioning 경로 확인용 placeholder 이미지를
사용했으므로 앱 기능은 `notObserved`다. 실패 직후 destroy는 13.5초에 통과했고 Terraform state는
비었으며, AWS 후속 조회에서 VPC 태그·backend 인스턴스 태그·ALB 이름·target group 이름 잔여가
모두 0이었다. 비용은 측정하지 않아 0으로 기록하지 않았다.

이 결과에서 두 paired 후보 모두 최종 앱 기능에 도달하지 못했다. 따라서 54셀에서 관측된 정적
평균 개선이 실제 cloud 기능 개선으로 이어졌다는 주장은 현재 지지되지 않는다. 동시에 정적 edge
참조와 provider schema 검증만으로 다중 AZ 배치 제약, 리전별 이미지 유효성, 앱 기능을 대신할 수
없다는 점을 직접 확인했다. 원시 결과는 다음과 같다.

- `artifacts/confirmatory/component-cloud-preflight-20260810.json`
- `artifacts/confirmatory/aws-lb-r2-full-apply-probe-20260810.json`

## 2. 누락 검출기 leave-one-edge-out

AWS·Azure·GCP의 provider 검증 fixture에서 근거 projection이 요구하는 구조 참조 31개를 대상으로
각 edge의 관측 pair만 하나씩 제거했다. 소스 Terraform을 파괴하거나 cloud apply를 수행한 실험이
아니라, 평가기의 민감도와 선택성을 확인하는 정적 개입이다.

| 지표 | 결과 |
|---|---:|
| 원본 필수 참조 통과 | 31/31 |
| 단일 edge 제거 실험 | 31건 |
| 제거 edge 검출 | 31/31 |
| 제거하지 않은 edge 상태 변화 | 0건 |

따라서 현재 범위의 근거 edge가 관측 결과에서 사라지면 평가기가 해당 edge만 실패로 판정한다.
이는 평가기 개발 적합성이지, 임의의 실제 Terraform 누락을 모두 탐지한다는 일반 정확도가 아니다.
결과는 `artifacts/confirmatory/dependency-leave-one-out-20260810.json`이다.

## 3. 실제 cloud 기능 개입

기존 GCP 실제 실행에서 backend service–backend group 참조를 한 요인으로 제거했다. 세 반복 모두
원본 구성의 provisioning·startup·업무 기능 통과, 관계 제거 뒤 업무 기능 실패, 관계 복원 뒤
업무 기능 재통과, 정리 성공과 잔여 리소스 0을 확인했다.

이는 해당 범위의 GCP unmanaged instance group 토폴로지에서 관계가 실제 기능에 필요하다는 직접
증거다. AWS·Azure, 다른 GCP backend 유형 또는 다른 관계로 일반화하지 않는다. 근거는
`evaluation/research_protocol/intervention-results/intervention.gcp.backend-service-backend-group.necessity.json`이다.

## 최종 판정

현재 결과는 다음 주장을 지지한다.

> 제한된 Docker-on-VM 개발 사례에서 DepKB를 제공하면 동일 입력의 VM delivery 완료율과 근거 기반
> 구조 참조 완결률이 평균적으로 증가했으며, 가장 큰 효과는 다중 VM 부하분산 축에서 관찰됐다.
> DepKB 평가기는 동결된 provider fixture의 근거 edge 누락을 선택적으로 검출했고, 모델의 GCP 관계
> 하나는 실제 기능 개입에서도 필요성이 확인됐다.

추가 cloud preflight와 apply는 정적 개선의 한계도 보여준다. 구조 대조 후보 두 쌍은 모두 paired
앱 기능 관측에 도달하지 못했으며, AWS full의 정적 의존 참조 완결도 실제 다중 AZ 제약과 리전별
AMI 유효성을 보장하지 못했다.

다음 주장은 지지하지 않는다.

- 모든 capability와 CSP에서 DepKB가 생성 성공을 보장한다.
- 관측된 +18.5%p 또는 +22.2%p가 CNA 모집단의 기대효과다.
- 정적 구조 참조 통과가 cardinality, 배치 제약 또는 앱 기능 성공을 의미한다.
- GCP 한 관계의 실제 기능 결과가 전체 DepKB 관계에 적용된다.

추가 사례나 capability를 늘리는 대신, 현재 결과는 “평균적인 양의 개발 신호가 있으나 축·CSP·반복
변동성이 크다”는 결론으로 확정한다.
