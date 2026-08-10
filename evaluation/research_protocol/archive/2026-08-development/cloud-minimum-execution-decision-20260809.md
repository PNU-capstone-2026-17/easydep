# 실제 클라우드 최소 실행 결정

## 결정

지금 즉시 새 `apply`를 시작하지 않는다. 현재 실제 클라우드 증거를 재사용하면 LB·HTTPS 의존성의
기능 효과는 이미 GCP에서 세 번 확인됐고, 새로 남은 핵심 gap은 **생성 앱의 영속 저장 종단 1셀**이다.
하지만 현재 Azure 저장 후보는 정적 gate와 앱 테스트까지 통과했을 뿐, 실제 실행에 필요한
컨테이너 이미지·SSH·TLS 변수 값이 아직 결합되지 않았다. 이를 비워 둔 채 apply하는 것은 기능
검증이 아니라 리소스 생성만 반복하므로 실행하지 않는다.

## 현재 증거의 층위

| 증거 | 범위 | 현재 판정 | 대신 주장할 수 없는 것 |
|---|---|---|---|
| 3사 native CLI replication | VM·disk·network·LB의 create/reference/delete 관찰 | 벤더별 의존 관계 근거 | EasyDep 생성 IaC·앱 종단 성공 |
| GCP backend-service↔backend-group 개입 3회 | control의 `/readyz`·업무 HTTP 성공, 관계 제거 시 기능 실패, 복원 성공 | 실제 의존 관계의 기능 효과와 cleanup 확인 | AWS·Azure 및 모든 LB 토폴로지 일반화 |
| P1/P2와 snapshot 파일럿 | 앱 build·MockMvc HTTP·재시작 영속성·부분 복구 | 앱 기능과 계약 validator 역할 확인 | 실제 cloud endpoint 성공 |
| 현재 AWS/Azure/GCP IaC preflight | HCL·provider schema·binding | 생성 가능성의 사전 조건 | provider create·ready·업무 기능 |

GCP 개입 결과 `intervention.gcp.backend-service-backend-group.necessity.json`은 세 반복 모두 control
provision/startup/function 통과, 관계 제거 뒤 function 실패, 관계 복원 뒤 function 통과를 기록한다.
`cleanupVerified=true`, `residualResources=[]`, budget·scheduler censoring은 세 번 모두 false다. 이
증거는 LB와 HTTPS 경로의 실제 기능 gate를 다시 실행하지 않을 근거다.

## 현재 영속 저장 후보

최신 `app-cloud-snapshot-repair-storage-20260809.json` 결과는 다음과 같다.

- 소유 하위 작업: `implementation.vm_delivery`
- 상위 단계 재실행: 0
- LLM 호출: 생성 1회, 검증 피드백 수정 1회
- 수정 단계: 65.256초
- Azure provider init: 5.769초, validate: 8.319초
- 앱–cloud storage binding: 통과
- MockMvc HTTP acceptance test: 통과, 45.277초
- cloud apply: 미실행

초기 실패에서는 생성 지침이 data disk의 attach·guest mount만 요구하고 Docker target과
`applicationMountPath`의 동일성을 명시하지 않았다. production validator가 이미 요구하는 일반
계약과 생성 지침을 정렬했다. 호스트 source와 컨테이너 target은 서로 달라도 되며 target만 앱
접근 경로와 같아야 한다. 특정 DB·경로 별칭·사례 ID는 추가하지 않았다.

## 잔여 리소스 확인

2026-08-08T23:30:01Z에 읽기 전용 CLI로 실험 접두사를 확인했다.

- AWS `ap-northeast-2`: 실행 중·중지 VM, EBS volume, ELBv2 0
- Azure: `easydep`·`depkb`·`edbg` resource group 0
- GCP `cloud-resource-testing`: VM, backend service, forwarding rule, HTTPS proxy, URL map,
  health check, instance group, disk, network 0

상세 범위와 한계는 `cloud-residual-audit-20260809.json`에 보존했다. 접두사가 다른 사용자 자원은
조회 결과에 포함하지 않았다.

## 다음 실제 실행의 단일 후보

새 cloud 실행은 Azure 영속 저장 1셀만 후보로 둔다. 다음 조건이 모두 준비될 때만 시작한다.

1. 현재 검증된 application·IaC snapshot을 불변 digest로 보존한다.
2. 생성 앱 Docker image를 임시 registry에 게시하고 digest로 고정한다.
3. SSH public key, TLS certificate/key, image URI 등 required Terraform 변수를 실행 시점의
   비커밋 파일이나 환경으로 결합한다.
4. 예상 비용이 bundle cap 안이고 cleanup reserve를 침범하지 않는지 확인한다.
5. `apply → VM/container ready → health → 업무 POST/GET → container 또는 VM restart → 동일 데이터
   조회 → destroy → 접두사 residual 0`을 각각 독립 phase로 기록한다.
6. 어느 phase에서든 실패하면 새 셀을 시작하지 않고 destroy와 residual 확인을 먼저 수행한다.

임시 registry까지 범용 배포 subsystem으로 구현하는 것은 학부 과제 범위를 넘는다. 실험용으로
한 이미지 digest를 준비하고 기존 `container_image` 입력 계약에 주입하는 정도만 허용한다. 이
1셀은 “모든 CSP에서 종단 성공”을 주장하기 위한 것이 아니라, 현재 시스템이 생성한 앱 계약과
영속 cloud 자원이 한 번 실제로 연결되는지 확인하는 최소 보완이다.

## 2026-08-10 최종 실행 판정

최신 동일 스냅샷 평가에서 storage binding 수리가 실패해 안전 후보가 남지 않았다. 실패 소유 작업인
`implementation.vm_delivery`만 한 번 더 실행했으며 요구사항·설계·앱 생성은 재실행하지 않았다.
118.7초 동안 생성 1회와 provider 피드백 수정 1회를 수행했지만, 컨테이너 경로가 계속
`/mnt/evaluation-mismatch`로 관측돼 계약 경로 `/srv/state`와 일치하지 않았다. 상위 단계 실행은
0회이고 승격 후보도 없다.

따라서 위 “다음 실제 실행” 조건 1을 충족하지 못해 Azure apply를 수행하지 않는다. 특정 사례 경로를
프롬프트에 추가하거나 정적 gate를 우회하지 않으며, 실제 생성 앱의 Azure 영속 저장 종단은 최종
미충족으로 보고한다. 원시 결과는
`artifacts/confirmatory/app-cloud-storage-retry-20260810.json`이다.
