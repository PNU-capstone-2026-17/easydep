# Baseline vs 우리 파이프라인 — 정량 비교

- 입력 요구사항 32개
- **baseline**: `intake → 명세 원샷(1콜) → 관계 원샷(1콜)` — **FR/NFR 분류 없이 요구사항 전체를 그대로** 주고, 커버리지 강제·명세 검증/반성·관계 참조가드 **없음**
- **ours**: 4단계 분해(액터→UC→명세→관계) + 각 단계 정적/의미 검증·반성·커버리지 강제·참조 가드
- 채점: 양쪽 산출물에 **동일한 결정론 검증기**(check_coverage·_validate_spec·관계 무결성)를 적용

## 입력 요구사항
- Travelers shall be able to search for available train routes by origin, destination, and date.
- The platform shall return a list of matching itineraries with departure/arrival times, duration, and available seat classes.
- Users shall be able to select a specific seat class and view a seat map for each train.
- Travelers shall be able to reserve a particular seat and add it to a pending booking.
- The system shall calculate total fare based on selected class, distance, and applicable discounts.
- Travelers shall be able to complete payment using supported payment methods and receive a confirmation receipt.
- Users shall be able to view a summary of all active and historical orders in a personal dashboard.
- Travelers shall be able to cancel a confirmed booking and receive an automatic refund according to the cancellation policy.
- The platform shall generate a refundable ticket PDF and email it to the traveler upon successful purchase.
- Travelers shall be able to order in‑trip meals from a menu linked to the selected train and seat.
- Users shall be able to add, edit, and remove consignments, and track their status through a real‑time tracking view.
- The system shall validate passenger information against the traveler's profile before confirming a booking.
- Travelers shall be able to register a new account, verify email, and set a secure password.
- Users shall be able to log in using password or federated identity providers (e.g., SSO).
- An administrator shall create, update, and delete train records, including train number, capacity, and equipment type.
- An administrator shall define, modify, and publish route definitions with intermediate stations and mileage.
- Administrators shall schedule train departures, assign rolling stock, and publish timetables.
- Administrators shall view, filter, and export all traveler orders for reporting purposes.
- The platform shall send booking confirmation, cancellation, and refund notifications via email and push.
- The system shall publish travel news and service alerts to subscribed travelers based on their itinerary.
- Users can retrieve a QR code for each ticket to be scanned at station gates.
- Travelers shall be able to add multiple passenger contacts to a booking and designate a primary contact.
- TrainTicket shall be deployed as 41 containerized microservices orchestrated by Kubernetes, enabling horizontal scaling of each service.
- The platform shall achieve 99.9% availability measured on a monthly basis, with automatic failover across multiple zones.
- System shall enforce role‑based access control (RBAC) so that only authorized administrators can modify train and schedule data.
- All data in transit shall be encrypted using TLS 1.3, and sensitive data at rest shall be encrypted with AES‑256.
- The platform shall emit distributed tracing spans for each request, compatible with OpenTelemetry, to support end‑to‑end observability.
- Response time for route search shall not exceed 2 seconds for the 95th percentile of requests under normal load.
- The system shall process payment transactions within 3 seconds and guarantee exactly‑once semantics.
- Each microservice shall be stateless or store state in a centralized data store, allowing seamless container replacement.
- The platform shall support rolling updates with zero‑downtime deployments, rolling back automatically on health‑check failures.
- Logs shall be centralized and retained for at least 90 days, searchable by correlation ID.

## 지표 대비

| 지표 | baseline | ours | 우위 |
|---|---|---|---|
| 유스케이스 수 | 13 | 12 | — |
| 명세 수 | 13 | 12 | — |
| 액터 수 | 7 | 6 | — |
| FR 총계 | 32 | 21 | — |
| FR 커버리지(결정론: 주장 기준) | 0.81 | 1 | ours |
| FR 커버리지(의미론: 검증된 것만) | 0.50 | 0.71 | ours |
| 거짓 커버리지 주장(LLM 판정) | 16 | 6 | ours |
| 고아 FR(어떤 UC에도 미포함) | 6 | 0 | ours |
| 유령 요구 참조(환각) | 0 | 0 | 동률 |
| 명세 정적 검증 위반 | 21 | 0 | ours |
| precondition 없는 명세 | 0 | 0 | 동률 |
| success_guarantee 없는 명세 | 0 | 0 | 동률 |
| 다이어그램 유령 참조 | 0 | 0 | 동률 |
| 고아 액터 | 0 | 0 | 동률 |
| 총 확장(예외·대안 흐름) | 16 | 42 | ours |
| 평균 주시나리오 스텝 | 5.85 | 4.58 | — |
| 관계 수 | 41 | 19 | — |

> **해석 유의**: baseline은 **분류 없이 요구사항 전체를 그대로** 주므로(번호 R1..는 추적용, 커버리지=전체 요구 대비 참조 비율), 우리 파이프라인의 clarify 구체화+FR/NFR 분류를 거친 요구 집합과 FR 총계·개수 지표가 달라진다(교란요인). 따라서 개수 지표는 참고용이고, **정당성의 핵심 근거는 요구 수와 무관한 품질 지표** — 특히 `명세 정적 검증 위반`(양쪽에 동일 검증기 적용)·`유령 참조`·`고아 액터` — 다. baseline이 남긴 위반을 우리 시스템의 검증·반성 루프가 0으로 수렴시키는지가 대비의 요점이다.

## 결함 상세
### baseline
- **명세 정적 위반**:
  - `UC1`: 2a: outcome=alternate_success인데 resume_at_step이 설정됨
  - `UC2`: 4a: outcome=alternate_success인데 resume_at_step이 설정됨 · 5a: outcome=alternate_success인데 resume_at_step이 설정됨 · step 4: UI 용어 ['clicks'] — black-box 위반
  - `UC3`: 4a: outcome=alternate_success인데 resume_at_step이 설정됨 · 5a: outcome=alternate_success인데 resume_at_step이 설정됨 · step 5: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC5`: 4a: outcome=alternate_success인데 resume_at_step이 설정됨
  - `UC6`: 2a: outcome=alternate_success인데 resume_at_step이 설정됨
  - `UC7`: 3a: outcome=alternate_success인데 resume_at_step이 설정됨
  - `UC8`: 4a: outcome=alternate_success인데 resume_at_step이 설정됨 · step 5: UI 용어 ['clicks'] — black-box 위반
  - `UC9`: 3a: outcome=alternate_success인데 resume_at_step이 설정됨 · step 4: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리) · step 5: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC10`: 5a: outcome=alternate_success인데 resume_at_step이 설정됨 · step 6: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC11`: 4a: outcome=alternate_success인데 resume_at_step이 설정됨 · step 1: UI 용어 ['button'] — black-box 위반 · step 2: UI 용어 ['clicks'] — black-box 위반
  - `UC12`: 4a: outcome=alternate_success인데 resume_at_step이 설정됨
- **고아 FR**(누락): R23, R26, R27, R30, R31, R32
- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):
  - UC1 claims R28: The scenario contains no information about response‑time constraints, performance measurements, or load conditions; therefore the 2‑second response‑time requirement is not addressed.
  - UC2 claims R12: No step validates passenger information against the traveler's profile.
  - UC2 claims R22: The use case does not address adding multiple passenger contacts or designating a primary contact.
  - UC3 claims R5: The use case only mentions a generic “calculate fare” in the goal; it does not specify that the calculation uses class, distance, and discounts as required.
  - UC3 claims R9: A refundable ticket PDF is generated, but the use case only emails a receipt; it does not state that the ticket PDF itself is emailed to the traveler.
  - UC3 claims R29: The scenario contains no timing constraint nor any guarantee of exactly‑once transaction semantics.
  - UC4 claims R19: Only push notifications are described; the requirement also mandates email notifications, which are not covered by the scenario.
  - UC4 claims R20: The scenario contains no functionality for publishing travel news or service alerts based on itineraries.
  - UC5 claims R19: The use case sends cancellation and refund notifications via email and push, but it does not address sending the initial booking confirmation, which is part of the requirement.
  - UC5 claims R24: The scenario contains no information about system availability, failover mechanisms, or related non‑functional performance metrics.
  - UC10 claims R25: The scenario does not mention any role‑based access control checks or enforcement; it only assumes an administrator is performing the actions.
  - UC11 claims R16: The use case covers creating and publishing route definitions with stations and mileage, but it does not include any step for modifying existing route definitions, which is required by R16.
  - UC11 claims R25: The scenario does not address role‑based access control or any restriction on who can modify train or schedule data; it only describes route definition actions.
  - UC12 claims R25: The scenario does not describe any enforcement of role‑based access control; it only assumes an administrator role without showing access checks.
  - UC13 claims R19: No email or push notifications for booking confirmation, cancellation, or refund are mentioned in the scenario; therefore the requirement is not addressed.
  - UC13 claims R20: The scenario does not involve publishing travel news or service alerts to travelers, so this requirement is not covered.

### ours
- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):
  - UC2 claims FR11: The use case only covers adding consignments and showing status information; it does not provide edit/remove capabilities nor a real‑time tracking view as required.
  - UC2 claims FR12: Validation against the stored profile is described, but the scenario does not specify rejection of bookings with missing or inconsistent passenger data, which is part of the requirement.
  - UC2 claims FR21: Multiple passengers can be added and a primary passenger designated, but the scenario does not state that the primary contact is highlighted in the booking summary.
  - UC3 claims FR6: The use case describes providing a confirmation receipt but does not specify the required timing of within 5 seconds, so the timing constraint is not satisfied.
  - UC5 claims FR8: The use case describes the steps for cancelling a booking and processing a refund, but it does not specify that the refund is processed within the 48‑hour window required by FR8. The time constraint is a mandatory part of the requirement, and its absence means the requirement is not fully satisfied.
  - UC9 claims FR16: The use case covers defining, modifying, publishing, and activating a route, but it does not include any step for deactivating a route. Since the requirement explicitly calls for the ability to activate **or** deactivate a route, the missing deactivation capability means the claim is not fully supported.

## 산출 다이어그램 (PlantUML)
### baseline
```
@startuml
left to right direction
actor "Traveler" as Traveler
actor "Administrator" as Administrator
rectangle System {
  usecase "Search Train Routes" as UC1
  usecase "Select Seat & View Seat Map" as UC2
  usecase "Complete Booking & Payment" as UC3
  usecase "Manage Personal Dashboard" as UC4
  usecase "Cancel Booking & Refund" as UC5
  usecase "Order In‑Trip Meals" as UC6
  usecase "Manage Consignments" as UC7
  usecase "Register New Account" as UC8
  usecase "Login" as UC9
  usecase "Admin – Manage Trains" as UC10
  usecase "Admin – Define Routes" as UC11
  usecase "Admin – Schedule Departures" as UC12
  usecase "Admin – Reporting & Export" as UC13
  usecase "Process Payment" as D1
  usecase "Process Refund" as D2
  usecase "Arrange Meal Delivery" as D3
  usecase "Track Consignment" as D4
  usecase "Send Confirmation Notification" as D5
  usecase "Admin – Manage Operations" as D6
}
actor "PaymentGateway" as PaymentGateway
actor "EmailService" as EmailService
actor "NotificationService" as NotificationService
actor "AuthProvider" as AuthProvider
actor "LogisticsProvider" as LogisticsProvider
Traveler --- UC1
Traveler --- UC2
Traveler --- UC3
Traveler --- UC4
Traveler --- UC5
Traveler --- UC6
Traveler --- UC7
Traveler --- UC8
Traveler --- UC9
Administrator --- UC10
Administrator --- UC11
Administrator --- UC12
Administrator --- UC13
UC3 --- PaymentGateway
UC8 --- EmailService
UC3 --- EmailService
UC5 --- EmailService
UC6 --- EmailService
UC3 --- NotificationService
UC5 --- NotificationService
UC6 --- NotificationService
UC7 --- NotificationService
UC9 --- AuthProvider
UC8 --- AuthProvider
UC6 --- LogisticsProvider
UC7 --- LogisticsProvider
UC3 ..> D1 : <<include>>
UC5 ..> D2 : <<include>>
UC6 ..> D3 : <<include>>
UC7 ..> D4 : <<include>>
UC3 ..> D5 : <<include>>
UC5 ..> D5 : <<include>>
UC6 ..> D5 : <<include>>
UC8 ..> D5 : <<include>>
UC2 ..> UC1 : <<extend>>
UC5 ..> UC4 : <<extend>>
UC6 ..> UC4 : <<extend>>
UC7 ..> UC4 : <<extend>>
D6 <|-- UC10
D6 <|-- UC11
D6 <|-- UC12
@enduml
```

### ours
```
@startuml
left to right direction
actor "Traveler" as Traveler
actor "Administrator" as Administrator
rectangle System {
  usecase "Search train routes" as UC1
  usecase "Create booking" as UC2
  usecase "Pay for booking" as UC3
  usecase "View dashboard" as UC4
  usecase "Cancel booking" as UC5
  usecase "Register account" as UC6
  usecase "Log in" as UC7
  usecase "Maintain train data" as UC8
  usecase "Maintain route data" as UC9
  usecase "Schedule train departures" as UC10
  usecase "Export traveler orders" as UC11
  usecase "Receive travel alerts" as UC12
}
actor "Payment Gateway" as Payment_Gateway
actor "Email Service Provider" as Email_Service_Provider
actor "Federated Identity Provider" as Federated_Identity_Provider
actor "Consignment Tracking Service" as Consignment_Tracking_Service
Traveler --- UC1
Traveler --- UC2
Traveler --- UC3
Traveler --- UC4
Traveler --- UC5
Traveler --- UC6
Traveler --- UC7
Traveler --- UC12
Administrator --- UC8
Administrator --- UC9
Administrator --- UC10
Administrator --- UC11
UC3 --- Payment_Gateway
UC3 --- Email_Service_Provider
UC5 --- Email_Service_Provider
UC6 --- Email_Service_Provider
UC12 --- Email_Service_Provider
UC7 --- Federated_Identity_Provider
UC2 --- Consignment_Tracking_Service
@enduml
```

