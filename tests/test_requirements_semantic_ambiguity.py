from __future__ import annotations

from app.requirements.modeling import specifications
from app.requirements.modeling.specifications import find_source_grounded_semantic_ambiguity
from app.requirements.schemas import SemanticAmbiguityReview


def _state() -> dict[str, object]:
    return {
        "classified": [
            {
                "id": "FR1",
                "type": "FR",
                "text": "A student may submit a request for the current authenticated student or another selected student.",
            }
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "name": "Submit request",
                "requirement_ids": ["FR1"],
                "nfr_ids": [],
                "preconditions": [],
                "trigger": "Student submits a request.",
                "main_scenario": [],
                "extensions": [],
                "success_guarantee": [],
                "minimal_guarantee": [],
                "public_contract": {},
                "issues": [],
            }
        ],
    }


def test_semantic_ambiguity_keeps_only_source_grounded_two_choice_question() -> None:
    def propose(schema, _messages):
        assert schema is SemanticAmbiguityReview
        return schema.model_validate(
            {
                "question": {
                    "useCaseId": "UC1",
                    "sourceRequirementIds": ["FR1"],
                    "evidenceSpans": ["current authenticated student", "another selected student"],
                    "prompt": "Whose student record should this request target?",
                    "options": [
                        {
                            "id": "self",
                            "label": "Current student",
                            "description": "Use the authenticated student only.",
                            "requestedEffect": "Target the authenticated student and do not accept another student identifier.",
                        },
                        {
                            "id": "delegate",
                            "label": "Selected student",
                            "description": "Permit an authorized request for another student.",
                            "requestedEffect": "Permit a selected student only when the requirements define delegated authority.",
                        },
                    ],
                }
            }
        )

    question = find_source_grounded_semantic_ambiguity(_state(), proposal_call=propose)

    assert question is not None
    assert question["useCaseId"] == "UC1"
    assert [option["id"] for option in question["options"]] == ["self", "delegate"]


def test_semantic_ambiguity_rejects_evidence_not_present_in_the_linked_source() -> None:
    def propose(schema, _messages):
        return schema.model_validate(
            {
                "question": {
                    "useCaseId": "UC1",
                    "sourceRequirementIds": ["FR1"],
                    "evidenceSpans": ["invented policy"],
                    "prompt": "Choose a policy.",
                    "options": [
                        {"id": "a", "label": "A", "description": "A path.", "requestedEffect": "Use A."},
                        {"id": "b", "label": "B", "description": "B path.", "requestedEffect": "Use B."},
                    ],
                }
            }
        )

    assert find_source_grounded_semantic_ambiguity(_state(), proposal_call=propose) is None


def test_check_specs_persists_the_selective_review_for_the_feedback_gate(monkeypatch) -> None:
    candidate = {"useCaseId": "UC1", "prompt": "Choose.", "options": []}
    calls = []

    def review(state):
        calls.append(state)
        return candidate

    monkeypatch.setattr(specifications, "find_source_grounded_semantic_ambiguity", review)

    patch = specifications.check_specs(_state())

    assert patch["semantic_ambiguity_question"] == candidate
    assert len(calls) == 1


def test_workspace_semantic_ambiguity_is_version_pinned_two_choice_with_free_text(monkeypatch) -> None:
    from app.db.models import TYPE_USECASE_SPEC
    from app.workspace import service as workspace_service
    from app.workspace.conversation.contracts import RevisionTarget

    target = RevisionTarget(
        ref="use_case_spec:UC1",
        kind="use_case_spec",
        element_id="UC1",
        owner="requirements",
        artifact_type=TYPE_USECASE_SPEC,
        artifact_version_id=37,
        display_label="Submit request",
    )
    monkeypatch.setattr(
        workspace_service.ProjectTools,
        "current_revision_target",
        lambda _self, ref: target if ref == "use_case_spec:UC1" else None,
    )
    candidate = {
        "useCaseId": "UC1",
        "sourceRequirementIds": ["FR1"],
        "evidenceSpans": ["current authenticated student", "another selected student"],
        "prompt": "Whose student record should this request target?",
        "options": [
            {
                "id": "self",
                "label": "Current student",
                "description": "Use the authenticated student only.",
                "requestedEffect": "Target the authenticated student only.",
            },
            {
                "id": "delegate",
                "label": "Selected student",
                "description": "Permit an authorized request for another student.",
                "requestedEffect": "Permit a selected student when delegated authority is defined.",
            },
        ],
    }

    question = workspace_service.WorkspaceService._semantic_ambiguity_question("app-1", candidate)

    assert question is not None
    assert question.base_revisions[0].artifact_type == TYPE_USECASE_SPEC
    assert question.base_revisions[0].version_id == 37
    assert [option.option_id for option in question.options] == ["self", "delegate"]
    assert question.allow_free_text is True
