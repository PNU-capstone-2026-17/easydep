# GCP backend group 기능 개입 실행서

## 목적

`backendService.backends[].group` 참조가 제어면 객체의 생성에만 필요한지, 실제
Docker-on-VM 애플리케이션의 트래픽 처리에 필요한지를 분리해 확인한다. 현재 공식 문서는
참조의 존재는 보여 주지만 빈 backend service의 생성 자체를 금지하지 않으므로 이 한 건만
실증 대상으로 남았다.

## 사전조건

- 비용과 삭제 권한이 허용된 전용 GCP 프로젝트
- 활성 `gcloud` 계정과 명시적인 프로젝트·리전·존
- 다른 사용자가 쓰지 않는 실험 전용 VPC와 이름 접두사
- P3 계약을 만족하는 VM 두 대, instance group, health check, backend service, URL map,
  target HTTPS proxy, forwarding rule, 인증서
- 비밀값을 제외한 제어면 응답과 HTTP 원시 결과를 보존할 결과 디렉터리

현재 작업공간 점검에서는 Cloud SDK와 기본 프로젝트는 발견했지만 활성 계정은 발견하지
못했다. 따라서 유료 리소스를 생성하거나 기존 프로젝트를 변경하지 않았다.

## 반복 단위

각 반복은 `dependency-experiment-plan.json`의 여덟 단계를 순서대로 수행한다.

1. 기준 리소스가 모두 안정 상태인지 조회한다.
2. 두 VM, 컨테이너, 앱 프로세스의 기동 상태를 확인한다.
3. 외부 주소에서 `/readyz`와 대표 업무 요청을 반복해 기준 성공을 확인한다.
4. backend service의 다른 필드는 유지하고 `backends[].group`만 제거한다.
5. 변경 작업과 backend service가 안정 상태인지 확인한다.
6. VM·컨테이너·앱이 계속 실행 중인지 확인한다.
7. 동일 외부 주소에 동일 기능 오라클을 적용한다.
8. 원래 group 참조를 복구하고 동일 기능 요청의 회복을 확인한다.

한 반복을 마칠 때마다 원상복구하고 다음 반복을 시작한다. 세 반복 사이에는 리소스 ID와
결과 파일을 분리한다. 다른 구성 변경, 재부팅, 이미지 갱신을 함께 수행하지 않는다.

## 판정과 보존

- 생성 거부는 `provisionBlocked`, 런타임 정지는 `runtimeBlocked`, 앱 요청만 실패하면
  `functionBlocked`, 모두 유지되면 `noEffect`다.
- 세 반복 모두 기준 기능 성공, 개입 기능 실패, 복구 기능 성공이어야 필수성을 확정한다.
- `budgetCensored` 또는 `schedulerDelayed` 반복은 실패로 세지 않고 재실행한다.
- 결과는 `dependency-intervention-result-template.json` 구조로 작성해
  `intervention-results/intervention.gcp.backend-service-backend-group.necessity.json`에 둔다.
- 프로젝트 ID는 허용하지만 토큰, 키, 쿠키, 인증서 개인키, 사용자 식별 정보는 보존하지
  않는다.

결과를 배치한 뒤 다음 명령이 `ready=true`가 되어야 확인적 멀티 에이전트 실험을 실행할
수 있다.

```powershell
python -m evaluation.research_protocol.commands.readiness
```

## 2026-08-08 실행 결과

`cloud-resource-testing` 프로젝트에서 서로 다른 접두사의 bundle 세 개를 직렬 실행했다. 각 반복은 소형 VM, 비관리형 instance group, health check, global backend service, URL map, target HTTPS proxy, 하루짜리 self-signed 인증서와 forwarding rule로 동일한 `/readyz` 및 `/business` 기능 oracle을 구성했다.

세 반복 모두 다음 순서를 재현했다.

1. 제어 구성에서 VM 실행과 두 HTTPS 기능 요청이 성공했다.
2. backend service에서 instance group 참조만 제거한 뒤 제어 평면과 VM 실행은 유지됐지만 두 기능 요청은 실패했다.
3. 같은 group 참조를 복구하자 두 기능 요청이 다시 성공했다.
4. 반복별 리소스를 역순으로 삭제하고 `edbgint` 접두사 잔존 목록이 0건임을 확인했다.

반복별 소요 시간은 약 16분, 15분 31초, 15분 32초로 모두 bundle당 45분 측정 창 안이었다. 결과 판정은 세 반복 모두 `functionBlocked`이며, `gcp.backend-service-backend-group.necessity`는 `replicated-removal-recovery` 근거로 `confirmed`가 되었다. 원시 명령 응답과 HTTP 관측은 `intervention-results`의 반복별 evidence JSON에 보존했다.

첫 실행 시도는 로컬 OpenSSL 탐색 실패로 원격 생성 전에 중단됐다. 해당 기록은 `intervention-results/attempts/20260808-openssl-preflight-failure`에 별도로 보존했으며 그 시도에서도 잔존 리소스는 0건이었다.
