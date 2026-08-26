"""Focused contracts for bounded Step 4 relationship generation."""
from __future__ import annotations

import pytest

from app.requirements.agent.steps import step4_diagram as s4
from app.requirements.schemas import ExtendSelection, IncludeSelection, RelationshipModel


def _actors() -> list[dict]:
    return [
        {"name": "User", "description": "general", "parent_actor": None},
        {"name": "Member", "description": "specialized", "parent_actor": "User"},
    ]


def _use_case(
    identifier: str,
    name: str,
    *,
    actor: str = "Member",
    requirement_ids: list[str] | None = None,
) -> dict:
    return {
        "id": identifier,
        "name": name,
        "primary_actor": actor,
        "supporting_actors": [],
        "level": "user_goal",
        "goal": name,
        "requirement_ids": requirement_ids or [],
    }


def _spec(identifier: str, steps: list[dict], **overrides) -> dict:
    return {
        "use_case_id": identifier,
        "main_scenario": steps,
        "extensions": [],
        "issues": [],
        "semantic_status": "ok",
        **overrides,
    }


def _shared_state() -> dict:
    return {
        "actors": _actors(),
        "classified": [
            {"id": "FR-SHARED", "type": "FR", "text": "The system validates eligibility."}
        ],
        "use_cases": [
            _use_case("UC-1", "Register", requirement_ids=["FR-SHARED"]),
            _use_case("UC-2", "Swap", requirement_ids=["FR-SHARED"]),
        ],
        "use_case_specs": [
            _spec(
                "UC-1",
                [{
                    "step_number": 2,
                    "sentence": "System validates enrollment eligibility.",
                    "covered_req_ids": ["FR-SHARED"],
                }],
            ),
            _spec(
                "UC-2",
                [{
                    "step_number": 4,
                    "sentence": "System validates enrollment eligibility for the replacement.",
                    "covered_req_ids": ["FR-SHARED"],
                }],
            ),
        ],
    }


def test_include_is_identified_once_from_exact_shared_step_coverage(monkeypatch):
    state = _shared_state()
    candidate = s4._include_candidates(
        state,
        state["use_cases"],
        {item["use_case_id"]: item for item in state["use_case_specs"]},
    )[0]

    def decide(schema, messages):
        return RelationshipModel(
            includes=[
                IncludeSelection(
                    candidate_id=candidate["candidate_id"],
                    decision="approve",
                    included_use_case_name="Validate enrollment eligibility",
                )
            ]
        )

    monkeypatch.setattr(s4, "invoke_structured", decide)
    rel = s4.identify_relationships(state)["relationships"]

    assert len(rel["includes"]) == 2
    assert len(rel["derived_use_cases"]) == 1
    assert {item["base_use_case_id"] for item in rel["includes"]} == {"UC-1", "UC-2"}
    assert {ref["step_ref"] for ref in candidate["step_refs"]} == {
        "main:2",
        "main:4",
    }
    assert rel["derived_use_cases"][0]["use_case_id"].startswith("UC_INC_")
    assert "candidates" not in rel and "candidate_decisions" not in rel


def test_same_words_without_shared_covered_fr_do_not_form_include_candidate(monkeypatch):
    monkeypatch.setattr(s4, "invoke_structured", lambda *_: RelationshipModel())
    state = {
        "actors": _actors(),
        "classified": [
            {"id": "FR-1", "type": "FR", "text": "One"},
            {"id": "FR-2", "type": "FR", "text": "Two"},
        ],
        "use_cases": [
            _use_case("UC-1", "One", requirement_ids=["FR-1"]),
            _use_case("UC-2", "Two", requirement_ids=["FR-2"]),
        ],
        "use_case_specs": [
            _spec("UC-1", [{"step_number": 1, "sentence": "System validates input.", "covered_req_ids": ["FR-1"]}]),
            _spec("UC-2", [{"step_number": 1, "sentence": "System validates input.", "covered_req_ids": ["FR-2"]}]),
        ],
    }

    rel = s4.identify_relationships(state)["relationships"]

    assert rel["includes"] == []
    assert rel["derived_use_cases"] == []


def test_semantic_include_rejection_does_not_create_a_diagram_node(monkeypatch):
    def reject(schema, messages):
        candidate = s4._include_candidates(
            _shared_state(),
            _shared_state()["use_cases"],
            {item["use_case_id"]: item for item in _shared_state()["use_case_specs"]},
        )[0]
        return RelationshipModel(
            includes=[
                IncludeSelection(candidate_id=candidate["candidate_id"], decision="reject")
            ]
        )

    monkeypatch.setattr(s4, "invoke_structured", reject)
    rel = s4.identify_relationships(_shared_state())["relationships"]

    assert rel["includes"] == []
    assert rel["derived_use_cases"] == []
    assert "candidate_decisions" not in rel


def test_materialized_relationships_are_independently_reviewed(monkeypatch):
    state = _shared_state()
    candidate = s4._include_candidates(
        state,
        state["use_cases"],
        {item["use_case_id"]: item for item in state["use_case_specs"]},
    )[0]
    monkeypatch.setattr(
        s4,
        "invoke_structured",
        lambda *_: RelationshipModel(
            includes=[
                IncludeSelection(
                    candidate_id=candidate["candidate_id"],
                    decision="approve",
                    included_use_case_name="Validate enrollment eligibility",
                )
            ]
        ),
    )
    reviewed = {}

    def review(stage, artifact, **kwargs):
        reviewed.update({"stage": stage, "artifact": artifact, **kwargs})
        return s4.validator.Review(
            findings=["[rel] Invalid include [rule:rel.include-is-the-default-relationship]"],
            status=s4.validator.OK,
            unexamined=("rel.generalization-keeps-meaning",),
        )

    monkeypatch.setattr(s4.validator, "review", review)

    rel = s4.identify_relationships(state)["relationships"]

    assert reviewed["stage"] == s4.rules.DRAW_DIAGRAM
    assert reviewed["confirm_violations"] is True
    assert reviewed["artifact"]["relationships"]["includes"] == rel["includes"]
    assert reviewed["artifact"]["requirements"][0]["id"] == "FR-SHARED"
    assert reviewed["artifact"]["actors"][0]["name"] == "User"
    assert rel["semantic_status"] == "ok"
    assert rel["relationship_issues"]
    assert rel["unexamined_rules"] == ["rel.generalization-keeps-meaning"]
    assert rel["repair_stopped"] == "unresolved"


def test_extend_selection_is_bounded_to_existing_ids_and_exact_base_step(monkeypatch):
    state = {
        "actors": [{"name": "Actor", "description": "a", "parent_actor": None}],
        "classified": [
            {"id": "FR-B", "type": "FR", "text": "View the schedule."},
            {"id": "FR-E", "type": "FR", "text": "Optionally export it."},
        ],
        "use_cases": [
            _use_case("UC-BASE", "View schedule", actor="Actor", requirement_ids=["FR-B"]),
            _use_case("UC-EXT", "Export schedule", actor="Actor", requirement_ids=["FR-E"]),
        ],
        "use_case_specs": [
            _spec("UC-BASE", [
                {"step_number": 1, "sentence": "Actor requests the schedule."},
                {"step_number": 4, "sentence": "System presents the current schedule."},
            ]),
            _spec("UC-EXT", [{"step_number": 1, "sentence": "System exports the schedule."}]),
        ],
    }
    monkeypatch.setattr(
        s4,
        "invoke_structured",
        lambda *_: RelationshipModel(
            extends=[
                ExtendSelection(
                    base_use_case_id="UC-BASE",
                    extending_use_case_id="UC-EXT",
                    base_step_ref="main:4",
                    extension_point_name="after schedule presented",
                    condition="while viewing the current schedule",
                )
            ]
        ),
    )

    rel = s4.identify_relationships(state)["relationships"]
    extend = rel["extends"][0]

    assert extend["base_use_case_id"] == "UC-BASE"
    assert extend["extending_use_case_id"] == "UC-EXT"
    assert extend["extension_point"] == "main:4"
    assert extend["step_refs"][0]["sentence"] == "System presents the current schedule."
    assert ("Actor", "UC-EXT") not in {
        (item["actor"], item["use_case_id"]) for item in rel["associations"]
    }


def test_invalid_extend_references_are_dropped(monkeypatch):
    state = _shared_state()
    monkeypatch.setattr(
        s4,
        "invoke_structured",
        lambda *_: RelationshipModel(
            extends=[
                ExtendSelection(
                    base_use_case_id="UC-1",
                    extending_use_case_id="UC-MISSING",
                    base_step_ref="main:99",
                    extension_point_name="invalid point",
                    condition="invalid condition",
                )
            ]
        ),
    )

    rel = s4.identify_relationships(state)["relationships"]

    assert rel["extends"] == []
    assert "cannot create use cases" in rel["dropped_refs"][0]


def test_actor_projection_uses_canonical_participation_and_generalization(monkeypatch):
    monkeypatch.setattr(s4, "invoke_structured", lambda *_: pytest.fail("no accepted specs"))
    use_cases = [
        _use_case("UC-G", "General action", actor="User"),
        _use_case("UC-M", "Member action", actor="Member"),
    ]
    rel = s4.identify_relationships(
        {"actors": _actors(), "use_cases": use_cases, "use_case_specs": []}
    )["relationships"]

    assert {(item["actor"], item["use_case_id"]) for item in rel["associations"]} == {
        ("User", "UC-G"),
        ("Member", "UC-M"),
    }
    assert [(item["parent"], item["child"]) for item in rel["generalizations"]] == [
        ("User", "Member")
    ]


def test_renderer_preserves_input_order_and_previous_extend_notation():
    use_cases = [
        _use_case("UC-10", "Tenth", actor="User"),
        _use_case("UC-2", "Second", actor="User"),
        _use_case("UC-1", "First", actor="User"),
    ]
    state = {
        "actors": _actors(),
        "use_cases": use_cases,
        "relationships": {
            "associations": [],
            "includes": [],
            "extends": [{
                "base_use_case_id": "UC-2",
                "extending_use_case_id": "UC-1",
                "extension_point": "main:4",
                "extension_point_name": "after schedule presented",
                "condition": "while viewing the schedule",
            }],
            "generalizations": [],
            "derived_use_cases": [],
        },
    }

    diagram = s4.render_diagram(state)["diagram"]

    declarations = [diagram.index(f'as {s4._san(identifier)}') for identifier in ("UC-10", "UC-2", "UC-1")]
    assert declarations == sorted(declarations)
    assert 'Second\\n-- extension points --\\nafter schedule presented' in diagram
    assert (
        f"{s4._san('UC-2')} <.. {s4._san('UC-1')} : "
        "<<extend>>\\n[while viewing the schedule]"
    ) in diagram


def test_unaccepted_specs_do_not_reach_the_model(monkeypatch):
    monkeypatch.setattr(s4, "invoke_structured", lambda *_: pytest.fail("no accepted spec"))
    state = _shared_state()
    state["use_case_specs"] = [
        {**item, "issues": ["invalid"]} for item in state["use_case_specs"]
    ]

    rel = s4.identify_relationships(state)["relationships"]

    assert rel["includes"] == [] and rel["extends"] == []
    assert len(rel["dropped_refs"]) == 2


def test_aliases_are_collision_proof_and_empty_projection_is_complete():
    assert s4._san("actor-one") != s4._san("actor one")
    rel = s4.identify_relationships({"actors": [], "use_cases": []})["relationships"]

    assert all(not value for value in rel.values())
    assert s4.render_diagram(
        {"actors": [], "use_cases": [], "relationships": rel}
    )["diagram"] == "@startuml\n@enduml"
