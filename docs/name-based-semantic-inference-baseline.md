# 이름 기반 의미 추론 제거 — 0단계 기준선

이 문서는 `name-based-semantic-inference-removal-plan.md`의 0단계에서 확인한 현재 경로를 분류한다. 여기서 “의미 권위”는 이름·문구의 일치만으로 관계, 출처, 신뢰 또는 행위 의미를 확정하는 것을 뜻한다.

## 분류 기준

| 분류 | 허용 범위 |
|---|---|
| 프로토콜/문법 | OpenAPI/JSON 키, Java 식별자·시그니처, `operationId`·`callId`, 명시적으로 생성된 ref처럼 계약에 정해진 정확한 문자열 처리 |
| 후보 검색 | 이름·자연어 유사도로 가능한 대상을 회수하거나 사용자에게 선택지를 제시. 단독으로 최종 관계를 확정하지 않음 |
| 의미 권위 | 이름·토큰 겹침·문장 유사성만으로 주체, 신뢰 출처, 타입 호환, 요구사항 관계, 호출 의미를 확정. 안정 ref/구조화 계약으로 교체 대상 |

## 활성 경로 인벤토리

| 경로와 구체 함수 | 분류 및 현재 근거 |
|---|---|
| `app/requirements/modeling/use_cases.py`: `normalize_actors`, `normalize_use_cases`, `_audit_requirement_traceability`, `_actor_reference_defects` | 현재 actor/UC 관계 정규화는 생성 제안의 `actorRef`, `primaryActorRef`, `supportingActorRefs`, `parentActorRef`로 해소하고, RTM 결정도 UC ref 목록으로 적용한다. 표시 이름은 출력용 canonical label로 복사된다. `_actor_key`가 남아 있어도 현재 연결 경로의 authority는 ref다. 모델 설명에서 이름을 후보로 읽는 과정은 후보 검색이며, exact JSON field/ref 계약은 **프로토콜/문법**이다. |
| `app/design/services/class_diagram/trusted_context.py`: `_authenticate_obligations`, `trusted_context_sources`, `trusted_context_evidence` | 현재 구현은 이름·actor 설명·precondition 문구·parameter type으로 trusted context를 만들지 않는다. 현재 use case의 accepted `authenticate` obligation과 정확한 `obligation_ref` 선택만 `context#...` source로 인정하고 obligation/requirement refs를 evidence로 보존한다. 기존 `parameter_name`, `parameter_type`, `actors` 인자는 presentation compatibility용이며 권한 판단에 쓰이지 않는다. |
| `app/design/services/class_diagram/public_contract_review.py`: v3 identity/required-value evidence verifier | Identity mapping은 obligation `identity_source_kind`를 authority로 삼아 canonical actor input, exact authenticate context, 또는 prior accepted call result에 binding되는지 검증한다. Residual audit: 별도 `act_on_behalf` delegation proof gap은 현재 16-requirement app에서 발생하지 않았으며 미해결 일반 경로다. `required_value`의 `authenticated_actor_context` source check는 현재 prefix-only라 stronger typed/evidence validation으로 보완 중이며, 아직 통과 근거로 기록하지 않는다. |
| `app/design/services/class_diagram/collaboration.py`: `_binding_candidates`(494), `_candidate_detail`(702), `materialize`(913) | 후보 source는 사전 부여한 call/parameter/field stable refs와 유한 source catalog에서 구성된다. 이름 일치로 parameter source를 자동 선정하거나 DTO를 조립하는 과거 경로는 제거되었다. 모델의 선택은 source ref 중에서 이루어지고 검증기는 ref·범위·타입을 확인한다. 이름은 후보 설명에만 남을 수 있다. |
| `app/design/services/class_diagram/operations.py`: `normalize_operation_fragment` 및 operation validator | `_canonicalize_downstream_input_types` 필드 겹침 타입 보정은 현재 경로에 없다. 명시된 하류 parameter type을 보존하며, 필드명 변경/겹침/동점에도 타입이 유지된다는 `tests/test_name_semantic_phase2_baseline.py` focused tests가 있다. 잘못된 명시 타입은 validator finding으로 보고한다. |
| `app/design/services/sequence_diagram/validation.py`: `sequence_message_methods`(301), `sequence_actor_step_involvement`(1065), `sequence_step_operation_distinctness`(1363), `sequence_fragment_condition_consistency`(1613), `sequence_extension_replays_anchor_operation`(1984) | actor-step 연결은 flow step의 `subject_ref`와 participant의 안정 ref를 비교하고, message/operation/extension은 전달된 call·operation·step·extension refs로 검증한다. `sequence_message_methods`의 non-actor target은 `participant.source_class`로 선언 클래스를 찾는다. display label→class fallback은 제거되었다. renamed label도 exact declared signature가 있으면 허용하고 `source_class`가 없으면 거부한다(`-k message_methods`: 4 passed). Fragment의 `condition_ref`는 정확한 source ref와 구조 연결을 검사한다. `test_fragment_condition_allows_paraphrased_text_with_same_fragment_ref`는 같은 fragment/condition ref 아래 paraphrase를 허용함을 확인하므로, 이는 ref 일관성 증거이지 자연어 조건의 의미 동등성 증거가 아니다. |
| `app/artifact_trace_projection.py`: `_deployment`(431), resource/workload projection | resource의 `workloadRef`와 `sourceRefs` 값이 workload ID 또는 정확한 formatted workload ref와 같을 때만 연결한다. 토큰 분해·부분 겹침으로 만든 간선은 없다. 명시 ref 비교는 **프로토콜/문법**이다. |
| `app/workspace/service.py`: `_prepare_conversational_message`, `_route_identity_source_answer`; `app/workspace/conversation/feedback_envelope.py`: `free_text_decision`; `app/workspace/conversation/project_tools.py`: catalog 해석 | 자연어 대상 표현을 catalog 항목에 연결하는 일은 **후보 검색**으로 허용한다. 정규화된 `authoritative_target_refs`는 질문에서 허용한 ref 집합 안에 있어야 한다. 정확한 same-app repair `action_id`는 열린 repair 명령의 server-owned stage를 전달한다. Requirements identity-source 답변은 동일 앱·현재 artifact revision·저장된 질문과 옵션에 결속되고, typed `IdentitySourceAnswer`로 source kind 및 authenticated-context obligation ref를 전달한다. 현재 Workspace focused route 확인은 2 passed/1 failed이며, stale artifact version에서 clarification 대신 `identity_source_retry`가 반환되는 실패를 수정 중이다. 이름 유사도 단독으로 실행 대상을 확정하지 않으며 live E2E는 별도 미검증이다. |

이 목록은 활성 위험 경로의 기준선이며 저장소의 모든 문자열 사용을 의미 추론으로 간주하지 않는다. 계획의 구현/테스트 보조 목록(`canary.py`, `task_check.py`, `evaluation.py`)은 프로토콜 출력·오류 분류 중심이므로 이 의미 추론 제거 단계의 활성 의미 권위로 확인되지 않았다.

## 신뢰 컨텍스트 characterization

`tests/test_name_semantic_baseline.py`는 DB/LLM 없이 `build_scenario_index`와 `trusted_context_sources`만 호출한다. 아래는 코드 경로의 focused regression evidence이며 live E2E 검증이 아니다.

| 사례 | 현재 관찰 동작 | 해석 |
|---|---|---|
| 인증 전제 문장과 `AuthContext` typed parameter, obligation ref 없음 | 빈 source 반환 | 타입명·전제 문장만으로 권한 부여하지 않음 |
| 주체 문구를 바꾼 전제와 명시 `authenticate` obligation/ref, parameter type도 변경 | `context#ob:2` source와 obligation/requirement evidence refs 반환 | type/name/prose와 분리된 명시 obligation 연결 |

관련 focused tests는 `tests/test_api_spec_typed_boundaries.py::test_accepted_trusted_context_stays_internal_to_the_control`, `::test_plain_precondition_ref_is_not_projected_as_trusted_context`, `tests/test_class_design_value_source_contract.py::test_trusted_context_obligation_ref_is_control_only`다. 조건 paraphrase의 구조 허용은 `tests/test_design_artifact_detectors.py::test_fragment_condition_allows_paraphrased_text_with_same_fragment_ref`, repair-stage routing은 `tests/test_workspace_conversation_revision.py::test_repair_linked_message_uses_repair_stage_as_conversation_context`와 `::test_repair_linked_revision_requires_explicit_cross_stage_target`가 다룬다. Requirements identity-source contract, identity-source gate, public-contract review v3의 focused suites는 각각 17, 8, 20 passed로 보고됐다. Workspace identity-source route는 saved-vs-current revision 검증을 수정한 뒤 focused tests 3 passed로 확인됐다. 이 focused 결과들은 live E2E 성공을 입증하지 않는다. 이 문서 갱신에서 실행한 focused pytest 시도는 환경 import/실행 문제로 통과 여부를 확인하지 못했으며, 기존 앱의 UC2 실패와 최신 design repair 상태는 별도 live evidence로 유지한다.
