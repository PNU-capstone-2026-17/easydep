# Azure 영속성 후보 실행 준비성 점검

## 판정

실제 cloud apply는 아직 시작하지 않는다. 현재까지 검증된 것은 앱 단위 테스트, IaC 정적 계약, Azure provider validate이며, 실제 실행에 필요한 불변 이미지와 재현 가능한 최종 IaC가 아직 함께 보존되지 않았다.

## apply 전 발견한 결함

기존 P2 Azure 스냅샷의 cloud-init은 부팅할 때마다 `mkfs.ext4 -F`를 실행했다. 이 구성은 독립 managed disk를 만들고 VM에 연결하는 데 성공하더라도 VM 재생성 시 기존 파일시스템을 지울 수 있다. 따라서 리소스 생성 가능성과 앱의 영속 기능 성공은 별개의 게이트라는 연구 범위를 직접 보여 준다.

이를 특정 SQLite 또는 경로 규칙으로 처리하지 않고 다음 일반 계약으로 보완했다.

- 영속 볼륨은 파일시스템이 없을 때만 포맷한다.
- 부팅 스크립트의 볼륨 초기화는 멱등적이어야 한다.
- 계약된 컨테이너 접근 경로와 실제 Docker mount target이 일치해야 한다.
- 명백한 무조건 `mkfs`는 `BIND-STORAGE-DESTRUCTIVE-INIT`으로 진단하고 `implementation.vm_delivery`에 귀속한다.

## 재실행 결과와 선택 편향 방지

일반 계약 추가 후 동일 앱 스냅샷의 VM delivery만 재실행했다.

1. 첫 실행은 진단 해소, provider validate, 앱 테스트를 모두 통과했다. 상위 단계 재실행은 0회였다.
2. 성공 산출물을 임시 디렉터리 밖에 보존하도록 하네스를 보완한 뒤 재실행했으나, Terraform template 안의 셸 변수를 이스케이프하지 않아 provider validate에서 실패했다.
3. 일반 template 경계 규칙을 추가한 다음 실행은 수리 출력에서 고정 provider 선언을 누락해 provider contract에서 실패했다.

성공 결과가 나올 때까지 반복하면 생성 변동성이 가려지고 성공 샘플 선택 편향이 생긴다. 따라서 추가 LLM 재시도는 중단했다. 최신 실패 원시는 `app-cloud-snapshot-repair-storage-20260809.json`에 남기며, 직전 성공 원시는 커밋 `a9294e3`에서 추적할 수 있다.

## 남은 실행 입력

- 앱 테스트를 통과한 정확한 최종 application/IaC 스냅샷과 해시
- 외부 VM이 pull할 수 있는 불변 container image digest
- 비커밋 SSH 공개 키 및 TLS 인증서·키 입력
- 비용 상한과 cleanup reserve

현재 로컬 Docker 접근도 권한/daemon 상태로 사용할 수 없음을 확인했다. 이미지 입력이 준비되지 않은 상태에서 Terraform apply만 수행하면 앱 기능이 아니라 자원 생성만 측정하게 되므로 실행하지 않는다.

## 다음 개발 범위

새 registry subsystem이나 무제한 자동 재시도는 만들지 않는다. 다음 회차에는 생성 변동성을 결과로 유지하면서, 성공 산출물을 즉시 해시 보존하는 하네스가 독립 테스트로 검증되는지 확인한다. 이후 정확한 후보와 이미지 digest가 모두 준비된 경우에만 Azure 한 셀을 `apply → ready → POST/GET → 재시작 후 데이터 조회 → destroy → residual 0` 순서로 실행한다.

## 실제 P2 후보 후속 점검

기존 P2 실행의 `repairs/attempt-1`은 앱 로직 단계에서 SQLite 구성과 누락 드라이버 문제를 H2 file 저장으로 복구한 스냅샷이다. 이 앱을 콘텐츠 해시 이미지로 빌드해 로컬 컨테이너에서 health, Notes POST/GET, 컨테이너 재시작 뒤 레코드 보존을 29.407초에 확인했다. 반면 복구 전 원본 P2 앱은 패키징에는 성공했지만 `org.sqlite.JDBC` 누락으로 컨테이너 시작에 실패했다. 따라서 MockMvc 통과와 패키징된 런타임 성공은 구분해야 한다.

실제 P2 스냅샷을 입력으로 VM delivery만 다시 실행한 결과는 66.891초에 provider validate와 앱 테스트를 통과했고, 해시 `3663abf673fe...` 후보가 보존됐다. 그러나 보존 파일을 다시 읽는 후속 게이트에서 `lsblk` 목록의 첫 장치를 데이터 디스크로 선택하는 `BIND-STORAGE-DEVICE-AMBIGUOUS`가 발견됐다. Azure attachment가 아직 보이지 않거나 OS 부모 디스크가 먼저 열거되면 잘못된 장치를 선택할 수 있으므로 이 후보도 cloud apply에서 제외했다.

로컬 이미지는 registry에 게시하지 않았고, 실험 컨테이너·볼륨·두 로컬 태그를 제거했다. 세부 판정은 `cloud-p2-candidate-post-audit-20260809.json`에 기록했다.

장치 식별 계약을 추가한 뒤 실제 P2 VM delivery를 한 번 더 실행했다. 출력은 고정 장치 경로와 대기 방향을 시도했지만 `templatefile` 변수 map에는 `container_port`를 제공하고 템플릿에서는 `application_port`를 요구했다. provider validate가 80.380초에 승격을 차단했고 보존 후보는 생성되지 않았다. 일반 template 경계 규칙이 이미 있는데도 단일 수리 한도에서 해소되지 않았으므로 추가 재시도는 하지 않았다. 원시는 `app-cloud-p2-device-repair-20260809.json`에 보존한다.
