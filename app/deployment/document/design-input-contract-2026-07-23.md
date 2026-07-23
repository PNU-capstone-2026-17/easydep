# 설계 산출물 입력 계약 (appkb v1) — 무엇을 받을 것인가 (2026-07-23)

앱 계층 P2입니다. 계획에서 "파서 4종"이라 했던 것을 **정정**합니다 — 설계 산출물은
상류(설계 단계 에이전트)에서 **JSON으로 나오고, PlantUML은 그 표현일 뿐**입니다.
그래서 파싱 문제가 아니라 **상류와의 계약 문제**이고, 이 문서가 그 계약의 근거입니다.

계약 실체는 코드로 굳혔습니다:

| | |
|---|---|
| 스키마 | `appkb/schema.json` (JSON Schema 2020-12) |
| 예제 | `appkb/examples/order-demo.json` (주문 데모 — 4종 산출물 전부) |
| 의미 검증 | `appkb/contract.py` `validate_design()` — 참조 무결성, 문제를 목록으로 |
| 테스트 | `tests/test_appkb_contract.py` 18건 |

---

## 설계 방법 — 소비자에서 거꾸로

"UML에 뭐가 있는가"로 설계하면 상류가 만들 것만 늘어납니다. 반대로 갔습니다 —
**칸 하나하나가 우리 KB의 어느 소비자에 답하는가**(판정 기준 7). 소비자 없는 칸은
받지 않습니다.

| 받는 것 | 배포 결정에 주는 신호 | 소비하는 KB |
|---|---|---|
| OpenAPI 문서 (3.x 그대로) | HTTP 서비스다 · 노출 표면 크기 · 인증 필요(securitySchemes) | 컴퓨트 경로(resource_guideline) · LB · secretStore(svcmap) |
| ER `entities` + `ownerComponentId` | 영속 데이터가 있다 · **누가 소유하나** | svcmap(relationalDatabase) · DB 토폴로지 |
| ER `engineHint` | 엔진 선택(postgresql 등) | svcmap의 벤더 flavor (Azure DBforPostgreSQL vs DBforMySQL) |
| ER `relations` | 컴포넌트 경계를 넘는 관계 | 데이터 결합 경고 (advisory) |
| 시퀀스 `async: true` | 큐 후보 | svcmap(messageQueue) |
| 시퀀스 `actor` | 공개 노출 | nlb·보안그룹 (core-graph) |
| 시퀀스 동기 호출 | 컴포넌트 간 통신 | 배포 엣지 · 보안그룹 규칙 후보 |
| 클래스 `stereotypes` | 영속성·표면 신호 (**보조**) | 아키타입 추론 보강 |
| `requirements` | provider·region·규모·예산 | costkb·perfkb·sizingkb 조인 전부 |
| `components[].id` | 리소스 이름 씨앗 | **capacitykb 이름 제약 검증** (135,745건) |

**받지 않는 것**과 이유도 계약의 일부입니다:

- **PlantUML 텍스트** — 렌더링이지 원본이 아니다. `async`는 `->>' 화살표 표기가
  아니라 의미로 받는다.
- **클래스의 속성·메서드** — 소비자가 없다. 필요하면 `meta`로 (읽지 않는다).
- **배포 결정** — "이건 VM에 올려라"를 상류가 정하면 환각이 검사 없는 곳으로
  이사할 뿐이다. 예외는 `deployHint` 하나 — 그것도 **설계자의 주장**으로 기록되지
  우리 판단이 되지 않는다.

## 계약의 세 규칙

**1. 조인은 명시 id로만.** 네 산출물이 같은 것을 다른 이름으로 부릅니다
("OrderService" / "order-service" / "주문 서비스"). 이름 퍼지 조인은 svcmap에서
"Amazon RDS"가 Compute Optimizer 행에 걸렸던 그 실패의 앱판이 됩니다. 그래서 모든
산출물 요소는 `componentId`로 컴포넌트를 가리키고, 시퀀스 참여자는
componentId·externalId·actor 중 **정확히 하나**를 가리켜야 합니다. 못 가리키면
짐작하지 않고 검증이 거부합니다.

**2. 오타는 조용히 죽지 않는다.** `additionalProperties: false` — `comonentId`
오타가 조용히 무시되면 그 요소가 미매칭으로 빠지는데 왜 빠졌는지 아무도 모릅니다.
렌더링 좌표 같은 우리가 안 읽는 정보의 자리는 `meta`뿐입니다(계약은 좁게, 탈출구는
명시적으로).

**3. 모르는 값을 기본값으로 채우지 않는다.** 구체적으로 —

- `ownerComponentId`가 **필수**인 이유: 없으면 DB 토폴로지(공유 vs 서비스별)를 정할
  수 없고, 그때 공유 DB를 기본값으로 두는 것이 정확히 서브넷 256의 실패입니다.
- `requirements`가 비면 → 비용 조인을 못 한다고 답하지, 대표 리전을 임의로 고르지
  않습니다(프로바이더 명시가 라우팅 변수라는 실측).
- `engineHint`가 없으면 → flavor를 고르지 않고 선택지를 보여줍니다.

`components[].id`를 `^[a-z0-9][a-z0-9-]{0,62}$`로 제한한 것도 같은 결입니다 —
이 id가 클라우드 리소스 이름이 되므로, capacitykb 패턴 제약과 어긋날 문자를
**처음부터 받지 않습니다.**

## 산출물별 능력 행렬 — 무엇만 있으면 무엇까지 답하나

부분 입력은 부분 답입니다. 지어내지 않습니다.

| 입력 조합 | 답할 수 있는 것 | 답할 수 없는 것 (정직하게) |
|---|---|---|
| OpenAPI만 | HTTP 서비스 + 컴퓨트 + LB | DB·큐 — "설계에 없어서 모른다" |
| + ER | 위 + DB 필요·소유·엔진 | 큐·노출 경로 |
| + 시퀀스 | 위 + 큐 후보·공개 노출·통신 엣지 | — |
| 클래스만 | 거의 없음 — 컴포넌트 묶음·영속성 **힌트**뿐 | **"클래스 다이어그램만으론 배포를 정할 수 없다"** |
| requirements 없음 | 구조는 전부 | **값 전부** — 단가·리전·사이징 조인 불가 |

## 검증이 실측으로 잡은 것 — oneOf가 세부를 삼킨다

산출물 4종이 `oneOf`라, `ownerComponentId` 누락이 *"is not valid under any of the
given schemas"*로 뭉개졌습니다. jsonschema의 `best_match` 휴리스틱은 **엉뚱한
분기**(openapi의 componentId)를 골랐습니다. 산출물마다 `kind` 판별자가 있으므로
휴리스틱 대신 **판별자로 분기를 직접 고르게** 했고, `_KIND_BRANCH`가 스키마의 oneOf
순서와 함께 움직이는 것까지 테스트 4건이 고정합니다.

문제는 예외가 아니라 **목록으로** 돌려줍니다 — 상류는 에이전트라 첫 오류에서 멈추면
고치고-다시를 반복하게 됩니다.

## 아키타입 추론은 계약 밖이다 — 일부러

"order-api는 HTTP 서비스, order-worker는 큐 소비자"라는 판단은 이 계약에 **없습니다.**
그건 P3(composer)가 위 신호들로 내리는 추론이고 `inferred`로 hedge됩니다. 계약이
설계 도메인(클래스·엔티티·참여자·엔드포인트)에 머무는 이유입니다 — 상류는 설계를
말하고, 배포 해석의 책임(그리고 hedge)은 우리가 집니다.

## 상류와 확인할 것

계약은 상대가 있는 문서라, 다음 셋은 상류 파이프라인 쪽 확인이 필요합니다.

1. **componentId를 상류가 줄 수 있는가** — 설계 에이전트가 산출물 간 공통 id를
   유지해야 합니다. 못 하면 이 계약의 1번 규칙이 상류에서 성립하지 않습니다.
2. **requirements의 출처** — 요구사항 분석 단계 산출물에서 오는 것으로 설계했는데,
   그 단계의 JSON 형식과 맞춰야 합니다.
3. **출력 다이어그램 형식** — 파이프라인이 PlantUML을 쓰므로 P3의 배포 다이어그램도
   PlantUML로 내는 것으로 계획을 바꿉니다(원래 Mermaid였음).
