# EasyDep

EasyDep은 자연어 요구사항에서 소프트웨어 설계, 구현, 테스트 및 VM 배포 산출물까지 생성하는
멀티 AI 에이전트 기반 개발 지원 시스템이다. 현재 지원 범위는 AWS·Azure·GCP의
Docker-on-VM 애플리케이션이다.

현재 구현 수준과 알려진 한계는 [현재 시스템 구성](docs/current-system-status.md)을 기준으로 한다.
문서 전체의 역할은 [문서 안내](docs/README.md)에서 확인할 수 있다.
코드를 처음 읽는다면 [초보자용 코드 탐색 순서](docs/code-reading-guide.md)와
[백엔드 코드 길잡이](app/README.md)부터 보는 것을 권장한다.

## 파이프라인

```text
사용자 요구사항
  → 1. 요구사항 분석
  → 2. 소프트웨어·클라우드 설계
  → 3. 소스·수용 테스트·Dockerfile·Terraform 생성
  → 4. 테스트
```

| 영역 | 위치 | 역할 |
|---|---|---|
| 요구사항 | `app/requirements/` | 요구사항 구체화, FR/NFR 분류, 유스케이스 및 다이어그램 생성 |
| 설계 | `app/design/` | 클래스·시퀀스·ERD·OpenAPI·배포 설계 |
| 구현 | `app/implementation/` | 애플리케이션 소스, 테스트, Dockerfile, Terraform 생성 |
| 테스팅 | `app/testing/` | 생성 애플리케이션 검증 |
| 워크스페이스 | `app/workspace/` | 프론트엔드 명령, 4단계 연결, 진행 이벤트와 재개 조율 |
| 클라우드 지식 | `app/cloudkb/` | VM 자원 의존성, 가격 및 성능 데이터 |
| 제품 경로 실행기 | `evaluation/easydep/` | 프론트엔드와 같은 Workspace API로 요구사항 한 건을 실행 |

프론트엔드에서 시작한 명령, 진행 이벤트, 애플리케이션 산출물과 단계별 체크포인트는 MySQL에
저장된다. 제품 실행은 별도의 파일 기반 run 디렉터리를 만들지 않는다.
테이블 관계, 키·인덱스 선택과 대안 비교는 [MySQL 구조와 설계 이유](docs/mysql-architecture.md)에
정리되어 있다.

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
- HTTPS/TLS, 인증서 발급·갱신과 도메인 관리
- 모든 CSP 리소스를 포괄하는 범용 지식베이스

## 실행

필수 환경은 Python 3.11 이상, Node.js 22 이상과 Docker Desktop이다. 개발 UI는 호스트에서
Vite hot reload로 실행하고, 장시간 작업을 맡는 FastAPI는 기본적으로 자동 재시작 없이 실행한다.
MySQL만 Docker 컨테이너로 준비한다. 생성 코드의 컴파일·단위 테스트는
물론 실제 브라우저 엔진이 필요한 DOM·JavaScript E2E도 하나의 `easydep-toolchain`을
사용한다. 각 작업은 필요한 도구만 실행하며, 큰 BERT 모델과 PlantUML은 서버 runtime에만 둔다.

### 통합 실행 스크립트

Docker Desktop을 실행한 다음 저장소 루트에서 아래 명령 하나를 사용한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -OpenBrowser
```

첫 실행에서는 `.env.example`을 `.env`로 복사한다. LLM 단계를 실행하기 전
`LLM_PROVIDER`, `API_KEY`, `BASE_URL`, `MODEL`을 사용할 엔드포인트에 맞게 수정한다.
`MODEL`은 요구사항 분석, 설계
구조화 호출, 구현 OpenHands, Testing 계획과 LLM 지연 진단에서 함께 사용한다. 실제 URL,
인증 header, provider 구분과 OpenHands용 모델 이름은 `app.llm_connection` 한 곳에서 만든다.
구현용 Docker runner도 이 함수가 만든 환경변수 묶음을 전부 전달하며 별도의 LLM 변수 목록을
관리하지 않는다.

이 스크립트는 다음 작업을 한 번에 수행한다.

1. `.venv`를 만들고 의존성이 바뀐 경우에만 `uv`로 Python 패키지를 동기화한다.
2. `package-lock.json`이 바뀐 경우에만 `npm ci`를 실행한다.
3. Docker 입력이 바뀐 경우에만 구현용 이미지와 브라우저 E2E 확장 이미지를 빌드·검증한다.
4. `easydep-mysql-dev` 컨테이너를 생성하거나 재사용하고 준비 완료까지 기다린다.
5. FastAPI는 안정 실행하고 Vite만 hot reload로 시작한 뒤 UI·워크스페이스 API 연결을 확인한다.

정상적으로 준비되면 기본 UI는 `http://127.0.0.1:5173/`, API 문서는
`http://127.0.0.1:8100/docs`에서 볼 수 있다. 실행 상태와 로그는 `.easydep/dev/`에 저장된다.

| 옵션 | 용도 |
|---|---|
| `-OpenBrowser` | 준비 완료 후 기본 브라우저에서 UI를 연다. |
| `-ProductionLike` | Vite 대신 `frontend/build`을 FastAPI에서 제공해 배포와 비슷하게 실행한다. |
| `-BackendReload` | 백엔드 소스 개발 중에만 Uvicorn 자동 재시작을 켠다. 실행 중인 앱 생성 작업은 중단될 수 있다. |
| `-SkipFrontendBuild` | `-ProductionLike`에서 기존 정적 빌드를 재사용한다. |
| `-ForceFrontendBuild` | `-ProductionLike`에서 정적 프론트엔드를 강제로 다시 빌드한다. |
| `-SkipBootstrap` | 기존 Python 환경과 툴체인 이미지를 신뢰하고 준비 작업을 생략한다. |
| `-ForceToolchainBuild` | 입력 해시가 같아도 Docker 빌드를 다시 실행한다. Docker layer cache는 재사용한다. |
| `-ResetDatabase` | 현재 코드와 DB 구조가 맞지 않을 때 개발 DB와 저장된 앱을 지우고 새로 만든다. |
| `-ResetDatabaseSchema` | 시작 시 `easydep` DB의 기존 구조·데이터를 삭제하고 현재 7개 테이블로 재생성한다. |
| `-Port 8110` | 백엔드 포트를 변경한다. 기본값은 `8100`이다. |
| `-FrontendPort 5174` | 개발 UI 포트를 변경한다. 기본값은 `5173`이다. |
| `-DatabasePort 33061` | 호스트의 개발용 MySQL 포트를 변경한다. 기본값은 `33060`이다. |
| `-DatabaseImage mysql:8.4` | 최초 컨테이너 생성에 사용할 MySQL 이미지를 지정한다. |
| `-Stop` | 이 스크립트가 시작한 백엔드·Vite와 개발용 MySQL을 중지한다. |

의존성과 툴체인 준비가 끝난 뒤 빠르게 재시작하는 예시는 다음과 같다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -SkipBootstrap
```

스키마가 바뀌었고 기존 개발 데이터를 보존할 필요가 없을 때에는 한 번만 다음처럼 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -ResetDatabaseSchema
```

이 옵션을 계속 사용하면 실행할 때마다 개발 데이터가 지워진다. 로컬 MySQL의 `3306`을 직접
사용한다면 `.env`의 `DB_PORT=3306`, 실제 계정·비밀번호와
`DB_SCHEMA_RESET_ON_START=true`를 설정해 백엔드를 한 번 시작한 뒤 다시 `false`로 바꾼다.

```powershell
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

- [현재 시스템 구성](docs/current-system-status.md): 실행 프로세스, 단계별 책임, 저장·툴체인과 현재 한계
- [문서 안내](docs/README.md): 활성 문서와 이력 문서 구분
- [HTTP API](docs/api.md): 요구사항·설계·구현 API 계약
- [초보자용 코드 탐색 순서](docs/code-reading-guide.md): UI 요청부터 단계 서비스까지 따라가는 방법
- [백엔드 코드 길잡이](app/README.md): bounded context와 의존성 방향
- [LangSmith 관측](docs/langsmith-observability.md): 전 에이전트 기본 trace·대시보드 설정
- [대화형 워크스페이스](app/workspace/README.md): 프론트엔드 명령과 단계 전환 계약
- [제품 경로 실행기](evaluation/easydep/README.md): 프론트엔드와 같은 API로 전체 흐름 실행
- [클라우드 지식베이스](app/cloudkb/document/README.md): DepKB 및 VM 지식 문서

## 현재 주의점

- 정식 구현 provider는 아직 확정되지 않았으며 비교실험에서는 명시적 LLM scaffold를 사용한다.
- VM 최소 용량이 입력되지 않으면 선택기는 임의 추천 대신 추천을 보류한다.
- 종단 성공은 현재 P1-GCP 파일럿 한 건으로 확인됐으며 일반화와 DepKB 효과는 추가 실험이 필요하다.
- `.env`에는 비밀값이 들어갈 수 있으므로 커밋하지 않는다.
