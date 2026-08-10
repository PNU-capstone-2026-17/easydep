# `research.md` 목표 충족을 위한 잔여 계획

## 범위 판정

현재 방향은 학부 졸업과제 범위에 맞다. 대상은 AWS·Azure·GCP의 Docker-on-Linux-VM이며,
범용 클라우드 모델이나 자동 용량 예측이 아니라 다음 연결을 구현·평가한다.

```text
자연어 요구사항
→ 근거 있는 클라우드 capability와 벤더별 리소스 관계
→ 앱–클라우드 계약 및 사용자 질문
→ 단계별 앱·Docker·Terraform 산출물
→ 정적 검증, 실제 생성, 앱 기능, 정리
```

P1~P3은 모델의 근거가 아니라 종단 회귀 사례다. 모든 합법 토폴로지 열거, CSP 전체 API 전수화,
부하에서 VM 사양을 자동 예측하는 모델, 멀티 에이전트 구조만의 인과효과는 수행하지 않는다.

## 연구 목표별 현재 증거

| `research.md` 목표 | 현재 증거 | 판정 | 남은 최소 증거 |
|---|---|---|---|
| 산출물 검증과 사용자 피드백 | 앱 계약 고정입력 절제, provider/region·용량 질문, HA 요구 완화의 같은 run revision과 앱 기능 검증 | 구현 충족·종단 의미 실패 관찰 | 교정된 영속성 배선의 회귀 유지와 최소 cloud 기능 gate |
| 클라우드 특성·의존성·비용·성능 가이드 | 공식 근거 DepKB, PS/LB/TLS 고정입력 절제, 65,032건 VM 카탈로그, 3 CSP 추천→IaC 반영 gate | 제한 범위 충족·실제 cloud 불균등 | 안전한 해시 후보가 있을 때만 Azure P2 apply·기능·cleanup; 목록가격 범위 유지 |
| 에이전트 연계와 단계별 산출물 | 4단계·구현 하위 작업, revision별 checkpoint, 실패 소유 작업 부분 재개, full/no-validator 수정 파일럿과 시간 계측 | 제한 범위 구현·개발 증거 충족 | P1~P3·외부 기준선은 시스템 수준 보조평가로만 보고 |

## 완료된 네 작업 묶음

1. 의존성 측정기를 교정했다. mount 하드코딩을 제거하고 정적 edge, cardinality, runtime constraint를
   분리했으며 필수 edge를 근거 projection에서 파생한다.
2. DepKB 고정입력 절제를 수행했다. 이는 projection 처치 충실도이며 앱·cloud 성공과 구분한다.
3. 앱–클라우드 validator 절제와 자연어 질문/부분 재개를 구현했다. 실패한 수정은 원본 앱 snapshot으로
   복원한다.
4. VM 선택의 용량·compute 예산·성능 경고를 실제 카탈로그에서 확인하고 추천값의 Terraform 반영을
   HCL AST로 검증한다.

## 잔여 실행 순서와 중단 기준

### 1. 사용자 선택의 상류 회송

`BIND-STATE-HA-001`에서 `revise-availability-requirement`를 선택하면 같은 run의 요구사항 계약에
명시적 사용자 결정을 추가하고 설계 이후만 무효화한다. 요구사항 원문을 조용히 덮어쓰지 않는다.
한 자연어 사례에서 질문→선택→요구사항/설계 갱신→scaffold 재검증→앱 기능 성공을 확인하면 멈춘다.
공유 파일 시스템이나 관리형 DB capability는 이 작업을 위해 새로 추가하지 않는다.

같은 run의 질문→두 차례 명시적 요구사항 정정→revision별 요구사항·설계→구현→앱 기능 검증은
완료했다. provider validate, Docker build, HTTP 업무 기능, 컨테이너 재시작 영속성은 통과했지만
Terraform 영속 디스크·mount가 누락되어 의미 평가는 실패했다. 따라서 사용자 피드백 회송 가능성은
확인했으나 종단 배포 성공으로 세지 않는다. 고정 capability 별칭 대신 상태 durability 의미로 계약
배선을 교정했고, 같은 자연어 사례를 성공할 때까지 반복하지 않는다.

### 2. 앱 계약의 실제 효과

동일한 사전 생성 앱 snapshot을 `full`과 `no-consistency-validator`에 복제한다. 기존 네 진단군 중
build/runtime dependency, port, storage path 세 종류만 사용하고 새 오류 문자열을 추가하지 않는다.
발견 단계, 변경 파일, 상류 재실행 회피, 복구 시간, build·HTTP 업무 기능을 기록한다. 1회 파일럿에서
처치가 분리되지 않으면 본 반복을 중단하고 경계만 수정한다.

동일 생성 앱 snapshot의 입력 동일성, 조기 탐지, downstream Gradle 결과까지 완료했다. dependency는
후속 컴파일에서도 발견됐지만 port와 storage target은 앱 테스트가 통과해 validator의 별도 역할이
관찰됐다. 남은 범위는 각 소유 하위 작업의 LLM 수정 1회, 변경 파일, 수정 후 build·HTTP 기능이다.

소유 하위 작업 수정 파일럿도 완료했다. dependency와 port는 상위 단계 재실행 없이 진단 해소와
MockMvc HTTP acceptance test 통과를 확인했다. storage target은 초기 한 번의 수정 뒤 안전하게
실패했고, validator와 생성 지침의 일반 container-target 계약을 정렬한 뒤 Azure provider·binding과
HTTP test를 통과했다. 세 고정 사례의 개발 성공을 일반 성공률로 확대하지 않는다. 남은 핵심
증거는 최소 cloud 셀의 apply·ready·업무 기능·정리와 자연어 종단 사례다.

### 3. 클라우드 최소 종단 확인

PS/LB/TLS의 정적 생성과 provider validate는 3 CSP 개발 셀에서 확인한다. 실제 cloud apply는 비용과
정리 가능성이 확인된 capability별 최소 셀만 수행하며 `apply`, ready, 업무 기능, 요구된 재시작/장애
기능, destroy를 분리 기록한다. destroy 실패 시 새 실행을 시작하지 않고 정리를 우선한다.

기존 GCP LB·HTTPS 개입 3회가 실제 control 기능, 관계 제거 실패, 복원, cleanup을 이미 충족하므로
중복 실행하지 않는다. 남은 단일 후보는 Azure 영속 저장이다. 한 차례 static·provider·앱 test를
통과했지만 성공 산출물이 임시 디렉터리와 함께 제거되어 정확한 후보가 보존되지 않았다. 이후
보존 재실행 두 번은 각각 template 변수 경계와 provider contract에서 실패했으므로 성공할 때까지
반복하지 않는다. 해시로 보존된 검증 후보와 실행 입력이 함께 준비된 경우에만 한 셀에서
apply·ready·업무 POST/GET·restart 영속성·destroy·residual 0을 확인한다.

최신 감사에서도 보존된 안전 후보와 registry image digest가 없으므로 Azure apply를 시작하지 않았다.
AWS 태그 자원, Azure EasyDep 리소스 그룹, GCP EasyDep 프로젝트의 읽기 전용 조회는 모두 0이었다.
이는 cleanup 확인이지 실제 Azure 종단 성공 증거가 아니다.

### 4. 최종 보조 평가

P1~P3은 EasyDep 전체의 종단 실용성, CoT·MetaGPT는 시스템 수준 비교로만 사용한다. 각 조건 1회
파일럿의 시간·토큰·검열률을 본 뒤 감당 가능한 반복 수를 정한다. DepKB 효과는 동일 입력
`full/no-depkb`, validator 효과는 동일 snapshot `full/no-validator`에서만 해석한다.

저장된 P2-Azure 산출물에 현재 공통 평가기를 균등 적용한 개발 파일럿을 완료했다. CoT와 MetaGPT는
각각 고정 provider 버전 불일치/구문 오류와 Docker build 입력 누락으로 조기 실패했다. EasyDep의
구현 하위 작업 복구본은 정적 의미 13/13, provider 검증, health, CRUD, 컨테이너 교체 영속성을
통과했다. 실행 시점의 코드 리비전이 다르므로 이는 평가 절차와 실패 분리의 확인이며 시스템 간
효과 추정은 아니다. 다음 비교는 같은 현재 리비전에서 새로 생성한 각 1회로 제한한다.

## 최종 보고 지표

- 요구사항 ID→capability→CSP 리소스/간선→IaC 관측의 추적 여부
- 정적 reference 누락, provider validate, 실제 create를 별도 결과로 보고
- build, container 시작, health, 업무 API, 재시작/장애 기능을 별도 보고
- 질문 정확성, 답변 뒤 변경 계약, 재실행 단계, 오수정 차단 여부
- VM 용량 충족, compute 목록가격, 성능 경고, IaC 추천값 반영
- 단계·하위 작업·LLM·provider·기능 probe·cleanup 시간

효과크기나 일반화를 주장할 반복이 부족하면 원시 사례 결과와 한계를 제시한다. `research.md`는
수정하지 않으며 이 문서는 실행 계획과 주장 경계만 담당한다.
