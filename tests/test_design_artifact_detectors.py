from app.design.knowledge import detectors


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
            {"source": "OrderBoundary", "target": "OrderControl", "label": "createOrder()", "type": "sync"},
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


def test_message_methods_normalizes_names():
    """가시성 기호와 매개변수가 달라도 메서드 이름이 같으면 일치로 본다."""
    model = {
        "Participants": [
            {"name": "OrderControl", "kind": "control", "source_class": "OrderControl"},
        ],
        "Messages": [
            # BCE methods에는 "+ createOrder(items: List): Order"
            # 메시지 라벨은 간단히 "createOrder"만
            {"source": "User", "target": "OrderControl", "label": "createOrder", "type": "sync"},
        ],
    }
    findings = detectors.sequence_message_methods(model, STATE)
    assert findings == []


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



