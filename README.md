# EasyDep

요구사항 문장을 받아 요구사항 명세 → 시스템 설계 산출물까지 이어 만드는 캡스톤 프로젝트.
에이전트별로 따로 개발하던 저장소를 하나로 합친 통합 저장소다.

| 에이전트 | 위치 | 상태 |
|---|---|---|
| 요구사항 분석 | `app/requirements/` | 구체화·FR/NFR 분류 → 액터/유스케이스 → 유스케이스 명세 → 유스케이스 다이어그램 |
| 시스템 설계 | `app/design/` | 클래스·시퀀스 다이어그램, API 명세, ERD, 배포 다이어그램 |
| 구현 / 테스트 | (미합류) | `app/<agent>/` 로 같은 방식으로 들어온다 |

두 에이전트는 **하나의 FastAPI 프로세스**(`server.py`)로 서빙되고,
산출물은 **하나의 MySQL 저장소**(`app/db`, `app/repositories`)를 공유한다.

## 두 에이전트가 이어지는 지점

산출물은 요청 본문이 아니라 `app_id`(UUID)로 오간다. 요구사항 분석이 끝나면 그 결과가
저장소에 기록되고, 설계 에이전트는 같은 `app_id`로 그것을 읽어 설계를 시작한다.

```text
POST /api/apps                              → app_id 발급
POST /api/requirements/analyze  {app_id}    → refined_requirements / usecase_spec / usecase_diagram 저장
POST /api/apps/{app_id}/stages/class_diagram/generate
                                            → 저장된 usecase_spec 을 읽어 설계 산출물 생성
```

`app_id` 없이 `/api/requirements/analyze` 를 호출하면 저장 없이 응답만 돌려주므로,
요구사항 에이전트만 단독으로 돌려보는 것도 그대로 된다.

저장 자리(`STAGE_ARTIFACTS`)와 버전 관리 규칙은 [HANDOFF.md](HANDOFF.md)에 정리돼 있다.

## 화면

| 경로 | 내용 |
|---|---|
| `/` | 시스템 설계 워크플로우 UI (`frontend/index.html`) |
| `/requirements` | 요구사항 분석 UI (`app/requirements/static/index.html`) |
| `/docs` | FastAPI 자동 문서 |
| `/healthz` | 쿠버네티스 프로브 |

## 실행

```bash
cp .env.example .env      # API_KEY / DB 접속 정보 등을 채운다
pip install -r requirements.txt
uvicorn server:app --reload
```

MySQL은 기동 시 `init_db()`가 데이터베이스와 테이블을 만든다. PlantUML 렌더링에는
`PLANTUML_JAR_PATH`의 jar가 필요하고, BERT 검증 분류기를 쓰려면 가중치를 따로 받아야 한다
(둘 다 저장소에 없다 — [docs/requirements-agent.md](docs/requirements-agent.md) §0-1 참고).

```bash
python -m pytest          # 요구사항 에이전트 테스트 (LLM/BERT 목킹, 네트워크 불필요)
python verify_db.py       # 산출물 저장소 왕복 확인 (MySQL 필요)
```

## 문서

- [HANDOFF.md](HANDOFF.md) — 산출물 저장소 설계와 설계 에이전트 인수인계 노트
- [docs/requirements-agent.md](docs/requirements-agent.md) — 요구사항 분석 에이전트 상세, 배포(minikube/AKS), 운영 스크립트
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 요구사항 분석 그래프 구조
- `docs/research/` — 프롬프트·베이스라인 비교 실험 기록
