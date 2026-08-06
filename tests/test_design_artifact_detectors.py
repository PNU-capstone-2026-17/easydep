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

