from __future__ import annotations

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import collaboration
from app.design.services.class_diagram.identity import (
    materialize_pre_collaboration_refs,
    reconcile_stable_ids,
)
from app.design.services.class_diagram.proposals import CallPlanProposal
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.validation.model import operation_catalog
from tests.class_design_fixtures import single_use_case


def _model() -> BCEModel:
    return BCEModel.model_validate({
        "Classes": [{
            "className": "RequestBoundary", "stereotype": "Boundary",
            "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "ignored", "name": "submit",
                "parameters": [{"name": "request", "type": "RequestData"}],
                "returnType": "RequestResult", "stepRefs": ["UC1:main:1"],
            }],
        }, {
            "className": "RequestControl", "stereotype": "Control",
            "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "ignored", "name": "process",
                "parameters": [{"name": "request", "type": "RequestData"}],
                "returnType": "RequestResult", "stepRefs": ["UC1:main:2"],
            }],
        }, {
            "className": "RequestStore", "stereotype": "Entity",
            "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "ignored", "name": "record",
                "parameters": [{"name": "request", "type": "RequestData"}],
                "returnType": "void", "stepRefs": ["UC1:main:2"],
            }],
        }],
        "DataTypes": [{
            "name": "RequestData", "kind": "valueObject", "fields": ["value : String"],
        }, {
            "name": "RequestResult", "kind": "valueObject", "fields": ["accepted : Boolean"],
        }],
        "Relationships": [],
        "Collaborations": [],
    })


def test_call_refs_are_visible_to_selector_and_survive_acceptance(monkeypatch) -> None:
    index = build_scenario_index(single_use_case())
    model = materialize_pre_collaboration_refs(None, _model())
    plan = CallPlanProposal.model_validate({"calls": [
        {
            "receiverOperationId": "RequestBoundary::submit(request:RequestData)",
            "parentCallIndex": None,
        },
        {
            "receiverOperationId": "RequestControl::process(request:RequestData)",
            "parentCallIndex": 1,
        },
        {
            "receiverOperationId": "RequestStore::record(request:RequestData)",
            "parentCallIndex": 2,
        },
        {
            "receiverOperationId": "RequestStore::record(request:RequestData)",
            "parentCallIndex": 2,
        },
    ]})
    selector_calls: list[dict] = []

    def choose_first(_use_case, ambiguous, _types, **kwargs):
        selector_calls.extend(kwargs["calls"])
        return {location: candidates[0] for location, candidates in ambiguous.items()}

    monkeypatch.setattr(collaboration, "select_ambiguous_bindings", choose_first)
    result = collaboration.materialize(index, model, index.use_case("UC1"), plan)

    issued = [call.stable_id for call in result.calls]
    assert all(issued)
    assert len(issued) == len(set(issued))
    assert [call["stableId"] for call in selector_calls] == issued

    accepted, _metadata = reconcile_stable_ids(None, BCEModel.model_validate({
        **model.model_dump(by_alias=True),
        "Collaborations": [result.model_dump(by_alias=True)],
    }))
    assert [call.stable_id for call in accepted.Collaborations[0].calls] == issued


def test_earlier_root_source_candidate_survives_parameter_display_rename() -> None:
    index = build_scenario_index(single_use_case())
    payload = _model().model_dump(by_alias=True)
    payload["Classes"][0]["stableId"] = "class_boundary"
    payload["Classes"][1]["stableId"] = "class_control"
    payload["Classes"][2]["stableId"] = "class_store"
    payload["DataTypes"][0]["stableId"] = "type_request"
    payload["DataTypes"][1]["stableId"] = "type_result"
    for class_item in payload["Classes"]:
        for operation in class_item["operations"]:
            operation["stableId"] = f"op_{operation['name']}"
            for position, parameter in enumerate(operation["parameters"], start=1):
                parameter["stableRef"] = f"param_{operation['name']}_{position}"
    source_operation = payload["Classes"][0]["operations"][0]
    target_operation = payload["Classes"][1]["operations"][0]

    def candidates(source_name: str) -> list[str]:
        source_operation["parameters"][0]["name"] = source_name
        source_operation["operationId"] = (
            f"RequestBoundary::submit({source_name}:RequestData)"
        )
        calls = [{
            "callId": "UC1::call:1",
            "stableId": "stable_call_1",
            "receiverOperationId": source_operation["operationId"],
            "stepRefs": ["UC1:main:1"],
        }, {
            "callId": "UC1::call:2",
            "stableId": "stable_call_2",
            "receiverOperationId": target_operation["operationId"],
            "parentCallId": "UC1::call:1",
            "stepRefs": ["UC1:main:2"],
        }]
        return collaboration._binding_candidates(
            payload,
            index.use_case("UC1"),
            None,
            False,
            calls,
            1,
            {"name": "renamedTarget", "type": "RequestData", "stableRef": "target_ref"},
            operation_catalog(payload),
        )

    original = candidates("request")
    renamed = candidates("submittedRequest")

    assert original == ["stable_call_1#param_submit_1"]
    assert renamed == original
