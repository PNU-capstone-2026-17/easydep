# 용량 질문·HA 상태 충돌·VM 반영 개발 파일럿

## 최소 용량 질문과 부분 재개

provider, region, 월 예산을 고정하고 vCPU·메모리 하한만 비운 자연어 입력을 실행했다. 최초 실행은
42.666초, LLM 7회였고 `minVCpu` 또는 `minMemoryGiB`를 묻는 권고 질문을 생성했다. 사용자가
`minVCpu=2`로 답하자 7.851초, LLM 1회에 계약이 갱신됐다. 재개 호출은
`CloudConstraintExtraction` 하나뿐이어서 clarify와 배포 필요사항 도출을 반복하지 않았다.

## HA와 노드 파일 상태

자연어 입력에 로컬 파일 저장과 AZ 전체 장애 생존을 함께 명시했다. 요구사항 에이전트는 두 요구를
각각 수락하고 `multiZone=true`를 만들었다. 실제 LLM scaffold는 `java.nio.file.Files`와
`${FILE_DB_PATH:/data/records.db}`를 사용했지만 기존 관측기가 JDBC 경로만 찾아 처음에는 통과했다.

관측기를 특정 DB 이름이 아니라 다음 두 증거의 결합으로 일반화했다.

- Java 파일 I/O API가 실제 소스에 존재한다.
- 외부설정 placeholder가 절대 파일 경로를 제공한다.

재관측 결과 앱 계약에 `accessScope=node-filesystem`, `accessPath=/data`가 생겼고,
`multiZone=true`와 결합해 `BIND-STATE-HA-001` 질문을 생성했다. 단순 경로 문자열이나 로그 경로만
있는 control은 상태로 분류하지 않는다.

사용자가 상태 외부화 또는 복제를 선택한 수정도 한 번 실행했다. 모델은 파일 상태를 없애지 않고
같은 경로를 “모든 인스턴스가 접근 가능한 shared file”이라고 설명만 바꿨다. 결정론적 재검증은 이를
다시 `needs_input`으로 막았다. 현재 VM 범위에 공유 파일 시스템이나 상태 복제 capability가 없으므로
이 결과를 성공으로 세지 않는다. 요구사항 완화 선택을 상류 요구사항 checkpoint로 되돌리는 연결이
남아 있다. 사용자 선택 기반 수정은 원본 앱 snapshot을 임시 보존하며, 재검증 실패나 예외가 나면
수정 전 파일을 복원한다.

이 판정의 기술적 근거는 다음 공식 문서다.

- [AWS EBS 연결](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-attaching-volume.html): 볼륨과 VM은 같은 가용 영역이어야 하며 Multi-Attach도 별도 조건이다.
- [Azure Managed Disks](https://learn.microsoft.com/en-us/azure/virtual-machines/managed-disks-overview): 공유 디스크의 다중 VM 사용에는 클러스터 관리자와 write locking이 필요하다.
- [GCP 영역 리소스](https://docs.cloud.google.com/compute/docs/regions-zones/global-regional-zonal-resources): zonal disk는 같은 zone의 VM에 연결된다.

공식 문서는 CSP 저장소의 제약을 뒷받침한다. `BIND-STATE-HA-001`의 질문 문구와 수정 소유 작업은
EasyDep의 연구 가설이다.

## VM 선택과 IaC 반영

65,032건 고정 카탈로그에서 VM당 2 vCPU, 4 GiB, 월 compute 예산 1,000 USD로 선택했다.

| CSP | 리전 | 추천 | 월 compute 목록가격 | 성능 주의 | Terraform 관측 |
|---|---|---|---:|---|---|
| AWS | ap-northeast-2 | t3a.medium | 34.16 USD | burst 경고 | `aws_instance.instance_type` 통과 |
| Azure | eastus | Standard_B2als_v2 | 27.45 USD | burst 경고 | `azurerm_linux_virtual_machine.size` 통과 |
| GCP | asia-northeast3 | e2-standard-2 | 62.76 USD | 없음 | `google_compute_instance.machine_type` 통과 |

HCL AST에서 VM 리소스의 literal 또는 변수 기본값을 읽어 추천값과 비교한다. 다른 값이나 기본값 없는
동적 변수는 `BIND-VM-SIZE-001`로 실패한다. Azure Korea Central은 카탈로그에 정확한 가격 리전이 없어
`region_not_exact_in_catalog`로 보류됐으며 다른 리전에 임의 매핑하지 않았다.

이번 IaC 연결 시험의 provider schema 검증은 주입형 generator라 `skipped`다. 따라서 실제 Terraform
생성 가능성, cloud apply, 앱 처리량, 전체 비용을 주장하지 않는다. 기계 판독 결과는
`feedback-conflict-vm-pilot-20260809.json`에 있다.
