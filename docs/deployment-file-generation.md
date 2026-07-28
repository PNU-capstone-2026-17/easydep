# 시스템 구현 에이전트의 배포 파일 생성

시스템 구현 에이전트는 생성된 Spring 애플리케이션의 소스·테스트 검증이 끝난 뒤,
배포 다이어그램과 클라우드 리소스 명세를 사용해 Dockerfile 및 Kubernetes manifest를
자동 생성한다. Kubernetes YAML 자체는 LLM이 작성하지 않는다. 구현 에이전트가 먼저
배포 의도(deployment intent)를 추론하고, 결정적 renderer가 그 intent를 파일로 변환한다.

## 실행 조건과 시점

일반 구현 job에는 클래스 다이어그램과 OpenAPI 명세가 필수다. 배포 파일 생성을 위해서는
추가로 다음 중 하나가 필요하다.

- cloud resource spec (`RESOURCE_SPEC`)
- 수동 CLI 실행에서 전달한 검증 완료 deployment intent JSON

배포 다이어그램은 cloud resource spec과 함께 사용해 외부 진입점 및 workload의 노출 의도를
판단한다. 구현 phase와 E2E 테스트가 모두 성공하고 완료 감사가 `COMPLETE`가 된 경우에만
renderer가 실행된다. 따라서 소스 구현이 실패하거나 승인 대기 중인 job에서는 배포 파일을
만들지 않는다.

```text
배포 다이어그램 + cloud resource spec
                 ↓
시스템 구현 에이전트의 deployment intent 추론·검증
                 ↓
결정적 Dockerfile / Kubernetes renderer
                 ↓
YAML·이름·리소스 참조 검증
                 ↓
run 보고서 및 DEPLOYMENT_FILE artifact 저장
```

## 배포 의도

시스템 구현 에이전트가 현재 run에서 생성하는 `easydep-deployment-intent/v1alpha1` JSON이다. 이는 Kubernetes YAML의 축약본이 아니라
**애플리케이션을 어떤 방식으로 실행·노출·확장·운영할지에 대한 선언적 중간 모델**이다.
예를 들어 ``orders-api``가 HTTP API이므로 Service와 Ingress가 필요하고, replica 범위가
2~5이므로 HPA와 PDB가 필요하다는 판단을 표현한다. renderer는 이 판단을 바탕으로 필요한
manifest만 선택해 생성한다.

- workload 이름, 유형: `Deployment`, `StatefulSet`, `Job`, `CronJob`
- 이미지, 포트, replica 범위, CPU·메모리, health endpoint
- Service, Ingress, HPA, PDB, NetworkPolicy, ServiceAccount의 필요 여부
- ConfigMap, PVC, ExternalSecret, ServiceMonitor의 필요 여부

간략한 형태는 다음과 같다.

```json
{
  "schemaVersion": "easydep-deployment-intent/v1alpha1",
  "namespace": "orders",
  "workloads": [{
    "name": "orders-api",
    "kind": "Deployment",
    "image": "registry.example/orders-api:<tag>",
    "replicas": { "min": 2, "max": 5 },
    "capabilities": {
      "service": true,
      "ingress": true,
      "hpa": true,
      "pdb": true,
      "networkPolicy": true
    }
  }]
}
```

생성된 의도는 아래 파일에서 확인할 수 있다.

```text
<run-root>/reports/deployment-intent.json
```

수동 검토가 필요한 경우 `plan-deployment` CLI에 이 형식의 JSON을 입력하여 자동 추론을
대체할 수 있다. 이 입력도 renderer가 schema와 workload 간 제약 조건으로 검증한다.

### YAML보다 먼저 의도를 생성하는 이유

Kubernetes manifest의 종류와 개수는 서비스마다 다르다. 단순 HTTP API는 Deployment와
Service만 필요할 수 있지만, 외부 노출이 있으면 Ingress가 추가되고, 가변 부하이면 HPA와
PDB가 추가된다. StatefulSet에는 headless Service와 저장소가 필요할 수 있으며, batch 작업은
Service나 Ingress가 필요하지 않다. 따라서 처음부터 정해진 파일 묶음을 만들거나 LLM에게
YAML을 바로 작성하게 하면 불필요한 리소스가 생기거나 필요한 리소스가 빠질 수 있다.

배포 의도를 중간 단계로 두면 다음을 분리할 수 있다.

- **판단**: 다이어그램과 resource spec을 근거로 어떤 workload와 capability가 필요한지 추론한다.
- **검증**: `Job`에 Ingress를 붙이거나, HPA에 유효하지 않은 replica 범위를 주는 등 의미상
  모순된 요청을 YAML 생성 전에 거부한다.
- **렌더링**: 검증된 intent를 동일 입력에 대해 동일 결과를 내는 결정적 템플릿으로 변환한다.

이 분리는 LLM의 역할을 "배포 판단"으로 제한하고 YAML 문법·들여쓰기·리소스 참조 오류를
결정적 renderer가 통제하게 한다. 또한 `deployment-intent.json`을 보면 왜 특정 manifest가
생성됐는지 추적할 수 있고, intent만 변경해 재렌더링하면 이전 renderer가 관리한 오래된 파일도
안전하게 제거할 수 있다.

## 생성 파일

생성 대상은 구현 run의 `application/` 아래다.

```text
<run-root>/
├── application/
│   ├── Dockerfile
│   ├── .dockerignore
│   └── k8s/
│       ├── namespace.yaml
│       └── <workload>/
│           ├── deployment.yaml | statefulset.yaml | job.yaml | cronjob.yaml
│           ├── service.yaml                 # 필요 시
│           ├── ingress.yaml                 # 필요 시
│           ├── hpa.yaml / pdb.yaml           # 필요 시
│           ├── network-policy.yaml           # 필요 시
│           ├── service-account.yaml          # 필요 시
│           ├── config-map.yaml / pvc.yaml    # 필요 시
│           ├── external-secret.yaml          # 필요 시
│           └── service-monitor.yaml          # 필요 시
└── reports/
    ├── deployment-intent.json
    └── deployment-render.json
```

StatefulSet에는 headless Service를 만들고, renderer가 이전 실행에서 관리했던 파일만 정리한
뒤 새 결과를 쓴다. 그래서 capability나 workload를 제거한 뒤 재렌더링해도 오래된 manifest가
남지 않는다.

## Secret 및 외부 의존성

Key Vault가 resource spec에 있다는 사실만으로 `ExternalSecret`을 만들지 않는다.
`ExternalSecret`은 intent에 아래 정보가 모두 명시된 경우에만 생성된다.

- 기존 `ClusterSecretStore`의 정확한 이름
- 실제 secret의 `remoteKey`
- 클러스터에 External Secrets Operator와 필요한 cloud identity가 구성되어 있음

실제 secret 값은 어떤 산출물이나 보고서에도 기록하지 않는다. Ingress controller, TLS Secret,
Prometheus Operator CRD처럼 클러스터 밖에서 준비해야 하는 조건은 render report의
`externalPrerequisites`와 `warnings`에 표시된다.

## 검증 범위

renderer는 다음을 검증한다.

- deployment intent JSON Schema와 DNS-1123 이름
- CronJob schedule, resource quantity, workload 유형별 capability 제약
- YAML decoding 및 Kubernetes 객체의 필수 필드
- HPA → workload, Ingress → Service 연결
- Pod → ServiceAccount, ConfigMap, ExternalSecret target Secret, PVC 연결

### 입력 설계 반영 검증

구조 검증과 별도로 renderer는 `sourceConformance` 검증을 수행한다. 이는 "YAML이
유효한가"가 아니라 "생성된 YAML이 배포 다이어그램과 cloud resource spec의 요구를
보존했는가"를 확인한다.

- cloud resource spec의 각 workload가 deployment intent에 존재하는지
- workload의 replica min/max, container port, CPU·메모리 request/limit, readiness/liveness path가
  cloud resource spec과 같은지
- resource spec이 PVC 또는 ExternalSecret을 명시했을 때 해당 capability와 설정이 intent에
  반영됐는지
- `diagramAlias`로 workload를 배포 다이어그램 alias와 연결한 경우, 외부에서 도달하는
  workload에 Service가 있고 HTTPS 환경에서는 Ingress도 있는지
- intent가 요구한 workload 종류, 이미지, 포트, replica, resource, probe, capability별
  Kubernetes 리소스가 실제 manifest에 존재하는지

불일치가 있으면 renderer는 `deployment-render.json`에
`sourceConformance.status: FAILED`와 오류 목록을 먼저 기록한 뒤 job을 실패시킨다.
따라서 잘못된 배포 파일이 `DEPLOYMENT_FILE` artifact로 저장되지 않는다.

배포 다이어그램에 workload alias를 연결할 수 없는 경우(예: resource spec에
`diagramAlias`가 없음)는 오류 대신 warning을 남긴다. 이 경우 resource spec 반영 여부는
검증하지만, 해당 workload가 다이어그램에서 외부 노출되는지까지는 자동으로 확인할 수 없다.
엄격한 검증이 필요한 workload에는 `diagramAlias`를 지정해야 한다.

검증 결과는 아래 보고서에 저장된다.

```text
<run-root>/reports/deployment-render.json
```

`SUCCEEDED_WITH_WARNINGS`는 렌더링 결과가 구조적으로 유효하지만, 실제 배포 전에 이미지 tag,
Ingress class/TLS, NetworkPolicy egress 목적지처럼 환경별 값을 확정해야 한다는 뜻이다.
`sourceConformance`는 이 상태와 별개로 입력 설계 반영 여부를 나타낸다.

현재 renderer는 클러스터에 설치된 CRD·admission policy와 실제 컨테이너 이미지 빌드·기동까지는
검증하지 않는다. 이 검증은 CI/CD에서 `docker build`, Kubernetes schema validator,
`kubectl apply --dry-run` 또는 대상 클러스터의 policy 검증으로 추가해야 한다.

## Artifact 저장

구현 worker는 `application/Dockerfile`, `.dockerignore`, `k8s/` 아래 파일을
`DEPLOYMENT_FILE` artifact로 분류해 MySQL에 버전 저장한다. API를 통해 현재 버전과 과거
버전을 조회할 수 있으며, 소스 코드 피드백 job은 저장된 배포 파일 snapshot도 함께 복원한다.
