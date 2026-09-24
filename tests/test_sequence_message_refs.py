from copy import deepcopy

import pytest

from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.sequence_diagram.projection import project_sequence_model
from tests.test_sequence_diagram_projection import _accepted_model, single_use_case


def _project(monkeypatch, model):
    return project_sequence_model(
        build_scenario_index(single_use_case()), model, "@startuml\n@enduml",
    )


def test_sequence_refs_survive_display_renames(monkeypatch):
    original = _accepted_model(monkeypatch)
    before = _project(monkeypatch, original)
    payload = deepcopy(original.model_dump(by_alias=True))
    target_call = payload["Collaborations"][0]["calls"][0]
    old_operation_id = target_call["receiverOperationId"]
    class_item = next(
        item for item in payload["Classes"]
        if any(op["operationId"] == old_operation_id for op in item["operations"])
    )
    old_name = class_item["className"]
    old_operation = next(
        op for op in class_item["operations"] if op["operationId"] == old_operation_id
    )
    stable_class = class_item["stableId"]
    stable_operation = old_operation["stableId"]
    stable_calls = {
        call["callId"]: call["stableId"]
        for item in payload["Collaborations"] for call in item["calls"]
    }

    class_item["className"] = "RenamedControl"
    old_operation["operationId"] = "RenamedControl::renamedMethod(request:RequestData)"
    old_operation["name"] = "renamedMethod"
    for collaboration in payload["Collaborations"]:
        for call in collaboration["calls"]:
            if call["receiverOperationId"] == old_operation_id:
                call["receiverOperationId"] = old_operation["operationId"]

    after = _project(monkeypatch, type(original).model_validate(payload))
    before_calls = [message for message in before.Diagrams[0].Messages if message.call_ref]
    after_calls = [message for message in after.Diagrams[0].Messages if message.call_ref]
    assert [message.call_ref for message in after_calls] == [message.call_ref for message in before_calls]
    assert all(message.operation_ref == stable_operation for message in after_calls
               if message.label.startswith("renamedMethod("))
    assert any(item.participant_ref == stable_class and item.name == "RenamedControl"
               for item in after.Diagrams[0].Participants)
    assert stable_calls
    assert old_name != "RenamedControl"


def test_projection_rejects_missing_or_unknown_accepted_identity(monkeypatch):
    model = _accepted_model(monkeypatch)
    payload = deepcopy(model.model_dump(by_alias=True))
    payload["Collaborations"][0]["calls"][0]["stableId"] = None
    with pytest.raises(ValueError, match="no stableId"):
        _project(monkeypatch, type(model).model_validate(payload))

    payload = deepcopy(model.model_dump(by_alias=True))
    payload["Collaborations"][0]["calls"][0]["receiverOperationId"] = "missing::operation"
    with pytest.raises(ValueError, match="unknown receiver operation"):
        _project(monkeypatch, type(model).model_validate(payload))
