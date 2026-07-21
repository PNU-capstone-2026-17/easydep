# Baseline vs 우리 파이프라인 — 정량 비교

- 입력 요구사항 20개
- **baseline**: `intake → 명세 원샷(1콜) → 관계 원샷(1콜)` — **FR/NFR 분류 없이 요구사항 전체를 그대로** 주고, 커버리지 강제·명세 검증/반성·관계 참조가드 **없음**
- **ours**: 4단계 분해(액터→UC→명세→관계) + 각 단계 정적/의미 검증·반성·커버리지 강제·참조 가드
- 채점: 양쪽 산출물에 **동일한 결정론 검증기**(check_coverage·_validate_spec·관계 무결성)를 적용

## 입력 요구사항
- The format for service information description is defined
- The interaction protocol between Web Server-SR, SR-mediator is defined.
- Resource provisioning, delegation and reservation policies are in place
- There is an established norm that any resource failure will be reported
- Malicious requests are detected and rejected.
- Web servers have already replicated necessary content.
- Both anticipated and unanticipated user requests (traffic) are considered.
- The format of service requirements is defined.
- Mediator-SR, mediator-PR, mediator-PA interaction protocols are defined.
- Mediator works in conjunction with the PA to establish negotiation.
- Interaction protocols between PAs are identified.
- Malicious requests are identified and acted upon.
- There are existing policies for long-term peering arrangement.
- There is procedure to perform short-term negotiation.
- Negotiation is established between selected CDN peers.
- Primary CDN has already acquired sufficient external resources.
- Functional policies are identified and deployed.
- Effective content delivery is ensured through SLA satisfaction.
- Policies identifying the consequences of SLA violation are defined.
- Policies are in place to perform renegotiation for problem resolution.

## 지표 대비

| 지표 | baseline | ours | 우위 |
|---|---|---|---|
| 유스케이스 수 | 12 | 10 | — |
| 명세 수 | 12 | 10 | — |
| 액터 수 | 7 | 10 | — |
| FR 총계 | 20 | 10 | — |
| FR 커버리지(결정론: 주장 기준) | 1 | 1 | 동률 |
| FR 커버리지(의미론: 검증된 것만) | 0.85 | 0.80 | baseline |
| 거짓 커버리지 주장(LLM 판정) | 3 | 6 | baseline |
| 고아 FR(어떤 UC에도 미포함) | 0 | 0 | 동률 |
| 유령 요구 참조(환각) | 0 | 0 | 동률 |
| 명세 정적 검증 위반 | 20 | 0 | ours |
| precondition 없는 명세 | 0 | 0 | 동률 |
| success_guarantee 없는 명세 | 0 | 0 | 동률 |
| 다이어그램 유령 참조 | 0 | 0 | 동률 |
| 고아 액터 | 0 | 3 | baseline |
| 총 확장(예외·대안 흐름) | 12 | 28 | ours |
| 평균 주시나리오 스텝 | 3.92 | 4 | — |
| 관계 수 | 29 | 12 | — |

> **해석 유의**: baseline은 **분류 없이 요구사항 전체를 그대로** 주므로(번호 R1..는 추적용, 커버리지=전체 요구 대비 참조 비율), 우리 파이프라인의 clarify 구체화+FR/NFR 분류를 거친 요구 집합과 FR 총계·개수 지표가 달라진다(교란요인). 따라서 개수 지표는 참고용이고, **정당성의 핵심 근거는 요구 수와 무관한 품질 지표** — 특히 `명세 정적 검증 위반`(양쪽에 동일 검증기 적용)·`유령 참조`·`고아 액터` — 다. baseline이 남긴 위반을 우리 시스템의 검증·반성 루프가 0으로 수렴시키는지가 대비의 요점이다.

## 결함 상세
### baseline
- **명세 정적 위반**:
  - `UC1`: 2a: outcome=fail인데 resume_at_step이 설정됨
  - `UC2`: 3a: outcome=fail인데 resume_at_step이 설정됨 · step 2: UI 용어 ['fields'] — black-box 위반 · 3a.1: UI 용어 ['field'] — black-box 위반
  - `UC3`: 3a.2: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC5`: step 3: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리) · 1a.2: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC6`: 2a: outcome=fail인데 resume_at_step이 설정됨 · step 3: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC7`: 3a: outcome=fail인데 resume_at_step이 설정됨 · 3a.2: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC8`: 2a: outcome=fail인데 resume_at_step이 설정됨 · step 3: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC9`: 4a: outcome=fail인데 resume_at_step이 설정됨 · 4a.2: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC13`: 3a: outcome=fail인데 resume_at_step이 설정됨
  - `UC11`: 3a: outcome=fail인데 resume_at_step이 설정됨 · step 3: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리) · 3a.2: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
  - `UC12`: step 3: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)
- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):
  - UC4 claims R16: The scenario describes acquiring external resources; it does not state that the primary CDN already has sufficient resources, so the claim is unsupported.
  - UC7 claims R6: The use case describes the process of replicating content from a WebServer to edge nodes via a Mediator. It does not demonstrate that the WebServer has already performed replication of the necessary content; rather, it initiates replication. Therefore, the requirement that "Web servers have already replicated necessary content" is not satisfied by this scenario.
  - UC11 claims R19: The scenario only references SLA thresholds in the PolicyRepository and initiates a renegotiation on violation; it does not demonstrate that policies defining the *consequences* of an SLA violation are created or stored.

### ours
- **거짓 커버리지 주장**(UC가 실제 실현 안 하면서 커버한다고 주장):
  - UC2 claims FR1: The use case describes validation of a submitted description against an existing schema, but it does not include any behavior where the system defines (creates or maintains) the machine‑readable schema itself. Therefore the requirement's full intent is not satisfied.
  - UC4 claims FR3: The use case describes validation of the specification against an existing formal format (enforcement), but it does not show that the system defines that formal format. Since the requirement also mandates definition of the format, the scenario does not fully satisfy the requirement.
  - UC8 claims FR7: The use case only describes configuring detection criteria and mitigation actions, storing them, and acknowledging the configuration. It does not include any steps where the system actually identifies malicious requests, applies the mitigation actions, or records those actions in the security‑audit log. Therefore the requirement’s runtime behavior is not implemented by this scenario.
  - UC10 claims FR9: While the scenario establishes negotiation sessions with selected CDN peers and records terms in a persistent ledger (steps 2‑4), it never references the Peer‑Negotiation Protocol (PNP). Absence of explicit adherence to PNP means the requirement is not satisfied.
  - UC10 claims FR2: The use case mentions validation against “applicable provisioning policies” but does not specify that resource‑provisioning, delegation, and reservation policies are stored in a centrally managed repository nor that they are applied automatically. The requirement’s intent is therefore not demonstrated.
  - UC10 claims FR10: The scenario contains no mention of SLA‑violation handling, penalty calculations, notifications, or remediation steps, nor any automatic enforcement of such policies. Hence the requirement is not covered.
- **고아 액터**: Authentication Service, Notification Service, Peer Registry

## 산출 다이어그램 (PlantUML)
### baseline
```
@startuml
left to right direction
actor "WebServer" as WebServer
actor "User" as User
actor "CDNPeer" as CDNPeer
rectangle System {
  usecase "Define Service Information Description Format" as UC1
  usecase "Define Service Requirements Format" as UC2
  usecase "Define Interaction Protocols" as UC3
  usecase "Provision Resources" as UC4
  usecase "Report Resource Failure" as UC5
  usecase "Detect and Reject Malicious Requests" as UC6
  usecase "Replicate Content" as UC7
  usecase "Handle User Requests" as UC8
  usecase "Establish Negotiation with Peer Agents" as UC9
  usecase "Establish Long‑Term Peering Arrangement" as UC10
  usecase "Ensure SLA Satisfaction" as UC11
  usecase "Perform Renegotiation on SLA Violation" as UC12
  usecase "Define Service Specification" as D1
  usecase "Establish Negotiation with Peer Agents" as UC9
}
actor "Mediator" as Mediator
actor "ServiceRepository (SR)" as ServiceRepository_SR
actor "PolicyRepository (PR)" as PolicyRepository_PR
actor "PolicyAgent (PA)" as PolicyAgent_PA
WebServer --- UC4
WebServer --- UC5
WebServer --- UC7
User --- UC8
UC1 --- Mediator
UC2 --- Mediator
UC3 --- Mediator
UC6 --- Mediator
UC9 --- Mediator
UC10 --- Mediator
UC11 --- Mediator
UC12 --- Mediator
CDNPeer --- UC7
CDNPeer --- UC9
UC1 --- ServiceRepository_SR
UC2 --- PolicyRepository_PR
UC6 --- PolicyAgent_PA
UC9 ..> UC1 : <<include>>
UC9 ..> UC2 : <<include>>
UC9 ..> UC3 : <<include>>
UC8 ..> UC4 : <<include>>
UC8 ..> UC7 : <<include>>
UC10 ..> UC9 : <<extend>>
UC6 ..> UC8 : <<extend>>
UC5 ..> UC4 : <<extend>>
UC12 ..> UC11 : <<extend>>
Mediator <|-- PolicyAgent_PA
Mediator <|-- ServiceRepository_SR
Mediator <|-- PolicyRepository_PR
@enduml
```

### ours
```
@startuml
left to right direction
actor "Service Provider" as Service_Provider
actor "Authorized Entity" as Authorized_Entity
actor "Peer Aggregator" as Peer_Aggregator
actor "CDN Peer" as CDN_Peer
actor "Policy Administrator" as Policy_Administrator
actor "Security Administrator" as Security_Administrator
actor "SLA Manager" as SLA_Manager
rectangle System {
  usecase "Define service‑information schema" as UC1
  usecase "Publish service‑information" as UC2
  usecase "Define service‑requirement format" as UC3
  usecase "Submit service‑requirement" as UC4
  usecase "Define resource‑provisioning policies" as UC5
  usecase "Define interaction protocols" as UC6
  usecase "Define peer‑aggregator interaction protocols" as UC7
  usecase "Configure malicious‑request mitigation" as UC8
  usecase "Define SLA‑violation policies" as UC9
  usecase "Perform short‑term resource negotiation" as UC10
}
actor "Peer Registry" as Peer_Registry
actor "Authentication Service" as Authentication_Service
actor "Notification Service" as Notification_Service
Policy_Administrator --- UC1
Service_Provider --- UC2
Policy_Administrator --- UC3
Authorized_Entity --- UC4
Policy_Administrator --- UC5
Policy_Administrator --- UC6
Policy_Administrator --- UC7
Security_Administrator --- UC8
SLA_Manager --- UC9
Authorized_Entity --- UC10
Peer_Aggregator --- UC10
CDN_Peer --- UC10
@enduml
```

