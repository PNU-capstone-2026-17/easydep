# Requirements package ownership

`app.requirements`는 입력 요구사항을 분류하고, 자원 계약을 검증하며, 요구사항이
하류 산출물에 어떻게 연결되는지를 색인한다. 각 하위 영역의 경계는 다음과 같다.

| 영역 | 소유하는 것 | 소유하지 않는 것 |
|---|---|---|
| `contracts/` | HTTP input·AgentState·item typed shape | endpoint, graph, LLM 실행 |
| `runtime/` | structured LLM adapter, telemetry, ContextVar 전파 | prompt, stage repair·순서 |
| `stage_registry.py` | PIPELINE·group·batch·cascade 순서 | 단계 구현과 graph state |
| `resources/` | ResourceSpec 스키마, 질문·근거·gap 계약 | agent 실행 상태와 클라우드 사실 |
| `modeling/` | 정제·actor/use-case·명세·관계 proposal/normalize/validate/repair와 PlantUML projection | graph 순서, supervisor cascade, HTTP·저장 |
| `orchestration/` | PIPELINE 기반 graph·batch·feedback·supervisor·runner·현재 checkpoint·HTTP 조율 | 단계 prompt·검증·repair 규칙, 과거 checkpoint migration |
| `knowledge/` | 규칙, concern, evidence 분류와 결정론적 검출 | 파이프라인 단계와 설계/구현 결과 |
| `traceability.py` | 요구사항 ID에서 UC·제약·배포 필요·actor·capability·step으로의 링크 색인 | 구현 엔진의 파일 provenance matrix |

## Traceability contract

- **입력:** 특정 agent 타입에 결합하지 않은 상태 사전(`classified`, `use_cases`,
  `use_case_specs` 등)과 선택적 verdict 목록.
- **출력:** `Traceability` 색인, coverage/orphan/unknown 참조 계산, 그리고 요구사항
  추적 스냅샷. UC 하나에 필요한 제약을 뽑는 `constraints_for_use_case`도 제공한다.
- **부수효과:** 메모리에서만 색인을 계산하며 파일·네트워크·LLM 호출이 없다.
- **금지 의존성:** 설계·구현 서비스와 agent 실행기를 import하지 않는다. 구현 엔진의
  `source_artifact → generated_file` provenance와 합치지 않는다.
- **실패 조건:** 상태 구조가 없거나 링크가 존재하지 않는 요구사항/Use Case를
  가리키면 해당 참조를 unknown으로 노출해 호출자가 판정하게 한다. 조용히 링크를
  삭제하거나 coverage를 성공으로 보정하지 않는다.
