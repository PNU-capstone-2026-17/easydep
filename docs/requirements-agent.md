# 클라우드 네이티브 요구사항 분석 AI 에이전트 → minikube → AKS

LangGraph 기반 **요구사항 분석 에이전트**를 FastAPI로 서빙하고, minikube(로컬)와 Azure AKS(클라우드)에 배포한다.

사용자가 요구사항 문장 배열(추상/구체 혼재)을 입력하면, 에이전트가 **대화형으로 구체화**하고 **FR/NFR로 분류**한다. 전체 워크플로우는 4단계이며 현재 **마일스톤1(구체화+분류)**이 구현돼 있고, 2~4단계(액터·유스케이스·명세·다이어그램)는 그래프 스텁으로 골격만 잡혀 있다.

- **LLM**: Nvidia NIM (OpenAI 호환, `langchain-openai`), 기본 모델 `openai/gpt-oss-120b`
- **하이브리드 분류**: LLM 1차 분류 + `materials`의 파인튜닝 **BERT**로 검증/대조(confidence·일치 여부)
- **서빙**: FastAPI (`/api/requirements/analyze`, `/healthz`, 웹 UI `/`) — 통합 저장소에서는 `server.py`가 설계 에이전트와 함께 서빙한다
- **배포**: Docker → minikube → AKS (kustomize base/overlays)

## 아키텍처 (마일스톤1 그래프)
```
intake → assess ──(추상)──▶ clarify(interrupt) ──▶ assess (재평가 루프)
                └─(구체)──▶ elaborate → classify(LLM+BERT) → reconcile → END
                                                                        └(스텁 on)▶ actors→use_cases→specs→diagram
```
- 대화형 구체화는 LangGraph `interrupt()`로 질문 후 사용자 답변으로 재개, `MemorySaver`+`thread_id`로 세션 유지.

## 구조
```
app/requirements/  LangGraph 요구사항 분석 에이전트
  config.py     .env 로드 (API_KEY/BASE_URL/MODEL, BERT 토글)
  schemas.py    API·LLM 구조화 출력 Pydantic 스키마
  classifier.py BERT FR/NFR 검증 분류기 (지연 로딩)
  prompts.py    assess/classify/elaborate 시스템 프롬프트
  api.py        FastAPI 라우터 (/api/requirements/analyze) + 산출물 저장
  static/       웹 UI
  agent/        요구사항 분석 그래프 (멀티 단계 확장 진입점)
    state.py    AgentState / RequirementItem
    llm.py      LLM 접근 + 구조화 출력(+JSON 폴백)
    graph.py    노드 배선·컴파일 + 서빙 헬퍼(start/resume_analysis)
    steps/      단계별 노드
      step1_requirements.py    1단계: 구체화·분류 (구현)
      step2_usecases.py        2단계: 액터·유스케이스 (스텁)
      step3_specifications.py  3단계: 명세 생성 (스텁)
      step4_diagram.py         4단계: 다이어그램 (스텁)
materials/      BERT 모델 · PURE 데이터셋 · 유스케이스 PDF (자료)
                └ 가중치는 45MiB 조각으로 쪼개 저장소에 포함 — 로딩 시 재조립(§0-1)
k8s/            kustomize (base / overlays/local / overlays/aks)
Dockerfile
```

## API — `POST /api/requirements/analyze`
```jsonc
// 1) 신규 분석 시작
{ "requirements": ["Users must log in with email and password.",
                   "I want to build a shopping mall service."] }
// → 구체화 필요 시: { "thread_id", "status":"need_clarification", "questions":[...] }
// → 완료 시:        { "thread_id", "status":"completed", "requirements":[{id,text,type,category,rationale,bert_type,bert_confidence,agreement}] }

// 2) 구체화 질문에 대한 답변으로 재개
{ "answer": "B2C shoppers browse and buy products; admins manage the catalog.",
  "thread_id": "<이전 응답의 thread_id>" }
```

## 0. 환경변수 설정
```bash
cp .env.example .env
# .env를 열어 API_KEY, BASE_URL, MODEL 값을 채운다.
```

## 0-1. BERT 모델 가중치
파인튜닝 가중치는 **저장소에 들어 있다.** 따로 받을 것이 없고 `git clone` 하면 끝난다.

원본 `model.safetensors`는 417MiB로 GitHub 파일당 100MiB 한도를 넘어 그대로는 커밋할 수
없다. 그래서 45MiB 이하 조각으로 쪼개 커밋해 두고, 로딩 시점에 되살린다.

### 어떻게 쪼갰나
한 번에 자르지 않고 두 단계로 나눈다.

1. **텐서 단위 샤딩** — HuggingFace 네이티브 포맷(`model-0000N-of-00011.safetensors` +
   `model.safetensors.index.json`)으로 나눈다. transformers가 인덱스를 보고 샤드를
   **그대로 읽기 때문에 합칠 필요가 없고**, 샤드별로 mmap 되어 로딩 메모리도 덜 쓴다.
2. **바이트 단위 분할** — 텐서 하나가 한도보다 크면 샤딩으로는 더 못 줄인다. BERT-base의
   `bert.embeddings.word_embeddings.weight`(30522×768 f32 = 89.4MiB)가 여기 해당한다.
   이 샤드만 `.part000`/`.part001`로 잘라 두고 로딩 시 이어 붙인다.

전체를 100MiB씩 잘라 매번 417MiB를 통째로 다시 쓰는 방식보다, **한도를 넘는 샤드 하나만**
(89MiB) 이어 붙이면 되므로 재조립 쓰기량이 1/5 이하다. 나머지 78%는 복사조차 하지 않고
하드링크로 연결한다.

```
materials/BERT_FR_NFR_Classifier/bert_model/
  config.json  tokenizer.json  tokenizer_config.json       ← 저장소에 그대로
  weights/                                                 ← 저장소 (총 417MiB, 14개 파일)
    manifest.json                        재조립 명세 (파일별 크기·sha256)
    model.safetensors.index.json         HF 샤드 인덱스 (텐서 → 샤드 매핑)
    model-00001-of-00011.safetensors     ┐ 한도 이하 샤드 10개
    model-00003-of-00011.safetensors     │ 최대 42.8MiB, 재조립 없이 그대로 사용
    ...                                  ┘
    model-00002-of-00011.safetensors.part000   ┐ 89.4MiB 샤드만 45MiB씩 분할
    model-00002-of-00011.safetensors.part001   ┘
```

### 로딩 시 무슨 일이 일어나나
`app/requirements/model_assets.py`의 `ensure_model_dir()`가 `classifier.py`의 지연 로딩
직전에 호출된다.

- 조각을 `.easydep/models/bert_fr_nfr/`에 되살린다. **커밋된 디렉터리는 건드리지 않는다** —
  저장소에 들어간 것은 읽기 전용 입력, 되살린 것은 언제든 다시 만들 수 있는 산출물이다.
  (읽기 전용 루트 파일시스템 배포에서는 `BERT_MODEL_CACHE_DIR`로 위치만 바꾸면 된다.)
- 조각마다 `sha256`을 대조한다. Git 전송 중 손상되거나 조각이 빠지면 조용히 이상한 가중치를
  로드하는 대신 즉시 실패한다.
- 되살린 뒤 manifest 지문을 stamp로 남긴다. **두 번째 기동부터는 존재·크기 확인만** 하고
  건너뛴다(측정: 최초 2.1초, 이후 17ms).
- uvicorn 멀티 워커가 동시에 기동해도 잠금으로 한 번만 만든다. 파일은 임시 이름으로 쓴 뒤
  `os.replace`로 바꿔 넣어 중간 상태가 노출되지 않는다.

**Docker 이미지는 빌드 단계(`weights` stage)에서 미리 되살려 두므로 파드 기동에 재조립
비용이 아예 없다.** 최종 이미지에는 되살린 결과만 들어가 조각과 완성본이 중복되지 않는다
(이미지 증가분은 예전과 같은 약 417MB).

무결성 검사는 테스트에도 들어 있다: `python -m pytest tests/test_model_assets.py`

### 가중치를 다시 만들 때
모델을 재학습해 새 `model.safetensors`가 생기면 조각을 다시 만든다.

```bash
python scripts/shard_bert_model.py <새 model.safetensors 경로>
python scripts/shard_bert_model.py --verify <같은 경로>   # 원본과 비트 단위 대조
```

`--verify`는 재조립한 모델과 원본을 둘 다 로드해 state_dict가 비트 단위로 같은지, 같은
입력에 대한 logits 차이가 0인지 확인한다. 텐서 바이트를 그대로 복사할 뿐 torch로 다시 저장하지
않으므로 값이 바뀔 여지가 없다.

> 예전처럼 온전한 `model.safetensors`를 모델 디렉터리에 두면 재조립을 건너뛰고 그 파일을
> 그대로 쓴다. LLM 분류만 쓸 거라면 CLI는 `--no-bert`, 서버/배포는
> `ENABLE_BERT_VERIFY=false`로 BERT 로드 자체를 건너뛴다.

## Phase A — 로컬 파이썬 실행
```bash
pip install -r requirements.txt
uvicorn server:app --reload
```
- 브라우저: http://localhost:8000 (웹 UI)
- API 문서: http://localhost:8000/docs

### 터미널 실행 (CLI)
웹 서버 없이 터미널에서 바로 분석할 수 있다.
```bash
# 인자로 요구사항 전달 (여러 개)
python -m app.requirements.cli "Users can log in with email and password." "Respond within 2 seconds."

# 파일에서 (한 줄에 하나씩)
python -m app.requirements.cli --file reqs.txt

# 대화형 입력 (빈 줄로 종료). 추상 요구사항이면 되물으며 이어서 답변 가능
python -m app.requirements.cli

# BERT 검증 생략(빠르게)
python -m app.requirements.cli --no-bert "The system shall respond within 2 seconds."
```
추상 요구사항을 파이프로 넣으면(비대화형) 구체화 질문만 출력하고 종료(코드 2)한다.

### 테스트
LLM/BERT를 목킹한 결정적 테스트라 네트워크·API 키·torch 없이 돌아간다.
```bash
pip install -r requirements-dev.txt
python -m pytest                      # 목킹 단위/흐름 테스트 (기본)
RUN_LIVE_TESTS=1 python -m pytest     # 실제 NIM 호출 통합 테스트까지 (유효한 .env 필요)
```
- `tests/` 구성: 헬퍼(`test_llm_helpers`), STEP1 노드(`test_step1`), 그래프 흐름(`test_graph_flow`),
  응답/라우팅(`test_payload_and_routing`), CLI(`test_cli`), 라이브 NIM(`test_live_nim`, 옵트인).

## Phase B — minikube 배포
```powershell
minikube start --driver=docker

# 이미지 빌드 후 minikube로 로드
docker build -t langgraph-chatbot:local .
minikube image load langgraph-chatbot:local

# .env 값을 시크릿으로 생성
kubectl create secret generic nim-secret --from-env-file=.env

# 배포
kubectl apply -k k8s/overlays/local

# 상태 확인
kubectl get pods
kubectl logs -l app=langgraph-chatbot

# 접속 URL 열기
minikube service langgraph-chatbot --url
```

## Phase C — Azure AKS 배포
> 선행: Azure CLI(`az`) 설치 + 구독. `helm`은 불필요.
```bash
az login
az group create -n langgraph-rg -l koreacentral

# ACR 생성 + 이미지 빌드/푸시
az acr create -g langgraph-rg -n <acr> --sku Basic
az acr build -t langgraph-chatbot:v1 -r <acr> .

# AKS 생성 + ACR 연동 + kubeconfig
az aks create -g langgraph-rg -n langgraph-aks --node-count 1 --attach-acr <acr> --generate-ssh-keys
az aks get-credentials -g langgraph-rg -n langgraph-aks

# overlays/aks/kustomization.yaml 의 <acr> 를 실제 값으로 교체 후 배포
kubectl create secret generic nim-secret --from-env-file=.env
kubectl apply -k k8s/overlays/aks

# 외부 IP 확인
kubectl get svc langgraph-chatbot -w
```

## 운영 스크립트 (`scripts/`, PowerShell)
클러스터 이름/리전 등은 `scripts/_config.ps1`에서 한 곳으로 관리.

| 스크립트 | 용도 |
|---|---|
| `.\scripts\aks-stop.ps1` | **종료** — 노드 VM 할당 해제, 컴퓨트 과금 중단 (빠른 재개 가능) |
| `.\scripts\aks-start.ps1` | **시작** — 중지된 클러스터 재개 + 앱 URL 출력 |
| `.\scripts\deploy.ps1` | 코드 변경 후 이미지 빌드→ACR push→재배포 |
| `.\scripts\provision.ps1` | 완전 삭제 후 처음부터 재생성 (rg→acr→aks→배포) |
| `.\scripts\teardown.ps1` | **완전 삭제** — 리소스 그룹째 제거, 과금 완전 중단 |

> 일상적 비용 절감은 `aks-stop`/`aks-start`, 프로젝트 종료 시엔 `teardown`.
> `aks-stop` 상태에서도 LoadBalancer 공용 IP는 소액 과금되므로, 장기 미사용이면 `teardown` 권장.

## 참고
- `.env`는 커밋 금지(`.gitignore` 포함).
- 세션(대화 이력)은 인메모리(`MemorySaver`)라 파드 재시작 시 초기화됨 — 데모용. 멀티 레플리카 배포 시 세션 불일치 가능하므로 단일 레플리카 유지.
- **BERT 검증**은 `torch`+`transformers`+417MB 모델을 사용해 이미지가 커진다. 경량/메모리 제약 배포에서는 `ENABLE_BERT_VERIFY=false`(+ Dockerfile 마지막의 `COPY --from=weights` 제거)로 LLM 분류만 사용 가능. 가중치는 빌드 단계에서 조각으로부터 되살리므로(§0-1) 이미지 크기는 조각 도입 전과 같다.
- 구조화 출력은 `with_structured_output(method="json_schema")`(= OpenAI 네이티브 `chat.completions.parse` 경로를 langchain이 감싼 것)를 쓰고, gpt-oss가 간헐적으로 빈 `parsed`를 반환하면 자동으로 JSON 모드 폴백(`app/requirements/agent/llm.py::invoke_structured`).
- 다음 단계(2~4단계) 확장은 `app/requirements/agent/steps/step2~4`의 스텁 노드를 채우고 `app/requirements/agent/graph.py`에서 배선만 늘리면 되며, 서빙 코드는 그대로 재사용.
