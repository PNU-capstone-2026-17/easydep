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

## 결정적 배포 파일 생성

구현 job에 `DEPLOYMENT`와 `RESOURCE_SPEC` 산출물이 모두 있으면, 구현·E2E 단계 다음에
외부 LLM 호출 없이 결정적 renderer가 실행된다. `deployment_intent`가 제공되면
`easydep-deployment-intent/v1alpha1` 계약을 사용하고, 없으면 cloud resource spec의
workload·networking·registry·secret-store 근거로 intent를 생성한다.

각 workload는 `Deployment`, `StatefulSet`, `Job`, `CronJob` 중 하나이며 다음 capability를
독립적으로 활성화할 수 있다.

- Service, Ingress, HPA, PodDisruptionBudget
- NetworkPolicy, ServiceAccount, ExternalSecret
- ConfigMap, PersistentVolumeClaim, ServiceMonitor

Ingress는 Service를, ServiceMonitor는 Service를, HPA는 Deployment 또는 StatefulSet과
유효한 min/max replica 범위를 요구한다. Job/CronJob에 서비스·Ingress·HPA·PDB를 요청하면
렌더링 전에 거부한다. 결과 YAML은 구조와 HPA/Ingress/selector 참조를 검증하고
`reports/deployment-render.json`에 확정 intent, 파일 목록, 검증 결과를 기록한다.

배포 다이어그램과 리소스 명세는 task context로 전달된다. 실제 비밀값은 생성하지 않으며,
배포 파일은 `apiVersion`·`kind`·`metadata`, 프로브, HPA, Secret 예시를 정적 게이트로
검증한 뒤 전체 Gradle 검증과 함께 새 `DEPLOYMENT_FILE` artifact 버전으로 저장된다.

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
7. 정확히 일치하는 승인 후 OpenHands restricted editor로 해당 phase 전체를 실행한다.
8. 매 task와 phase 뒤 컴파일·테스트·의미 품질 gate·완료 감사를 수행한다.
9. 검증 오류가 다른 phase의 소스를 가리키면 해당 파일의 소유 task를 수리 대상으로
   되돌리고, 영향을 받는 Wiring과 E2E task를 자동으로 재계획한다. 파일 경로가 없는 E2E
   HTTP 실패는 관련 OpenAPI adapter를 우선 수리 대상으로 삼는다.
10. 새 소스와 수리 증거를 반영해 후속 prompt와 요청 ID를 다시 만들고 다음 승인을 기다린다.
11. 성공 phase의 파일 트리를 `SOURCE_CODE`, `TEST_CODE`의 새 불변 버전으로 MySQL에
    저장한다. 저장 계층은 이후 배포 단계 합류를 위해 `DEPLOYMENT_FILE`, `IAC_CODE`도
    분류할 수 있지만 현재 workflow는 이를 생성하지 않는다.

현재 자동 생성 완료 범위는 Control, Persistence, API·Boundary·Gateway adapter, Spring
wiring과 설계 기반 E2E 테스트까지다. 배포·IaC 생성은 이 workflow의 완료 조건에 포함하지
않는다. 설계 계약 자체가 부족한 경우에는 `NEEDS_INPUT`에서 멈추며 시스템 설계 에이전트를
자동 호출하거나 설계 산출물을 임의로 수정하지 않는다.

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
