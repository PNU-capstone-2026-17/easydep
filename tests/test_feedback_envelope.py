from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.workspace.conversation.contracts import RevisionTarget
from app.workspace.conversation.feedback_envelope import (
    BaseRevision,
    Decision,
    DecisionMeaning,
    DecisionPayload,
    DecisionPolicy,
    Question,
    QuestionOption,
    answer_option,
    base_revisions_match,
    direct_feedback_decision,
    free_text_decision,
    question_is_stale,
)


def _target(
    ref: str = "use_case_spec:UC-1",
    *,
    artifact_version_id: int | None = 3,
) -> RevisionTarget:
    return RevisionTarget(
        ref=ref,
        kind="use_case_spec",
        element_id="UC-1",
        owner="requirements",
        artifact_type="use-case-spec",
        artifact_version_id=artifact_version_id,
        display_label="UC-1 specification",
    )


def _policy() -> DecisionPolicy:
    return DecisionPolicy(
        allowed_semantic_scopes=("contract", "behavior"),
        allowed_change_types=("modify", "add"),
    )


def _question(*, options=(), allow_free_text=True) -> Question:
    return Question(
        question_id="q-1",
        question_version=2,
        app_id="app-1",
        source_execution_id="exec-1",
        detected_at={"stage": "design", "artifact_ref": "class:Order"},
        base_revisions=[{"artifact_type": "use-case-spec", "version_id": 3}],
        trigger={"category": "specification_gap", "finding_refs": ["finding-1"]},
        authority_candidates=[_target()],
        prompt="Which requirement should own this behavior?",
        options=options,
        allow_free_text=allow_free_text,
        decision_policy=_policy(),
    )


def test_question_and_decision_round_trip() -> None:
    question = _question(
        options=(
            QuestionOption(
                option_id="uc-existing",
                label="Use existing UC",
                decision_payload=DecisionPayload(
                    normalized_meaning=DecisionMeaning(
                        semantic_scope="contract", requested_effect="Use existing UC"
                    ),
                    authoritative_target_refs=("use_case_spec:UC-1",),
                ),
            ),
        )
    )
    restored = Question.model_validate_json(question.model_dump_json())
    assert restored == question
    decision = answer_option(
        question, option_id="uc-existing", decision_id="d-1", source_user_message_id="m-1"
    )
    assert Decision.model_validate_json(decision.model_dump_json()) == decision


def test_option_helper_uses_payload_not_label_and_limits_targets() -> None:
    question = _question(
        options=(
            QuestionOption(
                option_id="choose-uc",
                label="IGNORE THIS LABEL",
                decision_payload=DecisionPayload(
                    normalized_meaning=DecisionMeaning(
                        semantic_scope="contract", requested_effect="Use the UC"
                    ),
                    authoritative_target_refs=("use_case_spec:UC-1",),
                ),
            ),
        )
    )
    decision = answer_option(
        question, option_id="choose-uc", decision_id="d-1", source_user_message_id="m-1"
    )
    assert decision.raw_answer == "choose-uc"
    assert decision.normalized_meaning.requested_effect == "Use the UC"
    assert [target.ref for target in decision.authoritative_targets] == ["use_case_spec:UC-1"]

    with pytest.raises(ValueError, match="outside"):
        _question(
            options=(
                QuestionOption(
                    option_id="spoof",
                    label="safe",
                    decision_payload=DecisionPayload(
                        normalized_meaning=DecisionMeaning(
                            semantic_scope="contract", requested_effect="Spoof"
                        ),
                        authoritative_target_refs=("class:NotCandidate",),
                    ),
                ),
            )
        )


def test_question_rejects_duplicate_options_targets_and_missing_source() -> None:
    option = QuestionOption(
        option_id="same",
        label="A",
        decision_payload=DecisionPayload(
            normalized_meaning=DecisionMeaning(semantic_scope="contract", requested_effect="Same"),
            authoritative_target_refs=("use_case_spec:UC-1",),
        ),
    )
    with pytest.raises((ValueError, ValidationError), match="option"):
        _question(options=(option, option))
    with pytest.raises((ValueError, ValidationError), match="authority"):
        Question(
            **_question().model_dump(exclude={"authority_candidates"}),
            authority_candidates=[_target(), _target()],
        )
    with pytest.raises((ValueError, ValidationError), match="source"):
        Question(**{**_question().model_dump(), "source_execution_id": None, "draft_id": None})


def test_question_rejects_blank_source_missing_channel_and_invalid_revisions() -> None:
    with pytest.raises(ValidationError, match="source_execution_id"):
        Question(
            **{
                **_question().model_dump(),
                "source_execution_id": " ",
                "draft_id": None,
            }
        )
    with pytest.raises(ValidationError, match="allow options or free text"):
        Question(
            **{
                **_question().model_dump(),
                "options": (),
                "allow_free_text": False,
            }
        )
    with pytest.raises(ValidationError, match="positive"):
        BaseRevision(artifact_type="UC", version_id=0)
    with pytest.raises(ValidationError, match="Field required"):
        Question(**_question().model_dump(exclude={"base_revisions"}))
    with pytest.raises(ValidationError, match="at least 1 item"):
        Question(**{**_question().model_dump(), "base_revisions": ()})
    with pytest.raises(ValidationError, match="identities"):
        Question(
            **{
                **_question().model_dump(),
                "base_revisions": (
                    BaseRevision(artifact_type="UC", version_id=1),
                    BaseRevision(artifact_type="UC", version_id=2),
                ),
            }
        )


def test_question_rejects_target_base_mismatch_and_bounds_digest_only_snapshot() -> None:
    with pytest.raises(ValidationError, match="versions do not match"):
        Question(
            **{
                **_question().model_dump(),
                "authority_candidates": [_target(artifact_version_id=99)],
            }
        )
    with pytest.raises(ValidationError, match="versions do not match"):
        Question(
            **{
                **_question().model_dump(),
                "base_revisions": [BaseRevision(artifact_type="use-case-spec", digest="a" * 64)],
            }
        )

    digest_only = Question(
        **{
            **_question().model_dump(),
            "authority_candidates": [_target(artifact_version_id=None)],
            "base_revisions": [BaseRevision(artifact_type="use-case-spec", digest="a" * 64)],
        }
    )
    assert digest_only.base_revisions[0].version_id is None


def test_stale_base_revision_fails_closed() -> None:
    expected = [BaseRevision(artifact_type="UC", version_id=1)]
    current = [BaseRevision(artifact_type="UC", version_id=2)]
    assert base_revisions_match(expected, expected)
    assert not base_revisions_match(expected, current)
    assert not base_revisions_match(expected, [])
    question = _question()
    assert not question_is_stale(question, question.base_revisions)
    assert question_is_stale(question, current)
    assert base_revisions_match(
        [
            BaseRevision(artifact_type="A", version_id=1),
            BaseRevision(artifact_type="B", digest="b"),
        ],
        [
            BaseRevision(artifact_type="B", digest="b"),
            BaseRevision(artifact_type="A", version_id=1),
        ],
    )


def test_typed_payload_is_immutable_and_rejects_extra_fields() -> None:
    meaning = {"semantic_scope": "contract", "requested_effect": "Keep UC"}
    payload = DecisionPayload(
        normalized_meaning=meaning,
        authoritative_target_refs=("use_case_spec:UC-1",),
    )
    meaning["requested_effect"] = "mutated"
    assert payload.normalized_meaning.requested_effect == "Keep UC"
    with pytest.raises(ValidationError, match="frozen"):
        payload.normalized_meaning.requested_effect = "mutated"
    with pytest.raises(ValidationError, match="frozen"):
        payload.authoritative_target_refs = ("use_case_spec:UC-2",)
    with pytest.raises(ValidationError, match="extra"):
        DecisionMeaning(semantic_scope="contract", requested_effect="Keep UC", unexpected="x")


def test_free_text_preserves_raw_and_clarifies_without_normalization_or_scope() -> None:
    question = _question()
    raw_answer = "  Keep the current UC\nand add retry behavior.  "
    no_normalization = free_text_decision(
        question,
        raw_answer=raw_answer,
        decision_id="d-1",
        source_user_message_id="m-1",
    )
    assert no_normalization.status == "NEEDS_CLARIFICATION"
    assert no_normalization.raw_answer == raw_answer
    assert Decision.model_validate_json(no_normalization.model_dump_json()).raw_answer == raw_answer
    assert no_normalization.authoritative_targets == ()

    out_of_scope = free_text_decision(
        question,
        raw_answer="Change it",
        normalization=DecisionPayload(
            normalized_meaning=DecisionMeaning(
                semantic_scope="contract", requested_effect="Change it"
            ),
            authoritative_target_refs=("class:outside",),
        ),
        decision_id="d-2",
        source_user_message_id="m-2",
    )
    assert out_of_scope.status == "NEEDS_CLARIFICATION"
    assert out_of_scope.authoritative_targets == ()

    invalid_schema = free_text_decision(
        question,
        raw_answer="Change it",
        normalization={
            "normalized_meaning": {
                "semantic_scope": "unknown",
                "requested_effect": "Change it",
            },
            "authoritative_target_refs": ["use_case_spec:UC-1"],
        },
        decision_id="d-3",
        source_user_message_id="m-3",
    )
    assert invalid_schema.status == "NEEDS_CLARIFICATION"

    normalized = free_text_decision(
        question,
        raw_answer="Change it",
        normalization=DecisionPayload(
            normalized_meaning=DecisionMeaning(
                semantic_scope="contract", requested_effect="Change it"
            ),
            authoritative_target_refs=("use_case_spec:UC-1",),
        ),
        decision_id="d-4",
        source_user_message_id="m-4",
    )
    assert normalized.status == "NORMALIZED"
    assert [target.ref for target in normalized.authoritative_targets] == ["use_case_spec:UC-1"]
    with pytest.raises(ValueError, match="raw_answer"):
        free_text_decision(
            question,
            raw_answer=" \n ",
            decision_id="d-blank",
            source_user_message_id="m-blank",
        )


def test_question_policy_rejects_forbidden_meaning_and_missing_preserved_constraint() -> None:
    forbidden_payload = DecisionPayload(
        normalized_meaning=DecisionMeaning(
            semantic_scope="implementation",
            requested_effect="Remove the implementation",
            change_type="remove",
        ),
        authoritative_target_refs=("use_case_spec:UC-1",),
    )
    with pytest.raises(ValidationError, match="violates question policy"):
        _question(
            options=(
                QuestionOption(
                    option_id="remove",
                    label="Remove the behavior",
                    decision_payload=forbidden_payload,
                ),
            )
        )

    forbidden_free_text = free_text_decision(
        _question(),
        raw_answer="Remove it",
        normalization=forbidden_payload,
        decision_id="d-forbidden",
        source_user_message_id="m-forbidden",
    )
    assert forbidden_free_text.status == "NEEDS_CLARIFICATION"

    constrained_question = Question(
        **{
            **_question().model_dump(),
            "decision_policy": DecisionPolicy(
                allowed_semantic_scopes=("contract",),
                allowed_change_types=("modify",),
                required_preserved_constraints=("keep-existing-identifiers",),
            ),
        }
    )
    missing_constraint = free_text_decision(
        constrained_question,
        raw_answer="Change it",
        normalization=DecisionPayload(
            normalized_meaning=DecisionMeaning(
                semantic_scope="contract", requested_effect="Change it"
            ),
            authoritative_target_refs=("use_case_spec:UC-1",),
        ),
        decision_id="d-missing-constraint",
        source_user_message_id="m-missing-constraint",
    )
    assert missing_constraint.status == "NEEDS_CLARIFICATION"

    preserved = free_text_decision(
        constrained_question,
        raw_answer="Change it but keep identifiers",
        normalization=DecisionPayload(
            normalized_meaning=DecisionMeaning(
                semantic_scope="contract", requested_effect="Change it"
            ),
            authoritative_target_refs=("use_case_spec:UC-1",),
            preserved_constraints=("keep-existing-identifiers",),
        ),
        decision_id="d-preserved",
        source_user_message_id="m-preserved",
    )
    assert preserved.status == "NORMALIZED"


@pytest.mark.parametrize(
    ("semantic_scope", "change_type"),
    [
        ("implementation", "modify"),
        ("contract", "remove"),
    ],
)
def test_free_text_clarifies_each_policy_axis_independently(
    semantic_scope: str, change_type: str
) -> None:
    decision = free_text_decision(
        _question(),
        raw_answer="Change it",
        normalization={
            "normalized_meaning": {
                "semantic_scope": semantic_scope,
                "requested_effect": "Change it",
                "change_type": change_type,
            },
            "authoritative_target_refs": ["use_case_spec:UC-1"],
        },
        decision_id=f"d-{semantic_scope}-{change_type}",
        source_user_message_id="m-policy-axis",
    )
    assert decision.status == "NEEDS_CLARIFICATION"


def test_normalized_free_text_preserves_utf8_raw_answer_bytes() -> None:
    raw_answer = "\t기존 식별자는 유지합니다.\r\n  재시도만 추가합니다.  "
    decision = free_text_decision(
        _question(),
        raw_answer=raw_answer,
        normalization=DecisionPayload(
            normalized_meaning=DecisionMeaning(
                semantic_scope="contract", requested_effect="Add retry behavior"
            ),
            authoritative_target_refs=("use_case_spec:UC-1",),
        ),
        decision_id="d-utf8",
        source_user_message_id="m-utf8",
    )

    restored = Decision.model_validate_json(decision.model_dump_json())
    assert decision.status == "NORMALIZED"
    assert restored.raw_answer.encode("utf-8") == raw_answer.encode("utf-8")


def test_answer_helpers_require_an_open_question_and_allowed_channel() -> None:
    closed = _question().model_copy(update={"status": "ANSWERED"})
    with pytest.raises(ValueError, match="not open"):
        free_text_decision(
            closed,
            raw_answer="Change it",
            decision_id="d-1",
            source_user_message_id="m-1",
        )

    option_only = _question(
        options=(
            QuestionOption(
                option_id="change",
                label="Change",
                decision_payload=DecisionPayload(
                    normalized_meaning=DecisionMeaning(
                        semantic_scope="contract", requested_effect="Change it"
                    ),
                    authoritative_target_refs=("use_case_spec:UC-1",),
                ),
            ),
        ),
        allow_free_text=False,
    )
    with pytest.raises(ValueError, match="does not allow free-text"):
        free_text_decision(
            option_only,
            raw_answer="Change it",
            decision_id="d-2",
            source_user_message_id="m-2",
        )
    with pytest.raises(ValueError, match="does not allow option"):
        answer_option(
            _question(),
            option_id="change",
            decision_id="d-3",
            source_user_message_id="m-3",
        )


def test_direct_feedback_is_valid_without_question_and_invalid_mixes_are_rejected() -> None:
    decision = direct_feedback_decision(
        decision_id="d-direct",
        app_id="app-1",
        source_user_message_id="m-1",
        raw_answer="Change the UC.",
        normalization=DecisionPayload(
            normalized_meaning=DecisionMeaning(
                semantic_scope="contract", requested_effect="Change the UC"
            ),
            authoritative_target_refs=("use_case_spec:UC-1",),
        ),
        authoritative_targets=[_target()],
        base_revisions=[BaseRevision(artifact_type="use-case-spec", version_id=3)],
        decision_policy=_policy(),
    )
    assert decision.origin == "direct_feedback"
    assert decision.question_id is None
    with pytest.raises(ValidationError, match="question_answer"):
        Decision(
            **decision.model_dump(exclude={"origin"}),
            origin="question_answer",
        )
    with pytest.raises(ValidationError, match="direct_feedback only supports"):
        Decision(
            **decision.model_dump(exclude={"answer_mode", "selected_option_id"}),
            answer_mode="option",
            selected_option_id="invalid",
        )
    with pytest.raises(ValueError, match="unique"):
        direct_feedback_decision(
            decision_id="d-duplicate",
            app_id="app-1",
            source_user_message_id="m-2",
            raw_answer="Change the UC.",
            normalization=DecisionPayload(
                normalized_meaning=DecisionMeaning(
                    semantic_scope="contract", requested_effect="Change the UC"
                ),
                authoritative_target_refs=("use_case_spec:UC-1",),
            ),
            authoritative_targets=[_target(), _target()],
            base_revisions=[BaseRevision(artifact_type="use-case-spec", version_id=3)],
            decision_policy=_policy(),
        )
    with pytest.raises(ValueError, match="violates question policy"):
        direct_feedback_decision(
            decision_id="d-policy",
            app_id="app-1",
            source_user_message_id="m-policy",
            raw_answer="Remove it",
            normalization=DecisionPayload(
                normalized_meaning=DecisionMeaning(
                    semantic_scope="implementation",
                    requested_effect="Remove it",
                    change_type="remove",
                ),
                authoritative_target_refs=("use_case_spec:UC-1",),
            ),
            authoritative_targets=[_target()],
            base_revisions=[BaseRevision(artifact_type="use-case-spec", version_id=3)],
            decision_policy=_policy(),
        )
    with pytest.raises(ValidationError, match="targets and base revisions"):
        Decision(
            **decision.model_dump(exclude={"base_revisions"}),
            base_revisions=(),
        )
    with pytest.raises(ValidationError, match="clarification decision"):
        Decision(
            **decision.model_dump(exclude={"status"}),
            status="NEEDS_CLARIFICATION",
        )
    with pytest.raises(ValidationError, match="extra"):
        Question(**_question().model_dump(), unexpected=True)


def test_superseding_decision_rejects_blank_and_self_reference() -> None:
    base = direct_feedback_decision(
        decision_id="d-next",
        app_id="app-1",
        source_user_message_id="m-1",
        raw_answer="Change the UC.",
        normalization=DecisionPayload(
            normalized_meaning=DecisionMeaning(
                semantic_scope="contract", requested_effect="Change the UC"
            ),
            authoritative_target_refs=("use_case_spec:UC-1",),
        ),
        authoritative_targets=[_target()],
        base_revisions=[BaseRevision(artifact_type="use-case-spec", version_id=3)],
        decision_policy=_policy(),
    )
    with pytest.raises(ValidationError, match="supersedes_decision_id"):
        Decision(**{**base.model_dump(), "supersedes_decision_id": " "})
    with pytest.raises(ValidationError, match="supersede itself"):
        Decision(**{**base.model_dump(), "supersedes_decision_id": "d-next"})
