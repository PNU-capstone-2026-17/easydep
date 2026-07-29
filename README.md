# EasyDep

요구사항 문장을 받아 요구사항 명세 → 시스템 설계 산출물까지 이어 만드는 캡스톤 프로젝트.
에이전트별로 따로 개발하던 저장소를 하나로 합친 통합 저장소다.

| 에이전트 | 위치 | 상태 |
|---|---|---|
| 요구사항 분석 | `app/requirements/` | 구체화·FR/NFR 분류 → 액터/유스케이스 → 유스케이스 명세 → 유스케이스 다이어그램 |
| 시스템 설계 | `app/design/` | 클래스·시퀀스 다이어그램, API 명세, ERD, 배포 다이어그램 |
| 시스템 구현 | `app/implementation/` | 구현 계획·실행, phase별 HITL 승인, 생성 파일 버전 저장 |
| 배포 (지식베이스) | `app/core/cloudkb/` | 클라우드 지식베이스 9축 + 배포 계획 구성기. 줄마다 근거가 달리고 이 단계에는 LLM 호출이 없다 |
| 시스템 테스트 | (미합류) | 구현 worker가 E2E 생성·검증까지 담당하며 독립 테스트 에이전트는 추후 합류 |

에이전트들은 **하나의 FastAPI 프로세스**(`server.py`)로 서빙되고,
산출물은 **하나의 MySQL 저장소**(`app/db`, `app/repositories`)를 공유한다.

`app/core/cloudkb/`은 2026-07-25에 별도 저장소(agent-sdk)에서 합류했다. 자기
테스트(`app/core/cloudkb/tests/`)와 문서(`app/core/cloudkb/document/`)를 함께 갖고 있고,
지식베이스 산출물은 `app/core/cloudkb/data/`에 커밋돼 있어 클론 직후 빌드 없이 돈다.

합류는 main에 squash 한 커밋으로 들어왔다. **원본 커밋 198개는 태그
`agent-sdk-history`에 있다** (`git log --oneline agent-sdk-history`). 그 저장소는
리모트가 없었으므로 이 태그가 유일본이고, 그 코드의 판단 근거는 대부분 커밋
메시지에만 적혀 있다 — 지우지 말 것.

## 에이전트가 이어지는 지점

산출물은 요청 본문이 아니라 `app_id`(UUID)로 오간다. 요구사항 분석이 끝나면 그 결과가
저장소에 기록되고, 설계와 구현 에이전트는 같은 `app_id`로 앞 단계 산출물을 읽는다.

```text
POST /api/apps                              → app_id 발급
POST /api/requirements/analyze  {app_id}    → refined_requirements / usecase_spec / usecase_diagram 저장
POST /api/apps/{app_id}/stages/class_diagram/generate
                                            → 저장된 usecase_spec 을 읽어 설계 산출물 생성
POST /api/implementation/apps/{app_id}/jobs → 저장된 설계를 읽어 구현 workflow 계획
```

`app_id` 없이 `/api/requirements/analyze` 를 호출하면 저장 없이 응답만 돌려주므로,
요구사항 에이전트만 단독으로 돌려보는 것도 그대로 된다.

저장 자리(`STAGE_ARTIFACTS`)와 버전 관리 규칙은 [HANDOFF.md](HANDOFF.md)에 정리돼 있다.

## 화면

| 경로 | 내용 |
|---|---|
| `/` | 요구사항 분석 UI — 워크플로우의 시작 (`app/requirements/static/index.html`) |
| `/design` | 시스템 설계 워크플로우 UI (`frontend/index.html`) |
| `/docs` | FastAPI 자동 문서 |
| `/healthz` | 쿠버네티스 프로브 |

## 공통 필수 도구

- Python 3.11 이상
- JDK 21
- Node.js와 npm
- MySQL 8.0 이상
- PlantUML JAR (`PLANTUML_JAR_PATH`로 위치 지정)

구현 도구 bootstrap은 npm production dependency, OpenAPI Generator 7.24.0과 Gradle
8.14.2 Wrapper를 저장소 내부에 준비한다. 운영체제 전역 Gradle 설치는 필요 없다.

## Windows 설치 및 실행

PowerShell에서 필요한 도구를 설치한다. 설치 전 `winget search <이름>`으로 패키지 ID를
확인할 수 있다.

```powershell
winget install --id Python.Python.3.12 -e
winget install --id EclipseAdoptium.Temurin.21.JDK -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Oracle.MySQL -e

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-implementation-tools.ps1

$env:NVIDIA_API_KEY="<NVIDIA NIM API key>"
$env:API_KEY=$env:NVIDIA_API_KEY
$env:PLANTUML_JAR_PATH="C:\tools\plantuml.jar"
python -m uvicorn server:app --reload
```

## Linux(Ubuntu/Debian) 설치 및 실행

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip openjdk-21-jdk nodejs npm mysql-server curl

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
sh scripts/bootstrap-implementation-tools.sh

export NVIDIA_API_KEY='<NVIDIA NIM API key>'
export API_KEY="$NVIDIA_API_KEY"
export PLANTUML_JAR_PATH='/opt/plantuml/plantuml.jar'
python -m uvicorn server:app --reload
```

## macOS 설치 및 실행

```bash
brew install python@3.12 openjdk@21 node mysql
brew services start mysql
export PATH="$(brew --prefix openjdk@21)/bin:$PATH"

python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
sh scripts/bootstrap-implementation-tools.sh

export NVIDIA_API_KEY='<NVIDIA NIM API key>'
export API_KEY="$NVIDIA_API_KEY"
export PLANTUML_JAR_PATH="$HOME/tools/plantuml.jar"
python -m uvicorn server:app --reload
```

Docker는 Linux bootstrap을 이미지 build 중 자동 실행한다.

```bash
docker build -t easydep .
```

MySQL은 서버 기동 시 `init_db()`가 데이터베이스와 테이블을 생성한다. DB 접속 환경변수와
PlantUML 준비는 [요구사항 에이전트 문서](docs/requirements-agent.md)를 참고한다.

BERT FR/NFR 분류기 가중치(417MiB)는 GitHub 파일당 100MiB 한도 때문에 45MiB 조각으로 쪼개
저장소에 들어 있다. 따로 받을 것은 없고, 첫 기동 때 `.easydep/models/`에 한 번 재조립된다
(약 2초, 이후 기동은 건너뜀). 자세한 방식은
[요구사항 에이전트 문서 §0-1](docs/requirements-agent.md#0-1-bert-모델-가중치)에 있다.

```bash
python -m pytest
python verify_db.py       # MySQL이 필요한 저장소 왕복 검사
```

## 문서

- [HANDOFF.md](HANDOFF.md) — 산출물 저장소 설계와 설계 에이전트 인수인계 노트
- [docs/requirements-agent.md](docs/requirements-agent.md) — 요구사항 분석 에이전트 상세, 배포(minikube/AKS), 운영 스크립트
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 요구사항 분석 그래프 구조
- [docs/implementation-agent.md](docs/implementation-agent.md) — 구현 worker, 자동 phase DAG, HITL·재개·도구 설정
- [docs/deployment-file-generation.md](docs/deployment-file-generation.md) — 시스템 구현 에이전트의 배포 의도 추론, Dockerfile·Kubernetes manifest 생성과 검증
- [docs/api.md](docs/api.md) — 요구사항·설계·구현 HTTP API 명세

실험·조사 기록(`docs/research/`), PURE 데이터셋, 실험 스크립트, BERT 학습 노트북은
실행에 쓰이지 않아 저장소에서 빼고 바탕화면 `report/easydep-research/`에 보관한다.
