# 출처 — 이 한 벌은 어디서 나왔나

**씨앗만 있는 상태입니다** (2026-08-02, P1). 씨앗 문장(`INPUT.md` ·
`requirements.txt` · `constraints.txt`)은 사람이 비교실험 계획
(`document/archive/comparison-experiment-plan-2026-08-02.md`)의 관측점 설계에
맞춰 썼습니다. 아직 어느 계열도 돌지 않았습니다 — 산출물 디렉터리가 없는 것이
정상입니다.

## 이 표본이 다른 표본과 다른 점

- **비교실험의 공통 입력**입니다. 세 계열(본 시스템 · 단순 LLM · MetaGPT류)이
  같은 파일을 받습니다. 계열별 프롬프트 포장은 실행 시점에 여기 기록합니다.
- **관측점이 설계되어 있습니다**(`INPUT.md`의 표) — 어느 결정이 명시되고 어느
  결정이 침묵인지를 실행 전에 고정했습니다. 실행 후 이 표를 고치면 사후
  조정이므로, 고칠 일이 생기면 고치지 말고 여기 그 사실을 적습니다.
- **claims의 측정 기준이 아닙니다** — 의존 주장의 오라클은 컨트롤 플레인이고,
  이 표본은 사슬 시연·비교의 재료입니다(기존 설계 결정 유지).
- **비교 기준은 별도 문서**입니다: `document/archive/comparison-criteria-2026-08-02.md`.
  씨앗이 어떤 함정으로 세 계열을 가르는지(C1 침묵 실패 연대기), 무엇이 중립
  심판이고 무엇이 반중립인지, 어디서 비기고 어디서 갈릴지를 **실행 전에** 적어
  두었습니다. 부하 모양이 이 앱에서는 변별 안 됨(spiky라 버스트가 적합)도 미리
  기록했습니다 — 유리한 것만 세지 않기 위해서입니다.

## 정정 이력 (실행 전)

- **2026-08-02**: `constraints.txt`에 "클러스터 안에서, 관리형 데이터 서비스
  불사용(이식성)"을 추가. 사진 저장·DB의 관용적 답(S3·RDS)이 우리 측정 어휘
  밖이라, 이 제약이 없으면 비교가 서로 다른 흙 위에서 이뤄집니다(기준 문서
  §4). 실행 전 정정이라 사후 조정이 아닙니다.

## 실행 기록

| 계열 | 언제 | 무엇으로 | 산출물 |
|---|---|---|---|
| A 본 시스템 · 요구사항 | 2026-08-02 | `build_sample`, NIM `gpt-oss-120b`, 커밋은 RUN.json | `requirements/` 14종 |
| A 본 시스템 · 설계 | 2026-08-02 | `build_design`, NIM `gpt-oss-120b` | `design/` 4종 |
| A 본 시스템 · 구현 | — | — | — |
| A 본 시스템 · 배포 | — | — | — |
| B 단순 LLM | — | — | — |
| C 프레임워크 | — | — | — |

### A-요구사항 결과 — 관측점 대조 (2026-08-02, 실물)

**계약 충족.** `resource_spec.json`이 존재한다 — `provider=aws` ·
`region=ap-northeast-2`('Seoul'을 리전 해석 도구가 코드로) · `workloads=[k8sCluster]` ·
`monthlyBudgetUSD=400`. provenance에 값마다 출처(user/tool)가 붙는다. **침묵시킨
제약 쪽 결정 넷을 되묻기로 정확히 드러냈다**(`resource_intake.questions`):
minVCpu·minMemoryGiB(스펙 하한 — 없으면 스펙을 안 고른다) · trafficPattern ·
multiZone. 관측점 설계 그대로다.

**관심사 축은 신호 층만 돌았다**(`concern_linker_llm` 기본 꺼짐 — 과반 투표가
필요해 기본이 신호 층). 결과: load-shape=handoff · reachability=noted · **나머지
6=unjudged**. 이 값은 정확한 기록이다 — "안 다뤄졌다"가 아니라 "판정 수단이
없었다". 두 관찰:
- **신호 층의 한계가 드러났다**: NFR 10이 부하 모양(근무시간 집중)을 **말하는데도**
  load-shape가 handoff(미충족)로 찍혔다 — 사용자가 burst/peak/spike를 안 써서
  열쇠말이 못 잡았다. LLM 층이 잡을 자리다.
- **비교 함의**: 관심사 쪽 침묵 드러내기(C4)의 온전한 능력은 LLM 층(과반)에
  달렸다. 기본 실행은 계약 쪽 되묻기(4건)로만 침묵을 드러낸다. 실험에서 이
  구성 선택을 명시해야 공정하다 — 미결로 남긴다(§아래).

**미결 하나**: 관심사 LLM 층을 켠 완전 구성으로도 돌려 두 결과를 나란히 둘지.
비용(3배 호출) 대 C4 능력 온전성의 판단 — 실행 전 기준 문서의 정신대로 명시.

### A-설계 결과 — 관측점 대조 (2026-08-02, 실물)

**도구 재건이 선행됐다**: `app/design`이 순수 함수 → LangGraph 세션 구조로
리팩터돼 `build_design`의 `_run_stage`가 깨졌다. 팀원 코드는 **안 고치고**
생성 서브그래프(`DESIGN_SUBGRAPHS[stage]["generate"]`, persist·gate·DB 없는
순수 스테이지)를 **호출만** 하도록 도구를 고쳤다(커밋 별도). NIM 타임아웃이
한 번 났고(일시적), 4단계는 **한 프로세스**에서 완주해야 한다 — ERD가
클래스 단계의 인메모리 BCE 모델(`extracted_bce_classes`, 파일 미저장)에서
시드되기 때문.

산출물 4종:
- **class_diagram(9.8KB) — C-COVER 상류 강함**: BCE 경계 12·컨트롤 12·엔티티
  9가 **기능 요구 8개를 전부** 덮고, NFR까지 도메인 개념으로 뽑았다
  (`RetentionMetadata`=5년 보존 NFR 9 · `BackupBundle`=FR 8). 요구사항 단계의
  관심사 층(신호만)이 unjudged로 둔 data-fate·백업을 **설계가 엔티티로 surface**
  — 단계 간 교차 관찰.
- **sequence_diagram(11.7KB)**: 여러 유스케이스 흐름·alt 분기 담음. **비동기
  갭 실측**: `->>` 0개 — FR 4(사진→썸네일·PDF)와 NFR 13(사진 처리 실패가
  제출을 막으면 안 됨)이 큐 분리를 함의하는데 동기 메시지만 썼다. **우리 쪽
  약점**이고 lecture-platform과 같은 패턴 — 하류에 큐가 안 선다. C-COVER에서
  정직하게 감점.
- **api_spec(39KB)**: OpenAPI, 문법 통과.
- **erd(1.7KB)**: 엔티티 8(UserAccount·Site·InspectionReport·Thumbnail·
  PDFSummary·Photo·FollowUpRequest·BackupBundle).

**비결정성 관측**: 시퀀스가 한 실행에서 1KB(로그인만)·다른 실행에서 11.7KB
(여러 흐름)로 갈렸다 — gpt-oss-120b는 MoE라 temperature=0도 결정론이 아니다
(PROVENANCE 재현 단서 그대로). 흔들림은 실험의 T-생성 흔들림 그 자체.
