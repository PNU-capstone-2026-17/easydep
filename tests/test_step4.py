"""STEP 4 관계 선택과 결정론적 PlantUML 공개 계약을 검증한다."""
from __future__ import annotations

import re

import pytest

from app.requirements.modeling import diagram as diagram_service
from app.requirements.modeling import relationships as s4
from app.requirements.schemas import (
    ExistingIncludeModel,
    ExistingIncludeSelection,
    ExtendSelection,
    IncludeBaseStepRef,
    IncludeSelection,
    RelationshipModel,
)


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


def _shared_candidate() -> dict:
    """공개 selection 계약에 주입하는 근거 제한 include candidate다."""

    return {
        "candidate_id": "shared-validation",
        "requirement_id": "FR-SHARED",
        "participating_use_case_ids": ["UC-1", "UC-2"],
        "step_refs": [
            {
                "use_case_id": "UC-1",
                "step_ref": "main:2",
                "sentence": "System validates enrollment eligibility.",
            },
            {
                "use_case_id": "UC-2",
                "step_ref": "main:4",
                "sentence": "System validates enrollment eligibility for the replacement.",
            },
        ],
        "derived_use_case_id": "UC_INC_SHARED",
    }


def test_public_selection_materializes_one_include_from_exact_shared_step_coverage():
    state = _shared_state()
    candidate = _shared_candidate()

    def decide(_schema, _messages):
        return RelationshipModel(
            includes=[
                IncludeSelection(
                    candidate_id=candidate["candidate_id"],
                    decision="approve",
                    included_use_case_name="Validate enrollment eligibility",
                )
            ]
        )

    includes, extends, derived, dropped = s4.select_relationship_parts(
        state,
        state["use_cases"],
        {item["use_case_id"]: item for item in state["use_case_specs"]},
        [candidate],
        [],
        "",
        proposal_call=decide,
    )

    assert len(includes) == 2
    assert len(derived) == 1
    assert {item["base_use_case_id"] for item in includes} == {"UC-1", "UC-2"}
    assert {ref["step_ref"] for ref in candidate["step_refs"]} == {
        "main:2",
        "main:4",
    }
    assert derived[0]["use_case_id"] == "UC_INC_SHARED"
    assert extends == [] and dropped == []


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


def test_existing_use_case_can_be_included_from_different_requirement_steps():
    state = {
        "actors": _actors(),
        "classified": [
            {"id": "FR-VIEW", "type": "FR", "text": "Inspect a published status."},
            {"id": "FR-SUBMIT", "type": "FR", "text": "Submit a request."},
            {"id": "FR-REPORT", "type": "FR", "text": "Open a report."},
        ],
        "use_cases": [
            _use_case("UC-VIEW", "Inspect status", requirement_ids=["FR-VIEW"]),
            _use_case("UC-SUBMIT", "Submit request", requirement_ids=["FR-SUBMIT"]),
            _use_case("UC-REPORT", "Open report", requirement_ids=["FR-REPORT"]),
        ],
        "use_case_specs": [
            _spec(
                "UC-VIEW",
                [
                    {
                        "step_number": 1,
                        "sentence": "System presents the published status.",
                        "covered_req_ids": ["FR-VIEW"],
                    }
                ],
            ),
            _spec(
                "UC-SUBMIT",
                [
                    {
                        "step_number": 2,
                        "sentence": "Member inspects the published status before submitting.",
                        "covered_req_ids": ["FR-SUBMIT"],
                    }
                ],
            ),
            _spec(
                "UC-REPORT",
                [
                    {
                        "step_number": 3,
                        "sentence": "Member inspects the published status before opening the report.",
                        "covered_req_ids": ["FR-REPORT"],
                    }
                ],
            ),
        ],
    }
    options = [{
        "included_use_case_id": "UC-VIEW",
        "name": "Inspect status",
        "base_step_options": [
            {
                "use_case_id": "UC-SUBMIT",
                "step_ref": "main:2",
                "sentence": "Member inspects the published status before submitting.",
                "requirement_ids": ["FR-SUBMIT"],
            },
            {
                "use_case_id": "UC-REPORT",
                "step_ref": "main:3",
                "sentence": "Member inspects the published status before opening the report.",
                "requirement_ids": ["FR-REPORT"],
            },
        ],
    }]
    assert [(item["included_use_case_id"], item["name"]) for item in options] == [
        ("UC-VIEW", "Inspect status")
    ]
    assert {
        (item["use_case_id"], item["step_ref"])
        for item in options[0]["base_step_options"]
    } == {("UC-SUBMIT", "main:2"), ("UC-REPORT", "main:3")}

    def decide(_schema, _messages):
        return ExistingIncludeModel(
            existing_includes=[
                ExistingIncludeSelection(
                    included_use_case_id="UC-VIEW",
                    base_step_refs=[
                        IncludeBaseStepRef(use_case_id="UC-SUBMIT", step_ref="main:2"),
                        IncludeBaseStepRef(use_case_id="UC-REPORT", step_ref="main:3"),
                    ],
                )
            ]
        )

    includes, _extends, derived, dropped = s4.select_relationship_parts(
        state,
        state["use_cases"],
        {item["use_case_id"]: item for item in state["use_case_specs"]},
        [],
        options,
        "",
        proposal_call=decide,
    )

    assert {
        (item["base_use_case_id"], item["included_use_case_id"])
        for item in includes
    } == {("UC-SUBMIT", "UC-VIEW"), ("UC-REPORT", "UC-VIEW")}
    assert derived == [] and dropped == []
    assert {
        ref["requirement_id"]
        for ref in includes[0]["requirement_refs"]
    } == {"FR-SUBMIT", "FR-REPORT"}


def test_existing_include_rejects_a_base_step_outside_the_accepted_options():
    use_cases = [
        _use_case("UC-A", "Inspect", requirement_ids=["FR-A"]),
        _use_case("UC-B", "Submit", requirement_ids=["FR-B"]),
        _use_case("UC-C", "Review", requirement_ids=["FR-C"]),
    ]
    options = [
        {
            "included_use_case_id": "UC-A",
            "name": "Inspect",
            "base_step_options": [
                {
                    "use_case_id": "UC-B",
                    "step_ref": "main:1",
                    "sentence": "System presents a status.",
                    "requirement_ids": ["FR-B"],
                },
                {
                    "use_case_id": "UC-C",
                    "step_ref": "main:1",
                    "sentence": "System presents a status.",
                    "requirement_ids": ["FR-C"],
                },
            ],
        }
    ]
    def decide(_schema, _messages):
        return ExistingIncludeModel(
            existing_includes=[
                ExistingIncludeSelection(
                    included_use_case_id="UC-A",
                    base_step_refs=[
                        IncludeBaseStepRef(use_case_id="UC-B", step_ref="main:99"),
                        IncludeBaseStepRef(use_case_id="UC-C", step_ref="main:1"),
                    ],
                )
            ]
        )

    includes, _extends, _derived, dropped = s4.select_relationship_parts(
        {},
        use_cases,
        {"UC-A": {"main_scenario": []}},
        [],
        options,
        "",
        proposal_call=decide,
    )

    assert includes == []
    assert any("not supplied" in item for item in dropped)


def test_existing_include_uses_a_focused_call_separate_from_derived_candidates(
):
    state = {
        "actors": _actors(),
        "classified": [
            {"id": "FR-I", "type": "FR", "text": "Inspect a record."},
            {"id": "FR-A", "type": "FR", "text": "Submit a record."},
            {"id": "FR-B", "type": "FR", "text": "Review a record."},
            {"id": "FR-S", "type": "FR", "text": "Validate a record."},
        ],
        "use_cases": [
            _use_case("UC-I", "Inspect record", requirement_ids=["FR-I"]),
            _use_case("UC-A", "Submit record", requirement_ids=["FR-A"]),
            _use_case("UC-B", "Review record", requirement_ids=["FR-B"]),
            _use_case("UC-V1", "Validate record", requirement_ids=["FR-S"]),
            _use_case("UC-V2", "Confirm record", requirement_ids=["FR-S"]),
        ],
        "use_case_specs": [
            _spec("UC-I", [{"step_number": 1, "sentence": "System presents a record.", "covered_req_ids": ["FR-I"]}]),
            _spec("UC-A", [{"step_number": 1, "sentence": "Member inspects the record.", "covered_req_ids": ["FR-A"]}]),
            _spec("UC-B", [{"step_number": 1, "sentence": "Member inspects the record.", "covered_req_ids": ["FR-B"]}]),
            _spec("UC-V1", [{"step_number": 1, "sentence": "System validates the record.", "covered_req_ids": ["FR-S"]}]),
            _spec("UC-V2", [{"step_number": 1, "sentence": "System validates the record.", "covered_req_ids": ["FR-S"]}]),
        ],
    }
    schemas = []

    def decide(schema, _messages):
        schemas.append(schema)
        return ExistingIncludeModel() if schema is ExistingIncludeModel else RelationshipModel()

    relationships = s4.identify_relationships(
        state, proposal_call=decide
    )["relationships"]

    assert schemas == [RelationshipModel, ExistingIncludeModel]
    assert relationships["includes"] == []


def test_public_selection_rejection_does_not_create_a_diagram_node():
    candidate = _shared_candidate()

    def reject(_schema, _messages):
        return RelationshipModel(
            includes=[
                IncludeSelection(candidate_id=candidate["candidate_id"], decision="reject")
            ]
        )

    state = _shared_state()
    includes, _extends, derived, dropped = s4.select_relationship_parts(
        state,
        state["use_cases"],
        {item["use_case_id"]: item for item in state["use_case_specs"]},
        [candidate],
        [],
        "",
        proposal_call=reject,
    )

    assert includes == [] and derived == [] and dropped == []


def test_materialized_relationships_are_independently_reviewed():
    state = _shared_state()
    reviewed = {}

    def review(stage, artifact, **kwargs):
        reviewed.update({"stage": stage, "artifact": artifact, **kwargs})
        return s4.validator.Review(
            findings=["[rel] Invalid include [rule:rel.include-is-the-default-relationship]"],
            status=s4.validator.OK,
            unexamined=("rel.generalization-keeps-meaning",),
        )

    rel = s4.identify_relationships(
        state,
        proposal_call=lambda _schema, _messages: RelationshipModel(),
        review_call=review,
    )["relationships"]

    assert reviewed["stage"] == s4.rules.DRAW_DIAGRAM
    assert reviewed["confirm_violations"] is True
    assert reviewed["artifact"]["relationships"]["includes"] == rel["includes"]
    assert reviewed["artifact"]["requirements"][0]["id"] == "FR-SHARED"
    assert reviewed["artifact"]["actors"][0]["name"] == "User"
    assert rel["semantic_status"] == "ok"
    assert rel["relationship_issues"]
    assert rel["unexamined_rules"] == ["rel.generalization-keeps-meaning"]
    assert rel["repair_iters"] == 1
    assert rel["repair_stopped"] == "unresolved"


def test_confirmed_relationship_defect_gets_one_bounded_selection_repair():
    state = _shared_state()
    reviews = iter([
        s4.validator.Review(
            findings=["[rel] Invalid include [rel.include-is-the-default-relationship · p.81]"],
            status=s4.validator.OK,
        ),
        s4.validator.Review(status=s4.validator.OK),
    ])
    calls = {"n": 0}

    def propose(_schema, _messages):
        calls["n"] += 1
        return RelationshipModel()

    relationships = s4.identify_relationships(
        state,
        proposal_call=propose,
        review_call=lambda *_args, **_kwargs: next(reviews),
    )["relationships"]

    assert relationships["includes"] == []
    assert relationships["derived_use_cases"] == []
    assert relationships["relationship_issues"] == []
    assert relationships["repair_iters"] == 1
    assert relationships["repair_stopped"] == "clean"
    assert calls["n"] == 2


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
    state["classified"][0]["text"] = "The member may validate eligibility optionally."
    monkeypatch.setattr(
        s4,
        "invoke_structured",
        lambda *_: RelationshipModel(
            extends=[
                ExtendSelection(
                    base_use_case_id="UC-MISSING",
                    extending_use_case_id="UC-1",
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

    diagram = diagram_service.render_diagram(state)["diagram"]

    declarations = [diagram.index(f'usecase "{name}') for name in ("Tenth", "Second", "First")]
    assert declarations == sorted(declarations)
    assert 'Second\\n-- extension points --\\nafter schedule presented' in diagram
    second_alias = re.search(r'usecase "Second.* as (\S+)', diagram).group(1)
    first_alias = re.search(r'usecase "First.* as (\S+)', diagram).group(1)
    assert f"{second_alias} <.. {first_alias} : <<extend>>\\n[while viewing the schedule]" in diagram


def test_grounded_extend_condition_is_wrapped_by_the_public_renderer():
    condition = (
        "registration is rejected because the offering is full and its waitlist is enabled"
    )

    selection = ExtendSelection(
        base_use_case_id="UC-register",
        extending_use_case_id="UC-waitlist",
        base_step_ref="main:3",
        extension_point_name="after eligibility validation",
        condition=condition,
    )

    assert len(condition) > 60
    assert selection.condition == condition
    rendered = diagram_service.render_diagram({
        "actors": [],
        "use_cases": [
            _use_case("UC-register", "Register"),
            _use_case("UC-waitlist", "Waitlist"),
        ],
        "relationships": {
            "extends": [{
                "base_use_case_id": "UC-register",
                "extending_use_case_id": "UC-waitlist",
                "condition": selection.condition,
            }]
        },
    })["diagram"]
    assert "registration is rejected because\\nthe offering is full and its\\nwaitlist is enabled" in rendered


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
    collision_diagram = diagram_service.render_diagram({
        "actors": [
            {"name": "actor-one", "description": "a"},
            {"name": "actor one", "description": "b"},
        ],
        "use_cases": [_use_case("UC-1", "Compare", actor="actor-one")],
        "relationships": {},
    })["diagram"]
    aliases = re.findall(r'actor "actor(?:-| )one" as (\S+)', collision_diagram)
    assert len(aliases) == len(set(aliases)) == 2
    rel = s4.identify_relationships({"actors": [], "use_cases": []})["relationships"]

    assert all(not value for value in rel.values())
    assert diagram_service.render_diagram(
        {"actors": [], "use_cases": [], "relationships": rel}
    )["diagram"] == "@startuml\n@enduml"


def test_relationship_text_cleanup_removes_invisible_format_characters():
    rendered = diagram_service.render_diagram({
        "actors": [],
        "use_cases": [_use_case("UC-1", "after validation\u200b\u2060  completes")],
        "relationships": {},
    })["diagram"]
    assert 'usecase "after validation completes"' in rendered


def test_derived_use_case_name_is_rendered_as_plain_words():
    candidate = _shared_candidate()
    state = _shared_state()
    _includes, _extends, derived, _dropped = s4.select_relationship_parts(
        state,
        state["use_cases"],
        {item["use_case_id"]: item for item in state["use_case_specs"]},
        [candidate],
        [],
        "",
        proposal_call=lambda _schema, _messages: RelationshipModel(includes=[
            IncludeSelection(
                candidate_id=candidate["candidate_id"],
                decision="approve",
                included_use_case_name="ValidateEnrollmentEligibility",
            )
        ]),
    )
    assert derived[0]["name"] == "Validate Enrollment Eligibility"
