"""Invariant tests for the evidence-bound Step 4 relationship projection."""
from __future__ import annotations

from collections.abc import Callable

import pytest

from app.requirements.agent.steps import step4_diagram as s4
from app.requirements.schemas import RelationshipCandidateDecision, RelationshipModel


def _actors() -> list[dict]:
    return [
        {"name": "General actor", "description": "g", "parent_actor": None},
        {"name": "Special actor", "description": "s", "parent_actor": "General actor"},
    ]


def _use_cases() -> list[dict]:
    return [
        {
            "id": "UC-A",
            "name": "Display A",
            "primary_actor": "General actor",
            "supporting_actors": ["Special actor"],
        },
        {
            "id": "UC-B",
            "name": "Display B",
            "primary_actor": "Special actor",
            "supporting_actors": [],
        },
    ]


def _spec(use_case_id: str, *, steps: list[dict], extensions: list[dict] | None = None) -> dict:
    return {
        "use_case_id": use_case_id,
        "name": "intentionally unused display name",
        "main_scenario": steps,
        "extensions": extensions or [],
        "issues": [],
        "semantic_status": "ok",
    }


def _approve_all_candidates(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    captured: dict[str, str] = {}

    def decide(schema, messages):
        captured["prompt"] = messages[1].content
        candidates = s4.json.loads(messages[1].content.split("Candidates:\n", 1)[1])
        return RelationshipModel(
            candidate_decisions=[
                RelationshipCandidateDecision(candidate_id=item["candidate_id"], decision="approve")
                for item in candidates
            ]
        )

    monkeypatch.setattr(s4, "invoke_structured", decide)
    return lambda: captured


def test_include_projection_uses_ids_and_exact_evidence_not_display_names(monkeypatch: pytest.MonkeyPatch):
    captured = _approve_all_candidates(monkeypatch)
    use_cases = _use_cases()
    state = {
        "actors": _actors(),
        "use_cases": use_cases,
        "use_case_specs": [
            _spec("UC-B", steps=[{"step_number": 7, "sentence": "System performs shared action."}]),
            _spec("UC-A", steps=[{"step_number": 3, "sentence": "system performs shared action"}]),
        ],
    }

    rel = s4.identify_relationships(state)["relationships"]
    include = next(item for item in rel["includes"] if item["candidate_id"].startswith("rel-include-"))
    candidate = next(item for item in rel["candidates"] if item["candidate_id"] == include["candidate_id"])

    assert set(include) >= {"base_use_case_id", "included_use_case_id", "step_refs"}
    assert include["base_use_case_id"] in candidate["participating_use_case_ids"]
    assert {ref["use_case_id"] for ref in candidate["step_refs"]} == set(
        candidate["participating_use_case_ids"]
    )
    assert all(ref["step_ref"].startswith("main:") for ref in candidate["step_refs"])
    assert all(
        association["use_case_id"] != include["included_use_case_id"]
        for association in rel["associations"]
    )
    assert "candidate_decisions" in captured()["prompt"]


def test_candidate_ids_are_deterministic_under_input_order_and_display_name_changes():
    actors = _actors()
    use_cases = _use_cases()
    specs = [
        _spec("UC-A", steps=[{"step_number": 3, "sentence": "System performs shared action."}]),
        _spec("UC-B", steps=[{"step_number": 7, "sentence": "System performs shared action."}]),
    ]
    first, _ = s4._relationship_candidates({"use_case_specs": specs}, use_cases, actors)
    renamed = [{**use_case, "name": f"renamed-{index}"} for index, use_case in enumerate(use_cases)]
    second, _ = s4._relationship_candidates({"use_case_specs": list(reversed(specs))}, renamed, actors)

    assert [candidate["candidate_id"] for candidate in first] == [
        candidate["candidate_id"] for candidate in second
    ]
    assert all(candidate["participating_use_case_ids"] for candidate in first)
    assert all(candidate["step_refs"] for candidate in first)


def test_include_candidate_can_be_grounded_by_shared_accepted_requirement_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    _approve_all_candidates(monkeypatch)
    use_cases = [
        {"id": "UC-A", "name": "One", "primary_actor": "General actor", "requirement_ids": ["R-SHARED"]},
        {"id": "UC-B", "name": "Two", "primary_actor": "General actor", "requirement_ids": ["R-SHARED"]},
    ]
    state = {
        "actors": _actors(),
        "use_cases": use_cases,
        "use_case_specs": [
            _spec("UC-A", steps=[{"step_number": 2, "sentence": "System performs first action.", "covered_req_ids": ["R-SHARED"]}]),
            _spec("UC-B", steps=[{"step_number": 5, "sentence": "System performs second action.", "covered_req_ids": ["R-SHARED"]}]),
        ],
    }

    rel = s4.identify_relationships(state)["relationships"]
    candidate = next(item for item in rel["candidates"] if item.get("requirement_ids") == ["R-SHARED"])
    include = next(item for item in rel["includes"] if item["candidate_id"] == candidate["candidate_id"])

    assert {(ref["use_case_id"], ref["step_ref"], ref["requirement_id"]) for ref in candidate["requirement_refs"]} == {
        ("UC-A", "main:2", "R-SHARED"),
        ("UC-B", "main:5", "R-SHARED"),
    }
    assert include["requirement_refs"] == candidate["requirement_refs"]


def test_unaccepted_specification_is_rejected_before_relationship_mining(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(s4, "invoke_structured", lambda *_: pytest.fail("unaccepted specs make no candidates"))
    state = {
        "actors": _actors(),
        "use_cases": _use_cases(),
        "use_case_specs": [
            _spec(
                "UC-A",
                steps=[{"step_number": 1, "sentence": "System performs shared action."}],
                extensions=[],
            )
            | {"issues": ["scope mismatch"]},
            _spec(
                "UC-B",
                steps=[{"step_number": 1, "sentence": "System performs shared action."}],
                extensions=[],
            )
            | {"semantic_status": "failed"},
        ],
    }

    rel = s4.identify_relationships(state)["relationships"]

    assert not rel["candidates"] and not rel["includes"] and not rel["extends"]
    assert {item["use_case_id"] for item in rel["candidate_rejections"]} == {"UC-A", "UC-B"}
    assert {item["reason"] for item in rel["candidate_rejections"]} == {"unaccepted use-case specification"}


def test_actor_projection_suppresses_only_inherited_duplicate_associations(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(s4, "invoke_structured", lambda *_: pytest.fail("no candidates require no model"))
    state = {"actors": _actors(), "use_cases": _use_cases(), "use_case_specs": []}

    rel = s4.identify_relationships(state)["relationships"]
    pairs = {(item["actor"], item["use_case_id"]) for item in rel["associations"]}
    inherited_pair = ("Special actor", "UC-A")
    specialized_pair = ("Special actor", "UC-B")

    assert inherited_pair not in pairs
    assert specialized_pair in pairs
    assert ("General actor", "UC-A") in pairs
    assert {(item["parent"], item["child"]) for item in rel["generalizations"]} == {
        ("General actor", "Special actor")
    }
    report = s4.check_relationships({**state, "relationships": rel})["relationship_report"]
    assert report["missing_supporting_associations"] == []


def test_optional_top_level_use_case_extends_accepted_base_and_suppresses_redundant_link(
    monkeypatch: pytest.MonkeyPatch,
):
    _approve_all_candidates(monkeypatch)
    actor = {"name": "Actor", "description": "a", "parent_actor": None}
    base = {"id": "UC-BASE", "name": "Base", "primary_actor": "Actor", "requirement_ids": ["R-BASE"]}
    optional = {"id": "UC-OPTION", "name": "Optional", "primary_actor": "Actor", "requirement_ids": ["R-OPTION"]}
    condition = "The actor may request the optional behavior."
    state = {
        "actors": [actor],
        "classified": [
            {"id": "R-BASE", "type": "FR", "text": "The system performs the base behavior."},
            {"id": "R-OPTION", "type": "FR", "text": condition},
        ],
        "use_cases": [base, optional],
        "use_case_specs": [
            _spec("UC-BASE", steps=[{"step_number": 1, "sentence": "System performs base action."}]),
            _spec("UC-OPTION", steps=[{"step_number": 1, "sentence": "System performs optional action."}]),
        ],
    }

    rel = s4.identify_relationships(state)["relationships"]
    extend = next(item for item in rel["extends"] if item["extending_use_case_id"] == "UC-OPTION")

    assert extend["base_use_case_id"] == "UC-BASE"
    assert extend["condition"] == condition
    assert extend["extension_point"] == "main:1"
    assert extend["requirement_refs"] == [
        {"use_case_id": "UC-OPTION", "requirement_id": "R-OPTION", "source_text": condition}
    ]
    assert all(item["use_case_id"] != "UC-OPTION" for item in rel["derived_use_cases"])
    assert ("Actor", "UC-OPTION") not in {
        (item["actor"], item["use_case_id"]) for item in rel["associations"]
    }
    assert any(item["use_case_id"] == "UC-OPTION" for item in rel["suppressed_associations"])


def test_cockburn_extensions_stay_in_the_accepted_specification(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(s4, "invoke_structured", lambda *_: pytest.fail("no relationship candidate"))
    state = {
        "actors": _actors(),
        "use_cases": _use_cases()[:1],
        "use_case_specs": [
            _spec(
                "UC-A",
                steps=[{"step_number": 4, "sentence": "System reaches the extension point."}],
                extensions=[
                    {
                        "label": "4a",
                        "branch_step": 4,
                        "condition": "No result exists.",
                        "handling_steps": [
                            {"sub_step": "4a1", "sentence": "System reports the result."}
                        ],
                        "outcome": "alternate_success",
                    }
                ],
            )
        ],
    }

    rel = s4.identify_relationships(state)["relationships"]

    assert not rel["candidates"] and not rel["extends"] and not rel["derived_use_cases"]
    assert not rel["candidate_rejections"]


def test_model_output_is_candidate_decisions_only_and_unknown_ids_do_not_materialize(
    monkeypatch: pytest.MonkeyPatch,
):
    observed: dict[str, object] = {}

    def decide(schema, messages):
        observed["schema"] = schema.model_json_schema()
        observed["prompt"] = messages[1].content
        return RelationshipModel(
            candidate_decisions=[
                RelationshipCandidateDecision(candidate_id="not-a-candidate", decision="approve")
            ]
        )

    monkeypatch.setattr(s4, "invoke_structured", decide)
    state = {
        "actors": _actors(),
        "use_cases": _use_cases(),
        "use_case_specs": [
            _spec("UC-A", steps=[{"step_number": 1, "sentence": "System performs shared action."}]),
            _spec("UC-B", steps=[{"step_number": 1, "sentence": "System performs shared action."}]),
        ],
    }

    rel = s4.identify_relationships(state)["relationships"]

    assert set(observed["schema"]["properties"]) == {"candidate_decisions"}
    assert "not-a-candidate" in rel["dropped_refs"][0]
    assert not rel["includes"] and not rel["extends"] and not rel["derived_use_cases"]
    assert "Do not create, rename, or alter any relationship" in observed["prompt"]


def test_rendering_resolves_duplicate_display_names_through_use_case_ids():
    state = {
        "actors": [{"name": "Actor", "description": "a", "parent_actor": None}],
        "use_cases": [
            {"id": "UC-1", "name": "Same", "primary_actor": "Actor", "supporting_actors": []},
            {"id": "UC-2", "name": "Same", "primary_actor": "Actor", "supporting_actors": []},
        ],
        "relationships": {
            "associations": [{"actor": "Actor", "use_case_id": "UC-1"}],
            "includes": [
                {
                    "base_use_case_id": "UC-2",
                    "included_use_case_id": "UC-D",
                }
            ],
            "extends": [
                {
                    "base_use_case_id": "UC-1",
                    "extending_use_case_id": "UC-D",
                    "condition": "The actor chooses the path",
                    "extension_point": "1a at step 1",
                }
            ],
            "generalizations": [],
            "derived_use_cases": [{"use_case_id": "UC-D", "name": "Derived"}],
        },
    }

    diagram = s4.render_diagram(state)["diagram"]

    assert f"{s4._san('UC-2')} ..> {s4._san('UC-D')} : <<include>>" in diagram
    assert f"{s4._san('UC-D')} ..> {s4._san('UC-1')} : <<extend>>" in diagram
    assert f"{s4._san('Actor')} --- {s4._san('UC-1')}" in diagram


def test_top_level_extend_candidates_are_bounded_by_optional_evidence():
    actor = {"name": "Actor", "description": "a", "parent_actor": None}
    bases = [
        {
            "id": f"UC-{index}",
            "name": f"Process {token}",
            "primary_actor": "Actor",
            "requirement_ids": [f"R-{token}"],
        }
        for index, token in enumerate(("amber", "blue", "crimson", "dune"), start=1)
    ]
    optional = {
        "id": "UC-OPTION",
        "name": "Conditional action",
        "primary_actor": "Actor",
        "requirement_ids": ["R-OPTION"],
    }
    use_cases = [*bases, optional]
    classified = [
        {"id": f"R-{token}", "type": "FR", "text": f"The system processes {token} records."}
        for token in ("amber", "blue", "crimson", "dune")
    ] + [
        {
            "id": "R-OPTION",
            "type": "FR",
            "text": "The actor may continue if amber records are available.",
        }
    ]
    specs = [
        _spec(
            use_case["id"],
            steps=[
                {"step_number": step_number, "sentence": f"System processes {token} record {step_number}."}
                for step_number in range(1, 5)
            ],
        )
        for use_case, token in zip(bases, ("amber", "blue", "crimson", "dune"), strict=True)
    ] + [_spec("UC-OPTION", steps=[{"step_number": 1, "sentence": "System continues conditionally."}])]

    candidates, rejections = s4._top_level_extend_candidates(
        {"actors": [actor], "classified": classified}, use_cases, {spec["use_case_id"]: spec for spec in specs}
    )

    assert len(candidates) <= len(optional["requirement_ids"])
    assert {candidate["participating_use_case_ids"][0] for candidate in candidates} == {"UC-1"}
    assert not rejections


def test_top_level_extend_rejects_a_tie_across_supported_bases():
    actor = {"name": "Actor", "description": "a", "parent_actor": None}
    bases = [
        {"id": identifier, "name": "Process signal", "primary_actor": "Actor", "requirement_ids": [requirement]}
        for identifier, requirement in (("UC-A", "R-A"), ("UC-B", "R-B"))
    ]
    optional = {"id": "UC-O", "name": "Optional", "primary_actor": "Actor", "requirement_ids": ["R-O"]}
    specs = [
        _spec(item["id"], steps=[{"step_number": 1, "sentence": "System processes shared signal."}])
        for item in bases
    ] + [_spec("UC-O", steps=[{"step_number": 1, "sentence": "System performs an option."}])]

    candidates, rejections = s4._top_level_extend_candidates(
        {
            "actors": [actor],
            "classified": [
                {"id": "R-A", "type": "FR", "text": "The system processes shared signal."},
                {"id": "R-B", "type": "FR", "text": "The system processes shared signal."},
                {"id": "R-O", "type": "FR", "text": "The actor may continue if shared signal is present."},
            ],
        },
        [*bases, optional],
        {spec["use_case_id"]: spec for spec in specs},
    )

    assert not candidates
    assert {item["reason"] for item in rejections} == {"ambiguous supported base anchors"}


def test_top_level_extend_rejects_optional_evidence_without_a_supported_base():
    actor = {"name": "Actor", "description": "a", "parent_actor": None}
    base = {"id": "UC-B", "name": "Archive", "primary_actor": "Actor", "requirement_ids": ["R-B"]}
    optional = {"id": "UC-O", "name": "Optional", "primary_actor": "Actor", "requirement_ids": ["R-O"]}
    specs = [
        _spec("UC-B", steps=[{"step_number": 1, "sentence": "System stores cobalt ledger."}]),
        _spec("UC-O", steps=[{"step_number": 1, "sentence": "System performs a choice."}]),
    ]

    candidates, rejections = s4._top_level_extend_candidates(
        {
            "actors": [actor],
            "classified": [
                {"id": "R-B", "type": "FR", "text": "The system stores cobalt ledger."},
                {"id": "R-O", "type": "FR", "text": "The actor may continue if quartz is present."},
            ],
        },
        [base, optional],
        {spec["use_case_id"]: spec for spec in specs},
    )

    assert not candidates
    assert {item["reason"] for item in rejections} == {"no positively supported base anchor"}


def test_top_level_extend_requires_explicit_optional_modality_not_a_mandatory_condition():
    actor = {"name": "Actor", "description": "a", "parent_actor": None}
    base = {"id": "UC-B", "name": "Record trace", "primary_actor": "Actor", "requirement_ids": ["R-B"]}
    mandatory = {
        "id": "UC-M",
        "name": "Mandatory trace",
        "primary_actor": "Actor",
        "requirement_ids": ["R-M"],
    }
    specs = [
        _spec("UC-B", steps=[{"step_number": 1, "sentence": "System records transfer trace."}]),
        _spec("UC-M", steps=[{"step_number": 1, "sentence": "System records session trace."}]),
    ]

    candidates, rejections = s4._top_level_extend_candidates(
        {
            "actors": [actor],
            "classified": [
                {"id": "R-B", "type": "FR", "text": "The system records transfer trace."},
                {
                    "id": "R-M",
                    "type": "FR",
                    "text": "The system shall record a transfer trace when it occurs during a session.",
                },
            ],
        },
        [base, mandatory],
        {spec["use_case_id"]: spec for spec in specs},
    )

    assert not candidates
    assert not rejections


def test_top_level_extend_uses_ordered_phrase_evidence_to_break_a_token_set_tie():
    actor = {"name": "Actor", "description": "a", "parent_actor": None}
    bases = [
        {"id": "UC-A", "name": "Process state", "primary_actor": "Actor", "requirement_ids": ["R-A"]},
        {"id": "UC-B", "name": "Process state", "primary_actor": "Actor", "requirement_ids": ["R-B"]},
    ]
    optional = {"id": "UC-O", "name": "Optional", "primary_actor": "Actor", "requirement_ids": ["R-O"]}
    specs = [
        _spec("UC-A", steps=[{"step_number": 1, "sentence": "System maintains reviewed signal state."}]),
        _spec("UC-B", steps=[{"step_number": 1, "sentence": "System maintains signal reviewed state."}]),
        _spec("UC-O", steps=[{"step_number": 1, "sentence": "System continues an option."}]),
    ]

    candidates, rejections = s4._top_level_extend_candidates(
        {
            "actors": [actor],
            "classified": [
                {"id": "R-A", "type": "FR", "text": "The system maintains reviewed signal state."},
                {"id": "R-B", "type": "FR", "text": "The system maintains signal reviewed state."},
                {"id": "R-O", "type": "FR", "text": "The actor may proceed if reviewed signal is present."},
            ],
        },
        [*bases, optional],
        {spec["use_case_id"]: spec for spec in specs},
    )

    assert {candidate["participating_use_case_ids"][0] for candidate in candidates} == {"UC-A"}
    assert not rejections


def test_plantuml_aliases_are_collision_proof_and_labels_are_escaped():
    assert s4._san("actor-one") != s4._san("actor one")
    assert s4._san("UC-1") != s4._san("UC 1")

    diagram = s4.render_diagram(
        {
            "actors": [{"name": 'Actor "One"', "description": "a", "parent_actor": None}],
            "use_cases": [{"id": "UC-1", "name": 'Do "work"', "primary_actor": 'Actor "One"'}],
            "relationships": {"associations": [], "includes": [], "extends": [], "generalizations": []},
        }
    )["diagram"]

    assert 'actor "Actor \'One\'"' in diagram
    assert 'usecase "Do \'work\'"' in diagram


def test_empty_use_cases_has_a_complete_empty_projection():
    rel = s4.identify_relationships({"actors": [], "use_cases": []})["relationships"]

    assert all(not value for value in rel.values())
    assert s4.render_diagram({"actors": [], "use_cases": [], "relationships": rel})["diagram"] == (
        "@startuml\n@enduml"
    )
