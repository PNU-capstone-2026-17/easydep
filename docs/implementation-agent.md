# 시스템 구현 에이전트

## 저장소 내부 구성

- `app/implementation/api.py`: 구현 job 생성, 상태 조회, HITL 승인, 파일 산출물 조회 API
- `app/implementation/worker.py`: 단일/제한 병렬 background queue, checkpoint 복구, DB 저장
- `app/implementation/prototype_client.py`: 내부 엔진 subprocess 경계와 오류 증거 수집
- `app/implementation/engine/`: 구현 계획·OpenHands 실행·검증·감사 엔진
- `app/implementation/tools/puml2code-bce/`: jupe/puml2code 기반 BCE Java 변환 fork
- `app/implementation/tools/gradle/`: Gradle 8.14.2 Wrapper
- `scripts/bootstrap-implementation-tools.*`: npm runtime과 OpenAPI Generator 7.24.0 준비

EasyDep 밖의 별도 구현 저장소, 개인 가상환경, 절대 도구 경로는 사용하지 않는다. 실행
checkpoint와 생성 workspace는 기본적으로 `.easydep/implementation-runs/`에
놓이며 Git에서 제외된다.

## 자연어 소스 피드백

완료된 구현의 현재 `SOURCE_CODE`, `TEST_CODE`, 배포/IaC snapshot을 기준으로 증분 수정
job을 만들 수 있다.

```http
POST /api/implementation/apps/{app_id}/feedback-jobs
Content-Type: application/json

{
  "feedback": "배송이 시작된 주문은 취소할 수 없도록 수정하고 기존 테스트를 보강해 주세요.",
  "base_package": "com.example.generated",
  "allow_assumptions": false
}
```

피드백 job은 기존 파일을 별도 immutable run에 복원하고 `FEEDBACK_REVISION` OpenHands
task 하나를 계획한다. 이후 상태 조회와 전송 승인은 최초 구현 job과 같은 API를 사용한다.
OpenHands는 기존 파일만 수정할 수 있으며 새 파일 추가는 허용하지 않는다. 수정 후 전체
`compileJava test`와 완료 감사를 다시 수행하고, 결과는 기존 내용을 덮어쓰지 않고 새 파일
artifact 버전으로 저장한다. 저장 metadata에는 피드백, 부모 job, 기준 artifact 버전이 남는다.

피드백을 실행하기 전에 규칙 기반 적합성 판별을 수행한다. BCE/클래스 다이어그램의
클래스·필드·메서드 계약, OpenAPI endpoint/요청·응답/schema, 시퀀스 호출 순서, ERD의
테이블·컬럼·관계 변경을 명시한 피드백은 구현 피드백에 부적합하므로 `REJECTED`로 종료한다.
이 경우 다른 에이전트나 이전 설계 단계로 자동 전달하지 않으며, OpenHands 실행·승인 요청·
새 구현 run도 만들지 않는다. 결과는 `feedback-eligibility.json`에 남는다. 기존 계약 안의
동작·오류 처리·검증 보강 피드백만 OpenHands 수정 및 전체 검증 루프로 전달된다.

피드백 revision도 복원 직후 BCE/OpenAPI 계약 기준선을 다시 저장하고, 해당 생성 파일은
OpenHands의 writable allowlist에서 제외한다. 따라서 피드백 경로에서도 최초 구현과 같은
무결성·구조 계약 검증을 받는다.

## 결정적 배포 파일 생성

배포 의도는 시스템 구현 에이전트가 생성한다. 구현 완료 감사가 끝난 뒤 구현 에이전트는
배포 다이어그램의 외부 진입점과 cloud resource spec의 workload·networking·registry를
읽어 `easydep-deployment-intent/v1alpha1` JSON을 추론하고 검증한다. 이 intent는 설계
산출물이 아니며, 구현 run의 `reports/deployment-intent.json`과
`reports/deployment-render.json`에 증거로 기록된다. 수동 CLI 실행에서는 검토를 마친
intent JSON을 입력으로 제공해 추론을 대체할 수도 있다.

각 workload는 `Deployment`, `StatefulSet`, `Job`, `CronJob` 중 하나이며 다음 capability를
독립적으로 활성화할 수 있다.

- Service, Ingress, HPA, PodDisruptionBudget
- NetworkPolicy, ServiceAccount, ExternalSecret
- ConfigMap, PersistentVolumeClaim, ServiceMonitor

Ingress는 Service를, ServiceMonitor는 Service를, HPA는 Deployment 또는 StatefulSet과
유효한 min/max replica 범위를 요구한다. Job/CronJob에 서비스·Ingress·HPA·PDB를 요청하면
렌더링 전에 거부한다. ExternalSecret은 기존 External Secrets Operator와
ClusterSecretStore의 정확한 `storeName`·`remoteKey`가 intent에 명시된 경우에만 생성한다.
결과 YAML은 DNS 이름, 구조, HPA/Ingress 및 Pod의 ServiceAccount·ConfigMap·Secret·PVC
참조를 검증하고
`reports/deployment-render.json`에 확정 intent, 파일 목록, 검증 결과를 기록한다.

결정적 renderer는 구현 및 E2E 완료 감사에 성공한 뒤 실행된다. 이전 renderer 보고서에
기록된 관리 파일만 먼저 제거하므로 capability나 workload를 삭제해도 오래된 manifest가
남지 않는다. 실제 비밀값은 생성하지 않으며, 이미지 placeholder·Ingress class/TLS Secret·
완전 개방 egress처럼 배포 전에 확정해야 할 사항은 render report의 warning으로 남긴다.
검증된 결과는 새 `DEPLOYMENT_FILE` artifact 버전으로 저장된다.

## 결정적 IaC 생성

cloud resource spec의 `provider`가 `azure`, `aws`, `gcp` 중 하나이면 배포 파일 렌더링 직후
implementation agent가 `application/terraform/`에 Terraform을 생성한다. Azure는 VNet, ACR,
AKS, MySQL, Key Vault, Log Analytics를, AWS는 VPC/subnet, ECR, EKS, RDS, Secrets Manager,
CloudWatch Logs를, GCP는 VPC/subnetwork, Artifact Registry, GKE, Cloud SQL, Secret Manager,
Cloud Logging을 지원한다. 클러스터와 컨테이너 레지스트리가 함께 있으면 각 provider의
이미지 pull 권한 연결(AcrPull, ECR read policy, Artifact Registry reader)을 함께 생성한다.
EKS에는 managed node group을, GKE에는 node pool을 함께 생성하므로 생성된 Kubernetes
manifest를 실제로 스케줄할 수 있다. AWS의 EKS cluster/node IAM role ARN·name과 region,
GCP의 project/region 및 cluster별 GKE node service-account, Azure의 resource group/location 및 MySQL
관리자 비밀번호는 배포 환경에서 Terraform 변수로 제공해야 한다.
resource spec의 `dependsOn`은 VPC/VNet·subnet·Kubernetes cluster의 참조 관계를 결정한다.
특히 EKS cluster는 동일 네트워크에 연결된 서로 다른 `availabilityZone`의 subnet 두 개 이상을
명시해야 하며, 이 조건을 충족하지 못하면 IaC 생성이 중단된다. `iac-render.json`의
`requiredVariables`는 배포 전에 주입해야 하는 provider별 입력값을 구조화해 제공한다.
`reports/iac-render.json`은 resource spec 리소스의 Terraform 반영 여부와 deployment intent의
workload가 Kubernetes manifest 및 이미지 pull 권한과 연결되는지, EKS/GKE node 구성과
VPC·subnetwork 참조가 존재하는지를 검증한다. 오류가 있으면 IaC artifact를 저장하지 않는다. IaC는 `IAC_CODE`의
불변 artifact 버전으로 저장된다.

Terraform CLI가 설치된 환경에서는 `python -m app.implementation.engine.cli validate-iac <run>`으로
격리된 임시 복사본에서 `terraform fmt`, `init -backend=false`, `validate`를 실행할 수 있다.
동일 검증은 IaC renderer가 workflow 완료 전에 자동 실행하며, Terraform이 설치되어 있지 않거나
검증에 실패하면 IaC 생성 자체가 실패한다. 따라서 `IAC_CODE` artifact를 저장하기 전에 Terraform CLI를
반드시 실행 환경에 설치해야 한다.
기본적으로 `PATH`의 `terraform`을 사용하며, Windows 환경에서 PATH를 갱신하지 않은 경우에는
`EASYDEP_TERRAFORM_PATH`에 `terraform.exe`의 절대 경로를 지정할 수 있다.

## Registry image resolution

배포 manifest는 workload별 `__EASYDEP_REGISTRY_<registryRef>__` marker를 보존하고, Terraform은 모든
registry의 주소를 `registry_image_bases` map output으로 생성한다. 여러 registry를 사용하는 경우에는
각 workload의 `registryRef`를 cloud resource spec의 registry `id`로 명시하고, 여러 cluster가 있으면
`clusterRef`도 cluster `id`로 명시한다. IaC 검증은 workload의 두 참조에 해당하는 정확한 image-pull
권한 리소스가 Terraform에 있는지 확인한다. registry가 하나뿐일 때는 registry와 cluster를 자동 추론할 수
있지만, 여러 후보가 있으면 renderer가 실패로 처리한다.

`application/k8s/render-images.sh <terraform-dir> <output-dir>`은 Terraform apply 이후 output map으로
marker를 치환한 별도 manifest tree를 생성한다. `application/k8s/deploy.sh <terraform-dir> [terraform apply options...]`는
Terraform apply → 이미지 주소 치환 → kubectl apply를 순서대로 실행하는 최종 배포 entry point이다. 실행 권한에
의존하지 않도록 `sh application/k8s/deploy.sh <terraform-dir> [terraform apply options...]` 형태로 호출한다.
원본 manifest를 수정하지 않으므로, 생성 단계에서는 결정적 산출물을 유지하면서도 실제 배포에서는 registry의
실제 endpoint를 사용한다. `EASYDEP_IMAGE_TAG`에는 이미 registry에 push된 불변 이미지 tag를 반드시
지정해야 하며, 지정하지 않으면 치환·배포를 중단한다. 이 스크립트는 이미지를 build/push하지 않으므로 해당
단계는 사용자의 release workflow가 먼저 수행해야 한다. 스크립트는 `EASYDEP_TERRAFORM_PATH`가 있으면
그 절대 경로를, 없으면 PATH의 `terraform`을 사용한다. 실행에는 Terraform, Python 3, kubectl 및 각 provider
인증이 필요하다.

## 자동 실행 단계

1. MySQL에서 현재 `CLASS`, `SEQUENCE`, `API_SPEC`, `ERD`, `DEPLOYMENT`,
   `RESOURCE_SPEC` 설계를 읽는다.
2. BCE puml2code fork로 Boundary·Control·Entity·Gateway Java 계약을 만든다.
3. OpenAPI Generator로 Spring API interface와 DTO 계약을 만든다.
4. Gradle Wrapper로 생성 계약을 컴파일한다.
5. 설계 중립 IR로 아래 phase DAG를 계획한다.
   - Control 구현
   - ERD persistence entity/repository/mapper/schema
   - OpenAPI inbound adapter
   - BCE Boundary adapter
   - Gateway outbound adapter
   - Spring application wiring
   - 실제 구매 흐름을 포함한 설계 기반 E2E 테스트
6. 현재 실행 가능한 phase의 prompt·설계·관련 소스 hash로 외부 전송 요청 ID를 만들고
   `AWAITING_APPROVAL`에서 멈춘다.
7. 최초 승인에는 기본적으로 같은 run의 제한된 repair/revalidation 전송 위임도 포함한다.
   위임은 run ID·설계 입력 hash·초기 implementation manifest의 전체 task ID·최대 3회 repair·
   최대 50 task 시도에 묶인다. 따라서 최초 구현의 모든 정상 phase와 규칙 기반 repair plan에
   기록된 task에 적용된다. 계약/설계 변경, 입력 hash 변경, manifest에 없던 task, 한도 초과는
   위임 범위를 벗어나므로 새 승인이 필요하다. 사용자는 승인 요청의
   `delegate_repair_approvals: false`로 이 동작을 끌 수 있다.
8. 정확히 일치하는 승인 후 OpenHands restricted editor로 해당 phase 전체를 실행한다.
9. 매 task와 phase 뒤 컴파일·테스트·의미 품질 gate·완료 감사를 수행한다. 모든 unit/E2E
   테스트가 통과한 최종 run에서는 LLM 대신 규칙 기반 소스 설계 적합성 gate를 추가로 수행한다.
   생성 직후 기록한 `reports/generated-source-contracts.json`의 BCE/OpenAPI Java hash와
   현재 파일을 먼저 비교한다. 해시가 다르면 클래스 종류·이름, 필드 이름·타입, 메서드 이름·
   반환 타입·파라미터·예외 선언을 다시 비교하여 추가·수정·삭제를 구체적으로 기록하고
   거부한다. BCE로
   매핑 가능한 시퀀스 다이어그램 호출이 구현 소스의 호출로 존재하는지도 확인한다. 결과는
   `reports/source-design-conformance.json`에 남으며 실패하면 artifact 저장과 배포 렌더링을
   진행하지 않는다. 별칭이나 외부 참여자처럼 정적으로 매핑할 수 없는 시퀀스 호출은 warning
   으로 기록해 오탐으로 인한 차단을 피한다.
   스켈레톤 변경은 로컬 기준선으로 즉시 복원한 뒤 Gradle·완료 감사·적합성 검증을 다시
   실행한다. 시퀀스 검증은 구현 주체가 대상 port를 의존하고 호출하는지, 동일 주체의 호출
   순서가 다이어그램과 같은지, `alt`/`else`의 식별 가능한 조건 토큰이 소스에 있는지를
   확인한다. 위반은 보고서를 포함한 제한된 repair task와 E2E 재검증 task로 최대 3회
   재계획하며, 새 외부 전송에는 새 승인이 필요하다.
10. 검증 오류가 다른 phase의 소스를 가리키면 해당 파일의 소유 task를 수리 대상으로
   되돌리고, 영향을 받는 Wiring과 E2E task를 자동으로 재계획한다. 파일 경로가 없는 E2E
   HTTP 실패는 관련 OpenAPI adapter를 우선 수리 대상으로 삼는다.
11. 위임 범위 안의 소스 적합성 수리와 컴파일·단위/E2E 실패의 cross-phase repair 전송은
    자동으로 다음 실행을 시작하고, 범위를 벗어난 전송만 다음 승인을 기다린다.
12. 완료 감사와 소스 설계 적합성 gate가 모두 통과한 뒤 결정적 renderer로 배포 파일을 생성하고 파일 트리를 `SOURCE_CODE`,
    `TEST_CODE`, `DEPLOYMENT_FILE`의 새 불변 버전으로 MySQL에 저장한다. `IAC_CODE`는
    후속 IaC 생성 단계에서 사용한다.

소스 구현의 완료 조건은 Control, Persistence, API·Boundary·Gateway adapter, Spring
wiring과 설계 기반 E2E 테스트다. 이 완료 감사가 성공한 뒤 배포 파일 renderer가 후속으로
실행된다. IaC 생성은 아직 이 workflow의 범위에 포함하지 않는다. 상세한 입력·산출물·검증
범위는 [배포 파일 생성](deployment-file-generation.md)을 참고한다. 설계 계약 자체가 부족한
경우에는 `NEEDS_INPUT`에서 멈추며 시스템 설계 에이전트를 자동 호출하거나 설계 산출물을
임의로 수정하지 않는다.

## API와 보안

요청·응답 schema, job 상태, 오류 코드와 예시는 [HTTP API 명세](api.md)의 시스템 구현
절을 단일 기준으로 사용한다. 새 phase에는 새 요청 ID가 필요하며, 이전 ID 재사용이나 승인
전 실행은 거부된다. API key는 body, checkpoint, DB, event journal에 기록하지 않고 서버의
`NVIDIA_API_KEY`만 자식 프로세스가 상속한다.

## clone 후 준비

Python 3.11 이상, JDK 21, Node.js/npm을 설치한 뒤 다음을 실행한다.

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts/bootstrap-implementation-tools.ps1
python -m pytest
uvicorn server:app --reload
```

Linux에서는 `sh scripts/bootstrap-implementation-tools.sh`를 사용한다. Bootstrap은
OpenAPI Generator JAR SHA-256을 검증하고, npm production dependency만 설치하며, Gradle
Wrapper 버전을 확인한다. Docker build에서는 같은 Linux bootstrap이 자동 실행된다.

## 중단과 재개

job 상태는 각 job 디렉터리의 `easydep-job-state.json`, 엔진 상태는 run의
`reports/workflow-state.json`에 원자적으로 저장된다. 서버 재시작 시 `QUEUED`, `PLANNING`,
`RUNNING` job을 찾고, 이미 저장된 정확한 승인 파일이 있는 실행만 재개한다. prompt와 출력
hash가 같은 성공 task는 건너뛰며, 실행 중 끊긴 task는 `INTERRUPTED`로 되돌려 다시 수행한다.
NVIDIA NIM의 429, 일시적 5xx, timeout 및 연결 오류는 제한된 지수 backoff로 최대 3회
재시도한다. 컴파일 오류가 다른 task의 allowlist 파일을 가리키면 현재 task의 수리 횟수를
소진하지 않고 `reports/repair-plan.json`에 소유 task와 후속 재검증 대상을 기록한다. 수리
증거도 prompt hash와 HITL 요청에 포함되므로 기존 승인을 재사용하지 않는다.
