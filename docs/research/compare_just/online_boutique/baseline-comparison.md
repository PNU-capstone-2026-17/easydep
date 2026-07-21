# Baseline vs 우리 파이프라인 — 정량 비교

- 입력 요구사항 32개
- **baseline**: `intake → 명세 원샷(1콜) → 관계 원샷(1콜)` — **FR/NFR 분류 없이 요구사항 전체를 그대로** 주고, 커버리지 강제·명세 검증/반성·관계 참조가드 **없음**
- **ours**: 4단계 분해(액터→UC→명세→관계) + 각 단계 정적/의미 검증·반성·커버리지 강제·참조 가드
- 채점: 양쪽 산출물에 **동일한 결정론 검증기**(check_coverage·_validate_spec·관계 무결성)를 적용

## 입력 요구사항
- Shopper shall be able to browse the product catalog by category, brand, and popularity.
- Shopper shall be able to perform keyword searches and apply filters such as price range, rating, and availability.
- The product detail page shall display description, high‑resolution images, stock level, and price in the selected currency.
- User shall select a preferred currency from the supported list and have all prices rendered accordingly.
- Online Boutique shall retrieve live exchange rates from the external rates service and convert prices in real time.
- System shall cache exchange‑rate data for a maximum of five minutes to reduce external calls.
- Shopper shall add items to a shopping cart without requiring authentication.
- Cart shall persist across page navigations using a session identifier stored in a secure cookie.
- Shopper shall modify item quantities or remove items from the cart at any time before checkout.
- System shall compute cart subtotal, applicable taxes, and an estimated shipping cost based on the entered delivery zip code.
- Guest checkout shall be available, allowing shoppers to complete purchase without creating an account.
- Payment service shall process credit‑card, PayPal, and Apple Pay transactions and return a success or failure response.
- System shall validate payment details and reject transactions that fail verification rules.
- Upon successful payment, an order record shall be created with a globally unique order identifier.
- Order confirmation email shall be sent to the shopper’s email address within two minutes of order placement.
- Confirmation email shall contain an itemized order summary, shipping estimate, and a placeholder for a tracking link.
- Recommendation engine shall display at least three product suggestions on the order confirmation page based on the purchased items.
- Contextual advertising module shall serve relevant ads on product detail and checkout pages using shopper’s browsing context.
- An administrator shall upload new product data and update existing items through a secured API endpoint.
- Inventory service shall decrement stock quantities atomically when an order is confirmed.
- Shopper shall retrieve order status by providing order ID and email address on the order lookup page.
- System shall log all cart modifications and order lifecycle events to an immutable audit store.
- Online Boutique shall maintain an overall availability of 99.9 % across all microservices, measured on a monthly basis.
- Each gRPC call shall respond within 200 ms for the 95th percentile under normal traffic conditions.
- The platform shall automatically scale horizontally to support up to 10 000 concurrent shoppers by adding Kubernetes pods.
- Service mesh shall enforce mutual TLS for every inter‑service communication to ensure confidentiality and integrity.
- Sensitive data such as payment tokens shall be encrypted at rest using AES‑256 encryption.
- External dependencies (exchange‑rate service, payment gateway) shall be protected by circuit‑breaker patterns with defined fallback behavior.
- System shall emit structured logs and Prometheus metrics for all request paths to enable real‑time observability.
- Deployment pipeline shall perform zero‑downtime rolling updates via Helm charts and canary releases.
- Sessions shall be represented by signed JWTs stored in HttpOnly, Secure cookies and shall expire after 24 hours of inactivity.
- The application shall comply with PCI DSS requirements for all payment processing activities.

## 지표 대비

| 지표 | baseline | ours | 우위 |
|---|---|---|---|
| 유스케이스 수 | 10 | 5 | — |
| 명세 수 | 10 | 5 | — |
| 액터 수 | 8 | 5 | — |
| FR 총계 | 32 | 20 | — |
| FR 커버리지(결정론: 주장 기준) | 0.69 | 1 | ours |
| FR 커버리지(의미론: 검증된 것만) | 0.44 | 0.20 | baseline |
| 거짓 커버리지 주장(LLM 판정) | 14 | 17 | baseline |
| 고아 FR(어떤 UC에도 미포함) | 10 | 0 | ours |
| 유령 요구 참조(환각) | 0 | 0 | 동률 |
| 명세 정적 검증 위반 | 13 | 1 | ours |
| precondition 없는 명세 | 0 | 0 | 동률 |
| success_guarantee 없는 명세 | 0 | 0 | 동률 |
| 다이어그램 유령 참조 | 1 | 0 | ours |
| 고아 액터 | 0 | 0 | 동률 |
| 총 확장(예외·대안 흐름) | 15 | 19 | ours |
| 평균 주시나리오 스텝 | 4.40 | 5.60 | — |
| 관계 수 | 22 | 8 | — |

> **해석 유의**: baseline은 **분류 없이 요구사항 전체를 그대로** 주므로(번호 R1..는 추적용, 커버리지=전체 요구 대비 참조 비율), 우리 파이프라인의 clarify 구체화+FR/NFR 분류를 거친 요구 집합과 FR 총계·개수 지표가 달라진다(교란요인). 따라서 개수 지표는 참고용이고, **정당성의 핵심 근거는 요구 수와 무관한 품질 지표** — 특히 `명세 정적 검증 위반`(양쪽에 동일 검증기 적용)·`유령 참조`·`고아 액터` — 다. baseline이 남긴 위반을 우리 시스템의 검증·반성 루프가 0으로 수렴시키는지가 대비의 요점이다.

## 결함 상세
### baseline
- **명세 정적 위반**:
  - `UC1`: 2a: outcome=alternate_success인데 resume_at_step이 설정됨 · trigger: UI 용어 ['clicks'] — black-box 위반 · step 4: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC2`: trigger: UI 용어 ['clicks'] — black-box 위반
  - `UC3`: 1a: outcome=alternate_success인데 resume_at_step이 설정됨 · step 2: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC4`: 2a: outcome=alternate_success인데 resume_at_step이 설정됨 · 2b: outcome=alternate_success인데 resume_at_step이 설정됨 · trigger: UI 용어 ['button', 'clicks'] — black-box 위반
  - `UC5`: 3a: outcome=alternate_success인데 resume_at_step이 설정됨
  - `UC6`: trigger: UI 용어 ['button', 'clicks'] — black-box 위반
  - `UC7`: step 4: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC9`: step 3: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
- **고아 FR**(누락): R23, R24, R25, R26, R27, R28, R29, R30, R31, R32
- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):
  - UC2 claims R18: The scenario shows contextual ads being served on the product‑detail page only; it does not demonstrate ad serving on checkout pages, which is required by R18.
  - UC3 claims R5: The scenario may use a cached rate when the cached data is less than five minutes old, meaning the system does **not** always retrieve a live exchange rate before converting prices. Therefore it does not satisfy the requirement for always retrieving live rates and converting in real time.
  - UC4 claims R8: While the cart is stored in the session, the scenario does not specify that a session identifier is kept in a secure cookie, which is required by the requirement.
  - UC4 claims R9: The main scenario only covers adding an item; there are no steps for modifying quantities or removing items, so the requirement is not implemented.
  - UC4 claims R22: The scenario logs a cart‑modification event, satisfying the first part, but it does not log order‑lifecycle events, therefore the full requirement is not met.
  - UC5 claims R22: The scenario only logs the calculation event; it does not cover logging *all* cart modifications or order‑lifecycle events, nor does it specify an immutable audit store, so the requirement is not satisfied.
  - UC6 claims R12: The scenario only describes generic payment validation and a request to a PaymentGateway; it does not mention support for credit‑card, PayPal, or Apple Pay specifically, nor does it show handling of multiple payment types.
  - UC6 claims R13: While step 1 validates payment details, the scenario provides no behavior for rejecting transactions that fail the verification rules; only the successful path is described.
  - UC6 claims R22: The scenario logs only the order‑creation event (step 6). It does not log cart‑modification events, so the requirement to log *all* cart modifications and order lifecycle events is not fully met.
  - UC7 claims R15: The use case describes composing and sending an email within two minutes, but it does not explicitly state that the email is sent to the shopper’s email address. Without that explicit behavior, the requirement is not fully satisfied.
  - UC7 claims R22: The scenario only logs the email‑sent event to the audit store. It does not log cart modifications or other order lifecycle events, so the requirement is not met.
  - UC8 claims R22: The scenario only logs the recommendation‑display event; it does not log cart modifications or other order‑lifecycle events, so the requirement is not met.
  - UC9 claims R22: The use case only logs the order‑status lookup event. It does not log cart modifications, nor does it demonstrate logging of all order lifecycle events, so it does not fulfill the comprehensive audit‑logging requirement of R22.
  - UC10 claims R22: The scenario logs the product‑data upload/modification event, but R22 concerns logging *cart modifications* and *order lifecycle* events. Those behaviors are not present in this use case.
- **다이어그램 유령 참조**: generalization Shopper / Guest Shopper

### ours
- **명세 정적 위반**:
  - `UC5`: step 2: UI 용어 ['fields'] — black-box 위반
- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):
  - UC1 claims FR1: Use case includes filtered browsing and a generic ‘required response time’, but does not specify the 150 ms latency bound required by FR1.
  - UC1 claims FR2: Use case describes keyword search with up to three filters and a generic latency requirement, but does not state the ≤ 200 ms 95th‑percentile limit demanded by FR2.
  - UC1 claims FR3: Use case mentions a ‘high‑resolution image’ but does not define the minimum resolution (1920 × 1080 px) required by FR3.
  - UC1 claims FR5: Use case states retrieval over a secured HTTPS endpoint and conversion within ‘the conversion latency limit’, but does not quantify the limit as 100 ms as required by FR5.
  - UC1 claims FR20: No mention of JWTs, HttpOnly/Secure cookies, expiration, or refresh behavior in the described use case.
  - UC2 claims FR6: The use case shows an item being added but does not state that the shopper is unauthenticated nor does it provide any performance metric (≤ 150 ms).
  - UC2 claims FR7: The scenario mentions associating the addition with a session, but it does not describe persistence across page navigations nor the use of a secure, HttpOnly, SameSite‑Strict cookie.
  - UC2 claims FR8: Quantity changes and removals are covered, but the required UI update latency of ≤ 100 ms is not mentioned.
  - UC2 claims FR9: The scenario includes a cost breakdown with subtotal, taxes, and shipping, yet it does not specify that taxes are calculated from the shopper’s delivery zip code nor detail the estimation of shipping cost.
  - UC3 claims FR11: The scenario mentions validation against verification rules but does not specify the exact rules (Luhn, CVV, expiration) nor the required rejection behavior and error reporting.
  - UC3 claims FR12: An order identifier is created and persisted, but the use case does not state that it must be a UUID v4 nor that persistence must occur within 200 ms.
  - UC3 claims FR13: The email is sent within two minutes, but the scenario does not mention logging the delivery status as required.
  - UC3 claims FR14: The requirement concerns the contents of the confirmation email; the use case only describes those details on the order‑confirmation page, not in the email.
  - UC3 claims FR15: The page shows at least three recommendations, but the requirement adds constraints about derivation from purchased items and refresh per order, which are not addressed.
  - UC3 claims FR16: The use case makes no mention of contextual advertising or latency constraints on ad selection.
  - UC3 claims FR20: Session handling is mentioned only as a generic token refresh; the specific JWT format, cookie attributes, 24‑hour inactivity expiry, and refresh semantics are not described.
  - UC5 claims FR17: The use case only demonstrates creation of a new product entry; it does not show updating existing items, nor does it specify that the secured API is protected by mutual TLS and role‑based access control. Therefore the claimed requirement is not fully satisfied.

## 산출 다이어그램 (PlantUML)
### baseline
```
@startuml
left to right direction
actor "Shopper" as Shopper
actor "Administrator" as Administrator
rectangle System {
  usecase "Browse Catalog" as UC1
  usecase "View Product Detail" as UC2
  usecase "Select Preferred Currency" as UC3
  usecase "Manage Shopping Cart" as UC4
  usecase "Compute Cart Totals" as UC5
  usecase "Guest Checkout" as UC6
  usecase "Send Order Confirmation Email" as UC7
  usecase "Display Recommendations on Confirmation" as UC8
  usecase "Retrieve Order Status" as UC9
  usecase "Upload Product Data" as UC10
  usecase "Generate Recommendations" as D1
}
actor "ExchangeRateService" as ExchangeRateService
actor "PaymentGateway" as PaymentGateway
actor "RecommendationEngine" as RecommendationEngine
actor "AdvertisingModule" as AdvertisingModule
actor "InventoryService" as InventoryService
actor "AuditStore" as AuditStore
Shopper --- UC1
Shopper --- UC2
Shopper --- UC3
Shopper --- UC4
Shopper --- UC6
Shopper --- UC9
Administrator --- UC10
UC5 --- ExchangeRateService
UC6 --- PaymentGateway
D1 --- RecommendationEngine
UC1 --- AdvertisingModule
UC5 --- InventoryService
UC7 --- AuditStore
UC9 --- AuditStore
UC1 ..> UC2 : <<include>>
UC5 ..> UC3 : <<include>>
UC6 ..> UC5 : <<include>>
UC6 ..> UC7 : <<include>>
UC6 ..> UC8 : <<include>>
UC8 ..> D1 : <<include>>
UC3 ..> UC1 : <<extend>>
Shopper <|-- Guest_Shopper
@enduml
```

### ours
```
@startuml
left to right direction
actor "Shopper" as Shopper
actor "Guest Shopper" as Guest_Shopper
actor "Administrator" as Administrator
rectangle System {
  usecase "Browse product catalog" as UC1
  usecase "Manage shopping cart" as UC2
  usecase "Complete purchase (guest checkout)" as UC3
  usecase "Retrieve order status" as UC4
  usecase "Manage product catalog" as UC5
}
actor "Currency Exchange Service" as Currency_Exchange_Service
actor "Email Service" as Email_Service
Shopper --- UC1
Shopper --- UC2
Shopper --- UC4
Guest_Shopper --- UC3
Administrator --- UC5
UC1 --- Currency_Exchange_Service
UC3 --- Email_Service
Shopper <|-- Guest_Shopper
@enduml
```

