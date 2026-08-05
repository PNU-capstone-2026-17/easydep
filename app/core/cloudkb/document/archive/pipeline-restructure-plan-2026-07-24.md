# 진입 계약 · 구조 재편 · 보강 계획 (2026-07-24)

> **이력이다. 참조하지 않는다.**
>
> 현재 진실은 [`docs/cloud-native-extension.md`](../../../../docs/cloud-native-extension.md). 이 문서는 작성 시점의
> 스냅샷이고 전제가 바뀐 자리가 있다. **여기 적힌 결정·계획을 근거로 새 작업을
> 시작하지 말 것.** 안의 **실측치는 유효하다** — 다시 재지 말고 인용한다.

세 요구를 한 계획으로 묶습니다. 셋은 별개가 아니라 순서가 있는 하나입니다.

1. **진입 계약** — 첫 요청이 소프트웨어 요구사항과 클라우드 제약사항을 담고 온다.
   그 제약 중 **필수가 무엇인지** 정의한다.
2. **구조 재편** — 지식베이스 시스템이 얽혀 있다. 경계를 세운다.
3. **보강** — 부족한 축을 채운다.

**확정된 것 (2026-07-24 사용자):** 예산·규모는 필수. 요구사항 분석·시스템 설계는
**easydep**(`C:\Users\projw\Desktop\dev\easydep`)의 에이전트들과 협업 — 클래스·시퀀스·
ER·API 명세는 설계 에이전트가 만들고, **이 에이전트는 배포 다이어그램을 만든다.**
요구사항 분석 단계에도 개입 지점이 있다(클라우드 제약을 그때 받으므로).

---

## 0. 본체 실측 — easydep 어디에 꽂히나 (2026-07-24)

easydep은 FastAPI 한 프로세스에 에이전트들이 합류하는 구조입니다. 산출물은 요청
본문이 아니라 **MySQL 저장소를 `app_id`로 공유**합니다 (`STAGE_ARTIFACTS`, 버전 관리
`GENERATED/AUTO_FIXED/FEEDBACK_REVISED`). 요구사항 분석은 정확히 사용자가 말한
5단계(구체화 → FR/NFR 분류 → 액터/유스케이스 → 명세 → 다이어그램)로 있고,
**clarify 게이트가 있는 대화형 세션**입니다.

우리 자리는 실측으로 **정확히 두 곳 + 개입 한 곳**입니다.

### 자리 1 — `RESOURCE_SPEC`: 등록만 되고 **아무도 안 만든다**

- `apps.resource_constraints_text` — 사용자가 쓴 클라우드 제약 **원문 산문**이 앱
  생성 때 이미 저장됩니다. 우리가 정의하려던 "제약을 따로 받는" 그 칸이 이미 있습니다.
- `RESOURCE_SPEC` artifact_type이 `STAGE_ARTIFACTS`에 등록돼 있으나 **생산자가
  없습니다.** easydep HANDOFF 원문: *"Deployment diagram still does not require
  RESOURCE_SPEC, even though the report derives it from there. … Add it to
  PREREQUISITES once something does."* — 보고서상 배포 다이어그램의 유일한 파생원이
  비어 있는 채로 우리를 기다리고 있습니다.

**→ 우리 진입 계약이 곧 `RESOURCE_SPEC`의 스키마입니다.** 별도 발명이 아니라 빈
자리 채우기입니다.

### 자리 2 — `deployment_diagram`: 지금은 근거 없는 자유 LLM 생성

현재 노드(`app/design/nodes/artifact_generation.py:57`):

```python
deployment_puml = generate_deployment_diagram_with_llm(
    usecase_spec_text, class_puml, sequence_puml, api_spec, erd_puml)
```

프롬프트에 산출물을 넣고 PlantUML을 받으며, 검사는 **문법뿐**입니다. 클라우드
제약은 전제조건에도 프롬프트에도 없습니다. research.md ¶2가 걱정하는 환각이 정확히
이 모양으로 있습니다 — **이 노드를 우리 `compose()`로 교체**하는 것이 삽입의
본체입니다 (결정론·KB 근거·줄마다 근거 라벨·자체 검증 3종).

### 개입 — 요구사항 분석의 clarify 게이트

분석 세션이 이미 되묻기(clarify)를 합니다. 필수 제약 누락의 되묻기는 **새 메커니즘이
아니라 이 게이트에 질문을 하나 더 얹는 것**입니다: `resource_constraints_text` →
구조화 시 우리 `validate_request()`가 누락 목록을 주면 그것이 clarify 질문이 됩니다.
리전 지명은 우리 `cap_resolve_region`으로 확정합니다('서울'이 프로바이더마다 다른
코드인 것을 아는 쪽은 우리뿐입니다).

### ⚠ 전제와 어긋난 실측 — 설계 산출물의 최종본이 JSON이 아니다

우리 계약(appkb/schema.json)은 "설계도는 JSON이고 다이어그램은 표현"을 전제했는데,
easydep의 **저장되는 최종본**은 다릅니다:

| 산출물 | 최종본 형식 |
|---|---|
| class_diagram / sequence_diagram / ERD | **PlantUML 텍스트** |
| api_spec | JSON |

구조화 중간물(BCE JSON)은 **의도적으로 저장하지 않습니다** — 피드백이 PlantUML을
직접 고치므로 저장하면 어긋나기 때문(easydep이 실측으로 확인하고 제거). 대신
HANDOFF 스스로 *"최종 산출물을 소비 시점에 파싱하라"*를 추적성 그래프의 방침으로
적어 두었습니다. 선택지는 둘입니다:

- **(A) 소비 시점 어댑터** — 배포 생성 때마다 최종 PlantUML을 파싱해 우리 계약
  JSON으로 변환. easydep 방침과 일치, 남의 코드 변경 없음. 단, PlantUML 파싱이라는
  파서가 하나 생김(생성기가 만드는 방언이라 범위는 좁음).
- **(B) 설계 노드가 JSON을 원본으로 갖게 변경** — 우리 철학(JSON이 산출물, PlantUML은
  렌더링)과 일치하고 애초 전제대로지만, 설계 에이전트의 생성·피드백 루프를 고쳐야
  함(피드백이 JSON을 고치고 재렌더링하는 구조로) — **다른 팀 코드의 구조 변경**.

**제안: 단기 (A), 장기 (B)를 팀 합의 안건으로.** (A)에서도 componentId 합성(클래스
스테레오타입·API 경로에서 구성요소 추출)이 미결 설계 질문이라 **easydep 샘플
(inputs/ 12건)로 실물을 만들어 실측부터** 합니다.

### 통합 방식

요구사항 에이전트가 합류한 방식과 같게 — **in-process import**를 제안합니다.
`compose()`는 LLM 없는 결정론 함수라 easydep 서버가 직접 부를 수 있고, agent-sdk를
패키지로 설치하면 데이터 산출물 52개가 함께 갑니다. (HTTP 분리는 근거가 생기면
그때 — 지금 갈라 두면 배포·버전 관리만 늘어납니다.)

---

## 1. 구조 실측 — 어디가 얽혀 있나 (agent-sdk, 2026-07-24)

패키지 9개 · 데이터 산출물 52개 · 도구 39개 · 검사 1,158 passed 기준.

### 1-1. kbcommon 안에 지식베이스 6개가 숨어 있다 — 가장 큰 비구조

kbcommon은 공용 배관(artifact·basis·fetch·invariants·sources·display)이어야 하는데,
실측하면 **자체 데이터셋을 소유한 모듈이 6개**입니다.

| 모듈 | 줄 | 소유 산출물 | 쓰는 곳 |
|---|---|---|---|
| regions.py | 292 | cloud-regions.json | cap_resolve_region |
| carbon.py | 359 | region-carbon.json | cap_region_carbon |
| latency.py | 330 | region-latency.json | cap_* (지연) |
| lifecycle.py | 332 | service-lifecycle.json | cap_service_lifecycle |
| cbspider.py | 310 | cbspider-support.json | cap_csp_supports |
| images.py | 245 | basic-images.json | design·cap 도구 |

(cloudinfo.py 147줄은 regions의 파서입니다.) 합치면 **~1,900줄, 산출물 6개짜리
지식베이스가 "공용"이라는 이름 아래** 있습니다. "KB냐 공용이냐"의 기준이 없어서
다음 축도 여기 들어올 것입니다.

### 1-2. KB→KB 임포트 3곳 — 단방향 규약이 관행일 뿐 검사가 없다

| 어디서 | 무엇을 | 성격 |
|---|---|---|
| capacitykb/parsers/gcp.py | graphkb.parsers.gcp의 `_list_config_files`·`_load_yaml` | **비공개 헬퍼**를 가로질러 씀 |
| graphkb/parsers/avm.py | capacitykb.parsers.azure의 `_fetch_relative` | **비공개 헬퍼**, 방향도 반대 |
| perfkb/cli.py | costkb.dataset의 `BUILT_FILENAME` | **미러 전제** — 의도된 의존이나 규약으로 없음 |

앞의 둘은 "같은 원본을 받는 코드"가 한쪽 KB에 먼저 생겨 다른 쪽이 훔쳐 쓰는 모양.
셋째는 설계상 실재하는 방향인데 문서에도 검사에도 없습니다.

### 1-3. 도구 계층이 KB의 어디까지 만져도 되는지 규약이 없다

nim_agent의 임포트 표면이 네 겹(agent_api / dataset / model 상수 / query)으로
갈립니다. "agent_api = 문장, dataset·model = 값" 구분 자체는 옳지만(조인에는 값이
필요) **어디까지가 공개인지 적혀 있지 않아** 1-2 같은 비공개 관통을 아무도 못 막습니다.

### 1-4. 잔재

- `nim_agent/catalog.py`에 예제 시절 작업 3건(summarize·translate·brainstorm).
- `nim_agent/tools.py` docstring이 아직 "함수 호출 예제 도구들".
- `agent.py` INSTRUCTIONS 232줄 단일 산문 — 동작은 프로브 46건이 지키므로 전면
  개편은 않고, P1에서 진입 워크플로를 붙일 때 축별 블록화만.

### 1-5. 판정

**축 안은 규약이 서 있습니다** (소스 핀 → 파서 → 산출물 → 불변식 → 재배포 검사 —
9개 축 동일). 얽힌 곳은 전부 **축 사이**: 공용층 경계, KB 간 방향, 도구 계층 표면.
재작성이 아니라 **경계 선언 + 검사 + 소량 이동**으로 풀립니다.

---

## 2. P1 — 진입 계약 = RESOURCE_SPEC, 그리고 easydep 삽입

### 원칙 셋 (전부 이 저장소에서 이미 배운 것)

1. **필수의 기준은 목록이 아니라 판정식** — 그 칸이 없으면 뒤 단계 산출물의
   **요구사항 부합을 잴 수 없는** 칸이 필수다 (research.md의 동사가 "측정"이므로).
2. **계약의 모든 칸에는 소비자가 있어야 한다** — multiZone을 받아 놓고 안 읽던
   결함의 일반화. 칸 ↔ 소비자 대응을 검사로 고정.
3. **필수 누락 → 지어내지 않고 되묻는다** — easydep clarify 게이트로.

### 필수 (확정)

서비스 설명은 easydep의 `requirements_text`가 이미 담당하므로, **RESOURCE_SPEC의
필수는 제약 4칸**입니다.

| 칸 | 없으면 무엇을 잴 수 없나 |
|---|---|
| **provider** | 값 조인 전부 (실측된 라우팅 변수 — 비용·성능·번들·벤더 타입) |
| **region** (지명 허용 → resolve해 코드로 확정) | 단가·용량·탄소·리전 제약 |
| **monthlyBudgetUSD** | 비용 부합 판정의 기준값 |
| **규모 신호 1개** (동시 사용자 또는 대략 RPS) | 사이징 판정 |

예산 판정의 정직한 비대칭을 계약에 명시: 합계를 내지 않으므로(미가격 구성원 존재)
**초과는 확정 가능, 부합은 단정 불가** — "값 붙는 부분만의 합이 이미 예산을 넘으면
초과 확정, 아니면 '미달 단정 불가(미가격 N종)'".

### 권장 (소비자가 이미 있는 것만)

trafficPattern(→ 버스트 경고의 판정화) · availabilityTarget(→ multiZone·서브넷·복제
수) · statefulness(→ serverless/k8s 적합성) · multiZone(기존). **받지 않는 것**:
데이터 볼륨·runtime·dev/prod·레지던시 — 소비자가 없다. 생기면 연다.

### 산출물

| # | 무엇 | 어디에 |
|---|---|---|
| P1a | `RESOURCE_SPEC` 스키마 + `validate_request()` — appkb/schema.json의 `requirements`와 `$defs` 공유(두 벌 금지) | agent-sdk (appkb) |
| P1b | **어댑터**: easydep 최종 산출물(class/seq/ER PlantUML + api_spec JSON) → appkb 설계 JSON. componentId 합성 포함 — **inputs/ 샘플로 실물 실측부터** | agent-sdk (appkb 신규 모듈) |
| P1c | **배포 노드 교체**: `generate_deployment_diagram_with_llm` → `compose()` (+ PREREQUISITES에 `resource_spec` 추가 — HANDOFF가 예고한 그 줄) | easydep |
| P1d | clarify 게이트 연동: 제약 산문 → RESOURCE_SPEC 구조화 시 누락 질문 생성, `cap_resolve_region`으로 리전 확정 | easydep ↔ agent-sdk |
| P1e | `verify_against_requirements(plan, req)` — 예산(비대칭 판정)·multiZone↔서브넷·provider/region↔노드·traffic↔버스트·statefulness↔serverless. **목표 1의 "부합 측정"이 처음 코드가 됨** | agent-sdk (appkb/verify) |

프로브 2건: 필수 누락 시 지어내지 않고 되묻는가 / 전체 흐름이 부합 판정까지 닿는가.

### 팀 합의 안건 (계획은 안 바뀌고, 안건만 상정)

- 장기적으로 설계 산출물의 원본을 JSON으로 바꿀지 (§0의 (B)) — 피드백 루프가
  PlantUML 직접 수정이라 지금은 (A) 어댑터로 감.
- agent-sdk를 easydep의 패키지 의존으로 넣는 방식(경로/pip)과 데이터 동반 배포.

---

## 3. P2 — 구조 규약을 검사로

문서로 적은 규약은 이 저장소에서 두 번 뒤처졌습니다(decisions.md §7). 규약은
`tests/test_architecture.py`로 고정합니다 — AST로 임포트를 전수해:

```
kbcommon   → (프로젝트 내부 임포트 금지)
*kb        → kbcommon만            예외 허용표: perfkb→costkb (미러 전제, 사유 링크)
nim_agent  → *kb의 공개 표면만
공개 표면   = agent_api(문장) · dataset(값) · model(상수·타입) · appkb 전체(라이브러리)
비공개      = parsers · cli · invariants · query 내부 · `_` 접두 전부
```

- 검사를 **먼저** 넣고 알려진 위반 2건을 예외표에 올린 채 시작(레드 금지) — 이후
  작업 중 새 위반이 즉시 걸리게.
- 위반 해소: `_fetch_relative`·gcp yaml 헬퍼를 kbcommon.fetch로 올리고 양쪽이 받아
  씀. **파서 이동 후 재빌드해 산출물 바이트 대조.**
- capacitykb `query.resolve_type`은 dataset 없는 이 KB의 값 표면 — 예외가 아니라
  정의에 명시.
- perfkb→costkb 미러는 예외표에 **사유와 함께** 유지 (상수를 옮겨 숨기지 않음).

## 4. P3 — kbcommon 분리: envkb

규칙: **kbcommon에는 데이터셋이 없다.** 자체 산출물을 소유하면 KB다. 검사로 고정
(kbcommon 모듈의 ARTIFACT 선언·data/ 읽기 금지 — P3 완료 후 활성화).

- 1-1의 6모듈 + cloudinfo 파서 → 새 패키지 **`envkb`** (리전 명·위치, 탄소, 지연,
  수명주기, 드라이버 커버리지, 기본 이미지). **코드 이동이지 재작성이 아님.**
- 도구는 nim_agent/capacity_tools에 그대로(도구 수 39 유지 — 라우팅 실측 보호).
- 검증: 산출물 6종 바이트 동일 · 전체 테스트 green · cap_resolve_region 스모크.

## 5. P4 — 보강 (계약이 정한 소비자 순서대로)

| | 무엇 | 왜 이 순서 | 성격 |
|---|---|---|---|
| A | **계약 신설 칸의 판정 조인** — traffic×버스트, statefulness×serverless, availability×복제/서브넷 | P1이 열어 준 소비자. **새 데이터가 아니라 코드** | composer·verify 확장 |
| B | **관리형 서비스 가격** — 배포 계획의 managed 노드가 전부 "값 없음" | 클라우드 네이티브 청구서의 본체인데 0건 | **조사부터** — Azure Retail 미터↔아키타입 9종 조인 실재 여부 실측. 후보·범위로 담지 대표값 금지. 실측 전 착수 금지 |
| C | **patternkb** (FTS5, CC-BY 소스, advisory 전용) | 요구사항 분석 개입(P1d)과 배포 판단의 자문 축 | 기존 계획(app-layer-plan) 그대로 |
| D | sizingkb 폭 (61규칙) | A를 하다 부족이 실측되면 | 필요 시 |

**재조사 금지 유지**: 의존 엣지 0인 7곳 · AWS/GCP 쿼터 · SLA/벤치마크 ·
alibaba 등 6곳 성능 소스 (부재 확정, 근거는 goal2-open-items).

---

## 6. 실행 순서와 완료 기준

```
① P2 검사 신설 (예외표 포함, 반나절)        완료: 아키텍처 테스트 green + 위반이 예외표와 정확히 일치
② P1a 계약 + P1e 부합 검사기 + 프로브       완료: 필수 누락 되묻기 프로브 통과 · verify_against_requirements 실측 1건
③ P1b 어댑터 실측·구현                      완료: easydep inputs/ 샘플 1건이 설계→배포 다이어그램까지 통과
④ P1c·P1d easydep 삽입                      완료: /design에서 deployment_diagram이 근거 라벨 달고 나옴 · resource_spec이 전제조건
⑤ P2 잔여 + P3 envkb 분리                   완료: 예외표 1건(미러)만 · 산출물 바이트 동일 · kbcommon 데이터 0
⑥ P4 A → (조사) B → C                       완료: 각자 정의
```

②③이 ⑤보다 앞인 이유: easydep 쪽과 맞물리는 항목(계약·어댑터·삽입)은 합의와
왕복이 필요해 먼저 던지고, 구조 이동은 우리 안에서 끝나는 일이라 기다릴 수 있음.

**하지 않는 것**: 전면 재작성 · INSTRUCTIONS 전면 개편(진입 워크플로 부분만) ·
도구 수 변경 · KB 표준 모양의 소급 통일 · easydep 설계 에이전트의 생성 방식 변경
((B)는 합의 안건일 뿐).

> **전 단계 완료 (2026-07-24).** ①~⑤에 이어 ⑥까지: A(판정 조인, 34d6dc0 —
> availabilityTarget은 소비자가 없어 계약에 안 넣음), B(관리형 과금 축, 922862e·
> 1d30ee9), C(patternkb, 079de9d), D(3d128a5). D는 게이트 조건대로 **부족을 먼저
> 실측**했다 — tencent·oracle 경로에서 "예약 IP 모름"이 나와, 공식 문서가 고정
> 수를 명시한 4곳(tencent·oracle·nhn·ncp)만 손 검수로 채웠다(openstack·kt는
> 사유와 함께 미수록). "하지 않는 것"의 도구 수 변경 금지는 구조 이동(②~⑤)에
> 대한 것이고, ⑥-C의 pattern_search는 P4가 계획한 신설이라 40→41이 됐다.
