from __future__ import annotations

import pytest

from app.requirements.contracts.request import FeedbackEdit, IdentitySourceAnswer
from app.requirements.modeling.specifications import (
    apply_identity_source_overrides,
    identity_source_question,
)
from app.requirements.orchestration import feedback_gates


def _spec(kind: str = "unresolved") -> dict:
    return {
        "use_case_id": "UC1",
        "name": "Enroll in a course",
        "requirement_ids": ["FR1"],
        "public_contract": {"identity_obligations": [
            {"obligation_ref": "ob_identify", "obligation": "identify",
             "subject": "student", "identity_source_kind": kind,
             "requirement_ids": ["FR1"]},
            {"obligation_ref": "ob_auth", "obligation": "authenticate",
             "identity_source_kind": "unresolved", "requirement_ids": ["FR1"]},
        ]},
    }


def test_question_options_use_only_same_use_case_minted_authenticate_refs() -> None:
    question = identity_source_question({"use_case_specs": [_spec()]})

    assert question["useCaseId"] == "UC1"
    assert question["useCaseName"] == "Enroll in a course"
    assert question["obligationRef"] == "ob_identify"
    assert question["identitySubject"] == "student"
    assert "UC1" in question["prompt"]
    assert "Enroll in a course" in question["prompt"]
    assert "student" in question["prompt"]
    assert question["requirementIds"] == ["FR1"]
    assert {option["id"] for option in question["options"]} == {
        "caller_input", "system_result", "authenticated_context:ob_auth"
    }


def test_typed_answer_patches_exact_spec_and_persists_override(monkeypatch) -> None:
    state = {
        "use_case_specs": [_spec()],
        "identity_source_question": identity_source_question({"use_case_specs": [_spec()]}),
        "semantic_ambiguity_question": None,
    }
    monkeypatch.setattr(feedback_gates, "_ask", lambda *args, **kwargs: IdentitySourceAnswer(
        use_case_id="UC1", obligation_ref="ob_identify",
        identity_source_kind="authenticated_context",
        source_authenticate_obligation_ref="ob_auth",
    ))
    monkeypatch.setattr(feedback_gates, "check_specs", lambda state, **kwargs: {
        "spec_report": {}, "identity_source_question": None,
        "semantic_ambiguity_question": None,
    })

    result = feedback_gates.gate_specs(state)

    obligation = result["use_case_specs"][0]["public_contract"]["identity_obligations"][0]
    assert obligation["identity_source_kind"] == "authenticated_context"
    assert obligation["source_authenticate_obligation_ref"] == "ob_auth"
    assert result["identity_source_overrides"]["ob_identify"] == {
        "identity_source_kind": "authenticated_context",
        "source_authenticate_obligation_ref": "ob_auth",
    }


def test_natural_spec_feedback_returns_recomputed_identity_question(monkeypatch) -> None:
    question = identity_source_question({"use_case_specs": [_spec()]})
    monkeypatch.setattr(feedback_gates, "_ask", lambda *args, **kwargs: FeedbackEdit(
        stage="specs", scope="local", target_ids=["UC1"],
        instruction="Use the authenticated session identity.",
    ))

    def apply_feedback(state, _answer, *, up_to):
        assert up_to == "specs"
        state["use_case_specs"] = [_spec()]

    monkeypatch.setattr(feedback_gates, "apply_feedback_upto", apply_feedback)
    monkeypatch.setattr(feedback_gates, "check_specs", lambda state: {
        "spec_report": {},
        "identity_source_question": question,
        "semantic_ambiguity_question": None,
    })

    result = feedback_gates.gate_specs({
        "use_case_specs": [_spec("caller_input")],
        "identity_source_question": None,
    })

    assert result["gate_route"] == "loop"
    assert result["identity_source_question"] == question


def test_answered_source_override_survives_local_regeneration() -> None:
    regenerated = _spec()
    applied = apply_identity_source_overrides([regenerated], {
        "ob_identify": {
            "identity_source_kind": "authenticated_context",
            "source_authenticate_obligation_ref": "ob_auth",
        }
    })
    identify = applied[0]["public_contract"]["identity_obligations"][0]
    assert identify["identity_source_kind"] == "authenticated_context"
    assert identify["source_authenticate_obligation_ref"] == "ob_auth"


@pytest.mark.parametrize("replacement_ref", ["ob_new_auth", "ob_foreign_auth", None])
def test_stale_authenticated_source_override_reopens_question(replacement_ref) -> None:
    regenerated = _spec()
    obligations = regenerated["public_contract"]["identity_obligations"]
    if replacement_ref is None:
        obligations.pop()
    else:
        obligations[1]["obligation_ref"] = replacement_ref

    applied = apply_identity_source_overrides([regenerated], {
        "ob_identify": {
            "identity_source_kind": "authenticated_context",
            "source_authenticate_obligation_ref": "ob_auth",
        }
    })

    identify = applied[0]["public_contract"]["identity_obligations"][0]
    assert identify["identity_source_kind"] == "unresolved"
    assert "source_authenticate_obligation_ref" not in identify
    reopened = identity_source_question({"use_case_specs": applied})
    assert reopened is not None
    assert reopened["obligationRef"] == "ob_identify"
    assert "authenticated_context:ob_auth" not in {
        option["id"] for option in reopened["options"]
    }


def test_blank_answer_cannot_advance_unresolved_mandatory_source(monkeypatch) -> None:
    question = identity_source_question({"use_case_specs": [_spec()]})
    monkeypatch.setattr(feedback_gates, "_ask", lambda *args, **kwargs: "")

    result = feedback_gates.gate_specs({
        "use_case_specs": [_spec()], "identity_source_question": question,
    })

    assert result["gate_route"] == "loop"
    assert result["identity_source_question"] == question


def test_identity_answer_requires_authenticate_ref_only_for_authenticated_context() -> None:
    with pytest.raises(ValueError):
        IdentitySourceAnswer(
            use_case_id="UC1", obligation_ref="ob_identify",
            identity_source_kind="authenticated_context",
        )
    with pytest.raises(ValueError):
        IdentitySourceAnswer(
            use_case_id="UC1", obligation_ref="ob_identify",
            identity_source_kind="caller_input",
            source_authenticate_obligation_ref="ob_auth",
        )
