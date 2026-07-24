# 앱 계층 연결 계획 — 설계도에서 배포 다이어그램까지 (2026-07-23)

## 무엇을 하려는가

> 앱의 설계도(클래스/시퀀스/ER 다이어그램, API 명세)로부터 **클라우드 네이티브
> 애플리케이션 배포 다이어그램**을 생성하는 데 지금까지의 KB를 활용한다.

이건 `research.md` **목표 3**("개발 단계별 산출물 생성")과 목표 2의 교차점입니다.
지금까지 여덟 축은 전부 "리소스를 고른 뒤"의 질문에 답했는데, 이 사슬은 **고르기
전** — 설계도에서 출발합니다.

```
설계도 (클래스·시퀀스·ER·OpenAPI)
   ↓ ① 파싱                          ← 없음 (새로 만듦)
구성요소 + 아키타입 (IR)
   ↓ ② 아키타입 → 클라우드 서비스     ← 없음 (core 층에 DB·큐·캐시가 없다)
클라우드 리소스 선택지
   ↓ ③ 기존 KB 조인                  ← **있음** (이번 라운드까지 만든 것)
배포 다이어그램 + 값·경고·제약 검증
```

**③은 이미 있습니다.** `resource_guideline`(군+값), bundlekb(동반 리소스),
sizingkb(bitnami 컨테이너 프리셋 28건 — 크기를 안 정한 서비스의 기본값),
costkb/perfkb(단가·경고), capacitykb(**생성한 리소스 이름을 패턴·길이 제약
135,745건으로 검증** — 이 용도로는 아직 안 쓰고 있다), graphkb(생성 순서).

비는 것은 ①과 ②입니다. 그리고 실측 결과 **②를 처음으로 '명시(stated)' 근거로
만들 수 있습니다** — 지금 mapping-graph 82엣지는 전부 짐작·단일 출처입니다.

---

## 소스 실측 결과

전부 HTTP로 직접 확인했습니다 (2026-07-23).

### 채택 — ② 서비스 대응 (svcmap)

| 소스 | 라이선스 | 핀 | 실측 |
|---|---|---|---|
| **MicrosoftDocs/architecture-center** | **CC-BY-4.0** | 태그 없음 → 커밋 SHA (azure-docs 선례) | AWS↔Azure 표: index 76행 + compute 42 + storage 28 + networking 29 + messaging 9 + databases 7. **GCP↔Azure 표: 199행** |
| **mingrammer/diagrams** v0.24.4 | MIT | 태그 | 프로바이더 모듈: aws·azure·gcp·**ibm·alibabacloud·oci·openstack**·k8s. 카테고리 파일 구조(database.py 등)가 곧 분류 체계. aws/database.py에 클래스 35개 |

**Azure가 허브입니다** — AWS↔Azure와 GCP↔Azure가 있으니 AWS↔GCP는 추이적으로
나옵니다. 추이 대응은 직접 대응보다 약하므로 **그 사실을 basis에 새깁니다**
(mapping-graph의 `_weakest_link` 선례 그대로).

두 소스가 **독립**입니다(Microsoft 문서 vs 커뮤니티 큐레이션). 둘이 일치하는 대응은
`✓ 교차 확인` — 이 저장소에서 처음으로 벤더 간 대응에 명시+교차 근거가 생깁니다.

### 채택 — ① 설계도 파싱 (appkb)

| 입력 | 방법 | 근거 |
|---|---|---|
| OpenAPI (json/yaml) | 직접 파싱. 명세 스키마는 OAI 저장소로 검증 (Apache-2.0, 태그) | 사용자 산출물 = **그 앱에 대한 명시** |
| Mermaid classDiagram·erDiagram·sequenceDiagram | **텍스트 서브셋을 직접 파싱** (mermaid MIT v11 문법 참조; 코드는 안 씀) | 〃 |
| SQL DDL | sqlglot (MIT, v30.13.0, PyPI) — pgdumplib처럼 **선택 의존성** | 〃 |
| compose.yaml | 직접 파싱 (YAML) | 〃 |

PlantUML은 **뒤로 미룹니다** — 코드가 LGPL-3.0인 건 텍스트 파싱엔 무관하지만,
Mermaid가 GitHub·문서에서 더 흔하고 문법이 단순합니다. XMI는 기각(복잡도 대비 가치).

### 채택 — 아키타입 표의 검증 코퍼스

| 소스 | 라이선스 | 실측 |
|---|---|---|
| **docker/awesome-compose** | CC(법전문 — 정확 판별은 채택 시) | 스택 34개. 앞 20개의 image 어휘: postgres 4 · mariadb 4 · nginx 4 · redis류 3 · mongo 1 … |

이미지 이름 → 아키타입(postgres → 관계형 DB, redis → 캐시)은 손 표로 만들되,
이 코퍼스로 **실측 검증**합니다. n=34라 작습니다 — 비율의 근거가 아니라 손 표가
현실과 어긋나지 않는지 보는 용도입니다(bundlekb의 MIN_SAMPLES 규율 유지).

### 채택 — ④ 패턴 산문 (RAG, 새 형태)

| 소스 | 라이선스 | 용도 |
|---|---|---|
| architecture-center `docs/` 선별 하위 | CC-BY-4.0 (**재배포 허용, 저작자 표시 필수**) | 참조 아키텍처·설계 지침 검색 |
| heroku/12factor | MIT | 배포 원칙 |

**microservices.io는 기각** — 저장소가 404이고 사이트 본문은 저작권 명시 없이는
코퍼스에 담을 수 없습니다(앞선 조사와 동일 결론).

### 기각

| 소스 | 이유 |
|---|---|
| Crossplane platform-ref | v2에서 구성이 함수 패키지 안으로 → 기계 판독 실패 (앞선 조사) |
| XMI/UML 메타모델 | 복잡도 대비 답할 질문이 없음 |
| AWS 공식 비교 문서 | regional-table 선례 — "intended for use only on aws.amazon.com" 류 위험. MS 표가 같은 내용을 CC-BY로 줌 |

---

## 컨벤션에서 벗어나는 곳 — 그리고 벗어나지 않는 곳

사용자 지시대로 형태를 넓히되, **원칙과 형태를 구분**합니다.

| 벗어나는 것 (형태) | 유지하는 것 (원칙) |
|---|---|
| 산문 코퍼스를 검색(RAG)으로 제공 | 검색 결과는 **인용+출처+"지침이지 사실 아님"** 라벨로만. `is_fact`가 되는 일 없음 |
| 사용자 산출물이 소스가 됨 (핀 불가) | 산출물 해시를 IR에 기록 — "어느 설계도에서 나온 계획인지" 추적 |
| LLM이 아키타입 분류에 개입 | 결정론 신호(이미지 이름·ER 존재·async 화살표)를 먼저 쓰고, LLM 분류는 `inferred`로 hedge |
| 배포 다이어그램이라는 생성물 | **다이어그램의 모든 노드·엣지에 근거를 단다** — 설계도에서 왔는지, KB에서 왔는지, 지침에서 왔는지 |

RAG 구현도 단계적으로 갑니다: **1단계는 SQLite FTS5**(표준 라이브러리, 결정론,
의존성 0)로 시작하고, 재현율이 부족하다고 **실측되면** 임베딩(NIM 엔드포인트 또는
로컬 모델)으로 올립니다. "RAG = 벡터"라는 고정관념부터 버리는 게 이 저장소답습니다.

---

## 계획 — 4단계

### P1. svcmapkb — 벤더 간 서비스 대응 (명시 근거로)

가장 지렛대가 큽니다. 기존 약점(82엣지 전부 짐작)을 직접 고치고, ②를 엽니다.

- 새 층 `app::` 개념 ~10개: relationalDatabase · keyValueStore · messageQueue ·
  objectStorage · serverlessFunction · containerService · cdn · dnsZone ·
  secretStore · searchIndex. **core-graph에 넣지 않습니다** — 그건 tumblebug
  스웨거의 미러라 우리 개념을 섞으면 미러가 오염됩니다. 별도 산출물
  `svcmap-graph.json`.
- MS 표 파싱(191행 AWS↔Azure + 199행 GCP↔Azure) → `ms-learn-comparison` (stated).
- mingrammer 분류(7개 CSP) → `mingrammer-taxonomy` (큐레이션). 일치 시 교차 확인.
- 서비스명 → 타입 id 접두(`Amazon RDS` → `AWS::RDS::`)는 **손 표 + 빌드 검증**
  (endoflife 14건 손매핑 선례).
- `kb_equivalent_types`가 관리형 서비스에도 답하게 확장.
- **실행 경계 명시**: cb-tumblebug은 VM·k8s만 배포하므로 관리형 서비스 대응은
  "안내이지 이 도구로 만들 수 있다는 뜻이 아님" (cap_csp_supports와 같은 구분).

> **P1·P2·P3 완료 (2026-07-23).** 진행하며 계획이 두 곳에서 바뀌었습니다 —
> **P2는 파서가 아니라 입력 계약**이 됐고(설계 산출물이 JSON으로 오므로),
> 출력 다이어그램은 Mermaid가 아니라 **PlantUML**입니다(상류 파이프라인에 맞춤).
> 실제 산출물: `graphkb/parsers/svcmap.py` · `appkb/` · `nim_agent/design_tools.py`.
> 계약의 근거는 `document/design-input-contract-2026-07-23.md`.

### P2. appkb — 설계도 파서 + IR

- **IR(중간 표현) 스키마가 이 단계의 본체**입니다: 구성요소(이름·아키타입·상태성·
  노출·의존)와 엣지(동기 호출·비동기·읽기/쓰기), **칸마다 어느 산출물 어느 줄에서
  왔는지** 기록.
- 파서 4종(OpenAPI·Mermaid 서브셋·compose·DDL). 산출물이 말한 것은 stated,
  산출물에서 **추론**한 것(async 화살표 → 큐 후보)은 inferred로 구분.
- 손 아키타입 표(image → 아키타입)를 awesome-compose 34스택으로 검증.
- 정직성 규칙: **클래스 다이어그램만으로는 배포를 정할 수 없다** — 부족한 입력에는
  무엇이 더 필요한지 답한다(지어내지 않는다).

### P3. 배포 구성기 + 다이어그램 + 검증

- IR × svcmap × 기존 KB → 배포 계획: **2계층 출력** —
  실행 가능 부분(VM·k8s 경로, tumblebug 스펙까지) / 안내 부분(관리형 서비스,
  프로바이더별 대응과 알려진 값만).
- 다이어그램은 **Mermaid 텍스트**(architecture/C4) — 의존성 0, 어디서나 렌더.
- **다이어그램 주장 대조**: 생성된 다이어그램을 되파싱해서 모든 노드가 KB에
  해석되는지, 모든 엣지에 근거(설계도|KB|지침)가 있는지, 이름이 capacitykb
  패턴·길이 제약을 지키는지 기계로 검사. `claim_check` 계보의 확장.
- 합계 금지·부분 가격 원칙 그대로 (관리형 DB 가격은 여전히 0건 — decisions §8의
  T5와 연결).
- 프로브: petstore OpenAPI + 간단한 ER을 넣어 끝까지 가는 GL계열 프로브,
  그리고 역방향(큐가 필요한 앱 → "실행 경로로는 못 만든다"를 밝히는가).

### P4. patternkb — 산문 검색 (advisory 전용)

- 코퍼스: architecture-center 선별 하위(CC-BY-4.0) + 12factor(MIT). 커밋 가능 —
  CC-BY 저작자 표시를 NOTICE에.
- SQLite FTS5 색인. 도구는 인용문+문서 경로+라이선스를 돌려주고 **항상**
  "설계 지침이지 클라우드 사실이 아님"을 답니다.
- 아키타입 분류가 애매할 때 P3가 참고하되, 다이어그램 근거 라벨은 `pattern-advisory`.

> **P4 완료 (2026-07-24, 재편 계획 ⑥-C).** 코퍼스 90편 — architecture-center
> `docs/patterns`(44)·`guide/architecture-styles`(7)·`guide/design-principles`(11)·
> `best-practices`(13) + 12factor `content/en`(15, toc 제외). 계획에서 바뀐 것:
> 12factor는 heroku 저장소에 태그가 없어 **커밋 SHA 핀**(MIT는 LICENSE로 실측),
> 색인은 디스크 DB가 아니라 **커밋된 JSON 코퍼스에서 인메모리로** 만든다(바이트
> 대조 재현 검증을 지키려고). P3 참고 지점은 compose의 **engineHint 미상**
> 자리 하나로 좁혔다 — 분류·미결 판정은 자문이 바꾸지 않는다.
> 산출물: `patternkb/` · `data/pattern-corpus.json.gz` · `pattern_search` 도구.

### 순서의 이유

P1이 먼저인 것은 **③이 이미 있어서**입니다 — 대응만 열리면
`kb_equivalent_types`·`resource_guideline`이 즉시 관리형 서비스까지 넓어집니다.
P4가 마지막인 것은 **없어도 P1~P3이 돌아가기 때문**입니다(advisory는 부가).

---

## 위험 — 미리 적어 두는 것

- **추이 대응(AWS↔GCP)은 직접 대응보다 약하다.** basis에 새기고 답에 싣는다.
- **관리형 서비스 가격이 0건이다.** 배포 다이어그램의 비용 주석은 VM·k8s 부분만
  나온다. 합계를 내지 않는 원칙이 여기서도 지켜져야 한다.
- **아키타입 분류는 영원히 inferred다.** "이 클래스는 서비스다"를 사실로 만들
  방법은 없다 — hedge를 답에 싣는 것까지가 우리가 할 수 있는 일이다.
- **awesome-compose n=34.** 검증 코퍼스이지 통계 근거가 아니다.
- **MS 표는 문서라 표가 재편될 수 있다.** SHA 핀 + 행수 검사로 드리프트를 잡는다
  (aws-professional/services.md가 실제로 404가 되고 카테고리별로 쪼개져 있었다 —
  이번 조사에서 직접 겪었다).
