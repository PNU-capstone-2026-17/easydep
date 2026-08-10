# 구성요소 파일럿 결과 (2026-08-09)

## 목적과 판정 범위

이 파일럿은 영속 저장소(PS), 부하 분산(LB), HTTPS 종료(TLS)의 세 capability가 벤더별 Terraform으로 투영되는 과정과 단계 복구 동작을 개발 단계에서 점검했다. 실제 클라우드 `apply`는 수행하지 않았으므로 리소스 생성 성공이나 앱 기능 성공을 주장하지 않는다.

## 관찰 결과

| 실행 | 결과 | 확인한 사실 | 해석 제한 |
|---|---|---|---|
| PS-control Azure | 실패한 acceptance 하위 작업부터 복구하여 완료 | 상위 요구사항·설계·scaffold를 다시 실행하지 않고 125.6초에 복구 | 단일 개발 실행 |
| P2 Azure 기존 실행 | 완료 | IaC, 내부 기능, 재시작 후 영속성, 정리 기록 존재 | 과거 통합 사례이며 PS 쌍의 인과 비교가 아님 |
| TLS-treatment Azure | VM delivery 실패 | Azure Application Gateway의 `status_code` 형식과 동적 backend block 오류를 각각 관찰 | provider 검증 실패이며 클라우드 생성은 미실행 |
| LB-control Azure full | 동일 run의 VM delivery만 재실행 후 완료 | 누락된 `required_providers` 복구와 하위 단계 재개 | 앱 기능은 로컬 평가 범위 |
| LB-control Azure no-depkb | 동일 run의 VM delivery만 재실행 후 완료 | full과 동일 종류의 provider 오류 및 부분 복구 | full과 생성 앱이 달라 효과 비교 불가 |

TLS 실행 중 scaffold가 20분 제한에 도달했으나 별도 엔드포인트 호출은 TTFT 2.336초, 전체 2.579초, HTTP 429 없음이었다. 같은 산출물에 대한 로컬 포트 추론을 재현한 결과 DOTALL 정규식의 과도한 backtracking이 원인이었고, 수정 뒤 0.009405초가 됐다. 따라서 이 건은 LLM 속도 제한으로 분류하지 않는다.

## 절제실험 설계 변경

동일 사례와 seed의 `full` 및 `no-depkb` 앱 파일 해시가 서로 달랐다. 앱 구현 차이가 결과에 섞이므로 이 종단 비교에서 DepKB의 인과효과를 계산하지 않는다.

대신 저장된 동일 LLM `deploymentNeeds`를 두 조건에 그대로 입력하는 고정입력 투영 절제를 수행했다. 결과 파일은 `artifacts/measurements/capability-projection-fixed-output-ablation-20260809.json`이다.

- 입력 해시 일치: 9/9 provider cell
- full의 modeled outcome: 9, no-depkb: 0
- full의 realization: 6, no-depkb: 0
- 추가 LLM 호출 및 cloud apply: 0

이는 “현재 투영기가 동일 입력에서 DepKB를 사용할 때 모델 결과를 낸다”는 처치 충실도만 증명한다. 누락 감소, Terraform 정확성, 생성 가능성, 앱 기능 성공은 별도 평가가 필요하다. 관계 cardinality·제약은 정적 참조만으로 검증되지 않으므로, 기존 구성요소 통과율을 의존성 정확도로 해석하지 않는다. 과거 빈 `requiredDependencies` 사례 표는 이후 제거하고 근거 projection에서 기대 edge를 파생하도록 교정했다.

## 결정

추가 capability와 사례를 늘리지 않는다. 세 capability를 유지하되 정적 투영, provider validate, 실제 생성, 앱 기능, 정리를 서로 다른 게이트로 보고한다. control의 금지 조건은 대표 사용자 요구가 아니라 단일변수 누출을 막는 실험 조건으로만 사용한다.

## 2026-08-09 후속 측정 교정

빈 `requiredDependencies` 벤더 표는 삭제했다. 평가기는 profile의 `componentDeltas`, provider, 근거가 연결된 `component-projections.json`으로부터 다음 기대값을 실행 시 파생한다.

- 정적 참조 관계: Terraform 산출물에서 합격·실패를 채점한다.
- cardinality: 기대값은 보존하지만 별도 검증 전까지 점수에서 제외한다.
- 배치·런타임 제약: 다중 AZ, 전용 subnet, certificate 수, 장치 format/mount 등은 별도 게이트로 유지한다.

고정 provider fixture에서 파생된 정적 참조 관계는 AWS 9/9, Azure 11/11, GCP 11/11이 관측됐다. 이는 fixture와 평가기의 개발 검증이며 LLM 생성 결과나 실제 클라우드의 성공률이 아니다. CNA 감사기는 정적 관계 측정 준비를 `true`, cardinality·제약 주장 준비를 `false`로 명시한다.

과거에 실패로 보존된 `easydep-full-ps-treatment-azure-20260808T085013Z-9d5397`의 attempt 4도 새 평가기로 재채점했다. 요청 provider는 Azure였지만 실제 Terraform provider는 AWS였고, Azure의 attachment→disk 및 attachment→VM 참조는 0/2였다. 전체 정적 점수는 passed 5, failed 12, unknown 2, pass rate 0.263158이며 실험 적격이 아니다. 이 결과는 성공률 표본이 아니라 교정된 평가기가 실제 provider 불일치와 dependency 누락을 검출하는 개발 반례다. 별도의 기존 P2-Azure 성공 실행과 혼동하지 않는다.

## 2026-08-09 정적 관측과 기능 검증의 추가 분리

- `one-to-many` 같은 cardinality 문자열은 최소 인스턴스 수를 뜻하지 않는다. endpoint별 최소·최대
  multiplicity와 전체 edge 관측 계약이 없으므로 `not-measured`로 기록하고 점수에서 제외한다.
- constraint 선언이 projection에 보존됐다는 사실을 실제 gate 구현으로 표현하지 않도록 CNA 감사 필드를
  `constraintDeclarationsPreserved`로 변경했다. 감사 결과는 계속 cardinality·constraint 주장 적격을
  `false`로 유지한다.
- guest mount 명령 문자열은 배포 의도의 정적 흔적일 뿐 format, mount 성공, 재시작 후 영속성을
  증명하지 않는다. 따라서 `guestConfiguration` 구성요소는 `observed-unverified`로 기록해 정적 통과
  점수에서 제외한다. 실제 성공은 계약 경로를 사용한 컨테이너 재시작 또는 cloud 기능 gate에서만 판정한다.

교정된 평가기로 manifest와 application snapshot이 함께 남은 component 개발 run 8개를 재평가해
`artifacts/measurements/component-artifact-reassessment-20260809.json`에 기록했다. 그중 projection
edge가 요구되는 treatment 산출물은 PS-treatment Azure와 TLS-treatment Azure 두 개뿐이었다. 전자는
요청과 달리 AWS provider를 생성해 필수 참조 0/2, 후자는 유효 provider 경계를 확정하지 못하고 필수
참조 0/9였다. 나머지 여섯 개는 control이므로 이 edge의 성공 표본이 아니다. 따라서 현재 저장 자료로
full/no-depkb의 의존 관계 누락 효과를 계산하지 않으며, 이 재평가는 평가기가 실패 반례를 놓치지 않는다는
개발 증거로만 사용한다.
