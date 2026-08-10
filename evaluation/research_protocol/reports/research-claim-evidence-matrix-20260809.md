# 연구 주장–증거–한계 최종 연결표

이 문서는 불변 원천인 `docs/research.md`의 세 목표를 현재 구현과 직접 증거에 연결한다. 구현 존재, 정적 검증, 로컬 앱 기능, 실제 cloud 기능을 서로 대신 사용하지 않는다.

## 최종 주장 범위

EasyDep은 AWS·Azure·GCP의 Docker-on-Linux-VM 범위에서 다음을 지원하는 개발 지원 시스템이다.

1. 공식 근거 기반 클라우드 capability와 벤더별 리소스 관계를 설계·IaC 생성에 전달한다.
2. 앱 런타임 요구와 VM 배포 환경 사이의 관측 가능한 불일치를 계약으로 진단한다.
3. 요구사항·설계·구현·테스트 역할을 연결하고 실패 소유 하위 작업부터 부분 재개한다.
4. 사용자 하한 또는 개발 부하 실측에서 계산한 하한·compute 예산·고정 목록가격·성능 경고 범위에서 VM 후보를 안내한다.

범용 클라우드 모델의 완결성, 최적 비용·실제 처리량 보장, 멀티 에이전트 구조만의 인과적 우월성, 모든 앱·CSP에 대한 일반 성공률은 주장하지 않는다.

## 목표 1: 산출물 검증과 사용자 피드백

| 세부 주장 | 구현 | 직접 증거 | 판정 | 한계·남은 최소 작업 |
|---|---|---|---|---|
| 앱 요구–클라우드 불일치를 조기에 찾는다 | `app_cloud_contracts.py`, `iac_binding_validation.py` | `app-cloud-ablation-evaluation.md`, `../measurements/2026-08-development/cloud-p2-candidate-post-audit-20260809.json` | 제한 범위 충족 | 고정 진단군의 개발 사례이며 일반 오류 탐지율이 아님 |
| 앱 기능과 리소스 생성 가능성을 분리한다 | build, provider, container, 업무 API, 재시작 gate 분리 | P2 원본의 패키징 성공 후 컨테이너 시작 실패, 복구본의 POST/GET·재시작 보존 성공 | 직접 관찰 충족 | 실제 Azure apply는 안전 후보 부재로 미실행 |
| 모호·충돌 시 질문하고 영향을 받은 단계만 갱신한다 | `needsQuestion`, resume, 요구사항 revision | `ambiguity-and-sizing-evaluation.md`, `../archive/2026-08-development/feedback-conflict-vm-pilot-20260809.md`, 오케스트레이션 회귀시험 | 부분 충족 | HA 요구 완화 선택의 자연어→최종 앱 기능 종단 1회가 남음 |
| 실패 산출물을 조용히 승격하지 않는다 | HCL/provider/계약 gate, 원자적 IaC 교체 | template 변수·provider 선언·장치 선택 실패가 후보 보존/apply 전에 차단됨 | 충족 | 실패 종류별 성공률을 일반화하지 않음 |

## 목표 2: 클라우드 특성·의존성·VM 가이드

| 세부 주장 | 구현 | 직접 증거 | 판정 | 한계·남은 최소 작업 |
|---|---|---|---|---|
| 세 capability를 벤더 리소스군으로 투영한다 | DepKB claim, component projection, provider realization | `capability-projection-measurement-20260809.md`, `component-pilot-results-20260809.md` | 개발 측정 충족 | PS/LB/TLS 세 축에 한정; 정적 projection은 앱 기능 증거가 아님 |
| 필수 관계 누락을 정적으로 측정한다 | provider별 근거 projection에서 필수 edge 기계 파생 | fixture reference AWS 9/9, Azure 11/11, GCP 11/11 및 edge 제거 반례 | 평가기 개발 충족 | cardinality·영역·게스트 제약은 별도 gate이며 정적 점수에서 제외 |
| DepKB 제공이 생성 결과를 개선한다 | 동일 앱·요구분석 출력의 full/no-depkb VM delivery 3반복 | `docs/depkb-effect-evaluation-20260810.md`, `component-fixed-treatment-summary-20260810.json` | 평균 양의 개발 신호 | delivery +18.5%p·의존 완결 +22.2%p이나 축·CSP·반복 변동성이 크고 모집단 일반화 불가 |
| 생성 효과가 실제 cloud paired 기능 비교로 승격 가능한가 | 전달 성공과 근거 참조 완결을 분리한 54셀 실패모드 분석 | `component-fixed-failure-analysis-20260810.json` | LB의 GCP 반복 1·AWS 반복 2 두 쌍만 승격 후보 | 실제 apply 전 후보 판정이며 TLS와 다른 CSP의 기능효과를 뜻하지 않음 |
| 정적 생성 개선이 실제 cloud 앱 기능으로 이어지는가 | 두 LB 후보의 plan preflight와 AWS full 원본 apply | `component-cloud-preflight-20260810.json`, `aws-lb-r2-full-apply-probe-20260810.json` | 두 쌍 모두 앱 기능 미관측 | GCP·AWS no-depkb는 plan 실패, AWS full은 다중 AZ·AMI 문제로 apply 실패; 정적 개선을 기능 개선으로 주장할 수 없음 |
| 의존 관계가 실제 기능에 영향을 준다 | cloud 개입 실행기 | GCP backend-service↔backend-group 제거 시 기능 실패, 복원 시 성공 3회, residual 0 | 한 관계 직접 관찰 | 다른 CSP·관계로 일반화하지 않음 |
| 실측 하한으로 VM 후보를 안내한다 | HTTP·CPU·RSS 관측기, 하한 계산기, 65,032건 고정 catalog | `artifacts/measurements/http-capacity-development-point-20260810.json`, `capacity-recommendation-development-20260810.json` | 개발 경로 충족 | 단일 로컬 관측과 compute 목록가격만 사용; cloud 처리량·전체 비용·최적성 미보장 |
| 실제 생성 앱이 Azure 영속 자원을 안전하게 사용한다 | 멱등 포맷·mount·장치 선택 진단 | 로컬 P2 앱 기능은 성공, 생성 후보는 장치·template gate에서 실패 | 미충족 | 안전한 해시 후보가 생긴 경우에만 Azure 한 셀 apply·기능·정리 수행 |

## 목표 3: 에이전트 연계와 단계별 산출물

| 세부 주장 | 구현 | 직접 증거 | 판정 | 한계·남은 최소 작업 |
|---|---|---|---|---|
| 네 개발 단계와 구현 하위 역할을 연결한다 | 요구사항→설계→구현→테스트 graph와 provider registry | `docs/current-system-status.md`, run manifest와 단계 결과 | 구조 충족 | 구조 자체의 우월성은 입증하지 않음 |
| 실패 지점부터 부분 복구한다 | 동일 run checkpoint, repair owner, retry history | dependency·port·storage 수리에서 상위 단계 실행 0회; 체크포인트 hash/attempt 검증 | 개발 관찰 충족 | 전체 표본 성공률이 아님 |
| 병목을 하위 작업 수준으로 기록한다 | LLM·HCL·provider·앱 probe timing event | P2 VM delivery 생성 35.785초, 수리 16.214초, init 9.527초, validate 4.932초 등 | 충족 | 벤치마크 성능 우열로 해석하지 않음 |
| 외부 기준 시스템과 실용 성능을 비교한다 | CoT·MetaGPT 실행기와 공통 평가기 | 실행기·계약 존재 | 미충족 | 안정화 뒤 각 조건 1회 파일럿으로 비용·시간을 먼저 확인 |

## 현재 완료 판정

시스템의 제한된 구현 주장은 뒷받침되지만 `research.md` 전체 목표의 확인적 실험 완료는 아니다.
2026-08-10의 최신 수치와 허용 가능한 주장은 `docs/research-results-20260810.md`를 기준으로 한다.
남은 확인 작업은 다음과 같다.

1. 안전한 Azure P2 후보가 한 번의 개발 실행에서 모든 사전 gate를 통과할 경우에만 실제 apply·업무 기능·재시작 보존·destroy·residual 0을 수행한다. 계속 실패하면 실패 원인과 미충족 상태를 최종 한계로 보고한다.
2. EasyDep full·CoT·MetaGPT의 작은 시스템 수준 파일럿은 전체 시스템 비교로만 해석하고, 감당 가능한 경우에만 최소 반복한다.
3. 실제 cloud 후보에서 동일 부하를 반복하기 전에는 로컬 용량 하한을 최종 권장으로 표현하지 않는다.

이 작업을 위해 새 capability, 새 리소스 분류, 범용 registry subsystem 또는 학습 기반 용량 예측 모델을 추가하지 않는다.
