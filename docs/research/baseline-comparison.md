# Baseline vs 우리 파이프라인 — 정량 비교

- 입력 요구사항 7개
- **baseline**: `intake → 명세 원샷(1콜) → 관계 원샷(1콜)` — **FR/NFR 분류 없이 요구사항 전체를 그대로** 주고, 커버리지 강제·명세 검증/반성·관계 참조가드 **없음**
- **ours**: 4단계 분해(액터→UC→명세→관계) + 각 단계 정적/의미 검증·반성·커버리지 강제·참조 가드
- 채점: 양쪽 산출물에 **동일한 결정론 검증기**(check_coverage·_validate_spec·관계 무결성)를 적용

## 입력 요구사항
- A registered user can log in with email and password.
- A registered user can add a product to the shopping cart.
- A registered user can place an order for the items in the cart.
- The system shall validate the shipping address before checkout.
- An administrator can view all placed orders.
- The checkout page shall load within 2 seconds.
- All personal data shall be encrypted at rest.

## 지표 대비

| 지표 | baseline | ours | 우위 |
|---|---|---|---|
| 유스케이스 수 | 4 | 5 | — |
| 명세 수 | 4 | 5 | — |
| 액터 수 | 6 | 2 | — |
| FR 총계 | 7 | 7 | — |
| FR 커버리지(결정론: 주장 기준) | 0.71 | 1 | ours |
| FR 커버리지(의미론: 검증된 것만) | 0.43 | 0.86 | ours |
| 거짓 커버리지 주장(LLM 판정) | 2 | 1 | ours |
| 복합 FR(품질제약 융합) | 2 | 0 | ours |
| 고아 FR(어떤 UC에도 미포함) | 2 | 0 | ours |
| 유령 요구 참조(환각) | 0 | 0 | 동률 |
| 명세 정적 검증 위반 | 4 | 0 | ours |
| precondition 없는 명세 | 0 | 0 | 동률 |
| success_guarantee 없는 명세 | 0 | 0 | 동률 |
| 다이어그램 유령 참조 | 0 | 0 | 동률 |
| 고아 액터 | 0 | 0 | 동률 |
| 총 확장(예외·대안 흐름) | 7 | 15 | ours |
| 평균 주시나리오 스텝 | 4.50 | 2.40 | — |
| 관계 수 | 15 | 5 | — |

> **해석 유의**: baseline은 **분류 없이 요구사항 전체를 그대로** 주므로(번호 R1..는 추적용, 커버리지=전체 요구 대비 참조 비율), 우리 파이프라인의 clarify 구체화+FR/NFR 분류를 거친 요구 집합과 FR 총계·개수 지표가 달라진다(교란요인). 따라서 개수 지표는 참고용이고, **정당성의 핵심 근거는 요구 수와 무관한 품질 지표** — 특히 `명세 정적 검증 위반`(양쪽에 동일 검증기 적용)·`유령 참조`·`고아 액터` — 다. baseline이 남긴 위반을 우리 시스템의 검증·반성 루프가 0으로 수렴시키는지가 대비의 요점이다.

## 결함 상세
### baseline
- **명세 정적 위반**:
  - `UC2`: 3a: outcome=fail인데 resume_at_step이 설정됨 · trigger: UI 용어 ['button', 'clicks'] — black-box 위반
  - `UC3`: 3a: outcome=fail인데 resume_at_step이 설정됨 · 5a: outcome=fail인데 resume_at_step이 설정됨
- **복합 FR**(품질제약이 FR에 융합 — 원자성 위반):
  - R6: 융합된 품질제약 ['within 2 seconds']
  - R7: 융합된 품질제약 ['encrypted']
- **고아 FR**(누락): R6, R7
- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):
  - UC2 claims R2: The use case describes the steps for adding a product to a shopping cart, but it does not address the prerequisite that the user must be a registered (authenticated) user. Without any step that verifies or assumes user registration, the functional condition of the requirement is not demonstrated.
  - UC3 claims R3: The use‑case describes the checkout flow and order creation, but it never states that the user must be a registered (authenticated) user nor shows any step that checks registration. Without an explicit precondition or action confirming the user’s registered status, the functional claim that a *registered* user can place an order is not demonstrably satisfied.

### ours
- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):
  - UC2 claims FR2: The use case describes adding a product to a shopping cart but does not specify that the user is a registered user, which is a functional precondition of the requirement.

## 산출 다이어그램 (PlantUML)
### baseline
```
@startuml
left to right direction
actor "User" as User
actor "Administrator" as Administrator
rectangle System {
  usecase "Log In" as UC1
  usecase "Add Product to Shopping Cart" as UC2
  usecase "Place Order (Checkout)" as UC3
  usecase "View All Orders" as UC4
  usecase "Authenticate User" as D1
  usecase "Validate Shipping" as D2
  usecase "Process Payment" as D3
  usecase "Persist Order" as D4
}
actor "Authentication Service" as Authentication_Service
actor "Shipping Validation Service" as Shipping_Validation_Service
actor "Payment Gateway" as Payment_Gateway
actor "Database" as Database
User --- UC1
User --- UC2
User --- UC3
Administrator --- UC4
UC1 --- Authentication_Service
UC3 --- Shipping_Validation_Service
UC3 --- Payment_Gateway
UC2 --- Database
UC3 --- Database
UC4 --- Database
UC1 ..> D1 : <<include>>
UC3 ..> D2 : <<include>>
UC3 ..> D3 : <<include>>
UC3 ..> D4 : <<include>>
UC3 ..> UC1 : <<extend>>
@enduml
```

### ours
```
@startuml
left to right direction
actor "Registered User" as Registered_User
actor "Administrator" as Administrator
rectangle System {
  usecase "Log in" as UC1
  usecase "Add product to shopping cart" as UC2
  usecase "Place order" as UC3
  usecase "Maintain personal data" as UC4
  usecase "View all orders" as UC5
}
Registered_User --- UC1
Registered_User --- UC2
Registered_User --- UC3
Registered_User --- UC4
Administrator --- UC5
@enduml
```

