"""Operation-side identities exist before collaboration materialization."""
from __future__ import annotations

from copy import deepcopy

from app.design.schemas.class_model import BCEModel, Collaboration
from app.design.services.class_diagram import generation, service
from app.design.services.class_diagram.identity import materialize_pre_collaboration_refs
from app.design.services.class_diagram.models import RepairBudget
from app.design.services.class_diagram.scenario import build_scenario_index
from tests.class_design_fixtures import operation_fragment, single_use_case


def _skeleton(parameter_name: str = "request") -> BCEModel:
    return BCEModel.model_validate({
        "Classes": [
            {
                "className": "RequestBoundary", "stereotype": "Boundary",
                "use_case_ids": ["UC1"], "operations": [{
                    "operationId": "ignored", "name": "submit",
                    "parameters": [{"name": parameter_name, "type": "RequestData"}],
                    "returnType": "RequestResult", "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "RequestControl", "stereotype": "Control",
                "use_case_ids": ["UC1"], "operations": [{
                    "operationId": "ignored", "name": "process",
                    "parameters": [], "returnType": "RequestResult",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [
            {"name": "RequestData", "kind": "valueObject", "fields": ["value : String"]},
            {"name": "RequestResult", "kind": "valueObject", "fields": ["value : String"]},
        ],
        "Relationships": [], "Collaborations": [],
    })


def test_collaboration_receives_early_refs_and_acceptance_keeps_them(monkeypatch) -> None:
    index = build_scenario_index(single_use_case())
    previous = materialize_pre_collaboration_refs(None, _skeleton())
    revised = materialize_pre_collaboration_refs(
        previous,
        _skeleton("input"),
        targeted_refs=[f"operation:{previous.Classes[0].operations[0].operation_id}"],
    )
    prior_operation = previous.Classes[0].operations[0]
    operation = revised.Classes[0].operations[0]
    assert operation.stable_id == prior_operation.stable_id
    assert operation.parameters[0].stable_ref == prior_operation.parameters[0].stable_ref
    assert all(item.stable_id and item.field_refs for item in revised.DataTypes)

    observed: dict[str, BCEModel] = {}

    def materialize(_index, skeleton, use_case, _plan, **_kwargs):
        observed["skeleton"] = deepcopy(skeleton)
        return Collaboration.model_validate({
            "collaborationId": use_case.id, "useCaseIds": [use_case.id], "calls": [],
        })

    monkeypatch.setattr(generation.collaboration, "materialize", materialize)
    generation._materialize_use_case(
        index,
        revised,
        index.use_case("UC1"),
        {"fragment": operation_fragment(), "calls": [
            {"operationRef": "RequestBoundary.submit", "parentCallIndex": None},
        ]},
        RepairBudget("UC1"),
    )

    received = observed["skeleton"]
    assert received.Classes[0].operations[0].stable_id == operation.stable_id
    assert received.Classes[0].operations[0].parameters[0].stable_ref == operation.parameters[0].stable_ref
    final = service._accepted_model(previous, BCEModel.model_validate({
        **revised.model_dump(by_alias=True),
        "Collaborations": [],
    }), targeted_refs=[f"operation:{prior_operation.operation_id}"])
    assert final.Classes[0].operations[0].stable_id == operation.stable_id
    assert final.Classes[0].operations[0].parameters[0].stable_ref == operation.parameters[0].stable_ref
