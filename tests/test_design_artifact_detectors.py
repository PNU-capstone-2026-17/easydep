import dataclasses

from app.design.graphs.subgraphs import API_SPEC_SPEC
from app.design.knowledge import detectors
from app.design.nodes.artifact import check_node
from app.design.services.api_spec.openapi import build_openapi_from_model
from app.design.services.common.validation import validate_api_spec
from app.design.validation import design_readiness_report


STATE = {
    "class_diagram_puml": "class OrderBoundary <<Boundary>>\nclass OrderControl <<Control>>\nclass Order <<Entity>>\n",
    "usecase_spec": {"use_cases": [{"id": "UC1", "name": "Create order"}]},
    "extracted_bce_classes": {
        "Classes": [
            {
                "className": "OrderBoundary",
                "stereotype": "Boundary",
                "methods": ["displayOrderForm()", "showConfirmation()"],
            },
            {
                "className": "OrderControl",
                "stereotype": "Control",
                "methods": ["+ createOrder(items: List): Order", "validateOrder()"],
            },
            {
                "className": "Order",
                "stereotype": "Entity",
                "methods": ["- save()", "getTotal(): int"],
            },
        ],
    },
}


def test_control_value_operation_cannot_declare_void_return() -> None:
    model = {
        "Classes": [{
            "className": "CatalogControl",
            "stereotype": "Control",
            "methods": ["browseCatalog(): void", "dropExpiredCache(): void"],
        }],
    }

    findings = detectors.control_outcome_return_contract(model, STATE)

    assert len(findings) == 1
    assert "browseCatalog" in findings[0].message


def test_control_action_dispatcher_is_rejected_before_api_generation() -> None:
    model = {
        "Classes": [{
            "className": "TermControl",
            "stereotype": "Control",
            "methods": [
                "processTerm(termId : String, action : String, attributes : TermAttributes): void",
                "processImportedTerms(file : CsvFile): void",
            ],
        }],
    }

    findings = detectors.control_action_dispatch_contract(model, STATE)

    assert len(findings) == 1
    assert "processTerm" in findings[0].message
    assert "processImportedTerms" not in findings[0].message


def test_void_command_can_document_error_statuses_without_result_contract() -> None:
    state = {
        "extracted_bce_classes": {
            "Classes": [{
                "className": "EnrollmentControl",
                "stereotype": "Control",
                "methods": ["dropEnrollment(enrollmentId : String): void"],
            }],
        },
    }
    model = {
        "Endpoints": [{
            "path": "/enrollments/{enrollmentId}",
            "method": "delete",
            "responses": [{"status": 204}, {"status": 404}],
            "control_binding": {
                "control": "EnrollmentControl",
                "method": "dropEnrollment",
                "outcomes": [
                    {"status": 204, "outcome": "dropped"},
                    {"status": 404, "outcome": "not_found"},
                ],
            },
        }],
    }

    assert detectors.api_control_outcomes(model, state) == []


def test_sequence_detector_rejects_dangling_and_invalid_bce_messages():
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderControl", "type": "sync", "use_case_ids": ["UC404"]},
            {"source": "Missing", "target": "OrderControl", "type": "sync"},
        ],
    }
    found = {item.rule_id for item in detectors.sequence_diagram_findings(model, STATE)}
    assert {"sequence.message-participants-exist", "sequence.message-bce-flow", "sequence.references-exist"} <= found


def test_sequence_bce_flow_rejects_distinct_boundary_to_boundary_call():
    model = {
        "Participants": [
            {"name": "EntryScreen", "alias": "entry", "kind": "boundary"},
            {"name": "SelectionScreen", "alias": "selection", "kind": "boundary"},
        ],
        "Messages": [
            {"source": "entry", "target": "selection", "type": "sync", "label": "display()"},
        ],
    }

    findings = detectors.sequence_bce_flow(model, STATE)

    assert len(findings) == 1
    assert "boundary → boundary" in findings[0].message


def test_sequence_bce_flow_allows_boundary_self_call():
    model = {
        "Participants": [
            {"name": "EntryScreen", "alias": "entry", "kind": "boundary"},
        ],
        "Messages": [
            {"source": "entry", "target": "entry", "type": "self", "label": "refresh()"},
        ],
    }

    assert detectors.sequence_bce_flow(model, STATE) == []


def test_actor_cannot_invoke_boundary_display_operation():
    model = {
        "Participants": [
            {"name": "User", "alias": "user", "kind": "actor"},
            {"name": "OrderScreen", "alias": "screen", "kind": "boundary"},
        ],
        "Messages": [{
            "source": "user", "target": "screen", "type": "sync", "label": "display()",
        }],
    }

    findings = detectors.sequence_boundary_operation_direction(model, STATE)

    assert len(findings) == 1
    assert "출력 오퍼레이션" in findings[0].message


def test_control_can_call_declared_external_boundary_gateway():
    state = {
        "extracted_bce_classes": {
            "Relationships": [
                {
                    "source": "RegistrationControl",
                    "target": "ExternalEnrollmentGatewayBoundary",
                    "type": "Dependency",
                }
            ]
        }
    }
    model = {
        "Participants": [
            {
                "name": "RegistrationControl",
                "alias": "registration",
                "kind": "control",
                "source_class": "RegistrationControl",
            },
            {
                "name": "ExternalEnrollmentGatewayBoundary",
                "alias": "gateway",
                "kind": "boundary",
                "source_class": "ExternalEnrollmentGatewayBoundary",
            },
        ],
        "Messages": [{
            "source": "registration",
            "target": "gateway",
            "type": "sync",
            "label": "registerStudent(studentId:String,courseId:String)",
        }],
    }

    assert detectors.sequence_boundary_operation_direction(model, state) == []


def test_control_cannot_call_undeclared_boundary_input_operation():
    model = {
        "Participants": [
            {"name": "OrderControl", "alias": "control", "kind": "control"},
            {"name": "OrderScreen", "alias": "screen", "kind": "boundary"},
        ],
        "Messages": [{
            "source": "control", "target": "screen", "type": "sync",
            "label": "submitOrder(orderId:String)",
        }],
    }

    findings = detectors.sequence_boundary_operation_direction(model, STATE)

    assert len(findings) == 1
    assert "입력 오퍼레이션" in findings[0].message


def test_api_detector_rejects_invalid_references_and_path_parameters():
    model = {
        "Endpoints": [
            {
                "path": "/orders/{orderId}", "method": "get", "operation_id": "",
                "path_params": [{"name": "wrong"}], "request_schema": "Missing",
                "responses": [{"schema_name": "Missing"}],
                "source_classes": ["Ghost"], "use_case_ids": ["UC404"],
            }
        ],
        "Schemas": [],
    }
    found = {item.rule_id for item in detectors.api_spec_findings(model, STATE)}
    assert {"api.path-parameters-match", "api.schema-references-exist", "api.operation-ids-unique", "api.references-exist"} <= found


def test_api_detector_rejects_schema_only_model():
    model = {"Endpoints": [], "Schemas": [{"name": "Order", "fields": []}]}

    found = {item.rule_id for item in detectors.api_spec_findings(model, STATE)}

    assert "api.operations-present" in found


def test_api_artifact_validation_rejects_schema_only_openapi_document():
    result = validate_api_spec(
        {
            "openapi": "3.1.0",
            "info": {"title": "Orders", "version": "1.0.0"},
            "paths": {},
            "components": {"schemas": {"Order": {"type": "object"}}},
        }
    )

    assert result["syntax_valid"] is False
    assert result["syntax_errors"] == [
        "API specification paths must contain at least one HTTP operation."
    ]


def test_schema_only_api_model_is_repaired_before_rendering(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "design_max_repair_iters", 1)
    state, repaired = _cart_contract_state(
        {
            "control": "ShoppingCartController",
            "method": "getCart",
            "arguments": [{"name": "cartId", "source": "$path.cartId"}],
            "outcomes": [
                {"status": 200, "outcome": "found"},
                {"status": 404, "outcome": "not_found"},
            ],
        }
    )
    feedback_seen: list[str] = []

    def revise(_current, feedback, _state, _targets):
        feedback_seen.append(feedback)
        return repaired

    spec = dataclasses.replace(API_SPEC_SPEC, revise=revise)
    result = check_node(spec)(
        {**state, "api_spec_model": {"Endpoints": [], "Schemas": [{"name": "CartResponse"}]}}
    )

    assert "api.operations-present" in feedback_seen[0]
    assert result["api_spec_model"] == repaired
    assert result["api_spec_check"]["findings"] == []


def _cart_contract_state(binding: dict | None) -> tuple[dict, dict]:
    state = {
        "class_diagram_puml": (
            "class CartPage <<Boundary>>\n"
            "class ShoppingCartController <<Control>>\n"
            "class CartLookupResult <<Entity>>\n"
        ),
        "usecase_spec": {"use_cases": [{"id": "UC_CART", "name": "View cart"}]},
        "extracted_bce_classes": {
            "Classes": [
                {"className": "CartPage", "stereotype": "Boundary", "methods": []},
                {
                    "className": "ShoppingCartController",
                    "stereotype": "Control",
                    "methods": ["getCart(cartId: String): CartLookupResult"],
                },
                {"className": "CartLookupResult", "stereotype": "Entity", "methods": []},
            ]
        },
        "sequence_diagram_model": {
            "Participants": [
                {"name": "CartPage", "kind": "boundary", "source_class": "CartPage"},
                {
                    "name": "ShoppingCartController",
                    "kind": "control",
                    "source_class": "ShoppingCartController",
                },
            ],
            "Messages": [
                {
                    "source": "CartPage",
                    "target": "ShoppingCartController",
                    "label": "getCart(cartId: String)",
                    "type": "sync",
                    "use_case_ids": ["UC_CART"],
                }
            ],
        },
    }
    endpoint = {
        "path": "/carts/{cartId}",
        "method": "get",
        "operation_id": "getCart",
        "path_params": [{"name": "cartId", "type": "string", "required": True}],
        "responses": [
            {"status": 200, "schema_name": "CartResponse"},
            {"status": 404, "description": "Cart not found"},
        ],
        "source_classes": ["CartPage", "ShoppingCartController"],
        "use_case_ids": ["UC_CART"],
    }
    if binding is not None:
        endpoint["control_binding"] = binding
    model = {
        "Endpoints": [endpoint],
        "Schemas": [{"name": "CartResponse", "fields": []}],
    }
    return state, model


def test_api_control_contract_detects_missing_binding_before_implementation():
    state, model = _cart_contract_state(None)

    found = {item.rule_id for item in detectors.api_spec_findings(model, state)}
    report = design_readiness_report({**state, "api_spec_model": model}, ("api_spec",))

    assert "api.control-binding-exists" in found
    assert report["status"] == "BLOCKED"


def test_api_control_contract_accepts_exact_mapping_and_projects_openapi_extension():
    binding = {
        "control": "ShoppingCartController",
        "method": "getCart",
        "arguments": [{"name": "cartId", "source": "$path.cartId"}],
        "outcomes": [
            {"status": 200, "outcome": "found"},
            {"status": 404, "outcome": "not_found"},
        ],
    }
    state, model = _cart_contract_state(binding)

    assert detectors.api_spec_findings(model, state) == []
    operation = build_openapi_from_model(model)["paths"]["/carts/{cartId}"]["get"]
    assert operation["x-easydep-control"] == {
        "control": "ShoppingCartController",
        "method": "getCart",
        "arguments": {"cartId": "$path.cartId"},
        "outcomes": {"200": "found", "404": "not_found"},
    }


# ---------------------------------------------------------------------------
# sequence.participant-classes-exist
# ---------------------------------------------------------------------------
def test_participant_classes_valid_model_passes():
    """모든 비-액터 참가자가 클래스 다이어그램에 존재하면 위반 0건."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderBoundary", "kind": "boundary", "source_class": "OrderBoundary"},
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
            {"name": "Order", "kind": "entity", "source_class": "Order"},
        ],
        "Messages": [],
    }
    findings = detectors.sequence_participant_classes(model, STATE)
    assert findings == []


def test_participant_classes_rejects_nonexistent_class():
    """클래스 다이어그램에 없는 참가자를 지적한다."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "GhostService", "kind": "control"},
        ],
        "Messages": [],
    }
    findings = detectors.sequence_participant_classes(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.participant-classes-exist"
    assert "GhostService" in findings[0].message


def test_participant_classes_uses_source_class_when_present():
    """source_class가 있으면 그것으로 대조한다 — name과 달라도 source_class가 맞으면 통과."""
    model = {
        "Participants": [
            {"name": "OC", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [],
    }
    findings = detectors.sequence_participant_classes(model, STATE)
    assert findings == []


def test_participant_classes_source_class_wrong():
    """source_class가 존재하지 않는 클래스를 가리키면 지적한다."""
    model = {
        "Participants": [
            {"name": "OC", "kind": "control", "source_class": "NoSuchClass"},
        ],
        "Messages": [],
    }
    findings = detectors.sequence_participant_classes(model, STATE)
    assert len(findings) == 1
    assert "NoSuchClass" in findings[0].message


def test_participant_classes_skips_actors():
    """액터는 클래스가 아니므로 건너뛴다."""
    model = {
        "Participants": [
            {"name": "Admin", "kind": "actor"},
        ],
        "Messages": [],
    }
    findings = detectors.sequence_participant_classes(model, STATE)
    assert findings == []


# ---------------------------------------------------------------------------
# sequence.message-labels-match-methods
# ---------------------------------------------------------------------------
def test_message_methods_valid_model_passes():
    """메시지 라벨이 target 클래스의 실제 메서드이면 위반 0건."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderBoundary", "kind": "boundary", "source_class": "OrderBoundary"},
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
            {"name": "Order", "kind": "entity", "source_class": "Order"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderBoundary", "label": "displayOrderForm()", "type": "sync"},
            {"source": "OrderBoundary", "target": "OrderControl", "label": "createOrder(items: List)", "type": "sync"},
            {"source": "OrderControl", "target": "Order", "label": "save()", "type": "sync"},
        ],
    }
    findings = detectors.sequence_message_methods(model, STATE)
    assert findings == []


def test_message_methods_rejects_nonexistent_method():
    """target 클래스에 없는 메서드를 호출하면 지적한다."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderControl", "label": "deleteOrder()", "type": "sync"},
        ],
    }
    findings = detectors.sequence_message_methods(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.message-labels-match-methods"
    assert "deleteOrder" in findings[0].message


def test_message_methods_skips_return_messages():
    """return 타입 메시지는 호출이 아니므로 건너뛴다."""
    model = {
        "Participants": [
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
            {"name": "Order", "kind": "entity", "source_class": "Order"},
        ],
        "Messages": [
            {"source": "Order", "target": "OrderControl", "label": "ghostMethod()", "type": "return"},
        ],
    }
    findings = detectors.sequence_message_methods(model, STATE)
    assert findings == []


def test_message_methods_skips_empty_labels():
    """라벨이 비어 있으면 건너뛴다."""
    model = {
        "Participants": [
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
            {"name": "Order", "kind": "entity", "source_class": "Order"},
        ],
        "Messages": [
            {"source": "OrderControl", "target": "Order", "label": "", "type": "sync"},
        ],
    }
    findings = detectors.sequence_message_methods(model, STATE)
    assert findings == []


def test_message_methods_normalizes_signature_whitespace():
    """가시성/반환형은 제외하되 매개변수 선언까지 같은 시그니처여야 한다."""
    model = {
        "Participants": [
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderControl", "label": "createOrder(items:List)", "type": "sync"},
        ],
    }
    findings = detectors.sequence_message_methods(model, STATE)
    assert findings == []


def test_message_methods_rejects_hallucinated_parameter_content():
    """괄호만 온전한 임의 문자열은 클래스의 실제 메서드로 인정하지 않는다."""
    model = {
        "Participants": [
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            {
                "source": "User",
                "target": "OrderControl",
                "label": "createOrder(not a declaration!)",
                "type": "sync",
            },
        ],
    }
    findings = detectors.sequence_message_methods(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.message-labels-match-methods"


def test_message_methods_integrated_via_findings():
    """sequence_diagram_findings를 통해 새 검출기가 동작하는지 통합 확인."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "GhostService", "kind": "control"},
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderControl", "label": "nonExistentMethod()", "type": "sync"},
        ],
    }
    found = {item.rule_id for item in detectors.sequence_diagram_findings(model, STATE)}
    assert "sequence.participant-classes-exist" in found
    assert "sequence.message-labels-match-methods" in found


# ---------------------------------------------------------------------------
# sequence.return-label-matches-method-return
# ---------------------------------------------------------------------------
def _order_return_model(
    return_label: str, method: str = "createOrder(items: List)"
) -> dict:
    return {
        "Participants": [
            {"name": "OrderBoundary", "kind": "boundary", "source_class": "OrderBoundary"},
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            {
                "source": "OrderBoundary",
                "target": "OrderControl",
                "label": method,
                "type": "sync",
            },
            {
                "source": "OrderControl",
                "target": "OrderBoundary",
                "label": return_label,
                "type": "return",
            },
        ],
    }


def test_return_label_matches_declared_method_return_type():
    assert detectors.sequence_return_values_match_methods(
        _order_return_model("Order"), STATE
    ) == []


def test_return_label_rejects_a_different_method_return_type():
    findings = detectors.sequence_return_values_match_methods(
        _order_return_model("Customer"), STATE
    )
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.return-label-matches-method-return"


def test_return_label_rejects_method_without_declared_return_type():
    findings = detectors.sequence_return_values_match_methods(
        _order_return_model("boolean", method="validateOrder()"), STATE
    )
    assert len(findings) == 1
    assert "반환 타입" in findings[0].message


def test_return_label_rejects_empty_result():
    findings = detectors.sequence_return_values_match_methods(
        _order_return_model(""), STATE
    )
    assert len(findings) == 1
    assert "비어 있음" in findings[0].message


def test_async_call_cannot_have_a_return_message():
    model = _order_return_model("Order")
    model["Messages"][0]["type"] = "async"

    findings = detectors.sequence_async_returns(model, STATE)

    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.async-call-has-no-return"


def test_async_return_rule_is_integrated_via_findings():
    model = _order_return_model("Order")
    model["Messages"][0]["type"] = "async"

    found = {item.rule_id for item in detectors.sequence_diagram_findings(model, STATE)}

    assert "sequence.async-call-has-no-return" in found


def test_sequence_collection_requires_one_diagram_per_use_case():
    state = {
        **STATE,
        "usecase_spec": {
            "use_cases": [
                {"id": "UC1", "name": "Create order"},
                {"id": "UC2", "name": "Cancel order"},
            ]
        },
    }
    model = {
        "Diagrams": [
            {
                "use_case_id": "UC1",
                "use_case_name": "Create order",
                "Participants": [],
                "Messages": [],
            }
        ]
    }

    findings = detectors.sequence_diagram_findings(model, state)

    assert any(
        finding.rule_id == "sequence.usecase-step-coverage"
        and finding.location == "UC2"
        for finding in findings
    )


# ---------------------------------------------------------------------------
# sequence.initial-message-entry
# ---------------------------------------------------------------------------
def test_initial_entry_valid():
    """첫 메시지가 Actor -> Boundary이면 통과."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderBoundary", "kind": "boundary"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderBoundary", "type": "sync"},
        ],
    }
    assert detectors.sequence_initial_entry(model, STATE) == []


def test_initial_entry_invalid_direct_control():
    """첫 메시지가 Actor -> Control이면 지적한다."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderControl", "kind": "control"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderControl", "type": "sync"},
        ],
    }
    findings = detectors.sequence_initial_entry(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.initial-message-entry"


# ---------------------------------------------------------------------------
# sequence.unmatched-return-message
# ---------------------------------------------------------------------------
def test_unmatched_returns_valid():
    """선행 호출 후 return이 나오면 통과."""
    model = {
        "Participants": [
            {"name": "OrderBoundary", "kind": "boundary"},
            {"name": "OrderControl", "kind": "control"},
        ],
        "Messages": [
            {"source": "OrderBoundary", "target": "OrderControl", "type": "sync"},
            {"source": "OrderControl", "target": "OrderBoundary", "type": "return"},
        ],
    }
    assert detectors.sequence_unmatched_returns(model, STATE) == []


def test_unmatched_returns_rejects_dangling_return():
    """선행 호출 없이 return이 나타나면 지적한다."""
    model = {
        "Participants": [
            {"name": "OrderBoundary", "kind": "boundary"},
            {"name": "OrderControl", "kind": "control"},
        ],
        "Messages": [
            {"source": "OrderControl", "target": "OrderBoundary", "type": "return"},
        ],
    }
    findings = detectors.sequence_unmatched_returns(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.unmatched-return-message"


def test_unmatched_returns_rejects_a_second_return_for_one_call():
    """하나의 호출은 첫 반환에서 소비되므로 추가 반환은 환각으로 지적한다."""
    model = {
        "Participants": [
            {"name": "OrderBoundary", "kind": "boundary"},
            {"name": "OrderControl", "kind": "control"},
        ],
        "Messages": [
            {"source": "OrderBoundary", "target": "OrderControl", "type": "sync"},
            {
                "source": "OrderControl",
                "target": "OrderBoundary",
                "label": "Order",
                "type": "return",
            },
            {
                "source": "OrderControl",
                "target": "OrderBoundary",
                "label": "Customer",
                "type": "return",
            },
        ],
    }

    findings = detectors.sequence_unmatched_returns(model, STATE)

    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.unmatched-return-message"


# ---------------------------------------------------------------------------
# sequence.usecase-step-coverage
# ---------------------------------------------------------------------------
def test_usecase_coverage_valid():
    """모든 상류 유스케이스 ID가 사용되었으면 통과."""
    model = {
        "Participants": [{"name": "User", "kind": "actor"}],
        "Messages": [
            {"source": "User", "target": "OrderBoundary", "use_case_ids": ["UC1"]},
        ],
    }
    assert detectors.sequence_usecase_coverage(model, STATE) == []


def test_usecase_coverage_accepts_explicit_narrative_step_without_method_call():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Student submits the request"},
                    {"step_number": 2, "sentence": "System displays the result"},
                ],
                "extensions": [],
            }],
        },
    }
    model = {
        "use_case_id": "UC1",
        "Messages": [{
            "source": "Student", "target": "RequestBoundary", "type": "sync",
            "step_ids": ["UC1:main:1"],
        }],
        "NarrativeSteps": [{
            "step_id": "UC1:main:2",
            "sentence": "System displays the result",
            "reason": "Outcome of the preceding call",
        }],
    }

    assert detectors.sequence_usecase_coverage(model, state) == []


def test_usecase_coverage_requires_each_traced_operation_family():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "sentence": "User submits a request"},
                    {"step_number": 2, "sentence": "System retrieves the record"},
                ],
                "extensions": [],
            }],
        },
        "extracted_bce_classes": {"Classes": [
            {"className": "RequestBoundary", "operations": [{
                "name": "submit", "stepRefs": ["UC1:main:1"],
            }]},
            {"className": "RequestControl", "operations": [{
                "name": "handle", "stepRefs": ["UC1:main:2"],
            }]},
            {"className": "Record", "operations": [{
                "name": "find", "stepRefs": ["UC1:main:2"],
            }]},
        ]},
    }
    model = {
        "use_case_id": "UC1",
        "Participants": [
            {"name": "User", "alias": "actor", "kind": "actor"},
            {"name": "RequestBoundary", "alias": "boundary", "kind": "boundary", "source_class": "RequestBoundary"},
            {"name": "RequestControl", "alias": "control", "kind": "control", "source_class": "RequestControl"},
            {"name": "Record", "alias": "record", "kind": "entity", "source_class": "Record"},
        ],
        "Messages": [
            {"source": "actor", "target": "boundary", "type": "sync", "label": "submit()", "step_ids": ["UC1:main:1"]},
            {"source": "boundary", "target": "control", "type": "sync", "label": "handle()", "step_ids": ["UC1:main:2"]},
        ],
    }

    findings = detectors.sequence_usecase_coverage(model, state)

    assert [finding.location for finding in findings] == ["Record::find"]
    model["Messages"].append({
        "source": "control", "target": "record", "type": "sync",
        "label": "find()", "step_ids": ["UC1:main:2"],
    })
    assert detectors.sequence_usecase_coverage(model, state) == []


def test_usecase_coverage_rejects_uncovered_usecase():
    """유스케이스 ID가 시퀀스에 매핑되지 않았으면 지적한다."""
    state_multi_uc = {
        **STATE,
        "usecase_spec": {"use_cases": [{"id": "UC1"}, {"id": "UC2"}]},
    }
    model = {
        "Participants": [{"name": "User", "kind": "actor"}],
        "Messages": [
            {"source": "User", "target": "OrderBoundary", "use_case_ids": ["UC1"]},
        ],
    }
    findings = detectors.sequence_usecase_coverage(model, state_multi_uc)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.usecase-step-coverage"
    assert "UC2" in findings[0].message


# ---------------------------------------------------------------------------
# sequence.fragment-condition-consistency
# ---------------------------------------------------------------------------
def test_fragment_condition_valid():
    """group과 condition이 정당하게 짝지어져 있으면 통과."""
    model = {
        "Messages": [
            {"source": "A", "target": "B", "group": "alt", "condition": "재고 없음", "label": "msg()"},
            {"source": "A", "target": "B", "group": "", "condition": "", "label": "msg2()"},
        ],
    }
    assert detectors.sequence_fragment_condition_consistency(model, STATE) == []


def test_fragment_condition_rejects_missing_condition():
    """group은 선언되었으나 condition이 비어 있으면 지적한다."""
    model = {
        "Messages": [
            {"source": "A", "target": "B", "group": "loop", "condition": "", "label": "msg()"},
        ],
    }
    findings = detectors.sequence_fragment_condition_consistency(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.fragment-condition-consistency"


# ---------------------------------------------------------------------------
# sequence.database-access-discipline
# ---------------------------------------------------------------------------
def test_database_access_valid():
    """Control 또는 Entity에서 Database를 접근하면 통과."""
    model = {
        "Participants": [
            {"name": "OrderControl", "kind": "control"},
            {"name": "MyDB", "kind": "database"},
        ],
        "Messages": [
            {"source": "OrderControl", "target": "MyDB", "type": "sync"},
        ],
    }
    assert detectors.sequence_database_access_discipline(model, STATE) == []


def test_database_access_rejects_actor_or_boundary_direct_access():
    """Boundary 또는 Actor가 Database를 직접 접근하면 지적한다."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderBoundary", "kind": "boundary"},
            {"name": "MyDB", "kind": "database"},
        ],
        "Messages": [
            {"source": "OrderBoundary", "target": "MyDB", "type": "sync"},
        ],
    }
    findings = detectors.sequence_database_access_discipline(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.database-access-discipline"


# ---------------------------------------------------------------------------
# sequence.self-call-method-validation
# ---------------------------------------------------------------------------
def test_self_call_valid():
    """자기 자신 호출 시 라벨이 채워져 있으면 통과."""
    model = {
        "Messages": [
            {"source": "OrderControl", "target": "OrderControl", "label": "internalCalc()", "type": "sync"},
        ],
    }
    assert detectors.sequence_self_call_method_validation(model, STATE) == []


def test_self_call_rejects_empty_label():
    """자기 자신 호출 시 라벨이 비어 있으면 지적한다."""
    model = {
        "Messages": [
            {"source": "OrderControl", "target": "OrderControl", "label": "", "type": "sync"},
        ],
    }
    findings = detectors.sequence_self_call_method_validation(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.self-call-method-validation"


# ---------------------------------------------------------------------------
# sequence.orphan-participant-detection
# ---------------------------------------------------------------------------
def test_orphan_participant_valid():
    """모든 참가자가 메시지에 1회 이상 등장하면 통과."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderBoundary", "kind": "boundary"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderBoundary", "label": "open()"},
        ],
    }
    assert detectors.sequence_orphan_participant_detection(model, STATE) == []


def test_orphan_participant_rejects_unreferenced_participant():
    """메시지상에서 한 번도 등장하지 않는 고립 참가자를 지적한다."""
    model = {
        "Participants": [
            {"name": "User", "kind": "actor"},
            {"name": "OrderBoundary", "kind": "boundary"},
            {"name": "GhostControl", "kind": "control"},
        ],
        "Messages": [
            {"source": "User", "target": "OrderBoundary", "label": "open()"},
        ],
    }
    findings = detectors.sequence_orphan_participant_detection(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.orphan-participant-detection"
    assert "GhostControl" in findings[0].message


# ---------------------------------------------------------------------------
# sequence.duplicate-consecutive-messages
# ---------------------------------------------------------------------------
def test_duplicate_consecutive_messages_valid():
    """서로 다른 메시지거나 조각 조건이 다르면 통과."""
    model = {
        "Messages": [
            {"source": "A", "target": "B", "label": "doA()", "type": "sync"},
            {"source": "A", "target": "B", "label": "doB()", "type": "sync"},
        ],
    }
    assert detectors.sequence_duplicate_consecutive_messages(model, STATE) == []


def test_duplicate_consecutive_messages_rejects_duplicates():
    """연달아 완전히 동일한 메시지가 기입되면 지적한다."""
    model = {
        "Messages": [
            {"source": "A", "target": "B", "label": "doA()", "type": "sync"},
            {"source": "A", "target": "B", "label": "doA()", "type": "sync"},
        ],
    }
    findings = detectors.sequence_duplicate_consecutive_messages(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.duplicate-consecutive-messages"
    assert "2회" in findings[0].message


def test_duplicate_consecutive_messages_reports_one_run_with_its_size():
    model = {
        "Messages": [
            {"source": "A", "target": "B", "label": "doA()", "type": "sync"},
            {"source": "A", "target": "B", "label": "doA()", "type": "sync"},
            {"source": "A", "target": "B", "label": "doA()", "type": "sync"},
            {"source": "A", "target": "B", "label": "doB()", "type": "sync"},
        ],
    }

    findings = detectors.sequence_duplicate_consecutive_messages(model, STATE)

    assert len(findings) == 1
    assert "3회" in findings[0].message
    assert "messages 1-3" in findings[0].location


def test_extension_replaying_its_anchor_operation_is_rejected():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{"step_number": 1}, {"step_number": 2}],
                "extensions": [{
                    "label": "1a",
                    "branch_step": 1,
                    "handling_steps": [{"sub_step": "1a1"}],
                }],
            }]
        }
    }
    model = {
        "use_case_id": "UC1",
        "Participants": [
            {"alias": "Boundary", "kind": "boundary"},
            {"alias": "Control", "kind": "control"},
        ],
        "Messages": [
            {
                "source": "Boundary",
                "target": "Control",
                "label": "validate(input:String)",
                "type": "sync",
                "step_ids": ["UC1:main:1"],
                "fragments": [],
            },
            {
                "source": "Boundary",
                "target": "Control",
                "label": "validate(input:String)",
                "type": "sync",
                "step_ids": ["UC1:extension:1a:1a1"],
                "fragments": [{
                    "id": "invalid",
                    "type": "opt",
                    "branch": "main",
                    "condition": "invalid input",
                }],
            },
        ],
    }

    findings = detectors.sequence_extension_replays_anchor_operation(model, state)

    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.extension-replays-anchor-operation"


def test_extension_retry_inside_loop_is_not_treated_as_duplicate_operation():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "extensions": [{"label": "1a", "branch_step": 1}],
            }]
        }
    }
    model = {
        "use_case_id": "UC1",
        "Messages": [
            {"source": "A", "target": "B", "label": "submit()", "step_ids": ["UC1:main:1"]},
            {
                "source": "A",
                "target": "B",
                "label": "submit()",
                "step_ids": ["UC1:extension:1a:1a1"],
                "fragments": [{"id": "retry", "type": "loop", "branch": "main", "condition": "retry"}],
            },
        ],
    }

    assert detectors.sequence_extension_replays_anchor_operation(model, state) == []


def test_extension_repeated_boundary_display_is_not_an_operation_replay():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "extensions": [{"label": "1a", "branch_step": 1}],
            }]
        }
    }
    model = {
        "use_case_id": "UC1",
        "Participants": [
            {"alias": "control", "kind": "control"},
            {"alias": "boundary", "kind": "boundary"},
        ],
        "Messages": [
            {
                "source": "control", "target": "boundary",
                "label": "displayResult(message:String)", "type": "sync",
                "step_ids": ["UC1:main:1"], "fragments": [],
            },
            {
                "source": "control", "target": "boundary",
                "label": "displayResult(message:String)", "type": "sync",
                "step_ids": ["UC1:extension:1a:1a1"],
                "fragments": [{"id": "failed", "type": "opt", "branch": "main"}],
            },
        ],
    }

    assert detectors.sequence_extension_replays_anchor_operation(model, state) == []


# ---------------------------------------------------------------------------
# sequence.message-naming-convention
# ---------------------------------------------------------------------------
def test_message_naming_convention_valid():
    """camelCase나 verbNoun() 형태는 통과."""
    model = {
        "Messages": [
            {"source": "A", "target": "B", "label": "registerOrder()", "type": "sync"},
            {"source": "A", "target": "B", "label": "calculateTotal", "type": "sync"},
        ],
    }
    assert detectors.sequence_message_naming_convention(model, STATE) == []


def test_message_naming_convention_rejects_pascal_case_class_name():
    """메시지 라벨이 PascalCase 클래스명 형태이면 지적한다."""
    model = {
        "Messages": [
            {"source": "A", "target": "B", "label": "OrderControl", "type": "sync"},
        ],
    }
    findings = detectors.sequence_message_naming_convention(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.message-naming-convention"


# ---------------------------------------------------------------------------
# sequence.participant-kind-validity
# ---------------------------------------------------------------------------
def test_participant_kind_validity_valid():
    """표준 kind는 통과."""
    model = {
        "Participants": [
            {"name": "U", "kind": "actor"},
            {"name": "B", "kind": "boundary"},
            {"name": "C", "kind": "control"},
            {"name": "E", "kind": "entity"},
            {"name": "DB", "kind": "database"},
        ],
    }
    assert detectors.sequence_participant_kind_validity(model, STATE) == []


def test_participant_kind_validity_rejects_invalid_kind():
    """비표준 kind이면 지적한다."""
    model = {
        "Participants": [
            {"name": "CustomNode", "kind": "microservice"},
        ],
    }
    findings = detectors.sequence_participant_kind_validity(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.participant-kind-validity"


# ---------------------------------------------------------------------------
# sequence.message-type-validity
# ---------------------------------------------------------------------------
def test_message_type_validity_valid():
    """표준 type(sync, async, return)은 통과."""
    model = {
        "Messages": [
            {"source": "A", "target": "B", "type": "sync"},
            {"source": "A", "target": "B", "type": "async"},
            {"source": "B", "target": "A", "type": "return"},
        ],
    }
    assert detectors.sequence_message_type_validity(model, STATE) == []


def test_message_type_validity_rejects_invalid_type():
    """비표준 type이면 지적한다."""
    model = {
        "Messages": [
            {"source": "A", "target": "B", "type": "rpc_call"},
        ],
    }
    findings = detectors.sequence_message_type_validity(model, STATE)
    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.message-type-validity"


def _sequence_contract_model(messages: list[dict]) -> dict:
    return {
        "Participants": [
            {"name": "User", "alias": "User", "kind": "actor"},
            {
                "name": "OrderBoundary",
                "alias": "Boundary",
                "kind": "boundary",
                "source_class": "OrderBoundary",
            },
            {
                "name": "OrderControl",
                "alias": "Control",
                "kind": "control",
                "source_class": "OrderControl",
            },
        ],
        "Messages": messages,
    }


def test_nonvoid_sync_call_requires_matching_return_message():
    model = _sequence_contract_model([
        {"source": "User", "target": "Boundary", "type": "sync", "label": "displayOrderForm()"},
        {"source": "Boundary", "target": "Control", "type": "sync", "label": "createOrder(items: List)"},
    ])

    findings = detectors.sequence_nonvoid_calls_have_returns(model, STATE)

    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.nonvoid-call-requires-return"


def test_nonvoid_sync_call_accepts_one_matching_return_and_void_needs_none():
    model = _sequence_contract_model([
        {"source": "User", "target": "Boundary", "type": "sync", "label": "displayOrderForm()"},
        {"source": "Boundary", "target": "Control", "type": "sync", "label": "createOrder(items: List)"},
        {"source": "Control", "target": "Boundary", "type": "return", "label": "Order"},
        {"source": "Boundary", "target": "Control", "type": "sync", "label": "validateOrder()"},
    ])

    assert detectors.sequence_nonvoid_calls_have_returns(model, STATE) == []


def test_causal_chain_rejects_participant_that_acts_before_being_called():
    model = _sequence_contract_model([
        {"source": "User", "target": "Boundary", "type": "sync", "label": "displayOrderForm()"},
        {"source": "Control", "target": "Control", "type": "self", "label": "validateOrder()"},
    ])

    findings = detectors.sequence_causal_call_chain(model, STATE)

    assert len(findings) == 1
    assert findings[0].rule_id == "sequence.causal-call-chain"


def test_causal_chain_accepts_reached_participants():
    model = _sequence_contract_model([
        {"source": "User", "target": "Boundary", "type": "sync", "label": "displayOrderForm()"},
        {"source": "Boundary", "target": "Control", "type": "sync", "label": "validateOrder()"},
    ])

    assert detectors.sequence_causal_call_chain(model, STATE) == []


def test_explicit_alt_requires_main_and_else_but_opt_can_be_one_sided():
    alt = {
        "Messages": [{
            "source": "A",
            "target": "B",
            "label": "call()",
            "fragments": [{"id": "choice", "type": "alt", "branch": "main", "condition": "ok"}],
        }]
    }
    opt = {
        "Messages": [{
            "source": "A",
            "target": "B",
            "label": "call()",
            "fragments": [{"id": "choice", "type": "opt", "branch": "main", "condition": "ok"}],
        }]
    }

    assert detectors.sequence_fragment_condition_consistency(opt, STATE) == []
    assert len(detectors.sequence_fragment_condition_consistency(alt, STATE)) == 1


def test_explicit_return_link_rejects_wrong_direction_and_duplicate_reply():
    model = _sequence_contract_model([
        {
            "source": "Boundary", "target": "Control", "type": "sync",
            "label": "createOrder(items: List)", "call_id": "call-1", "reply_to": "",
            "arguments": [{"parameter": "items", "type": "List", "source_kind": "state", "source_ref": "cart"}],
        },
        {
            "source": "Control", "target": "Control", "type": "return",
            "label": "Order", "call_id": "", "reply_to": "call-1", "arguments": [],
        },
        {
            "source": "Control", "target": "Boundary", "type": "return",
            "label": "Order", "call_id": "", "reply_to": "call-1", "arguments": [],
        },
    ])

    findings = detectors.sequence_call_return_links(model, STATE)

    assert {"호출 'call-1'과 반환 방향이 일치하지 않음", "호출 'call-1'에 반환이 둘 이상 연결됨"} <= {
        finding.message for finding in findings
    }


def test_argument_data_flow_rejects_incompatible_preceding_result():
    state = {
        **STATE,
        "extracted_bce_classes": {
            "Classes": [
                {"className": "OrderBoundary", "stereotype": "Boundary", "methods": []},
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "methods": ["capture(): String", "accept(order: Order): void"],
                },
            ]
        },
    }
    model = _sequence_contract_model([
        {
            "source": "Boundary", "target": "Control", "type": "sync",
            "label": "capture()", "call_id": "capture", "reply_to": "", "arguments": [],
        },
        {
            "source": "Control", "target": "Boundary", "type": "return",
            "label": "String", "call_id": "", "reply_to": "capture", "arguments": [],
        },
        {
            "source": "Boundary", "target": "Control", "type": "sync",
            "label": "accept(order: Order)", "call_id": "accept", "reply_to": "",
            "arguments": [{
                "parameter": "order", "type": "Order",
                "source_kind": "call_result", "source_ref": "capture",
            }],
        },
    ])

    findings = detectors.sequence_argument_data_flow(model, state)

    assert any("타입 'String'" in finding.message for finding in findings)


def test_argument_data_flow_rejects_result_returned_to_another_participant():
    state = {
        **STATE,
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "methods": ["capture(): Order", "accept(order: Order): void"],
                },
                {"className": "OrderBoundary", "stereotype": "Boundary", "methods": []},
            ]
        },
    }
    model = _sequence_contract_model([
        {
            "source": "Control", "target": "Control", "type": "self",
            "label": "capture()", "call_id": "capture", "reply_to": "", "arguments": [],
        },
        {
            "source": "Control", "target": "Control", "type": "return",
            "label": "Order", "call_id": "", "reply_to": "capture", "arguments": [],
        },
        {
            "source": "Boundary", "target": "Control", "type": "sync",
            "label": "accept(order: Order)", "call_id": "accept", "reply_to": "",
            "arguments": [{
                "parameter": "order", "type": "Order",
                "source_kind": "call_result", "source_ref": "capture",
            }],
        },
    ])

    findings = detectors.sequence_argument_data_flow(model, state)

    assert any("'Control'에게 반환" in finding.message for finding in findings)
    assert any("'Boundary'가 사용할 수 없음" in finding.message for finding in findings)


def test_actor_led_step_requires_an_actor_originated_call():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{
                    "step_number": 4,
                    "sentence": "Purchaser browses and buys stock from the web site.",
                }],
                "extensions": [],
            }]
        }
    }
    model = {
        "Participants": [
            {"name": "Purchaser", "alias": "actor1", "kind": "actor"},
            {"name": "BuyScreen", "alias": "b1", "kind": "boundary"},
            {"name": "PurchaseControl", "alias": "c1", "kind": "control"},
        ],
        "Messages": [{
            "source": "c1", "target": "b1", "type": "sync",
            "label": "captureResponse()", "step_ids": ["UC1:main:4"],
        }],
    }

    findings = detectors.sequence_actor_step_involvement(model, state)

    assert len(findings) == 1
    assert findings[0].location == "UC1:main:4"


def test_actor_led_step_accepts_actor_to_boundary_entry():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{"step_number": 1, "sentence": "The user submits an order."}],
                "extensions": [],
            }]
        }
    }
    model = {
        "Participants": [
            {"name": "Purchaser", "alias": "actor1", "kind": "actor"},
            {"name": "OrderBoundary", "alias": "b1", "kind": "boundary"},
        ],
        "Messages": [{
            "source": "actor1", "target": "b1", "type": "sync",
            "label": "submitOrder()", "step_ids": ["UC1:main:1"],
        }],
    }

    assert detectors.sequence_actor_step_involvement(model, state) == []


def test_distinct_main_actor_steps_cannot_reuse_one_boundary_operation():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "sentence": "The user requests a purchase."},
                    {"step_number": 2, "sentence": "The user confirms the purchase."},
                ],
                "extensions": [],
            }]
        }
    }
    model = {
        "Participants": [
            {"name": "User", "alias": "user", "kind": "actor"},
            {"name": "PurchaseScreen", "alias": "screen", "kind": "boundary"},
        ],
        "Messages": [
            {
                "source": "user", "target": "screen", "type": "sync",
                "label": "requestPurchase()", "step_ids": ["UC1:main:1"],
            },
            {
                "source": "user", "target": "screen", "type": "sync",
                "label": "requestPurchase()", "step_ids": ["UC1:main:2"],
            },
        ],
    }

    findings = detectors.sequence_actor_step_involvement(model, state)

    assert len(findings) == 1
    assert findings[0].location == "UC1:main:2"
    assert "동일 Boundary 호출" in findings[0].message


def test_repeated_actor_step_can_reuse_the_only_boundary_operation():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Monitor requests a health probe."},
                    {"step_number": 2, "sentence": "Monitor requests the health probe again."},
                ],
                "extensions": [],
            }]
        },
        "extracted_bce_classes": {
            "Classes": [{"className": "HealthApi", "methods": ["healthProbe()"]}]
        },
    }
    model = {
        "Participants": [
            {"name": "Monitor", "alias": "monitor", "kind": "actor"},
            {
                "name": "HealthApi",
                "alias": "health",
                "kind": "boundary",
                "source_class": "HealthApi",
            },
        ],
        "Messages": [
            {
                "source": "monitor", "target": "health", "type": "sync",
                "label": "healthProbe()", "step_ids": ["UC1:main:1"],
            },
            {
                "source": "monitor", "target": "health", "type": "sync",
                "label": "healthProbe()", "step_ids": ["UC1:main:2"],
            },
        ],
    }

    assert detectors.sequence_actor_step_involvement(model, state) == []


def test_flow_order_rejects_reversed_main_step_and_late_extension():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{"step_number": 1}, {"step_number": 2}, {"step_number": 3}],
                "extensions": [{"label": "1a", "branch_step": 1, "handling_steps": [{"sub_step": "1a1"}]}],
            }]
        }
    }
    model = {
        "use_case_id": "UC1",
        "Messages": [
            {"source": "A", "target": "B", "label": "one()", "step_ids": ["UC1:main:1"]},
            {"source": "A", "target": "B", "label": "three()", "step_ids": ["UC1:main:3"]},
            {"source": "A", "target": "B", "label": "two()", "step_ids": ["UC1:main:2"]},
            {"source": "A", "target": "B", "label": "extension()", "step_ids": ["UC1:extension:1a:1a1"]},
        ],
    }

    findings = detectors.sequence_flow_order(model, state)

    assert any("단계 2가 단계 3 뒤" in finding.message for finding in findings)
    assert any("분기 단계 1 직후" in finding.message for finding in findings)


def test_flow_order_allows_outer_return_after_nested_later_step():
    model = {
        "use_case_id": "UC1",
        "Messages": [
            {
                "source": "Actor", "target": "Boundary", "type": "sync",
                "label": "request()", "call_id": "outer",
                "step_ids": ["UC1:main:1"],
            },
            {
                "source": "Boundary", "target": "Control", "type": "sync",
                "label": "handle()", "call_id": "inner",
                "step_ids": ["UC1:main:2"],
            },
            {
                "source": "Control", "target": "Boundary", "type": "return",
                "label": "Result", "reply_to": "inner",
                "step_ids": ["UC1:main:2"],
            },
            {
                "source": "Boundary", "target": "Actor", "type": "return",
                "label": "Result", "reply_to": "outer",
                "step_ids": ["UC1:main:1"],
            },
        ],
    }

    assert detectors.sequence_flow_order(model, STATE) == []


def test_flow_order_reports_extension_when_branch_main_step_is_missing():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [{"step_number": 1}, {"step_number": 2}],
                "extensions": [{
                    "label": "2a",
                    "branch_step": 2,
                    "handling_steps": [{"sub_step": "2a1"}],
                }],
            }]
        }
    }
    model = {
        "use_case_id": "UC1",
        "Messages": [{
            "source": "A",
            "target": "B",
            "label": "extension()",
            "step_ids": ["UC1:extension:2a:2a1"],
        }],
    }

    findings = detectors.sequence_flow_order(model, state)

    assert len(findings) == 1
    assert "주 흐름 단계 2가 없어" in findings[0].message


def test_fragment_reports_one_root_finding_for_one_sided_alt():
    fragment = {"id": "failure", "type": "alt", "branch": "else", "condition": "failed"}
    model = {
        "Messages": [
            {"source": "A", "target": "B", "label": "first()", "fragments": [fragment]},
            {"source": "B", "target": "C", "label": "second()", "fragments": [fragment]},
        ]
    }

    findings = detectors.sequence_fragment_condition_consistency(model, STATE)

    assert len(findings) == 1
    assert findings[0].location == "failure"


def test_fragment_rejects_identical_alt_branch_conditions():
    model = {
        "Messages": [
            {
                "source": "A",
                "target": "B",
                "label": "success()",
                "fragments": [{
                    "id": "result", "type": "alt", "branch": "main", "condition": "failed",
                }],
            },
            {
                "source": "A",
                "target": "B",
                "label": "retry()",
                "fragments": [{
                    "id": "result", "type": "alt", "branch": "else", "condition": " failed ",
                }],
            },
        ]
    }

    findings = detectors.sequence_fragment_condition_consistency(model, STATE)

    assert len(findings) == 1
    assert "상호 배타적이지 않음" in findings[0].message


def test_extension_trigger_without_main_flow_uses_opt_not_alt():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "extensions": [{
                    "label": "3a",
                    "condition": "Web failure",
                    "handling_steps": [{"sub_step": "3a1"}, {"sub_step": "3a2"}],
                }],
            }]
        }
    }
    model = {
        "Messages": [
            {
                "source": "A", "target": "B", "label": "report()",
                "step_ids": ["UC1:extension:3a:3a1"],
                "fragments": [{
                    "id": "ext3a", "type": "alt", "branch": "main",
                    "condition": "Web failure",
                }],
            },
            {
                "source": "B", "target": "A", "label": "retry()",
                "step_ids": ["UC1:extension:3a:3a2"],
                "fragments": [{
                    "id": "ext3a", "type": "alt", "branch": "else",
                    "condition": "Web setup succeeds",
                }],
            },
        ]
    }

    findings = detectors.sequence_fragment_condition_consistency(model, state)

    assert any("alt가 아니라 opt" in finding.message for finding in findings)


def test_unresolved_flow_step_blocks_behavior_generation_and_is_not_coverage_debt():
    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [],
                "extensions": [{
                    "label": "4a", "branch_step": 4,
                    "handling_steps": [{"sub_step": "4a1", "sentence": "What do we do here?"}],
                }],
            }]
        }
    }
    model = {"use_case_id": "UC1", "Messages": []}

    assert detectors.sequence_usecase_coverage(model, state) == []
    findings = detectors.sequence_unresolved_steps(model, state)
    assert [finding.location for finding in findings] == ["UC1:extension:4a:4a1"]



