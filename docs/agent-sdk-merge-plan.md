# agent-sdk 병합 계획 (2026-07-25)

agent-sdk(클라우드 지식베이스 + 배포 계획 생성)를 easydep으로 흡수한다. easydep은
이미 "에이전트별로 따로 개발하던 저장소를 하나로 합친 통합 저장소"이고, 배포
에이전트가 마지막 합류자다.

이 문서는 **병합 전 실측 스냅샷**이다. 병합이 끝나면 `docs/`에서 지우고 결과는
커밋 메시지가 기록한다 — agent-sdk의 문서 정책(`CLAUDE.md`)을 그대로 따른다.

## 확정된 결정 (2026-07-25 사용자)

| | 결정 | 사유 |
|---|---|---|
| 이력 | **커밋 198개 보존** | agent-sdk는 리모트가 없어 이 이력이 유일본이고, 그 저장소의 문서 정책이 "커밋 메시지 = 변경 기록"이라 이력이 곧 설계 근거다 |
| 배치 | **`app/` 밑으로 이동** | easydep 루트를 12개 디렉터리로 늘리지 않는다. 절대 임포트 재작성 비용을 감수 |
| 의존성 | **`requirements.txt` 유지** | Dockerfile·CI 경로를 그대로 둔다. `pyproject.toml`·`uv.lock`은 버린다 |
| 범위 | 준비 → 별도 브랜치 → PR | `main`은 승인 전까지 건드리지 않는다 |

## 실측 (병합 회귀 게이트)

| 항목 | 값 |
|---|---|
| agent-sdk 테스트 | **1365 passed · 21 skipped · 159초** (2026-07-25, `master` 9984245) |
| easydep 테스트 | 이 머신에서 **미측정** — 의존성 미설치(아래 위험 5) |
| 커밋 / 추적 파일 | 198 / 385 |
| `.git` / `data/` | 44MB / 9.1MB (커밋된 산출물 57건) |
| 고칠 절대 임포트 | **778줄 / 207파일** (도구 실측) |
| 동적 임포트 | **1건** — `envkb/__main__.py:52` `import_module(f"envkb.{...}")` |
| 건드리면 안 되는 동명 문자열 | **75건 / 26파일** — 근거 라벨·CLI prog 이름 |

## 대상 배치

```
app/deployment/                     ← agent-sdk 저장소 루트가 통째로 여기로
  appkb/ bundlekb/ capacitykb/ costkb/ envkb/
  graphkb/ kbcommon/ patternkb/ perfkb/ sizingkb/
  nim_agent/                ← 도구 계층 (배포 구성기 deployment_puml_from_easydep)
  tools/                    ← 실험 하네스 (agent_probe·claim_check)
  data/                     ← 커밋된 산출물 57건 (gzip)
  document/                 ← kb-book·research + archive/
  NOTICE                    ← 법적 필요, 재배포 고지
tests/kb/                   ← agent-sdk tests/ 103개 파일
```

`app/deployment/`를 고른 근거는 **경로 해석이 살아 있기 때문**이다.
`kbcommon/artifact.py`의 `REPO_ROOT = Path(__file__).resolve().parent.parent`가
새 위치에서 `app/deployment/`를 가리키고, `data/`·`output/`이 함께 이동하면 그대로 맞는다.
다른 `parent.parent` 참조(`bundlekb/parsers/*` → `schema.json`, `graphkb/parsers/
review.py` → `reviewed/`)는 전부 패키지 내부라 영향이 없다.

> 이 앵커링은 우연이 아니다. `artifact.py`의 주석이 **2026-07-24 easydep 임포트
> 실측**을 근거로 상대경로 해석을 CWD에서 저장소 기준으로 바꾼 기록이다. 병합을
> 미리 겪은 코드다.

## 절차

각 단계는 다음 단계 없이도 성립해야 한다 — 중간에 멈춰도 저장소가 깨지지 않는다.

### 0. 사전 (완료)

- 되돌려진 삽입(e2c6a5b→b1df3f3)의 잔재 `.pyc` 정리
- 파이썬 3.13 정합성 확인 → **문제 없음** (위험 1)
- 임포트 재작성 도구 작성 + 건수 검증 (`scripts/rewrite_kb_imports.py`)

### 1. 이력 이식 — 브랜치 `merge/agent-sdk`

`git-filter-repo`로 agent-sdk 이력 전체의 경로를 미리 `app/deployment/`로 옮긴 뒤 병합한다.
subtree merge(`read-tree --prefix`)보다 이쪽을 택하는 이유: 과거 커밋의 경로가 현재
경로와 같아져 `git log app/deployment/costkb/...`가 `--follow` 없이 동작한다. 영구 통합이라
이 차이가 남는다.

```bash
pip install git-filter-repo                       # 미설치 상태
git clone C:/Users/projw/Desktop/dev/capstone/agent-sdk /tmp/agent-sdk-graft
cd /tmp/agent-sdk-graft && git filter-repo --to-subdirectory-filter app/deployment

cd easydep && git checkout -b merge/agent-sdk
git remote add agent-sdk-graft /tmp/agent-sdk-graft
git fetch agent-sdk-graft
git merge --allow-unrelated-histories agent-sdk-graft/master
```

**완료 기준**: `git log --oneline app/deployment/ | wc -l` 이 198에 가깝고, 워킹트리에
`app/deployment/{appkb,costkb,...}`가 있다. 이 시점에는 **아직 아무것도 임포트되지 않는다**
(임포트 경로가 안 맞으므로) — 정상이다.

### 2. 배치 정리

**계획을 하나 뒤집었다: 테스트를 옮기지 않는다.** `tests/kb/`로 빼려던 것을
`app/deployment/tests/` 그대로 두었다. 그 테스트들의 `ROOT = parent.parent`가 새
위치에서 정확히 `app/deployment/`를 가리켜, 구조 잠금 테스트 2종과 경로 상수
(`/"appkb"`·`/"data"`·`/"NOTICE"`)가 **무수정으로** 맞는다. 옮겼다면 전부 다시
계산해야 했다. conftest 분리라는 목적은 디렉터리가 다른 것만으로 이미 달성된다.

- `app/deployment/{pyproject.toml,uv.lock}` 삭제 — 의존성은 `requirements.txt`로 간다
- `app/deployment/__init__.py` 신설 — `app/design/`과 같은 모양의 패키지로 만든다
- `main.py`는 그대로 둔다. easydep `.gitignore`의 `/main.py`는 **루트 앵커**라
  `app/deployment/main.py`는 걸리지 않는다(추적됨을 확인)
- `NOTICE`도 `app/deployment/` 안에 둔다 — `test_redistribution_notice.py`가
  `ROOT/NOTICE`로 찾고, 그 고지가 덮는 것이 이 하위 시스템의 데이터다
- `.gitignore`: `/app/deployment/{output,.cache,.claude}/`·`token_budget.json`·
  `tool_count.json`. **빠뜨리면 KB 빌드 산출물이 커밋된다**
- `.dockerignore`: `output/`·`.cache/`·`tests/` 제외. 이미지는 커밋된 `data/`만 쓴다
- `pytest.ini`: `testpaths`에 `app/deployment/tests` 추가 + `--import-mode=importlib`
  (양쪽에 `test_cli.py`가 하나씩이라 기본 모드는 basename이 충돌한다)

### 3. 임포트 재작성

`scripts/rewrite_kb_imports.py`가 AST로 `import`/`from` 노드만 고친다. 기본이
미리보기이고 `--apply`로 쓴다. 접두는 `--prefix`로 바꾼다(기본 `app.deployment`).

2026-07-25 검증: 대표 5파일 사본에 적용해 임포트 46줄이 바뀌고 근거 라벨 15건이
**한 건도 안 바뀌었으며**, 결과가 전부 파싱됐다. 함수 안 들여쓴 임포트와
`from kbcommon import tumblebug_dump as dump_reader` 형태도 잡는다.

**정규식 치환 금지.** 같은 문자열이 코드에 75건 더 있는데 대부분이 임포트가 아니라
**근거 라벨**이다:

```python
notes.append(Note(text, ORIGIN_KB, "costkb"))    # 사용자에게 보이는 출처 표시
parser = argparse.ArgumentParser(prog="costkb")  # CLI 이름
```

이것들을 `app.deployment.costkb`로 바꾸면 답변의 출처 표시가 깨지고
`tests/kb/test_evidence_labels.py`·`test_claim_check*.py`가 무너진다. **바꾸는 것은
임포트 경로뿐이고, 라벨·prog 이름·아티팩트 이름은 그대로 둔다.**

도구가 못 보는 자리는 실측하니 **넷**이었다(계획에서 예상한 것보다 넓다):

| 무엇 | 건수 | 처리 |
|---|---|---|
| `tools/` 패키지 임포트 | 11 | 도구의 `PACKAGES`에 `tools` 추가 후 재실행 |
| `monkeypatch.setattr("graphkb.…")` 문자열 대상 | 6 | 첫 인자만 좁혀서 치환 |
| `import_module(f"envkb.{…}")` | 1 | **패키지 상대**로 바꿈 — 절대 경로를 박으면 다음 이동 때 또 남는다 |
| 형제 테스트 임포트 `from test_perfkb_details import` | 1 | 전체 경로로 |

`test_architecture.py`는 `ROOT`가 그대로 맞아 `PACKAGE_PREFIX` 한 줄만 더했다. 규약
검사가 접두를 떼고 판정하지 않으면 모든 임포트의 top이 `app`이 되어 **위반을 한 건도
안 잡으면서 통과하는** 검사가 된다.

#### CWD 상대 경로 — 초록불 뒤에 숨어 있던 것

배치를 옮기니 CWD가 agent-sdk 루트에서 easydep 루트로 바뀌었고, 그때 **11곳**이
드러났다. 넷은 실패로 드러났지만 나머지는 그러지 않았다:

- `test_costkb_determinism.py`·`test_gcp_parser.py`·`test_source_pinning.py`·
  `test_capacity_cfn.py` — `exists()` 가드가 있어 **실패 대신 조용히 스킵**됐다.
  통과 수가 1365 → 1362로 줄고 스킵이 21 → 24로 는 것이 유일한 신호였다.
- `test_basis_hedge.py` — `Path(kb).rglob("*.py")`가 빈 결과를 내서 검사가
  **아무것도 안 훑고 통과**했다. 그 파일의 docstring이 경고하던 바로 그 모양이다.

전부 `_ROOT = Path(__file__).resolve().parent.parent` 기준으로 앵커링했다. 프로덕션
코드의 상대 `Path("output")`은 **고치지 않는다** — `artifact.resolve()`가 상대 경로를
`REPO_ROOT` 기준으로 해석하도록 이미 만들어져 있고, `test_artifact_cwd.py`가 그
동작을 지킨다. 단 `kbcommon/fetch.py`의 `cache_dir()`은 그 경로를 안 거쳐 CWD에
`.cache/`를 만들고 있어 같은 기준으로 앵커링했다.

### 4. 의존성 통합

`requirements.txt`에 추가:

```
# --- 배포 에이전트(지식베이스) ---
ddgs>=9.14.4
httpx>=0.28
jsonschema>=4.23
fastjsonschema>=2.19      # 스키마를 코드로 컴파일 — 세션 첫 로드가 18배 빠르다
orjson>=3.10              # 28MB 산출물 읽기 1.9초 → 0.58초
openai-agents>=0.18.2
pyyaml>=6
regex>=2024.5.15          # 벤더 스키마의 .NET/PCRE 정규식 205건
```

**핀 하나를 반드시 올려야 한다** (위험 2):

```
openai==2.44.0   →   openai>=2.45.0,<3
```

선택 extras(`neo4j`, `pgdumplib`, `pypdf`)는 **넣지 않는다** — 전부 로컬 빌드 전용
경로다. 읽기 경로에는 필요 없다.

Dockerfile: `python:3.12-slim-bookworm` → `python:3.13-slim-bookworm` (2곳),
`COPY app ./app`가 `app/deployment/data`(9.1MB)를 함께 가져간다.

### 5. 검증 — **완료**

```
1365 passed, 21 skipped   (app/deployment/tests, 2026-07-25)
```

베이스라인과 **정확히 같다**. 도중에 1362/24가 나왔던 것이 위의 조용한 스킵이고,
그 3의 차이가 유일한 신호였다. 이 게이트를 다시 돌리려면 로컬에
`app/deployment/{output,.cache}/`가 있어야 한다(둘 다 gitignore — 각자 빌드한다).

### 6. 되돌려진 삽입 복원 — **완료**

- `app/design/services/deployment_diagram/kb.py` (그새 서비스가 하위 패키지로
  갈려서 e2c6a5b의 `services/kb_deployment.py` 자리와 다르다)
- `generate_deployment_diagram` 노드 교체, LLM 생성기 삭제
- **`PREREQUISITES`에 `resource_spec`은 넣지 않았다.** 이 계획서가 위에서 그렇게
  적었던 것이 틀렸다 — 생산자가 없는 산출물을 전제로 걸면 배포 단계가 통째로
  도달 불가가 된다. 요구사항 쪽이 만들기 시작하면 그때 넣는다(합의 안건 1)

실측: 제약을 주면 88줄·근거 주석 67개, 제약이 없으면 69줄로 **실패하지 않고**
무엇을 판정하지 못했는지가 주석에 남는다.

## 위험 (실측된 것만)

**1. 파이썬 3.12 → 3.13 — 해소됨.** agent-sdk는 `requires-python >=3.13`, easydep
Dockerfile은 3.12다. 확인 결과 `openhands-sdk`/`openhands-tools` 1.36.1은
`py3-none-any` 휠이고, torch 2.12·transformers 5.13·safetensors 0.8은 3.13에서
이미 설치돼 있다. 로컬 개발은 양쪽 다 이미 3.13이다(easydep `__pycache__`가
`cpython-313`). **이미지 태그만 올리면 된다.**

**2. `openai` 핀 충돌 — 조치 필요.** `openai-agents 0.18.2`가 `openai<3,>=2.45.0`을
요구하는데 easydep은 `openai==2.44.0`으로 고정돼 있다. **현재 상태로는 pip 해결이
실패한다.** 2.45로 올리고 설계 에이전트(OpenAI 호환 클라이언트 직접 호출)를 한 번
돌려 확인한다.

**3. `conftest.py` 두 벌.** agent-sdk conftest에 `autouse=True` 픽스처가 있어 한
디렉터리로 합치면 easydep 테스트에도 번지고, 그 픽스처가 `costkb.dataset`을
임포트한다. easydep conftest는 임포트 시점에 환경변수를 세팅한다. **합치지 말고
`tests/kb/`로 분리해 각자의 conftest를 각자 디렉터리에 둔다.** 파일명 충돌은
`test_cli.py` 1건뿐인데 이 분리로 함께 해소된다.

**4. 구조 잠금 테스트 2종.** `test_architecture.py`(AST 임포트 규약 + 예외표
1:1 대조)와 `test_docs_structure.py`(문서 허용 목록)는 저장소 루트 기준으로 짜여
있다. 이 둘은 agent-sdk가 스스로에게 건 규율이고 **병합으로 없앨 이유가 없다** —
`ROOT`와 패키지 이름만 새 배치로 옮긴다. 다만 규약의 뜻은 바뀐다: "kbcommon은
프로젝트 내부를 임포트하지 않는다"의 프로젝트가 이제 `app/deployment`다. easydep 코드가
KB를 부르는 방향(`app/design` → `app/deployment/nim_agent`)은 이 검사의 대상이 아니므로,
그 방향의 규약이 필요하면 새로 적는다.

**5. easydep 로컬 환경 부재.** 이 머신에 easydep 의존성이 설치돼 있지 않아
(`langgraph`·`langchain_core` 미설치) 기존 테스트의 베이스라인을 못 잡았다.
**병합 전에 easydep 테스트를 한 번 통과시켜 두어야** 병합 후 실패가 병합 탓인지
원래 그랬는지 구분된다.

**6. `data/` 9.1MB.** 병합하면 이미지와 저장소가 그만큼 커진다. 대안(런타임 빌드)은
클론 직후 동작을 잃고, AWS 관리형 가격처럼 **재배포가 금지된 소스는 어차피 각자
빌드**해야 한다(`python -m app.deployment.costkb build-aws-managed`). 지금 크기는 받아들인다.

## 디렉터리 이름 — `app/deployment/` (2026-07-25 확정)

`app/kb/`도 후보였다. 내용물의 9/12가 지식베이스라 그쪽이 사실에 가깝다. 그럼에도
`app/deployment/`를 택한 이유는 **easydep의 기존 이름이 전부 에이전트 이름**이기
때문이다(`app/requirements`·`app/design`·`app/implementation`). 이 합류자도
에이전트이고, 지식베이스는 그 에이전트가 답을 만드는 수단이다.

## 아직 안 한 것

1. **easydep 기존 테스트가 이 머신에서 한 번도 안 돌았다** (위험 5). `langgraph`·
   `langchain_core` 미설치라 병합 전 베이스라인도, 병합 후 확인도 없다.
   `pip install -r requirements.txt` 후 `pytest tests` 가 먼저다.
2. **`openai` 2.45 승격을 설계 에이전트로 확인하지 않았다.** 핀만 올렸다.
3. **이미지를 빌드해 보지 않았다.** 파이썬 3.13 휠은 확인했지만 실제 빌드는 미실행.
4. **배포 다이어그램 피드백이 여전히 LLM 직접 수정이다.** 근거 라벨이 값의 전부인
   문서를 LLM이 고치면 근거가 조용히 깨진다 — 이 스테이지는 수정보다 재생성이
   맞는다는 것이 e2c6a5b 때부터의 미결이다.

## 병합 후 (합의 문서의 우리 몫)

`app/deployment/document/archive/easydep-agenda-2026-07-24.md`의 안건 중 easydep이 코드를
바꿔야 하는 것:

- **안건 1** — 제약 구조화(`resource_constraints_text` → `RESOURCE_SPEC`)가
  `trafficPattern`·`stateless`·`dataResidency` 3칸을 채운다. 소비자는 이미 있다
- **안건 2** — ERD 생성 시 PlantUML 주석으로 `archetypeHint`를 싣는다. 어댑터의
  파싱은 agent-sdk 쪽에 이미 구현돼 있다(커밋 9984245)
- **안건 3** — 경로 의존 vs pip: **이 병합이 답이다**. 같은 저장소가 된다
