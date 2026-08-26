from __future__ import annotations

from app.requirements.agent import baseline
from app.requirements.agent.compare import score_relationships
from app.requirements.schemas import (
    BaselineModelResult,
    BaselineRelationshipModel,
    BaselineSpec,
    MainScenarioStep,
    UseCase,
)


def _spec(name: str) -> BaselineSpec:
    return BaselineSpec(
        use_case_name=name,
        trigger="The actor starts the use case.",
        main_scenario=[
            MainScenarioStep(
                step_number=1,
                sentence="System completes the requested action.",
                covered_req_ids=[],
            )
        ],
    )


def test_baseline_generate_is_one_free_form_call_and_keeps_unmatched_specs_untraced(monkeypatch):
    result = BaselineModelResult.model_validate(
        {
            "actors": [{"name": "Member", "description": "Uses the system."}],
            "use_cases": [
                UseCase(
                    name="Manage profile",
                    primary_actor="Member",
                    goal="The member updates a profile.",
                    requirement_ids=["R1"],
                    nfr_ids=["R2"],
                )
            ],
            "specs": [_spec("Manage profile"), _spec("Invented use case")],
        }
    )
    observed: dict[str, object] = {}

    def fake(schema, messages):
        observed["schema"] = schema
        observed["messages"] = messages
        return result

    monkeypatch.setattr(baseline, "invoke_structured", fake)

    output = baseline.baseline_generate({"raw_requirements": ["A requirement"]})

    assert observed["schema"] is BaselineModelResult
    assert len(observed["messages"]) == 2
    assert output["actors"][0]["source_refs"] == []
    assert output["use_case_specs"][0]["use_case_id"] == "UC1"
    assert output["use_case_specs"][0]["requirement_ids"] == ["R1"]
    assert output["use_case_specs"][0]["nfr_ids"] == ["R2"]
    assert output["use_case_specs"][1]["use_case_id"] == "UC2"
    assert output["use_case_specs"][1]["requirement_ids"] == []
    assert output["use_case_specs"][1]["nfr_ids"] == []


def test_baseline_diagram_uses_its_own_schema_and_current_stable_renderer(monkeypatch):
    result = BaselineRelationshipModel.model_validate(
        {
            "associations": [{"actor": "Member", "use_case": "Manage profile"}],
            "includes": [
                {
                    "base_use_case": "Manage profile",
                    "included_use_case": "Record audit entry",
                }
            ],
            "extends": [
                {
                    "base_use_case": "Manage profile",
                    "extending_use_case": "Record audit entry",
                    "extension_point": "after update",
                }
            ],
            "generalizations": [],
            "derived_use_cases": [
                {"name": "Record audit entry", "origin": "factored_include"}
            ],
        }
    )
    observed: dict[str, object] = {}

    def fake(schema, messages):
        observed["schema"] = schema
        observed["messages"] = messages
        return result

    monkeypatch.setattr(baseline, "invoke_structured", fake)
    output = baseline.baseline_diagram(
        {
            "actors": [{"name": "Member", "description": "Uses the system.", "parent_actor": None}],
            "use_cases": [
                {
                    "id": "UC1",
                    "name": "Manage profile",
                    "primary_actor": "Member",
                    "supporting_actors": [],
                }
            ],
        }
    )

    relationships = output["relationships"]
    assert observed["schema"] is BaselineRelationshipModel
    assert len(observed["messages"]) == 2
    assert relationships["associations"] == [{"actor": "Member", "use_case_id": "UC1"}]
    assert relationships["includes"][0]["base_use_case_id"] == "UC1"
    assert relationships["includes"][0]["included_use_case_id"] == "baseline-derived-1"
    assert relationships["extends"][0]["extending_use_case_id"] == "baseline-derived-1"
    assert 'usecase "Record audit entry" as baseline_derived_1_3ada8006' in output["diagram"]
    assert "UC1_45c30a63 ..> baseline_derived_1_3ada8006 : <<include>>" in output["diagram"]


def test_relationship_score_uses_stable_use_case_ids_and_actor_names():
    actors = [{"name": "Member"}, {"name": "Auditor"}]
    use_cases = [{"id": "UC1", "name": "Duplicate display name"}]
    relationships = {
        "associations": [{"actor": "Member", "use_case_id": "UC1"}],
        "includes": [
            {"base_use_case_id": "UC1", "included_use_case_id": "baseline-derived-1"}
        ],
        "extends": [
            {"base_use_case_id": "UC1", "extending_use_case_id": "baseline-derived-1"}
        ],
        "generalizations": [],
        "derived_use_cases": [
            {"use_case_id": "baseline-derived-1", "name": "Duplicate display name"}
        ],
    }

    score = score_relationships(relationships, actors, use_cases)

    assert score["dangling_refs"] == []
    assert score["orphan_actors"] == ["Auditor"]
