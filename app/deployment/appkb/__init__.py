"""appkb — 앱 설계 산출물(JSON)을 받는 입력 계약.

**여기는 파서가 없다.** 설계 산출물은 상류(설계 단계 에이전트)에서 JSON으로 나오고,
PlantUML 다이어그램은 그 JSON의 **표현**일 뿐이다. 우리가 정할 것은 파싱 방법이
아니라 **무엇을 받을 것인가** — 그 계약이 이 패키지다.

## 설계 방법 — 소비자에서 거꾸로

칸 하나하나가 "우리 KB의 무엇이 이걸 소비하는가"에 답해야 들어온다(판정 기준 7:
답할 질문이 있는가). 소비자 없는 칸은 받지 않는다 — 상류가 만들 것만 늘어난다.

    OpenAPI 문서        → HTTP 서비스라는 신호, 노출 표면        → 컴퓨트·LB 경로
    ER 엔티티           → 영속 데이터 필요                       → svcmap(relationalDatabase)
    ER engineHint       → 엔진 선택(postgresql 등)               → svcmap의 벤더 flavor
    시퀀스 async 메시지  → 큐 후보                                → svcmap(messageQueue)
    시퀀스 actor        → 공개 노출                               → nlb·보안그룹(core-graph)
    시퀀스 동기 호출     → 컴포넌트 간 통신                        → 배포 엣지·보안그룹 규칙
    클래스 stereotype   → 영속성·표면 신호(보조)                  → 아키타입 추론 보강
    requirements        → provider·region·규모·예산                → costkb·perfkb·sizingkb 조인
    component id        → 리소스 이름 씨앗                         → capacitykb 이름 제약 검증

## 계약의 세 규칙

1. **조인은 명시 id로만.** 산출물들이 같은 것을 다른 이름으로 부른다
   ("OrderService"/"order-service"/"주문 서비스") — 이름 퍼지 조인은 하지 않는다.
   모든 산출물 요소는 `componentId`로 컴포넌트를 가리켜야 하고, 못 가리키면
   짐작하지 않고 **미결로 보고**한다.
2. **배포 결정은 상류로 옮기지 않는다.** 아키타입 분류("이건 서비스다")는 우리
   추론이고 `inferred`로 hedge한다. 상류가 굳이 지정하고 싶으면 `deployHint`로 —
   그건 **설계자의 주장**으로 기록되지 우리 판단이 되지 않는다.
3. **모르는 값을 기본값으로 채우지 않는다.** requirements가 비면 비용 조인을
   못 한다고 답하지, 대표 리전을 임의로 고르지 않는다(프로바이더 명시가 라우팅
   변수라는 실측 그대로).

스키마: `appkb/schema.json` · 예제: `appkb/examples/` · 의미 검증: `appkb/contract.py`
설계 근거: `document/design-input-contract-2026-07-23.md`
"""
