# 이름 기반 의미 추론 제거 계획

## 진행 현황 (2026-09-24)

- 0단계: 활성 위험 경로 전체에 허용/거부 focused test 대응을 매핑하는 기준선 감사를 마쳤다. 이 대응표는 관련 테스트 위치와 현재 코드의 허용/거부 사례를 기록한 것이며, 모든 focused test를 이번 갱신 시점에 재실행했다는 뜻은 아니다. 감사 중 `sequence_message_methods`의 participant display-name→class fallback을 발견해 제거했으며, non-actor target은 `participant.source_class`가 있어야 하고 이름 변경된 표시 label도 선언 signature가 정확하면 허용한다. 이번 변경 경계의 `-k message_methods` 4개 focused tests는 통과했다. Phase 0의 inventory/coverage-mapping 범위는 완료로 기록한다. 이는 전체 E2E나 다른 단계의 완료를 뜻하지 않는다.
- 1단계: `identity_obligations`에 의무/주체 및 typed `identity_source_kind`를 두고, `caller_input`, `authenticated_context`(정확한 authenticate obligation ref 포함), `system_result`를 구분한다. 미해결/지원 불가 source는 authority로 승격하지 않고 source 선택 Question 경로로 보낸다. 요구사항 source contract focused 17개와 identity-source gate hardening 8개가 통과했다. Saved-vs-current artifact revision 검증 수정 뒤 Workspace identity-source route focused tests는 3 passed로 보고됐다. 추가로 sole-authenticate auto-link를 제거했고 관련 focused tests 26개가 통과했다. class public-contract reviewer v3 focused 20개는 별도 typed provenance 검증 근거다. 이름/prose/type 자체는 신뢰 근거가 아니며 trusted context는 내부 Control parameter에만 허용된다. 최신 live 앱은 identity_obligations가 없어 identity question을 실행하지 않았고 설계 리뷰에서 막혔으므로 Phase 1은 완료로 보지 않는다.
- 2단계: 필드명 겹침만으로 하류 DTO 타입을 바꾸던 보정을 제거하고 집중 검사 4개가 통과했다. 호출 값 바인딩에서 이름 기반 후보·우선순위 및 이름만으로 하는 DTO 조립을 제거했다. operation/parameter/Class·DataType field 및 call의 안정 참조를 바인딩 전에 부여하고, 클래스 계층 `sourceRef`와 검증 재실행을 이 참조로 전환했다(관련 집중 검사 51개 통과). 시퀀스 투영·인자 검증에서도 동일한 참조를 유지·해석하도록 바꾸고 집중 검사 5개가 통과했다. 새 앱 설계 실행에서 동일 인증 컨텍스트를 두 호출이 재사용할 때 후보 집합 중복 금지가 거짓 `NO_MATCH`를 만들고 같은 질문을 반복하는 문제를 발견했다. 후보별 유한 참조·타입·범위 검사는 유지하면서 이 일괄 중복 금지를 제거했고 집중 검사 1개가 통과했다. 같은 앱 재시도에서 해당 중복 컨텍스트 오류는 재발하지 않았다.
- 3단계: 배포 workload와 resource 사이의 토큰 겹침 추적 간선을 제거하고 정확 참조만 허용하는 집중 검사 3개가 통과했다. RTM 유스케이스 관계에서는 기존 `UC1` 형식 ID를 감사 전에 부여하고 trace slice가 `{id, name}` 카탈로그의 ID만 반환하도록 전환했다. 액터 참조 수리 후에 UC ID를 발급하도록 순서를 바로잡았다. 액터와 UC의 관계 및 parent 관계에도 명시적 `ACTn` 참조를 도입하고, 동명 액터를 별개로 유지하며 이름 없는 참조를 거부한다(요구사항 집중 검사 39개 통과, 기존 skip 13개). include/extend와 diagram의 액터 연결도 정확 ID/참조로 바꾸어 집중 검사 27개가 통과했다. 특정 액터 피드백은 `actor:ACTn`으로 대상을 지정하며, 이름 없는 옛 산출물 fallback을 제거했다(워크스페이스/피드백 집중 검사 52개 통과). 새 16개 요구사항 앱 `f8c5a23c-1728-446e-bef2-a86d55f07b1c`에서 액터 4개·UC 12개·명세 12개·흐름 단계 66개가 생성되었고 누락된 actor/step 참조는 0개였다. 요구사항 명령은 완료했으며, 설계부터 뒤 단계는 계속 확인 중이다.
- 5단계: 자유 텍스트가 동명 산출물 두 개를 가리킬 때 모델의 임의 단일 선택을 수락하지 않고 참조가 적힌 명확화 선택지를 반환한다. 실행 직전 `_exact_candidate_refs`는 표시 이름이 아닌 정식 ref만 인정하며 관계 카탈로그도 액터 이름 fallback을 제거했다. 정확 canonical ref 지정은 카탈로그 검증을 거치며 관련 집중 검사 4개가 통과했다. `test_current_revision_target_follows_stable_identity_across_operation_rename`도 통과해 operation 이름 변경 후 현재 revision target이 안정 identity를 따르는 것을 확인했다. 실제 워크스페이스 피드백 재생은 아직 완료되지 않았다.
- 4단계: 명세의 main/extension step에 명시적 `subject_ref`를 요구하고 설계 actor-entry가 그 참조만 비교하도록 전환했다. 새 집중 검사 5개가 통과했고 기존 fixture 전환에서 요구사항 명세 검사 52개, 피드백 검사 10개가 통과했다. 시퀀스 call/return 메시지와 participant에는 수락된 call·operation·actor/class 참조를 실어 보낸다(새 집중 검사 및 기존 adapter fixture 7개 통과). extension step, `extend` 관계, fragment의 `condition_ref` 및 opt fragment ID를 표시 label 대신 branch 위치·형제 순서와 source ref 기반 구조 참조로 연결했다. validator는 선언된 condition source ref의 정확 일치를 검사하며, 이 변경의 focused test 5개가 통과했다. RTM과 구현 설계 문맥도 동일 생성 함수를 사용하게 하여 집중 검사 2개가 통과했다. 추가 확인으로 fragment condition 검사는 같은 fragment ref 안의 의미상 paraphrase를 허용하며, `test_fragment_condition_allows_paraphrased_text_with_same_fragment_ref`가 이를 고정한다. 이 검사는 구조 일관성이지 조건 문장의 의미 동등성 검증이 아니다. 시퀀스의 나머지 의미 검증은 작업 중이며 4단계는 완료로 보지 않는다.
- 6단계: 미착수.

진행 판단은 코드 수정량이 아니라 각 단계의 완료 기준과 실제 검증 결과에 따른다. 특히 집중 테스트 통과와 새 앱의 종단 성공을 구분해 기록한다.

### 새 앱 실행 기록

- 2026-09-24: 공개 Workspace API로 16개 요구사항 입력을 사용해 앱 `f8c5a23c-1728-446e-bef2-a86d55f07b1c`을 새로 생성했다. `EASYDEP_DEMO_SKIP_VALIDATION`은 켜지 않았다. 요구사항 단계는 원문 클라우드 제약에 이미 명시된 `platform_constraints` 선택지를 답한 뒤 완료했다. 최종 요구사항 산출물에는 액터 4개, UC 12개, 명세 12개, 흐름 단계 66개가 있었고 액터·단계 주체 참조 누락은 없었다.
- 설계 첫 실행은 UC10의 인증값 출처 질문으로 멈췄다. 명세에는 `authenticate` 의무 참조가 있었지만, 호출 바인딩의 후보 집합 중복 금지가 같은 `context#ob_...` 출처의 두 호출 재사용을 거부해 모델이 한 항목을 `NO_MATCH`로 바꾸고 질문을 반복했다. 이 일반 규칙을 제거하고 같은 앱에서 재개한 결과 UC12 collaboration까지 통과해 해당 실패는 재현되지 않았다.
- 클래스 피드백 체크포인트 `fb_class_diagram`의 UC8을 제외한 operation base 합성에서 공유 지역 `DataType`인 `WaitlistEntryDto` 선언이 빠지는 경계를 확인했다. 타입 이름만 전달되던 컨텍스트를 일반화하고 수락된 타입을 합성에 보존하도록 수정했으며, 집중 테스트가 통과했다. 같은 앱 재시도에서는 이 `WaitlistEntryDto` 오류가 재발하지 않았다.
- 이후 같은 앱의 설계 재시도에서 UC1·UC7 호출 매개변수의 `sourceRef`가 참조 이름 변경 뒤에도 일관되게 재기준되지 않는 문제가 드러났다. call/parameter/field 안정 ID에 맞춰 바인딩 출처를 재기준하는 수정과 identity 집중 테스트 11개가 통과했다. 다음 live 재시도에서는 UC1·UC7 출처 호환성 오류가 재발하지 않았다.
- 그 재시도는 빈 `AwaitingOutcome.actions` 계약 오류로 실패했다. `MESSAGE` offer를 항상 포함하도록 보완하고 집중 테스트 2개가 통과했으며, 같은 앱 재시도는 오류 대신 class diagram의 수리 질문 상태를 반환했다.
- Workspace API로 보낸 자연어 UC7 수리 피드백(command `0059d70c-fdb4-4d00-9fda-db0f317d5ee4`)은 UC7 finding을 해소했지만 UC5의 구체적인 Control 반환 요구와 UC9의 `cancellation_confirmation` 문자열 근거 finding이 남아 `AWAITING_INPUT`으로 끝났다. 구조 감사에서 public-contract finding에 typed operation scope가 보존되지 않고 일반 merge가 class operation revision을 버리는 문제가 확인됐다. 선택적 batch-aware repair callback, 정확한 단일 UC batch 필터링, 직접 scope가 지정된 candidate 경로를 구현했고 집중 테스트 5개가 통과했으나 live 검증은 하지 않았다.
- 이어 `drop`/취소 결과를 설명하는 자연어를 같은 앱의 Workspace API로 전달했지만, 설계 피드백이 아니라 요구사항 UC5 수정으로 분류되어 stage 간 확인을 요청했다. 제공된 `dismiss_change` 선택지로 제안을 기각했으며 요구사항 변경은 적용되지 않았다. 앱은 기존 설계 산출물을 유지한 채 `current_stage=requirements`에 있다. 당시 원인은 일반 수리 대기 `MESSAGE`가 typed 질문과 달리 출처 명령의 설계 단계를 대화 라우팅에 전달하지 않아, 전역 수정 계획이 요구사항 UC5를 권위 대상으로 고른 것이었다. 이후 anchored routing focused tests는 통과했으며, 다음 live 결과는 아래 command 기록에 별도로 적었다. 설계·구현·테스팅 종단 성공은 확인되지 않았다.
- 후속 live repair command `0130ef00-2b4c-46fc-b8ce-499a0abf14a3`에서는 anchored routing이 올바르게 `stage=design`을 선택했다. 결과는 LLM이 선언되지 않은 `DropOutcome` 타입을 제안해 `FAILED`였다. repair scope는 UC5로 제한되었고 UC9는 포함되지 않았다. 이 원인은 앞서 관찰한 stage misroute와 구별된다. 구조 감사에서는 결합 타입 수리 경로는 존재하지만 targeted feedback이 이를 우회하는 경계를 확인했다. 이 시점에는 이를 위한 shared preflight가 아직 검증되지 않았다. 따라서 이번 실행은 라우팅 결과 확인이지 설계 수리 성공이나 E2E 성공이 아니다. Phase 0–6의 완료 판단은 바뀌지 않는다.
- 이후 shared type preflight의 focused tests가 통과했다. offered `retry_design` command `156d9a2a-df20-478b-aa2d-aaa0c984c2f2`는 design 단계에서 실패했으며, 이전의 undeclared `DropOutcome` 오류는 재발하지 않았다. 당시 새 실패는 `CombinedUnitProposal` schema-repair 응답의 JSON syntax error로 분류되어 원인 감사를 대기했다. 후속 read-only audit은 이를 preflight에 도달하기 전의 malformed model output으로 분류했다. preflight focused pass와 이 live 실패는 설계 완료 또는 E2E 성공을 의미하지 않는다.
- live retry `75522b50-5f8f-44f4-ac7d-698807130b18`은 마지막 상태 확인 시 design 단계에서 `RUNNING`이다. 이는 진행 중인 실행 기록이며 terminal 결과나 설계/E2E 성공으로 해석하지 않는다.
- 후속 fresh Workspace API E2E 앱 `27802fd0-81e7-4962-9a58-2717fd37168f`는 demo validation skip 없이(`EASYDEP_DEMO_SKIP_VALIDATION=false`) requirements를 완료했다. 결과는 refined record 17개, UC/spec 11개씩, `identity_obligations` 0개였으므로 이 실행은 identity-source Question 경로를 exercise하지 않았다. Design class review는 `Public-contract semantic review evidence is missing or stale; rerun the class-stage check` finding으로 실패했다. 제공된 retry 1회도 같은 finding에서 멈춰 feedback을 요청했다. Implementation/testing은 실행되지 않았다. 따라서 이 E2E는 requirements 완료만 확인하며 design 성공이나 전체 E2E 성공을 뜻하지 않는다.
- 같은 live retry `75522b50-5f8f-44f4-ac7d-698807130b18`는 후속 확인에서 design repair `AWAITING_INPUT`에 도달했다. 생성된 class artifact v25는 syntax-valid다. UC5 finding은 해소되었고, 남은 `class.public-contract-semantic` finding은 UC7 `WaitlistResult` field mapping, UC8 identity obligation realization, UC9 `CancellationConfirmationDto.cancellation_confirmation` evidence 세 건이다. UC9 DTO와 `cancellation_confirmation:String` field는 선언됐지만 해당 evidence는 여전히 unresolved다. 이는 repair 진행 상황이며 설계 완료나 E2E 성공이 아니다.
- 후속 `public_contract_review` v2 typed-evidence 감사에서 reviewer slice가 참조된 `DataTypes`와 중첩 wrapper type을 포함하도록 확장됐다. field mapping evidence는 `operationRef`(stableId), `callRef`(stableId), `parameterRef`, `fieldRef`를 인용하며, verifier는 call→operation 연결, binding, concrete input/return type 안의 field containment를 검사한다. 이 경계의 focused tests 15개가 통과했다. 별도 UC8 audit에서 `identify` obligation `ob_e507...`은 연결된 typed parameter/binding edge가 없고, `authenticate` obligation `ob_cfba...`은 exact context binding이 있음을 확인했다. UC8 `required_values`는 비어 있으며, prose와 `primary_actor_ref`는 source authority가 아니다. 따라서 현재 앱은 design-complete로 선언할 수 없다. 일반화된 identity source 선택 및 필요 시 question으로 넘기는 seam은 설계 중이며 아직 검증되지 않았다.
- 이후 `public_contract_review` v3는 identity obligation의 `identity_source_kind`를 권위 계약으로 사용한다. `caller_input`은 해당 use case의 canonical actor input, `authenticated_context`는 정확한 accepted authenticate context ref, `system_result`는 앞선 accepted call result에 매여야 한다. Reviewer mapping은 operation/call/parameter stable refs와 필요 시 field ref를 인용하고 verifier가 sourceKind/sourceRef와 call/operation/binding 경계를 검사한다. 이 reviewer의 focused tests 20개가 통과했다. UC8에 적용된 Question/Workspace routing은 별도 구간이며 saved-vs-current revision 검증 수정 뒤 route focused tests 3개가 통과했다. Residual audit는 별도 `act_on_behalf` delegation-proof gap(현재 16-requirement app에서는 미발생) 및 `required_value`의 `authenticated_actor_context` prefix-only check를 확인했으며, latter의 typed/evidence 보강은 진행 중이고 아직 검증되지 않았다. 이는 live E2E 성공을 뜻하지 않는다.

## 목적

EasyDep의 설계·추적·검증 로직이 이름이나 문장 유사성을 근거로 의미 관계를 확정하는 일을 단계적으로 제거한다. 표시 이름은 사용자와 모델이 읽고 자연어로 대상을 찾는 데 계속 사용할 수 있지만, 이름만으로 출처·권한·요구사항 관계·값 전달·호출 의미를 확정하지 않는다. 최종 판단은 생성 시 부여되어 전달된 안정 참조와 명시적 계약에 근거해야 한다.

## 범위와 전제

- 저장소 전체를 한 번에 다시 쓰지 않고 아래 단계 순서로 교체한다. 각 단계는 변경한 경계의 집중 테스트로 검증하고, 실제 실행이 필요한 경우에만 해당 단계의 재생 경로를 한 번 사용한다. 전체 종단 실행은 마지막에 한 번만 한다.
- DB 스키마 변경은 계획하지 않는다. 산출물 내부 계약은 단계별로 확장하되, 새 런에서 모든 산출물이 함께 생성되는 것을 전제로 한다. 과거 체크포인트를 새 계약으로 자동 마이그레이션하지 않는다.
- 외부 프로토콜의 정확 비교는 유지한다. 예: OpenAPI 경로·파라미터 이름, Java 문법의 식별자, JSON 키/열거형, `operationId`, `callId`, 생성된 참조의 정확한 비교. 여기서 제거할 것은 이 문자열들을 문법·계약으로 읽는 동작이 아니라, 일반 이름 유사성을 의미의 권위로 사용하는 동작이다.
- 자연어 검색과 후보 회수는 허용한다. 검색 결과는 후보 목록일 뿐이며, 관계를 확정할 때는 유한 후보의 안정 ID 중 선택하거나 명시적 질문으로 넘긴다.
- 한 유스케이스의 이름·문구에 과적합된 예외 규칙을 새로 만들지 않는다. 공통 계약과 여러 유스케이스의 반례를 통해 회귀를 방지한다.

## 현재 기준선과 위험 위치

| 영역 | 현재 경로 | 이름/문장 의존 및 위험 |
|---|---|---|
| 요구사항 모델링 | `app/requirements/modeling/use_cases.py`: `normalize_actors`, `normalize_use_cases`, `_audit_requirement_traceability`, `_actor_reference_defects` | 현재 actor/UC 관계 정규화는 제안의 `actorRef`, `primaryActorRef`, `supportingActorRefs`, `parentActorRef`로 해소하고 RTM 결정은 UC ref 목록으로 적용한다. 표시 이름은 canonical label 출력에 사용한다. `_actor_key`가 남아 있어도 활성 연결 경로의 authority는 ref이며, 정확 JSON/ref 계약은 프로토콜 처리다. |
| 신뢰 컨텍스트 | `app/design/services/class_diagram/trusted_context.py`: `_authenticate_obligations`, `trusted_context_sources`, `trusted_context_evidence`; `class_diagram/validation/operations.py`의 `_trusted_context_parameter_refs`; collaboration source 검증 | 현재 코드는 이름·전제 문구·parameter type으로 신뢰 후보를 만들지 않는다. 현재 use case의 accepted `authenticate` obligation과 정확 ref 선택이 필요하다. operation validation은 obligationRef와 Control stereotype을 확인하며, collaboration은 trusted source ref가 finite eligible catalog에 포함되는지 검사한다. 관련 focused tests는 있으나 live UC2 성공은 확인되지 않았으므로 1단계 완료 근거가 아니다. |
| 호출 값 바인딩 | `app/design/services/class_diagram/collaboration.py`: `_binding_candidates`, `_candidate_detail`, `materialize` 및 관련 검증 경로 | 현재 후보는 operation/parameter/call/field에 일찍 부여한 stable refs와 source catalog를 바탕으로 한다. focused tests는 이름만으로 구조 parameter나 DTO field source를 추론하지 않음을 확인한다. persisted source ref와 validator는 stable identity와 scope/type 검사를 사용한다. |
| DTO 타입 보정 | `app/design/services/class_diagram/operations.py`: `normalize_operation_fragment` 및 operation validator | `_canonicalize_downstream_input_types`는 현재 경로에 없다. focused baseline tests에서 field overlap·rename·tie 모두 authored downstream parameter type을 유지하고 잘못된 explicit type을 validator finding으로 남긴다. |
| 시퀀스 검증 | `app/design/services/sequence_diagram/validation.py`: `sequence_actor_step_involvement`(1065), `sequence_step_operation_distinctness`(1363), `sequence_fragment_condition_consistency`(1613), `sequence_extension_replays_anchor_operation`(1984), `sequence_message_methods`(301) 등 | actor-step 검증은 `subject_ref`와 participant ref를 사용하고 sequence message/participant에는 operation·call·actor/class ref가 전달된다. `sequence_message_methods`는 non-actor target을 `participant.source_class`로 확인하며 display-name fallback은 제거됐다. renamed label + exact signature는 허용하고 `source_class` 누락은 거부한다(`-k message_methods`: 4 passed). fragment consistency는 structural ref/type/branch 검사를 하며 같은 fragment ref의 paraphrased condition을 허용한다. 이 focused validation은 전체 E2E와 별개다. 정확한 선언 메서드 계약 비교는 유지한다. |
| 산출물 추적 | `app/artifact_trace_projection.py`: `_deployment`(431), resource/workload projection | resource의 `workloadRef` 또는 `sourceRefs` 항목이 workload ID/정확 formatted ref와 동일할 때 연결한다. 토큰 분해나 부분 겹침 경로는 없다. 명시 ref의 정확 비교는 보존한다. |
| 워크스페이스 대상 선택 | `app/workspace/service.py`: `_prepare_conversational_message`의 repair anchor 처리, `app/workspace/conversation/feedback_envelope.py`: `free_text_decision`, `app/workspace/conversation/project_tools.py`: 카탈로그 해석 | 자유 텍스트로 수정 대상을 찾는 기능은 필요하다. 검색·표시 이름을 후보로 제시하고 최종 수정은 카탈로그의 고유 canonical ref로 확정해야 한다. 서버의 `action_id`가 같은 앱의 `AWAITING_INPUT` repair command를 정확히 가리키고 repair state가 있으면 해당 명령의 stage를 대화 문맥으로 고정하는 anchored routing이 추가됐다. `tests/test_workspace_conversation_revision.py::test_repair_linked_message_uses_repair_stage_as_conversation_context`와 `test_repair_linked_revision_requires_explicit_cross_stage_target`가 focused 검증 근거다. 이는 live Workspace feedback 재생 증거가 아니다. |
| 구현·테스트 보조 | `app/implementation/agents/canary.py`: `classify_canary_exception`; `app/implementation/agents/task_check.py`, `app/implementation/agents/evaluation.py` | 예외 메시지/프로토콜 출력 파싱과 오류 분류에도 문자열 비교가 있지만, 이는 대체로 외부 프로토콜·오류 어휘 처리다. 의미 추론 제거 범위와 섞지 않는다. 보안·정확도 개선이 별도로 필요하면 독립 작업으로 분류한다. |

위 파일·함수 목록은 시작 기준선이다. 각 단계 시작 시 실제 호출 경로와 테스트를 다시 확인하고, 목록에 없는 의미 판정 경로가 발견되면 같은 기준으로 분류해 해당 단계에 추가한다.

## 목표 계약

1. **참조는 생성 시 부여한다.** 액터, 유스케이스, 단계, 작업(operation), 호출, 매개변수, 필드마다 안정 ref를 최초 생성 시 부여하고 후속 산출물에 전달한다. 이름을 나중에 역조회해 ID를 만들어 내지 않는다.
2. **표시 이름과 정체성을 분리한다.** operation에는 Java 메서드 이름/시그니처와 별개의 불투명한 안정 `operationRef`를 둔다. 매개변수·필드 ref도 독립적으로 유지해 이름 변경이 참조 변경을 뜻하지 않게 한다. 외부 API 이름은 호환성을 위해 정확히 유지한다.
3. **관계는 참조로 표현한다.** `primaryActorRef`, `supportingActorRefs`, 요구사항의 `realizedByUseCaseRefs`/`constrainsUseCaseRefs`, 단계의 `stepRef`, 시퀀스 호출의 `operationRef`·`callRef`, 호출 입력의 `parameterRef` 등을 명시한다. 참조는 생성 단계에서 전달하며 이름 기반 후처리로 만들지 않는다.
4. **값의 출처와 신뢰는 typed contract로 표현한다.** 값 후보는 `sourceRef`, 구조 타입, 소유 범위, `sourceKind` 및 evidence refs를 가진다. identify obligation은 `identity_source_kind` (`caller_input`, `authenticated_context`, `system_result`, 또는 아직 결정되지 않은 `unresolved`)를 요구사항에서 선언한다. 설계 reviewer는 caller input을 canonical actor input, authenticated context를 정확한 authenticate obligation source, system result를 선행 accepted call result에 binding했는지 검사한다. 미해결 source는 통과시키지 않고 source 선택 Question으로 보낸다. `IdentitySourceAnswer`는 use case/obligation ref와 kind를 구조화하고, authenticated context kind일 때만 source authenticate obligation ref를 받는다. Workspace answer routing은 열린 same-app question, 현재 revision, 저장된 option을 검증한 뒤 typed answer로 requirements retry를 만든다. Saved-vs-current revision 수정 후 관련 Workspace focused tests 3개가 통과했으나 live 질문 재생은 미검증이다. 별도 residual audit의 `act_on_behalf` delegation-proof 및 `authenticated_actor_context` evidence 검사는 추가 보강 중이다. 아직 생성되지 않은 하류 ref를 요구사항 계약에 역으로 요구하지 않으며 전제 문장·타입명 토큰·actor 표시 이름은 신뢰 권한의 근거가 아니다.
5. **LLM은 유한 후보 중 의미 선택만 한다.** LLM은 주어진 안정 ID 후보 중 단계 의미에 맞는 것을 고르거나 `unresolved`/질문 필요를 반환할 수 있다. 결정론 검증기는 참조 해석, 타입 호환, 호출 범위, 신뢰 출처의 유효성을 검사한다. LLM의 선택 자체가 유효성 검사를 대체하지 않는다.
6. **모호성은 질문으로 보존한다.** 현재 단계의 자동 수리로도 해소되지 않고 사용자 결정이 필요한 의미는 기존 Question 경로를 통해 선택을 요청한다. 임의의 이름 추론으로 조용히 채우지 않는다.

## 단계별 이행

각 단계의 테스트는 좁고 목적에 맞게 추가한다. 우선 해당 경계의 집중 테스트를 실행하고, 외부 실행으로만 확인되는 변화에 한해 관련 단계 재생을 한 번 수행한다. 기존 체크포인트 형식이 새 계약을 제공하지 못하면 재시도를 반복하지 않고 작은 신규 fixture로 경계를 검증한다. 과거 체크포인트 호환 계층은 만들지 않는다.

### 0단계 — 기준선 및 분류 고정

- **대상:** 위 인벤토리 함수와 호출자, 기존 실패 사례(특히 UC2 `AuthContext` 경로).
- **작업:** 이름 사용을 `프로토콜/문법 비교`, `후보 검색`, `의미 확정`으로 분류한다. 현재 UC2 실패와 최근 신뢰 컨텍스트 변경을 재현 가능한 집중 fixture로 고정한다. 각 fixture는 잘못된 추론뿐 아니라 허용되어야 하는 정상 경로도 담는다.
- **검증:** 분류 목록을 코드 위치/테스트에 연결하고 최근 테스트 결과와 실제 앱 재시도 결과를 구분해서 기록한다.
- **완료 기준:** 위험 경로마다 현재 동작 테스트가 있고, 의미 확정으로 쓰는 이름 비교가 누락 없이 목록화된다. 새 앱 전체 재실행은 아직 하지 않는다.

### 1단계 — UC2 신뢰 컨텍스트와 `AuthContext` 추론 제거

- **대상:** `trusted_context.py`, `scenario.py`의 use-case/evidence 모델, operation 생성 입력 및 컨텍스트 provenance 검증.
- **이전:** 정규식으로 전제 문장을 찾고 타입/액터 이름 토큰 또는 `AuthContext` 타입명으로 신뢰 후보 생성.
- **이후:** 요구사항의 typed `identity_obligations`가 evidence ref와 주체 ref를 제공한다. 설계 단계가 이 의무를 현재 operation/parameter ref의 `sourceKind=trusted_context` 후보에 연결한다. 산출된 값 후보는 해당 obligation의 provenance를 갖는다. 구조화된 obligation이 없으면 신뢰 후보를 만들지 않고 현재 단계의 자동 수리 후 필요한 사용자 결정만 Question으로 보낸다.
- **집중 테스트:** 인증됨/아님/모호한 전제, `AuthContext`·`AdminAuthContext` 등 이름만 인증형인 임의 타입, 중립 매개변수명, actor lineage, 경계 입력과 내부 Control handoff를 검증한다. 이름/영어 문구 또는 타입 이름만 바꾸어도 의미 결과가 변하지 않아야 한다.
- **완료/중단 기준:** UC2에서 신뢰 context 값이 명시 obligation에만 근거해 흐르고, 미지원 전제는 안전하게 unresolved/question이 된다. 실제 체크포인트 재시도에서 인증 의미나 caller-control이 바뀌면 후속 단계로 진행하지 않는다.

### 2단계 — 호출 바인딩과 DTO 필드 출처

- **대상:** `collaboration.py`의 후보 생성·범위·선정·materialization, 관련 호출 계획 계약과 `operations.py`의 `_canonicalize_downstream_input_types`.
- **이전:** 매개변수·필드 이름 일치와 DTO 필드명 겹침으로 후보를 선정하거나 타입을 교체. 호출되지 않는 `id` 접미사 판정 함수도 정리 대상이다.
- **이후:** 안정 `callRef`, `operationRef`, `parameterRef`, `fieldRef` 및 명시적 source catalog를 전달한다. 모델은 유한한 `sourceRef` 후보를 선택한다. 결정론 검증기는 구조 타입 및 허용 범위를 검사한다. 대상 DTO 필드 매핑은 필드 ref/명시 projection 계약으로 표현한다. 이름 겹침만으로 타입을 고치지 않으며, 충돌·미지원 타입은 진단 또는 질문으로 남긴다.
- **집중 테스트:** 매개변수/필드/DTO/메서드 이름을 바꾸어도 동일한 ref 관계는 유지되는지, 동명 필드·동형 타입의 모호성, 이전 결과 및 nested field 흐름, option unwrap, 경계 입력 금지, 타입 불일치 rejection을 검증한다.
- **완료/중단 기준:** 유효한 값 전달은 ref와 타입으로 재현되고, 이름만 맞는 잘못된 값은 거부된다. operation 표시 시그니처와 불투명 operation ref가 혼동되지 않는다. 외부 API 이름은 기존 값과 일치한다.

### 3단계 — 요구사항 관계와 산출물 trace

- **대상:** `use_cases.py`의 actor/유스케이스 정규화 및 추적 감사, `artifact_trace_projection.py`의 `_deployment` 및 trace 생성 호출부.
- **이전:** actor/use-case 이름 정규화로 관계 결합, 추적 검토 결과 이름을 재해석, workload token overlap으로 리소스 연결.
- **이후:** RTM의 기존 UC ID(`UC1` 등)는 감사 전에 부여되어 trace slice가 표시 이름과 함께 유한 `{id, name}` 후보로 받고 `realized_by_use_case_refs`/`constrains_use_case_refs`로 응답한다. 이 한정된 slice는 완료했으며, `primaryActorRef`와 diagram 관계의 생성 시 ref 부여는 후속 작업이다. deployment resource는 명시 workload ref/명시 source refs로만 연결한다. 자연어 검색은 후보 순위 제공까지만 한다.
- **집중 테스트:** 한글/영문 이름 변형, 중복·유사 actor/use-case 이름, 같은 표시 이름의 ref 분리, missing-use-case 생성, NFR 제약 관계, workload 토큰 우연 겹침 및 정확한 workload ref를 검증한다.
- **완료/중단 기준:** 이름 변경이 참조 관계에 영향을 주지 않고, 토큰 겹침만으로 trace edge가 생기지 않는다. 기존 명시 추적이 보존된다.

### 4단계 — 시퀀스의 의미 검증

- **대상:** `sequence_diagram/validation.py`의 단계-actor 연결, 단계-operation 구분, fragment/extension 조건 연결, message-operation 검사 및 생성 prompt/정규화.
- **이전:** 영어 문장 주어와 actor 이름, 조건 문자열 겹침, message label/메서드명 유사성으로 의미 관계 확정.
- **이후:** 각 flow step과 operation의 안정 ref, sequence message의 `operationRef`/`callRef`, branch의 source condition ref 또는 구조화된 predicate ref를 사용한다. 정확한 Java method signature/participant 선언 비교는 코드/계약의 무결성 검사로 남긴다. 자유 서술을 반드시 이해해야 하는 경우 LLM은 제한된 ref 후보를 고르고, 근거가 부족하면 unresolved/question을 반환한다.
- **집중 테스트:** actor 이름과 단계 문구 순서/언어 변경, method label 변경과 실제 operation ref, 조건 문장 paraphrase, main/extension 흐름, 반환/호출 링크, 실제 잘못 연결된 ref를 검증한다.
- **완료/중단 기준:** 의미 판단 테스트가 영어 단어·이름 겹침에 의존하지 않으며 ref 불일치와 구조 위반은 결정론적으로 탐지된다. 자연어 설명만으로 결론을 강제하지 않는다.

### 5단계 — 워크스페이스 자유 텍스트와 잔여 경로 정리

- **대상:** `workspace/conversation/feedback_envelope.py:free_text_decision`, `workspace/service.py` 대화 수정 진입, `workspace/conversation/project_tools.py` 카탈로그 선택, 인벤토리에서 발견된 추가 의미 판정 경로.
- **이전:** 자유 텍스트에서 대상을 추론해 표시 이름만으로 수정 대상을 확정할 수 있는 경로.
- **이후:** 자연어는 검색 질의로 후보 canonical ref를 찾는다. 하나의 후보가 명확하지 않으면 기존 Question/선택 UI를 사용한다. 변경 명령은 안정 ref를 포함한 구조화된 액션으로 실행한다. 범위 밖 외부 프로토콜·오류 파싱은 별도 분류로 남긴다.
- **집중 테스트:** 이름이 비슷한 두 대상, rename 후 기존 ref, 모호한 자유 입력, 정확 ref, 잘못된/존재하지 않는 ref, 읽기 전용 조회를 검증한다.
- **완료/중단 기준:** 자연어 후보 검색 이후 실행 대상이 반드시 하나의 유효 canonical ref로 확정되고, 동률/불확실성은 질문으로 이어진다.

### 6단계 — 전체 회귀 확인 및 정책 확정

- **대상:** 위 단계에서 추가된 seam 계약, 추적 projection, 사용자 질문 흐름.
- **검증:** 각 seam의 집중 테스트와 필요한 단계 재생이 통과한 뒤에만 16개 요구사항의 수강신청 앱을 새 런으로 한 번 실행한다. 결과에서 관계 누락/추가, operation/sequence 연결, 산출물 trace, 질문 필요 케이스를 점검한다.
- **완료 기준:** 전체 흐름에서 의미 근거가 안정 ref·typed contract·provenance 중 하나로 추적 가능하고, 무근거 관계는 질문 또는 unresolved로 드러난다. E2E가 실패하면 실패 seam 단계로 되돌아가며 전체 재설계를 시작하지 않는다.

## 공통 완료 체크리스트

- [ ] 의미 관계에 쓰이는 모든 이름 비교가 분류되어, 프로토콜/문법 비교와 후보 검색 외에는 제거되었거나 명시적으로 승인된 typed contract로 대체됨.
- [ ] 각 actor/use-case/step/operation/call/parameter/field 참조가 생성 시 부여되고 하위 산출물까지 전달됨.
- [ ] 표시 이름 변경만으로 신뢰 출처, 요구사항 관계, 값 흐름, 호출 매핑, trace edge가 바뀌지 않음.
- [ ] 값 출처에 `sourceKind`, `sourceRef`, provenance/evidence refs, 구조 타입이 존재함.
- [ ] LLM 선택은 유한 ref 후보 안에서만 이루어지고, 결정론 validator가 별도로 유효성을 검사함.
- [ ] 의미 근거가 부족하거나 후보가 모호하면 기존 Question 경로로 전달됨.
- [ ] 각 단계의 집중 테스트와 필요한 단계 재생을 완료한 뒤 한 번의 16-요구사항 fresh E2E를 통과함.
- [ ] DB 스키마는 변경하지 않았으며, 이전 체크포인트를 새 계약으로 자동 해석하지 않음.

## 결정이 필요한 설계 지점

1. 안정 ref의 표현 형식: 현재 `UC1:main:2`, `callId#parameterName`, `context#...` 같은 문자열 ref를 유지하면서 내부 typed ref 모델로 감쌀지, 산출물 JSON에 객체형 `{kind, id}`를 직접 도입할지 결정한다. 생성 시 발급·전달 및 이름과의 분리가 핵심 불변조건이다.
2. `identity_obligations` 생성 책임: 요구사항/use-case 구조화 단계에서 생성할지, 별도 의미 판정 단계가 유한 evidence를 보고 제안한 뒤 검증할지 결정한다. 어느 경우든 타입명/전제 문구 정규식은 권한 근거가 될 수 없다.
3. 안정성 범위: 한 실행 내 전달과 같은 앱의 피드백 수정·이름 변경을 거친 ref 지속성은 필수다. 서로 독립적인 새 앱 실행 간에 같은 ref를 재사용할 필요가 있는지는 별도로 결정한다.
4. 모호한 자연어 선택의 UX: 기존 Question 컴포넌트가 안정 ref와 표시 후보 설명을 함께 보여주도록 필요한 필드를 확인한다.

첫 구현 순서는 1단계 UC2 신뢰 컨텍스트 제거부터 시작하고, 각 seam이 검증된 다음에만 뒤 단계를 착수한다.
