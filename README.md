# EasyDep

EasyDep은 자연어 요구사항에서 소프트웨어 설계, 구현, 테스트 및 VM 배포 산출물까지 생성하는
멀티 AI 에이전트 기반 개발 지원 시스템이다. 현재 지원 범위는 AWS·Azure·GCP의
Docker-on-VM 애플리케이션이다.

현재 구현 수준과 알려진 한계는 [현재 시스템 상태](docs/current-system-status.md)를 기준으로 한다.
문서 전체의 역할은 [문서 안내](docs/README.md)에서 확인할 수 있다.

## 파이프라인

```text
사용자 요구사항
  → 1. 요구사항 분석
  → 2. 소프트웨어·클라우드 설계
  → 3. 소스·수용 테스트·Dockerfile·Terraform 생성
  → 4. 테스트
  → 공통 외부 평가
```

| 영역 | 위치 | 역할 |
|---|---|---|
| 요구사항 | `app/requirements/` | 요구사항 구체화, FR/NFR 분류, 유스케이스 및 다이어그램 생성 |
| 설계 | `app/design/` | 클래스·시퀀스·ERD·OpenAPI·배포 설계 |
| 구현 | `app/implementation/` | 애플리케이션 소스, 테스트, Dockerfile, Terraform 생성 |
| 테스팅 | `app/testing/` | 생성 애플리케이션 검증 |
| 오케스트레이션 | `app/core/orchestration/` | 4단계 provider 연결, 실행·재개·상태 저장 |
| 클라우드 지식 | `app/core/cloudkb/` | VM 자원 의존성, 가격 및 성능 데이터 |
| 평가 | `evaluation/` | EasyDep·CoT·MetaGPT 공통 비교 평가 |

실행 결과는 `artifacts/runs/<run-id>/` 아래에 단계별로 저장되며, 오케스트레이션 상태는
`.easydep/orchestration/runs.sqlite3`에 저장된다. 웹 API의 애플리케이션 산출물과 버전은
MySQL 저장소를 사용한다.

## 범위

포함 범위:

- AWS, Azure, GCP
- Linux VM과 Docker
- VM, 부트 디스크, 네트워크, 서브넷, NIC, 방화벽, 공인 IP
- 요구될 때의 영속 데이터 디스크와 로드밸런서
- VM 용량·가격·성능 후보 선택

현재 제외 범위:

- Kubernetes 기반 애플리케이션 배포
- VPN, 서버리스, 관리형 애플리케이션 플랫폼
- 모든 CSP 리소스를 포괄하는 범용 지식베이스

## 실행

필수 환경은 Python 3.11 이상, JDK 21, Node.js/npm, Docker Desktop이다. PlantUML 다이어그램은
고정 버전의 Docker 이미지로 검사하고 렌더링한다. 개발용 MySQL은 통합 실행 스크립트가 Docker
컨테이너로 준비한다.

### 통합 실행 스크립트

최초 한 번 Python 가상환경과 구현 도구를 준비하고, `.env.example`을 복사해 사용할 LLM과
데이터베이스 접속 정보를 설정한다. `MODEL`은 요구사항 분석과 공통 생성 경로에서,
`DESIGN_AGENT_MODEL`은 설계 구조화 호출과 LLM 지연 진단에서 사용한다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-implementation-tools.ps1

Copy-Item .env.example .env
# .env의 API_KEY, BASE_URL, MODEL, DESIGN_AGENT_MODEL 값을 사용할 엔드포인트에 맞게 수정한다.
```

Docker Desktop을 실행한 다음 저장소 루트에서 아래 명령을 사용한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -OpenBrowser
```

이 스크립트는 다음 작업을 한 번에 수행한다.

1. `package-lock.json`이 바뀌었거나 설치본이 없을 때만 `npm ci`를 실행한다.
2. 프론트엔드 입력 파일의 SHA-256이 바뀌었을 때만 SvelteKit을 다시 빌드한다.
3. `easydep-mysql-dev` 컨테이너를 생성하거나 재사용하고 준비 완료까지 기다린다.
4. FastAPI 백엔드를 시작하고 UI·워크스페이스 API의 종단 연결을 확인한다.

정상적으로 준비되면 기본 UI는 `http://127.0.0.1:8000/`, API 문서는
`http://127.0.0.1:8000/docs`에서 볼 수 있다. 실행 상태와 로그는 `.easydep/dev/`에 저장된다.

| 옵션 | 용도 |
|---|---|
| `-OpenBrowser` | 준비 완료 후 기본 브라우저에서 UI를 연다. |
| `-SkipFrontendBuild` | 기존 `frontend/build/index.html`을 그대로 사용한다. 빌드가 없으면 실패한다. |
| `-ForceFrontendBuild` | 입력 해시가 같아도 프론트엔드를 다시 빌드한다. |
| `-Port 8010` | 백엔드 포트를 변경한다. 기본값은 `8000`이다. |
| `-DatabasePort 33061` | 호스트의 개발용 MySQL 포트를 변경한다. 기본값은 `33060`이다. |
| `-DatabaseImage mysql:8.4` | 최초 컨테이너 생성에 사용할 MySQL 이미지를 지정한다. |
| `-Stop` | 이 스크립트가 시작한 백엔드와 개발용 MySQL을 중지한다. |

프론트엔드가 이미 빌드되어 있을 때 빠르게 재시작하거나 전체를 중지하는 예시는 다음과 같다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -SkipFrontendBuild
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -Stop
```

`-Stop`은 `easydep-mysql-dev-data` Docker 볼륨을 삭제하지 않으므로 기존 개발 데이터가
보존된다. 문제가 발생하면 `.easydep/dev/server.stderr.log`와
`.easydep/dev/server.stdout.log`를 먼저 확인한다.

### 백엔드만 직접 실행

MySQL과 프론트엔드를 별도로 준비한 개발 환경에서는 백엔드만 직접 실행할 수 있다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn server:app --reload
```

주요 화면과 상태 확인 경로:

| 경로 | 용도 |
|---|---|
| `/` | 4단계 개발 워크벤치 |
| `/requirements/` | 요구사항 분석 UI |
| `/design/` | 설계 UI |
| `/implementation/` | 시스템 구현·테스팅 UI |
| `/docs` | OpenAPI 문서 |
| `/healthz` | 배포 상태 확인 |

전체 테스트는 다음과 같이 실행한다.

```powershell
python -m pytest
```

외부 LLM, MySQL, Docker 또는 클라우드 자격 증명이 필요한 테스트는 별도 환경 설정이 필요하다.

## 주요 문서

- [현재 시스템 상태](docs/current-system-status.md): 구현 범위, 검증 결과, 부족한 점
- [문서 안내](docs/README.md): 활성 문서와 이력 문서 구분
- [HTTP API](docs/api.md): 요구사항·설계·구현 API 계약
- [오케스트레이션](app/core/orchestration/README.md): 4단계 실행과 provider 계약
- [비교실험 계약](evaluation/experiment-contract.md): 공통 평가 기준
- [클라우드 지식베이스](app/core/cloudkb/document/README.md): DepKB 및 VM 지식 문서

## 현재 주의점

- 정식 구현 provider는 아직 확정되지 않았으며 비교실험에서는 명시적 LLM scaffold를 사용한다.
- VM 최소 용량이 입력되지 않으면 선택기는 임의 추천 대신 추천을 보류한다.
- 종단 성공은 현재 P1-GCP 파일럿 한 건으로 확인됐으며 일반화와 DepKB 효과는 추가 실험이 필요하다.
- `.env`에는 비밀값이 들어갈 수 있으므로 커밋하지 않는다.
