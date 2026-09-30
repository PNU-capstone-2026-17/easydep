# Testing/Arazzo 파이프라인 전수 감사

작성일: 2026-09-30. 범위는 frozen TestingInput부터 Arazzo 계획, 실행, 결과/진행, 수리까지다. 코드 경로를 읽어 정리했으며 실모델 관찰은 별도 소규모 실험 기록을 인용한다. 현재 구조와 관찰된 결함, 아직 확인되지 않은 가설을 구분한다.

## 전체 흐름과 결정 소유자

| 구간 | 동작과 결정 소유자 | 근거 |
|---|---|---|
| Frozen 입력 / 범위 | Testing 노드는 frozen `requirements`, `use_cases`, `openapi`를 읽는다. 플래너는 명시된 trace link와 요구사항 링크로 UC별 target operation을 고르고 나머지를 setup 후보로 둔다. | `dynamic_functional.py:3201-3280`; `arazzo_planner.py:872-959` |
| 실행 가능한 계약 투영 | 결정론적 플래너가 OpenAPI에서 required input slot, 성공 응답 output, JSON pointer, 타입 호환 연결 후보를 만든다. 같은 배열 안의 match field → selected field 쌍도 `collectionSelectionCandidates`로 생성한다. | `arazzo_planner.py:253-352, 378-406, 551-725` |
| 의미상 producer 선택 | OSS가 대상 필수 path input마다 제한된 operation/output 후보 중 producer, deferred collection lookup, literal 또는 unsupported를 결정한다. 이 단계는 후보 선택만 맡는다. | `dynamic_functional.py:1262-1329, 1336-1545` |
| Workflow graph | OSS가 고정된 workflow intent와 operation/input/output catalog를 바탕으로 occurrence와 순서, 입력 바인딩, distinct-resource 관계, collection selection, 성공 조건을 한 번에 정한다. 잘못된 응답은 같은 근거에 오류 설명을 붙여 최대 한 번 다시 생성한다. | `dynamic_functional.py:1600-1808, 2640-2683` |
| Graph 검증 / 내부 투영 | 결정론적 projector가 LLM의 occurrence/slot 참조를 선택된 step 계약에 매핑하고, 입력 완전성·순서·호환성·producer 선택 준수·collection pair·distinct pair·상태 코드를 검증한다. 통과하면 canonical 내부 decision을 만든다. | `dynamic_functional.py:1811-2100` |
| Literal 입력 | graph가 남긴 고정 입력만 OSS에 요청한다. 값은 타입 제약된 불투명 키 `v1…`로 받고 정해진 slot에 코드가 연결한다. | `dynamic_functional.py:2239-2378` |
| Arazzo compile / 문서 검증 | compiler는 decision을 정식 Arazzo step과 input/output expression으로 만든다. 이후 document validator가 frozen trace, workflow 범위, OpenAPI pointer 및 허용된 step-output 참조를 다시 검증한다. | `dynamic_functional.py:508-705, 2399-2578, 2680-2683`; `arazzo_planner.py:977-1045` |
| 실행과 결과 | 전체 workflow plan이 생긴 다음 순차 실행한다. 각 결과에는 Arazzo workflow, frozen operation 계약, 입력 provenance, HTTP step 결과가 포함되고, 집계는 gate/contract/semantic 결과와 요구사항 trace를 계산한다. | `dynamic_functional.py:3039-3160, 3201-3630, 3720-3890`; executor `app/testing/utils/arazzo_executor.py` |
| Progress / checkpoint / repair | 계획 진행은 workflow별 PENDING/RUNNING/FAIL 이벤트로, 실행 진행은 dynamic workflow lane으로 투영한다. TEST_DEFECT일 때 실행 로그로 workflow를 수리하고 문서 재검증 후, 읽기 전용이면 즉시 재실행하고 상태 변경 workflow면 새 runtime 재실행 대기 상태로 둔다. | `dynamic_functional.py:2581-2635, 2697-2839, 2845-2930, 3720-3818`; 결과 ledger/report는 같은 모듈 `3039-3160, 3860-3890` |

## 정보 경계에서 관찰된 손실

1. **대상 필수값과 입력 slot이 별도로 전달된다.** UC의 `required_values`/identity evidence는 `_identity_input_requests`에서 입력 계약과 함께 자연어/구조 데이터로 모델에 제공되지만, `build_execution_candidates`는 OpenAPI schema만으로 입력 slot을 만든다. 즉, `value_ref`가 어떤 path slot을 가리키는지는 결정론적 연결로 보존되지 않는다. 코드는 추론에 필요한 근거를 보내지만, 명시적 slot-to-value relation으로 고정하지 않는다. 근거: `arazzo_planner.py:574-610`; `dynamic_functional.py:1262-1329`.

2. **Collection 후보는 producer 선택에서 요약된다.** 플래너는 정확한 `selectionId`, match/selected 출력, 두 JSON 표현식, 타입/format 및 item pointer parts를 보유한다 (`arazzo_planner.py:644-670`). 현재 producer payload는 각 output에 `collectionLookupAvailable`과 array root ID를 보내고, root catalog에 item field 목록을 보낸다 (`dynamic_functional.py:1376-1407, 1462-1484`). 정정 사항: 현재 작업 트리의 catalog item field에는 `outputExpression`도 포함된다. 그래도 producer 단계에는 정확한 pair ID와 해당 pair의 match/selected 관계가 없다. producer 선택 결과도 source operation/output과 deferred 여부만 기록한다 (`:1510-1545`). graph 단계가 원래 후보를 다시 훑어 pair를 복원한다 (`:1606-1624`).

3. **Graph 모델은 여러 결정을 한 응답에서 동시에 해야 한다.** occurrence 선택과 순서, 모든 required input 연결, literal 결정, resource identity 구분, collection 선택 및 success criteria가 한 graph에 담긴다. JSON schema는 operation IDs, input slot, output name의 전역 enum을 쓰므로, 존재하는 값들의 잘못된 조합까지는 막지 못한다. 시간 순서나 타입 연결도 semantic projector에서 확인한다. 근거: `_graph_response_format` `dynamic_functional.py:1625-1700`, `_GRAPH_PROMPT` `:1593-1600`, projector `:1811-2100`.

4. **Resource identity metadata가 중간 projection에서 약해진다.** producer 입력 요청에는 target slot의 `resourceRole`, `valueRef`, `evidenceRefs`가 있으면 제공하지만, graph catalog의 `requiredInputs`에는 slot/type/format/cardinality/description/literalAllowed만 있다. producerSelections가 보존한 판단만 graph에 전달되므로, original target evidence 자체를 graph가 재검토하지 못한다. 근거: `dynamic_functional.py:1304-1309, 1724-1735, 1750-1773`.

## 중복·겹침: 확인된 것과 유지 이유

- **모델 스키마와 로컬 JSON Schema 검증:** producer strict schema 뒤 로컬 validate (`:1488-1507`), graph schema 뒤 로컬 validate (`:1797-1808`), literal schema 뒤 로컬 validate (`:2364-2378`). 이중 검증이다. 다만 모델/provider별 structured-output 준수에 의존하지 않는 경계이며 graph는 `strict:false`; 제거 시 provider 전체의 보장을 확인해야 한다.
- **Graph projector와 compiler의 입력 검사:** projector는 모든 선택 input의 coverage/order/type 및 collection/producer 규칙을 확인하고 canonical decision을 만든다 (`:1811-2100`). compiler도 connection/fixed input coverage와 type/order를 검사한다 (`:508-650`). 같은 의미를 다른 표현에서 재확인한다. compiler가 별도 호출 경로의 공용 경계인지 확인하지 않고 한쪽 검사를 지우면 안 된다.
- **Document 검증 반복:** 후보 workflow마다 단독 문서를 검사하고 (`:2680-2683`), 병렬 결과를 모은 전체 문서도 검사하며 (`:2760-2840`), 보존 계획·수리 계획도 재검증한다 (`:2710-2839, 2845-2930`). 이 검사는 각각 단일 후보, 집계, 보존/수리 경계에 해당한다. 반복 호출 비용은 확인되지만 기능적 중복이라고 단정할 수 없다.
- **같은 계약을 재구성하는 경로:** typed slot/connection catalog에서 semantic producer의 option, graph catalog, graph schema enum 및 local projection을 각각 만든다. 다단계라서 각 모델 응답 경계에서 필요한 구조가 다르지만, operation/input-scoped relation 객체 하나를 canonical source로 사용하면 재투영과 정보 손실을 줄일 여지가 있다.

## 계획 이후의 상태·수리 경계

- `candidatePlan`은 최종 Arazzo 문서이고, 실행 결과는 `dynamicFunctional` 보고서다. 별도로 `testing_progress`가 이벤트를 접어 checkpoint에 저장되고, UI는 최종 보고서·checkpoint progress·실시간 이벤트를 다시 병합한다 (`service.py:962-1200`, `progress.py:259` 이후, `frontend/src/lib/testing-results.ts:495` 이후). 이들은 소비 목적이 다르지만 동일 계획의 revision을 가리키는지 확인할 단일 계약이 약하다.
- 동적 노드의 inline `TEST_DEFECT` 계획 수리, Testing service의 repair ledger/선택 재실행, Workspace의 기술적 재시도·구현 수리는 각각 다른 경계다 (`dynamic_functional.py:2845-2908, 3750` 이후, `service.py:962` 이후, `workspace/service.py:2710` 이후). 모두 동일한 종류의 수리라고 단정해 합치면 안 되지만, 계획 변경의 소유자와 progress 재발행 책임은 명확히 할 필요가 있다.
- `arazzo_executor.py:640` 이후 실행기는 workflow마다 전체 Arazzo 문서를 다시 검증한다. 이 비용은 확인된 반복 호출이며 실제 시간 병목인지는 미측정이다. 검증된 plan/digest를 재사용하려면 컴파일 후 plan 수정 가능성, 수리 시 revision, 실행 시 runtime 계약을 먼저 분리해야 한다.

## 실모델 관찰 (구현 효과의 범위)

- 소규모 OSS producer 호출에서 UC3 및 UC5의 선택 판단은 성공했다. UC3은 기존 축약 입력에서는 `unsupported`, finite selection relation을 포함한 enriched 입력에서는 GET의 등록 목록 경로를 선택했다. 별도 작은 호출에서도 relation 자체만 제공했을 때 선택했다. 이는 **producer 선택 단계의 정보 투영을 보완할 근거**다.
- 이 결과는 한두 번의 비결정적 샘플이며 workflow graph 성공이나 HTTP 실행 성공을 입증하지 않는다. 후속 소규모 호출에서 UC3은 다른 필수 입력의 grounded producer를 찾지 못해 graph 전에 멈췄다. UC5는 graph가 입력이 없는 GET 작업에 존재하지 않는 `query:searchCriteria`를 붙여 검증에서 실패했다. 이 handoff 시점에는 graph 쪽 구조 보완이 구현되지 않았다.
- 따라서 “전체 Arazzo가 고쳐졌다” 또는 “사용자 질문이 필요 없다”는 결론은 아직 검증되지 않았다. 관찰상 필요한 다음 질문은 모델의 선택지 문제가 아니라 관계를 단계 간에 온전히 전달하는지 여부다. 실제 정책 결정이 남는 경우만 제품이 사용자에게 질문해야 한다.

## 우선순위 제안

1. **Operation/input-scoped binding contract를 canonical source로 만든다.** frozen OpenAPI slot, 명시적으로 연결된 UC value/evidence, compatible direct outputs, finite collection pair, runtime checks를 target input별 구조화 객체 하나에 둔다. Producer는 이 객체에서 허용된 relation ID를 선택하고, graph는 선택된 ID를 occurrence에 배치한다. 이름/문자열 추론 대신 안정적인 후보 ID와 정확한 매핑을 유지한다. 소규모 OSS producer → graph 검증으로 UC3/5 및 다른 collection 도메인을 확인한 뒤 넓힌다.
2. **Validated workflow plan 경계를 명확히 한다.** graph response를 canonical validated plan으로 한 번 투영한 뒤 compiler, progress, executor, report/repair가 같은 객체를 소비하게 한다. 검증은 model boundary, canonical plan boundary, compiled Arazzo boundary에 각각 어떤 invariant를 맡길지 정하고 중복만 줄인다. 앱 event/progress projection은 별도 소비자라 삭제 대상이 아니다.
3. **기존 보호를 무작정 삭제하지 않는다.** typed OpenAPI schema projection, semantic graph validator, Arazzo/OpenAPI document validator, literal value validation, executor HTTP/runtime validation은 다른 계층의 책임을 가진다. 먼저 canonical plan으로 입력/출력 계약을 연결하고, 중복 invariant의 호출부와 재사용 경계를 확인한 뒤 통합한다.

우선 구현 전에 frozen UC3·UC5에서 작은 A/B를 한다. 이미 선택된 producer는 고정하고, OSS의 첫 호출은 occurrence와 순서만 고르게 한다. 코드가 각 occurrence의 실제 required slot과 허용된 direct output·fixed input·collection pair를 유한한 binding ID로 투영한 뒤, 두 번째 호출은 그 ID 및 필요한 distinct 관계만 고르게 한다. 기존 projector·compiler·Arazzo 검증까지 통과하는지, 총 호출 수·지연·토큰이 현 단일 graph+correction보다 나은지 비교한다. 745-token 축약 `oneOf` 호환성 호출은 공급자 형식 지원만 확인했으므로 이 A/B의 대체 증거가 아니다. 실패하면 곧바로 제품 경로를 바꾸지 않는다.

## 별도 점검이 필요한 미확인 가설

- `build_execution_candidates`는 모든 candidate의 setup operation을 먼저 펼친 뒤 schema issues를 모아 마지막에 예외를 던진다 (`arazzo_planner.py:551-725`). 선택될지 모르는 setup operation 하나의 문제가 전체 workflow candidate를 막을 수 있는지, 실제 실패 입력으로 확인하지 않았다.
- plan repair 이후 초기 planning progress가 UI 상태에 남는지/새 이벤트로 갱신되는지는 event consumer와 Workspace projection을 함께 확인해야 한다. 현재 코드는 planning RUNNING/PENDING 이벤트와 dynamic planned 이벤트를 각각 emit하지만, UI 표시 결함은 이 함수만으로 단정하지 않는다 (`dynamic_functional.py:2640-2695, 3470-3490`).
- 동적 계획, event/checkpoint 저장, Workspace API 결과 projection, Testing ledger 및 UI는 서로 다른 계층이다. 영속된 canonical Arazzo/report를 기준으로 어떤 이벤트가 재생되는지까지 이어서 확인하기 전에는 관찰된 화면을 planner 결함으로 환원하지 않는다.
- `app/testing/README.md`의 `orderedStepIds`/`connectionIds` 단일 선택 및 GLM 모델 설명과 현재 producer → graph → literal 다중 OSS 호출 경로는 일치하지 않는다. 실행 코드를 기준으로 문서를 갱신해야 이후의 구조 판단이 흔들리지 않는다.

## 주요 코드 위치

- Candidate/schemas/finite collection pair: `app/testing/utils/arazzo_planner.py`
- Producer, graph schema/catalog/prompt, projector, literals, compiler, validation, execution/report/progress/repair orchestration: `app/testing/nodes/dynamic_functional.py`
- Arazzo HTTP step execution: `app/testing/utils/arazzo_executor.py`
