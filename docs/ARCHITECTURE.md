# 요구사항 분석 에이전트 — 시스템 아키텍처

> 자연어 요구사항으로부터 **FR/NFR 분류 → 액터·유스케이스 식별 → 유스케이스 명세(Cockburn) →
> UML 관계·다이어그램**을 생성하는 LangGraph 기반 파이프라인. 전 과정을 Alistair Cockburn의
> *Writing Effective Use Cases* 로 그라운딩하고, **환각 최소화·재현성·근거성**을 우선한다.
>
> 스택: LangGraph(그래프/interrupt) · Nvidia NIM(OpenAI 호환, gpt-oss-120b) · 파인튜닝 BERT
> (FR/NFR) · FastAPI(서빙) · pydantic(구조화 출력).

---

## 1. 설계 원칙

| 원칙 | 구현 |
|---|---|
| **정적=코드 / 의미=근거LLM** | 정적으로 판정 가능한 규칙(커버리지·참조무결성·분기어·계약)은 **코드**가, 의미 판단(내부컴포넌트 누출·안티패턴)은 **근거 프롬프트 LLM**이. |
| **임의 사전 금지(오버피팅 방지)** | 근거 없는 키워드 목록·개수 상하한·ad-hoc 지침을 만들지 않는다. UI 용어 목록은 Cockburn 예시로만 한정, 개수 가드는 "엔지니어링일 뿐"으로 격하. |
| **Cockburn 그라운딩** | 모든 프롬프트 지침은 도서 페이지 인용으로 교차검증(`cockburn-grounding.md`, 아래 §참고). |
| **생성 → 검증 → 반성(repair)** | 각 생성 뒤 정적+의미 검증을 병합하고, 실패 시 수술적 지시로 재생성(bounded). |
| **전 구간 구조화 출력** | 모든 LLM 호출이 pydantic 스키마 강제(`with_structured_output`), 자유텍스트 파싱 제거. |
| **추적성(provenance)** | 유스케이스는 `requirement_ids`로, 스텝은 `covered_req_ids`로 소스 FR을 태깅 → 커버리지를 코드로 검증. |
| **대화형/자율 분리** | 단일 스위치 `enable_feedback_gates`로 모든 interrupt(clarify+피드백 게이트)를 켜고 끈다. |

---

## 2. 전체 파이프라인

```
STEP 1 — 요구사항 구체화 · FR/NFR 분류
  intake → clarify(few-shot 구체화) → classify(BERT 단독, id=FR1/NFR2)
        │
        ├ (gates on) → feedback_requirements[interrupt]  ── 피드백? 재분류·루프 / 없음? ↓
        ▼ (step2~4는 항상 실행)
STEP 2 — 액터 · 유스케이스 식별
  identify_actors → identify_use_cases(커버리지 강제-수리 루프) → check_coverage(결정론)
        │
        ├ (gates on) → feedback_use_cases[interrupt]
        ▼
STEP 3 — 유스케이스 명세 (병렬)
  generate_specs(UC마다 ThreadPool: 생성 → 정적+의미 검증 → 반성 재생성) → check_specs(검증 집계)
        │
        ├ (gates on) → feedback_specs[interrupt]
        ▼
STEP 4 — 관계 · 다이어그램
  identify_relationships(후보 마이닝 → LLM → 의미검증·반성 → 결정론 가드) → check_relationships(검증 집계)
        → render_diagram(결정론 PlantUML)
        │
        └ (gates on) → feedback_relationships[interrupt]  → END
```

**매크로 흐름(4단계)은 고정**(재현성). 표본화·검증·반성·피드백은 각 단계 **내부**에서만 일어난다.
비대화형(gates off, 배치/자율)에서는 interrupt 없이 끝까지 진행한다.

---

## 3. 단계별 상세

### STEP 1 — 요구사항 구체화 + FR/NFR 하이브리드 분류
`app/requirements/agent/steps/step1_requirements.py`

1. **intake**: 입력 요구 문장을 첫 메시지로 그래프에 주입.
2. **assess**: LLM이 요구가 유스케이스 도출에 충분히 **구체적인지** 판단(`Assessment`). 부족하면 clarifying
   questions, 충분하면 `refined_requirements` 확정.
3. **clarify [interrupt]**: (gates on & 추상) 사용자에게 질문하고 답을 받을 때까지 일시정지. 답 후 assess 재평가.
   - gates off면 `route_after_assess`가 무조건 elaborate로(질문 없이 진행).
4. **elaborate**: 대화·답변을 종합해 최종 구체 요구 목록 확정.
5. **classify** (단일 노드, 하이브리드): refined 요구를 LLM으로 **FR/NFR 분류**(카테고리·근거,
   `ClassificationResult`)한 뒤, 사용 가능하면 파인튜닝 BERT로 재분류해 **검증 confidence·일치 여부**를
   채움. LLM 판정이 authoritative, BERT는 대조 신호(불일치 UI 강조용). `enable_bert_verify=False`면
   BERT 건너뜀(경량 배포).
6. **reconcile**: 분류 결과 확정(병합은 classify에서 완료).

산출: `classified: list[RequirementItem]` (id/text/type/category/rationale + llm_type/bert_type/agreement).

### STEP 2 — 액터 · 유스케이스 식별
`app/requirements/agent/steps/step2_usecases.py`

- **identify_actors**: FR에서만 액터 도출. primary(외부 인간·시스템)/supporting(외부 서비스) 두 종류.
  **SuD(설계 대상 시스템)는 primary/supporting 액터가 아님**(Cockburn p.59). 경계 리트머스("앱의 일부로
  배포되면 내부")로 내부 컴포넌트를 배제. `parent_actor`로 일반화 관계 기록.
- **identify_use_cases**: FR만 유스케이스가 됨. **user-goal(EBP) 고도**로 군집(Cockburn coffee-break/EBP,
  p.62). subfunction FR은 상위 UC의 `requirement_ids`로 흡수. NFR은 유스케이스가 아니라 `nfr_ids` 제약으로 부착.
  - **커버리지 강제-수리 루프**: 고아 FR(어떤 UC에도 안 걸린 FR)을 코드로 계산 → 그것만 담아 재프롬프트로
    보충(최대 `max_coverage_iters`). 요구 누락 원천 차단.
- **check_coverage(결정론)**: 집합 연산으로 `orphan_fr_ids`(누락)·`unattached_nfr_ids`(전역 제약 후보)·
  `unknown_requirement_refs`(환각)·`coverage_ratio` 산출.

### STEP 3 — 유스케이스 명세 (병렬 + 반성)
`app/requirements/agent/steps/step3_specifications.py`

- **generate_specs**: UC마다 독립이라 **ThreadPoolExecutor로 병렬**(상한 `spec_concurrency`). 입력 순서로 취합.
- UC당 산출(Cockburn 풀 템플릿, `UseCaseSpec`): preconditions · trigger · **main_scenario**(스텝별 `covered_req_ids`) ·
  **extensions** · success/minimal guarantee.
  - **확장 구조화**: `label`(3a) · `branch_step`(int, 분기 스텝) · `handling_steps`(sub_step 코드) ·
    `outcome`(resume|alternate_success|fail) · `resume_at_step`. → 어디서 갈라지고 어디로 복귀/실패하는지 파싱 없이 구조로.
  - **스텝 수는 목표 아님**: "3-9"는 Cockburn 관찰(p.208)이지 하드룰이 아니라 게이트하지 않음. 각 스텝은 하나의
    sub-goal(Guideline 6). 자동 결과(로깅/감사/암호화/확인)는 스텝이 아니라 guarantee(p.64).
- **반성(reflection) 루프** — LLM 출력을 그대로 믿지 않음:
  - `_clean`: 마크다운/특수문자 정리(결정론).
  - `_validate_spec`(정적): 확장 분기/복귀 참조 무결성, 무분기(if/else), 제어토큰(Success!/Fail!),
    black-box UI 용어(Cockburn **예시** 단어만), 계약 완결성.
  - `_semantic_findings`(LLM, `enable_semantic_validator`): hidden branching, 내부컴포넌트 누출, scope creep,
    consequence-as-step 등 정적이 못 잡는 것.
  - 두 검증을 병합해 `issues`; 남으면 지시로 재생성(최대 `max_repair_iters`, 회귀하면 직전본 유지). `repair_iters` 기록.
- **check_specs** (결정론 요약 노드): 위 반성 결과(잔여 `issues`·`repair_iters`)를 `spec_report`로 집계해
  그래프에서 보이는 별도 단계로 표면화한다(step2의 `check_coverage`와 대칭).

### STEP 4 — 관계 · 다이어그램
`app/requirements/agent/steps/step4_diagram.py`

- **identify_relationships** (의미=LLM):
  1. **입력**: 액터 + 유스케이스 + **주 시나리오**(공유행위 판단 근거) + 결정론 **후보 힌트**(공유 스텝→include,
     `parent_actor`→일반화). include 힌트는 top-N만(프롬프트 크기 가드; 출력 개수 제한 아님).
  2. **LLM 도출**: association/include/extend/generalization/파생 UC.
     - **include는 기본 관계**(Cockburn "first rule of thumb", p.207) — 실제 공유 sub-goal에 적극.
     - **공유 인증/로그인/인가는 include 아님 = precondition**(선행 Log On 유스케이스, p.81).
     - 실패/에러/취소를 extend·파생 UC로 **승격 금지**(인라인 확장 유지, p.109). extend는 진짜 optional 인터럽션만.
  3. **의미검증 + 반성**(`RELATIONSHIP_VALIDATOR_SYSTEM`): precondition-as-include, consequence-as-include,
     extend 오용을 근거 LLM으로 판정 → 재생성. `relationship_issues`로 잔여 표면화.
  4. **결정론 가드**: `parent_actor` 일반화 보강, **존재하지 않는 UC/액터 참조 관계 드롭**(`dropped_refs`),
     주액터 association 보강, **orphan 액터**(어떤 association에도 없는 액터) 플래그.
- **check_relationships** (결정론 요약 노드): 관계 카운트·`orphan_actors`·`dropped_refs`·잔여 안티패턴
  `relationship_issues`를 `relationship_report`로 집계해 그래프에서 보이는 별도 단계로 표면화.
- **render_diagram** (구조=결정론, 순수 함수): 관계 모델을 **PlantUML** 유스케이스 다이어그램으로 렌더.
  - primary 액터 왼쪽 `actor→UC`, **supporting 액터 오른쪽 `UC→actor`**(Cockburn Guideline 18, p.243),
    include `base ..> included`, extend `extending ..> base`, generalization `parent <|-- child`.
  - Mermaid는 유스케이스 다이어그램 미지원이라 PlantUML 채택.

---

## 4. 횡단 메커니즘

### 4.1 하이브리드 검증 (정적 + 의미 + 반성)
- **정적(코드)**: `check_coverage`, `_validate_spec`, 관계 참조검증·orphan 탐지, `render_diagram`.
  결정론이라 재현 가능하고 값싸다. 정적 결과는 LLM이 "말로 무마" 못 하게 코드에서 확정.
- **의미(근거 LLM)**: `SPEC_VALIDATOR_SYSTEM`(명세), `RELATIONSHIP_VALIDATOR_SYSTEM`(관계). 정적이 못 잡는
  안티패턴만 판정. `enable_semantic_validator`로 on/off.
- **반성 루프**: 검증 실패 → 수술적 지시로 재생성 → 재검증(bounded, 회귀 방지, 마지막 정상본 유지).

### 4.2 Cockburn 그라운딩
> 아래 `docs/research/*` 는 실행에 쓰이지 않아 저장소 밖
> `report/easydep-research/docs/research/` 로 옮겼다. 경로는 그 안에서의 상대 경로다.

Cockburn, *Writing Effective Use Cases* 로 프롬프트 지침을 항목별 교차검증
(`docs/research/cockburn-grounding.md`, 페이지 인용). 도서는 저작물이라 저장소에 없다.
핵심 교정:
- 공유 인증 = **precondition**(include 아님, p.81) · 자동결과 = **guarantee**(스텝·include 아님, p.64)
- include는 **권장 기본**, extend/generalize만 sparingly(p.207) · supporting 액터 오른쪽(p.243)
- UI 금지 단어목록은 명문화되지 않음 → **예시 단어로만 한정**(p.209) · "3-9 steps"는 관찰이지 제한 아님(p.208)

### 4.3 대화형 피드백 (2경로)
1. **그래프 게이트**(`app/requirements/agent/steps/feedback_gates.py`, `enable_feedback_gates`): 각 스텝(1~4) 말미에서
   `interrupt`로 피드백 요청. 피드백 주면 그 스텝 재생성 + 게이트로 루프백, 빈 값이면 forward 진행(하위 스텝은
   자연스럽게 fresh 재실행 = cascade). step1 clarify도 같은 스위치로 통일.
2. **완료본 배치 편집**(`app/feedback.py` + `python -m app.apply_feedback <run_dir> "<피드백>"`): 완료된
   artifact에 자연어 피드백 → **의도 분류**(stage/scope/target_ids/instruction) → 범위별 재생성 → **하위 cascade
   재실행** → 정합성 리포트. 새 artifact run으로 저장(원본 보존).

**cascade 정책**: 상위 stage를 재생성하면 하위(specs/relationships/diagram)는 fresh 재실행(보존 안 함).

---

## 5. 상태 · 스키마

### AgentState (`app/requirements/agent/state.py`) — 모든 노드가 공유하는 단일 상태
```
messages, raw_requirements, refined_requirements, pending_questions, is_concrete   # step1
classified: list[RequirementItem]                                                  # step1 산출
actors: list[ActorItem], use_cases: list[UseCaseItem], coverage: dict              # step2
use_case_specs: list[UseCaseSpecItem]                                              # step3
relationships: dict, diagram: str                                                 # step4
phase
```
TypedDict: `RequirementItem`(FR/NFR+BERT) · `ActorItem`(name/kind/parent_actor) ·
`UseCaseItem`(id/name/primary_actor/level/goal/requirement_ids/nfr_ids) ·
`UseCaseSpecItem`(main_scenario/extensions/guarantees/issues/repair_iters).

### 구조화 출력 스키마 (`app/requirements/schemas.py`)
- step1: `Assessment`, `ClassifiedRequirement`, `ClassificationResult`
- step2: `Actor`, `UseCase`, `ActorResult`, `UseCaseResult`
- step3: `MainScenarioStep`, `ExtensionHandlingStep`, `Extension`, `UseCaseSpec`, `SpecCritique`
- step4: `Association`, `IncludeRelation`, `ExtendRelation`, `GeneralizationRelation`,
  `DerivedUseCase`, `RelationshipModel`, `RelationshipCritique`
- 피드백: `FeedbackIntent`
- HTTP: `AnalyzeRequest`, `AnalyzeResponse`, `RequirementItemOut`

---

## 6. 실행 · 서빙

| 진입점 | 용도 |
|---|---|
| `app/requirements/agent/graph.py` | LangGraph StateGraph 조립·컴파일(MemorySaver 체크포인터). `start_analysis`/`resume_analysis` 서빙 헬퍼. `rebuild_graph()`로 런타임 플래그 변경 후 재컴파일. |
| `app/requirements/api.py` (FastAPI 라우터) | `POST /api/requirements/analyze`(신규/재개), 정적 UI `/requirements`. 응답에 status(need_clarification/need_feedback/completed) + 산출물. `app_id`가 오면 완료 산출물을 MySQL 저장소에 기록. 앱 생성과 `/healthz`는 저장소 루트의 `server.py`. |
| `app/requirements/cli.py` | 터미널 대화형. `--interactive/-i`로 게이트+파이프라인 켜고 재빌드, 종료 시 유스케이스·다이어그램 출력. |
| `app/requirements/runner.py` + `app/requirements/run_pipeline.py` | **배치 러너**: `inputs/*.json`을 step2~4에 태우고(그래프 미사용, 함수 직접 호출) `artifacts/run_*/`에 재현 가능하게 저장. |
| `app/requirements/apply_feedback.py` | 완료 run에 자연어 피드백 적용 → 새 run 저장. |

### 아티팩트 (`artifacts/run_<UTC>_<inputsha10>/`)
`input.json` · `manifest.json`(config·sha·스테이지 요약) · `actors/use_cases/coverage/use_case_specs/
relationships.json` · `diagram.puml` · `use_cases/uc_NN_<slug>/{use_case,spec}.json`. (`.gitignore`)

### 입력 데이터셋 (`inputs/*.json`)
`{name, description, classified:[{id,text,type}]}`. 테스트와 러너가 공유. 참고 프로젝트의 labeled 요구는
`scripts/import_labeled_requirements.py`로 변환. (PURE 등 원문은 step1 분류 후 투입 예정)

---

## 7. 설정 플래그 (`app/requirements/config.py`)

| 플래그 | 기본 | 의미 |
|---|---|---|
| `model` / `base_url` / `api_key` / `temperature` | gpt-oss-120b / NIM / (env) / 0.2 | LLM 접속 |
| `enable_bert_verify` | True | step1 FR/NFR 분류 BERT 로드(끄면 전부 FR로 강등) |
| `embed_model` | nvidia/llama-nemotron-embed-1b-v2 | few-shot mmr+nim 샘플링용 NIM 임베딩 |
| `example_sampling_method` | random | step1 few-shot 예시 선별(random \| mmr+nim) |
| `spec_concurrency` | 4 | step3 UC별 병렬 상한 |
| `max_repair_iters` | 2 | step3/step4 반성 재생성 최대 |
| `max_coverage_iters` | 2 | step2 커버리지 강제-수리 최대 |
| `enable_semantic_validator` | True | LLM 의미검증 병합 여부 |
| `enable_feedback_gates` | False | **대화형 모드**(clarify + 각 스텝 피드백 게이트) |

---

## 8. 디렉토리 구조

```
app/
  config.py          # 설정(단일 소스)
  schemas.py         # 모든 구조화 출력 pydantic 스키마
  prompts.py         # 프롬프트 빌더(Cockburn 그라운딩) + 피드백/검증 프롬프트
  classifier.py      # 파인튜닝 BERT FR/NFR 로더·추론
  runner.py          # 배치 러너(run_pipeline/persist_run/load_state)
  run_pipeline.py    # 러너 CLI
  feedback.py        # 완료본 피드백(의도분류+cascade)
  apply_feedback.py  # 피드백 CLI
  cli.py / main.py   # 터미널 / FastAPI 서빙
  agent/
    graph.py         # StateGraph 조립 + 서빙 헬퍼
    state.py         # AgentState + TypedDict
    llm.py           # NIM 접속 + 구조화 출력(폴백)
    steps/
      step1_requirements.py  # 구체화·분류
      step2_usecases.py      # 액터·유스케이스·커버리지
      step3_specifications.py# 명세(병렬+반성)
      step4_diagram.py       # 관계(마이닝+검증)·PlantUML
      feedback_gates.py      # 각 스텝 말미 interrupt 게이트
inputs/              # 입력 데이터셋(*.json)
artifacts/           # 실행별 산출물(.gitignore)
scripts/             # 데이터셋 변환 등
materials/           # BERT 모델 · Cockburn PDF · PURE
                     # (조사·근거 문서 docs/research/ 는 저장소 밖 report/easydep-research/ 로 옮김)
tests/               # 결정론+목킹 단위 · 라이브(RUN_LIVE_TESTS) 통합
```

---

## 9. 테스트 전략
- **결정론+목킹 단위**: `invoke_structured` 목킹으로 각 노드·검증·반성·게이트·커버리지·참조검증·렌더를 LLM 없이 검증.
  헤르메틱(더미 키, BERT/의미검증 off).
- **라이브(옵트인)**: `RUN_LIVE_TESTS=1` 시 실제 NIM으로 데이터셋별 parametrize end-to-end(`test_live_step2/3/4`).
- 병렬성은 `threading.Barrier`로 동시성 증명, cascade·정합성은 스테이지 목킹으로 검증.

---

## 10. 근거 문서
- `docs/research/cockburn-grounding.md` — 프롬프트/체크의 도서 페이지 인용 교차검증(핵심 교정 포함)
- `docs/research/requirements-concreteness-and-gore.md` — 구체성 rubric·GORE 조사
- `docs/research/reference-project-analysis.md` — 참고 프로젝트 기법 선별(적용/미적용 근거)
