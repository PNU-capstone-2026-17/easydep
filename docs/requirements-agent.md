# 클라우드 네이티브 요구사항 분석 AI 에이전트 → minikube → AKS

LangGraph 기반 **요구사항 분석 에이전트**를 FastAPI로 서빙하고, minikube(로컬)와 Azure AKS(클라우드)에 배포한다.

사용자가 요구사항 문장 배열(추상/구체 혼재)을 입력하면, 에이전트가 **대화형으로 구체화**하고 **FR/NFR로 분류**한다. 전체 워크플로우는 4단계이며 현재 **마일스톤1(구체화+분류)**이 구현돼 있고, 2~4단계(액터·유스케이스·명세·다이어그램)는 그래프 스텁으로 골격만 잡혀 있다.

- **LLM**: Nvidia NIM (OpenAI 호환, `langchain-openai`), 기본 모델 `openai/gpt-oss-120b`
- **하이브리드 분류**: LLM 1차 분류 + `materials`의 파인튜닝 **BERT**로 검증/대조(confidence·일치 여부)
- **서빙**: FastAPI (`/api/requirements/analyze`, `/healthz`, 웹 UI `/requirements`) — 통합 저장소에서는 `server.py`가 설계 에이전트와 함께 서빙한다
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
                └ 가중치(model.safetensors)는 저장소 제외 — Releases에서 받는다(§0-1)
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

## 0-1. BERT 모델 가중치 내려받기
파인튜닝 가중치 `model.safetensors`(417MB)는 GitHub 파일당 100MB 제한 때문에 저장소에 포함돼 있지 않고 [Releases](https://github.com/KimJW02/ai-agent-with-langgraph/releases/tag/bert-v1)로 배포한다.
같은 디렉터리의 `config.json`·`tokenizer.json`·`tokenizer_config.json`은 저장소에 들어 있으므로 가중치 파일만 받아 넣으면 된다.

```bash
# gh CLI 사용
gh release download bert-v1 -p model.safetensors \
  -D materials/BERT_FR_NFR_Classifier/bert_model

# 또는 curl
curl -L -o materials/BERT_FR_NFR_Classifier/bert_model/model.safetensors \
  https://github.com/KimJW02/ai-agent-with-langgraph/releases/download/bert-v1/model.safetensors
```

배치 후 경로가 아래와 같아야 한다.
```
materials/BERT_FR_NFR_Classifier/bert_model/model.safetensors
```

> 이 파일이 없으면 BERT 검증 분류기 로딩에서 실패한다. LLM 분류만 쓸 거라면 받지 않아도 되며,
> CLI는 `--no-bert`, 서버/배포는 `ENABLE_BERT_VERIFY=false`로 우회한다.

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
- **BERT 검증**은 `torch`+`transformers`+417MB 모델을 사용해 이미지가 커진다. 경량/메모리 제약 배포에서는 `ENABLE_BERT_VERIFY=false`(+ Dockerfile의 모델 COPY 제거)로 LLM 분류만 사용 가능.
- 구조화 출력은 `with_structured_output(method="json_schema")`(= OpenAI 네이티브 `chat.completions.parse` 경로를 langchain이 감싼 것)를 쓰고, gpt-oss가 간헐적으로 빈 `parsed`를 반환하면 자동으로 JSON 모드 폴백(`app/requirements/agent/llm.py::invoke_structured`).
- 다음 단계(2~4단계) 확장은 `app/requirements/agent/steps/step2~4`의 스텁 노드를 채우고 `app/requirements/agent/graph.py`에서 배선만 늘리면 되며, 서빙 코드는 그대로 재사용.
