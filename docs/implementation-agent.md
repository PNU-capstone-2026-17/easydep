# 구현 단계

EasyDep에는 서로 목적이 다른 두 구현 실행 경로가 남아 있다. 새 종단 실행과 비교실험은
`app/core/orchestration/` 경로를 기준으로 한다. FastAPI의 비동기 구현 job은 기존 웹 UI와
산출물 버전 API를 위한 독립 경로다.

## 현재 종단 실행 경로

4단계 오케스트레이터의 구현 단계는 다음 하위 작업을 순서대로 수행한다.

```text
소프트웨어·클라우드 설계
  → 애플리케이션 골격 생성
  → 수정 불가능한 수용 테스트 생성
  → 업무 로직 완성
  → VM 후보 선택
  → Dockerfile과 VM Terraform 생성
```

주요 코드는 다음 위치에 있다.

| 위치 | 역할 |
|---|---|
| `app/core/orchestration/adapters/` | 구현, VM 선택, 테스트 provider 연결 |
| `app/core/orchestration/scaffold_worker.py` | 격리된 구현 골격 작업 실행 |
| `app/core/orchestration/vm_selection.py` | 최소 용량 필터와 가격·성능 후보 선택 |
| `app/implementation/engine/` | 구현 IR, 품질 gate, Docker·IaC renderer |
| `app/core/cloudkb/` | VM 가격·성능·의존성 근거 |

기본 provider 조합과 실행 API는 [오케스트레이션 문서](../app/core/orchestration/README.md)를
따른다. provider가 실패해도 다른 provider로 자동 전환하지 않으며, 실제 선택은 실행
manifest에 기록된다.

현재 멤버 구현기는 완성되지 않았다. 비교실험에서는 `implementation_scaffold=llm`을
명시하며, 이 임시 경로와 멤버 결과를 동일한 것으로 취급하지 않는다.

## 산출물과 검증

종단 실행 산출물은 다음 위치에 기록된다.

```text
artifacts/runs/<run-id>/03-implementation/
```

구현 단계는 애플리케이션 소스, 수용 테스트, Dockerfile과 CSP별 VM Terraform을 생성한다.
테스팅 단계는 Gradle 테스트를 실행하고 테스트가 0개인 성공을 거부한다. Docker build,
health, 업무 API, OpenTofu 및 IaC 의미 검증은 비교군에 공통으로 적용되는 외부 평가기의
책임이다.

현재 배포 범위는 Kubernetes가 아니라 AWS·Azure·GCP의 Docker-on-VM이다. 세부 범위는
[VM 기반 클라우드 네이티브 확장](cloud-native-extension.md)을 참고한다.

## 웹 API 구현 job

`app/implementation/api.py`와 `app/implementation/worker.py`는 저장된 설계 산출물을 읽어
비동기 구현 job을 실행하고 MySQL artifact version으로 결과를 보존한다.

주요 endpoint:

| 메서드와 경로 | 역할 |
|---|---|
| `POST /api/implementation/apps/{app_id}/jobs` | 구현 job 생성 |
| `GET /api/implementation/jobs/{job_id}` | 상태와 승인 대기 조회 |
| `POST /api/implementation/jobs/{job_id}/approval` | HITL 승인 또는 거부 |
| `POST /api/implementation/apps/{app_id}/feedback-jobs` | 기존 산출물 피드백 수정 |
| `GET /api/implementation/apps/{app_id}/artifacts/{type}` | 최신 파일 산출물 조회 |

이 경로의 내부 엔진은 checkpoint 복구, phase별 승인, 제한된 편집 작업과 파일 snapshot
버전 관리를 제공한다. HTTP 요청·응답의 정확한 계약은 [API 문서](api.md) 또는 실행 중인
`/docs`를 기준으로 한다.

## 필요한 도구

- Python 의존성: `requirements.txt`
- JDK 21과 저장소 내 Gradle wrapper
- Node.js/npm 및 puml2code 도구
- OpenAPI Generator 7.24.0
- Docker와 OpenTofu: 종단 외부 평가를 수행할 때 필요

구현 도구는 다음 스크립트로 준비한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-implementation-tools.ps1
```

과거 Kubernetes manifest 생성 설계는
[이력 문서](archive/kubernetes-deployment-file-generation.md)에만 보관하며 현재 계약으로
사용하지 않는다.
