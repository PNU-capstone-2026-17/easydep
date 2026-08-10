# 클라우드 리소스 모델과 에이전트 평가 방법

## 연구 질문과 범위

이 연구는 Docker-on-VM 배포 지원에 한정한다. 리소스 모델 자체의 타당성(MQ)과 그 모델이 멀티 에이전트의 산출물에 미치는 효과(RQ)를 분리한다.

- MQ1: 공급자별 리소스와 하위 구성 경계를 공식 제어 평면 API에서 재현할 수 있는가?
- MQ2: 중립 개념과 공급자 구현 사이의 다대다 대응 및 의미 손실을 표현할 수 있는가?
- MQ3: 문서상 전제와 실제 기능상 필수 의존성을 구별할 수 있는가?
- RQ1: 근거 모델이 누락된 공급자 구성요소와 잘못된 IaC 주장을 줄이는가?
- RQ2: 생성 가능한 인프라를 넘어 애플리케이션의 대표 기능까지 정상 작동시키는가?
- RQ3: 근거가 부족하거나 구현을 바꾸는 모호성이 있을 때 안전하게 질문하는가?

## 모델의 역할

중립 모델은 완결된 공통 온톨로지가 아니라 비교 좌표다. `load-balanced-ingress` 같은 capability는 AWS, Azure, GCP에서 서로 다른 수의 독립 리소스와 내장 블록으로 실현된다. 정본은 `app/core/cloudkb/depkb/provider-projections.json`이며 다음을 보존한다.

- `independent`, `embedded`, `composite-member` 표현 방식
- `full`, `partial` 의미 범위
- 하나의 중립 개념과 여러 공급자 구성요소 사이의 다대다 대응
- 공급자 고유 확장과 Terraform 타입 또는 소유 블록 근거

이 projection은 문서 전용 자료가 아니다. 요구사항에서 load balancer가 채택되면 `InfraIntent.capabilityRealizations`를 통해 설계·프로비저닝 뷰와 IaC 생성 에이전트 입력에 전달된다. 모델에 없는 capability는 추측해 만들지 않는다.

공식 의존관계는 동결된 공급자 근거 모델에서 `official-dependencies.json`으로 기계적으로 생성한다. 과거 `claims.json`은 기존 화면과 감사 추적을 위한 탐색 자료로만 유지하며, IaC 에이전트 입력에서는 리소스·간선·생성 순서·런타임 주장을 모두 제외한다. 따라서 확정 비교에서 새 근거 모델의 효과와 과거 실험 KB의 효과가 섞이지 않는다. 후보 필수성은 `candidate` 그대로 전달하며 mandatory로 해석하지 않는다.

## 근거와 의존성 검증

공식 공급자 문서와 API 스키마는 리소스 경계 및 문서상 전제의 우선 근거다. 중립 모델과 논문은 후보 개념과 비교 축을 만드는 데만 사용한다. 문서가 필수성을 명시하지 않으면 제거·복구 개입을 3회 반복한다.

각 개입은 다음 결과를 별도로 기록한다.

1. 제어 평면 생성 가능 여부
2. VM 및 컨테이너 실행 여부
3. `/readyz` 응답 여부
4. 고정된 대표 업무 HTTP 요청의 입력·출력 일치 여부
5. 복구 후 동일 기능 회복 여부

결과는 `provisionBlocked`, `runtimeBlocked`, `functionBlocked`, `noEffect` 중 하나다. 공급자 지연이나 삭제 대기는 `budgetCensored`, `schedulerCensored`로 기록하며 의존성 실패로 간주하지 않는다.

## 에이전트 비교와 지표

`easydep-full`, 동일 LLM의 단일 CoT, MetaGPT 기준군을 동일 사례·seed·시간 제한으로 비교한다. 개발 세트 81회와 holdout 27회를 계획하며 미완성 및 실패 실행도 제외하지 않고 원인과 함께 보고한다.

공통 평가기는 다음을 측정한다.

- 의미 정확성: 동결 oracle의 capability·금지 조건·의존관계 충족률
- 공급자 projection 완전성: 필요한 독립 리소스와 내장 블록 중 실제 구현 비율 및 누락 수
- 잘못된 클라우드 주장: OpenTofu/Terraform 공급자 스키마가 `Invalid resource type`, `Unsupported block type`, `Unsupported argument`, `Reference to undeclared resource`로 거부한 수
- 구현 누락: `Missing required argument` 등은 환각과 분리
- 기능 성공: Docker 빌드·시작, 준비 상태, 대표 업무 요청 성공
- 비용·시간·복잡도·테스트 커버리지 및 측정 불가 건

`unknownProviderTypes`는 평가기 사전의 범위 밖이라는 뜻일 뿐 곧바로 환각으로 세지 않는다.

## 보류와 질문 정책

LLM의 자기보고 confidence는 사용하지 않는다. 서로 독립인 5회 제안의 일치도를 원점수로 삼고 개발 라벨에 isotonic calibration을 적용한다. 자동 수락은 precision 0.90 이상이면서 Wilson 95% 하한이 0.80 이상인 임계값이 있을 때만 허용한다.

현재 개발 캠페인에는 추론 제안이 없어 임계값을 추정할 수 없으므로 `autoAcceptEnabled=false`다. 명시되고 근거가 유효한 제약만 수락하며 다음은 질문 또는 보류한다.

- 근거가 없거나 근거 범위를 벗어난 제안
- provider, region, security, availability, scale, budget처럼 구현을 바꾸는 미해결 항목
- 모델 범위 밖 또는 논리적으로 불가능한 요구
- 그 밖의 모든 추론 capability

보류 성능은 별도 capability 캠페인에서 `정답 보류`, `불필요한 보류`, `위험한 자동 수락`, `정답 자동 수락`의 혼동행렬로 평가한다. 모든 비교군이 같은 질문 출력 계약을 사용하기 전에는 종단 비교의 우열 지표로 주장하지 않는다.

## 확정 실행 조건

확정 실험 전에 `python -m evaluation.research_protocol.commands.readiness`가 `ready=true`여야 한다. 동결된 공식 근거 모델, 그 모델에서 재생성한 런타임 의존관계, decision anchor, capability 정책, provider projection과 필수 개입 결과의 해시가 모두 일치해야 한다. holdout의 새 유형은 1차 결과를 수정하지 않고 coverage 실패와 적응 실험 입력으로 남긴다.

현재 미해결 조건은 GCP backend service와 backend group 관계의 기능 개입 3회다. 실제 계정 인증 전에는 유료 리소스를 변경하지 않는다.

로컬 실험은 한 번에 하나만 실행한다. 남은 디스크가 5 GiB 미만이면 시작하지 않고, 시간 초과 시 하위 프로세스 트리와 임시 worker 파일을 정리한다. 클라우드 실험은 생성 리소스 ID를 즉시 기록하고 성공·실패·중단 모두 정리를 수행한다. 잔존 리소스가 있으면 같은 공급자의 다음 bundle을 차단하며, 정리 예비 비용과 60분 정리 시간을 측정 예산과 별도로 보존한다.

## 주요 참고 자료

- IaC-Eval, NeurIPS 2024 Datasets and Benchmarks Track
- Empirical Standards for Software Engineering Research, arXiv:2010.03525
- OpenTofu `validate -json` 및 계획 JSON 명세
- Cloud-Barista CB-Tumblebug, OASIS TOSCA 2.0, OCCI 1.2, CAMEL, Crossplane Composition, Terraform 공급자 설계 원칙

중립 모델별 추상화 근거와 실제 대응은 `벤더-중립-모델-근거-검토.md`에 정리한다.
