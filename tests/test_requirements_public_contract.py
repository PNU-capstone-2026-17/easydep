import pytest

from app.requirements.contracts.state import UseCaseItem
from app.requirements.modeling.specifications import (
    _semantic_findings,
    _accepted_public_contract_proposal,
    normalize_specification,
    spec_review_payload,
    validate_specification,
)
from app.requirements.modeling import validation
from app.requirements.knowledge import rules
from app.requirements.prompts import IDENTITY_SOURCE_INSTRUCTIONS
from app.requirements.schemas import IdentityObligation, UseCaseSpec


def test_accepted_contract_projects_to_proposal_fields_for_regeneration() -> None:
    projected = _accepted_public_contract_proposal({
        "identity_obligations": [
            {"obligation_ref": "ob_id", "subject_ref": "sub_id", "subject": "student",
             "obligation": "identify", "identity_source_kind": "authenticated_context",
             "requirement_ids": ["R1"], "source_authenticate_obligation_ref": "ob_auth"},
            {"obligation_ref": "ob_auth", "subject_ref": "sub_auth", "subject": "session",
             "obligation": "authenticate", "identity_source_kind": "unresolved",
             "requirement_ids": ["R1"]},
        ],
        "required_values": [{
            "value_ref": "val_id", "name": "student id", "source": "authenticated_actor_context",
            "value_type": "identifier", "usage": "control", "requirement_ids": ["R1"],
            "identity_obligation_ref": "ob_id",
        }],
    })

    assert projected == {
        "identity_obligations": [
            {"subject": "student", "obligation": "identify", "requirement_ids": ["R1"],
             "source_authenticate_obligation_index": 2},
            {"subject": "session", "obligation": "authenticate", "requirement_ids": ["R1"]},
        ],
        "required_values": [{
            "name": "student id", "source": "authenticated_actor_context",
            "value_type": "identifier", "usage": "control", "requirement_ids": ["R1"],
            "identity_obligation_index": 1,
        }],
    }


def test_spec_projects_grounded_identity_and_required_value_obligations() -> None:
    spec = UseCaseSpec.model_validate(
        {
            "trigger": "A clerk submits a request.",
            "public_contract": {
                "identity_obligations": [
                    {
                        "subject": "the customer",
                        "obligation": "identify",
                        "requirement_ids": ["R1"],
                    },
                    {
                        "subject": "the clerk",
                        "obligation": "act_on_behalf",
                        "requirement_ids": ["R2"],
                    },
                ],
                "required_values": [
                    {
                        "name": "request details",
                        "source": "caller_input",
                        "value_type": "object",
                        "usage": "control",
                        "requirement_ids": ["R1"],
                    },
                    {
                        "name": "request reference",
                        "source": "system_result",
                        "value_type": "identifier",
                        "usage": "result",
                        "requirement_ids": ["R1"],
                    },
                ],
            },
        }
    )
    use_case = UseCaseItem(
        id="UC1",
        name="Submit a request",
        primary_actor="Clerk",
        supporting_actors=[],
        level="user_goal",
        goal="Submit a request for a customer.",
        requirement_ids=["R1", "R2"],
        nfr_ids=[],
    )

    item = normalize_specification(spec, use_case)
    contract = item["public_contract"]
    assert contract["schema_version"] == "PublicBehaviorContract/v1"
    assert contract["identity_obligations"][1]["obligation"] == "act_on_behalf"
    assert contract["identity_obligations"][0]["obligation_ref"].startswith("ob_")
    assert contract["identity_obligations"][0]["subject_ref"].startswith("sub_")
    assert contract["identity_obligations"][0]["requirement_ids"] == ["R1"]
    assert contract["required_values"][0]["source"] == "caller_input"
    assert contract["required_values"][1] == {
        "value_ref": contract["required_values"][1]["value_ref"],
        "name": "request reference",
        "source": "system_result",
        "value_type": "identifier",
        "usage": "result",
        "requirement_ids": ["R1"],
    }
    assert contract["required_values"][0]["value_ref"].startswith("val_")

    payload = spec_review_payload(item, requirements=[{"id": "R1", "text": "Clerk identifies customer and supplies request details."}])
    assert payload["public_contract"] == contract


def test_identity_obligation_refs_ignore_subject_display_label() -> None:
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1"], nfr_ids=[],
    )

    def normalize(subject: str) -> dict:
        proposal = UseCaseSpec.model_validate({
            "trigger": "A clerk submits.",
            "public_contract": {"identity_obligations": [{
                "subject": subject, "obligation": "identify", "requirement_ids": ["R1"],
            }]},
        })
        return normalize_specification(proposal, use_case)["public_contract"]["identity_obligations"][0]

    first = normalize("the customer")
    renamed = normalize("the account holder")
    assert first["obligation_ref"] == renamed["obligation_ref"]
    assert first["subject_ref"] == renamed["subject_ref"]
    assert first["subject"] != renamed["subject"]


def test_disjoint_identity_obligations_receive_distinct_opaque_refs() -> None:
    proposal = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": [
            {"subject": "customer", "obligation": "identify", "source_authenticate_obligation_index": 2, "requirement_ids": ["R1"]},
            {"subject": "clerk", "obligation": "authenticate", "requirement_ids": ["R2"]},
        ]},
    })
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1", "R2"], nfr_ids=[],
    )

    obligations = normalize_specification(proposal, use_case)["public_contract"]["identity_obligations"]
    assert len({entry["obligation_ref"] for entry in obligations}) == 2
    assert all(entry["requirement_ids"] for entry in obligations)
    assert all(entry["obligation_ref"].startswith("ob_") for entry in obligations)
    assert all(entry["subject_ref"].startswith("sub_") for entry in obligations)


def test_obligations_sharing_evidence_keep_distinct_position_refs():
    use_case = UseCaseItem(
        id="UC9", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1"], nfr_ids=[],
    )

    def normalize(obligations: list[dict]) -> list[dict]:
        proposal = UseCaseSpec.model_validate({
            "trigger": "A clerk submits.",
            "public_contract": {"identity_obligations": obligations},
        })
        return normalize_specification(proposal, use_case)["public_contract"][
            "identity_obligations"
        ]

    first = normalize([
        {"subject": "customer", "obligation": "identify", "requirement_ids": ["R1"]},
        {"subject": "delegate", "obligation": "identify", "requirement_ids": ["R1"]},
    ])
    edited = normalize([
        {"subject": "account holder", "obligation": "authenticate", "requirement_ids": ["R1"]},
        {"subject": "representative", "obligation": "act_on_behalf", "requirement_ids": ["R1"]},
    ])
    retained = normalize([{
        "subject": "customer", "subject_ref": "sub_0123456789abcdef0123",
        "obligation": "identify", "requirement_ids": ["R1"],
    }])

    assert len({item["subject_ref"] for item in first}) == 2
    assert len({item["obligation_ref"] for item in first}) == 2
    assert [item["subject_ref"] for item in first] == [item["subject_ref"] for item in edited]
    assert [item["obligation_ref"] for item in first] == [item["obligation_ref"] for item in edited]
    assert all(item["requirement_ids"] == ["R1"] for item in first + edited)
    assert retained[0]["subject_ref"] == "sub_0123456789abcdef0123"


def test_proposal_uses_required_value_source_and_resolves_exact_authenticate_index():
    proposal = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": [
            {"subject": "customer", "obligation": "identify", "source_authenticate_obligation_index": 2, "requirement_ids": ["R1"]},
            {"subject": "customer session", "obligation": "authenticate", "requirement_ids": ["R1"]},
            {"subject": "staff session", "obligation": "authenticate", "requirement_ids": ["R2"]},
        ], "required_values": [{
            "identity_obligation_index": 1, "name": "customer identifier",
            "source": "authenticated_actor_context", "value_type": "identifier",
            "usage": "control", "requirement_ids": ["R1"],
        }]},
    })
    assert "identity_source_kind" not in IdentityObligation.model_fields
    assert "source_authenticate_obligation_ref" not in IdentityObligation.model_fields
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1", "R2"], nfr_ids=[],
    )

    contract = normalize_specification(proposal, use_case)["public_contract"]
    identify, chosen_auth, _ = contract["identity_obligations"]
    assert identify["identity_source_kind"] == "authenticated_context"
    assert identify["source_authenticate_obligation_ref"] == chosen_auth["obligation_ref"]


@pytest.mark.parametrize("source", ["caller_input", "system_result"])
def test_identify_source_kind_is_projected_from_linked_required_value(source):
    spec = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": [{
            "subject": "customer", "obligation": "identify", "requirement_ids": ["R1"],
        }], "required_values": [{
            "identity_obligation_index": 1, "name": "customer identifier", "source": source,
            "value_type": "identifier", "usage": "control", "requirement_ids": ["R1"],
        }]},
    })
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1"], nfr_ids=[],
    )
    identified = normalize_specification(spec, use_case)["public_contract"]["identity_obligations"][0]
    assert identified["identity_source_kind"] == source
    assert "source_authenticate_obligation_ref" not in identified


def test_invalid_authenticate_source_ref_is_rejected_by_contract_validation():
    findings = validate_specification({
        "requirement_ids": ["R1"],
        "public_contract": {"identity_obligations": [
            {"obligation_ref": "ob_aaaaaaaaaaaaaaaaaaaa", "subject": "customer",
             "obligation": "identify", "identity_source_kind": "authenticated_context",
             "source_authenticate_obligation_ref": "ob_bbbbbbbbbbbbbbbbbbbb", "requirement_ids": ["R1"]},
            {"obligation_ref": "ob_cccccccccccccccccccc", "subject": "clerk",
             "obligation": "authenticate", "requirement_ids": ["R1"]},
        ], "required_values": []},
    })

    assert any("same-use-case authenticate" in item for item in findings)


def test_old_spec_review_payload_remains_compatible_without_public_contract() -> None:
    payload = spec_review_payload(
        {
            "trigger": "A user starts.",
            "preconditions": [],
            "main_scenario": [],
            "extensions": [],
            "success_guarantee": [],
            "minimal_guarantee": [],
        }
    )

    assert "public_contract" not in payload


def test_public_contract_references_must_be_linked_functional_requirements() -> None:
    findings = validate_specification(
        {
            "requirement_ids": ["R1"],
            "nfr_ids": ["N1"],
            "public_contract": {
                "identity_obligations": [
                    {
                        "subject": "customer",
                        "obligation": "authenticate",
                        "requirement_ids": ["N1"],
                    }
                ],
                "required_values": [
                    {
                        "name": "account number",
                        "source": "caller_input",
                        "value_type": "identifier",
                        "usage": "control",
                        "requirement_ids": ["R2"],
                    }
                ],
            },
        }
    )

    contract_findings = [finding for finding in findings if "public-contract-integrity" in finding]
    assert len(contract_findings) == 2
    assert any("N1" in finding for finding in contract_findings)
    assert any("R2" in finding for finding in contract_findings)


def test_public_contract_flags_duplicate_and_conflicting_value_declarations() -> None:
    base = {
        "name": "account number",
        "source": "caller_input",
        "value_type": "identifier",
        "usage": "control",
        "requirement_ids": ["R1"],
    }
    duplicate_findings = validate_specification(
        {
            "requirement_ids": ["R1"],
            "public_contract": {
                "identity_obligations": [],
                "required_values": [base, dict(base)],
            },
        }
    )
    conflicting_findings = validate_specification(
        {
            "requirement_ids": ["R1"],
            "public_contract": {
                "identity_obligations": [],
                "required_values": [base, {**base, "source": "authenticated_actor_context"}],
            },
        }
    )

    duplicate_contract_findings = [
        finding for finding in duplicate_findings if "public-contract-integrity" in finding
    ]
    conflict_contract_findings = [
        finding for finding in conflicting_findings
        if "public-contract-integrity" in finding and "Required value" in finding
    ]
    assert len(duplicate_contract_findings) == 1
    assert "declared more than once" in duplicate_contract_findings[0]
    assert len(conflict_contract_findings) == 1
    assert "conflicting" in conflict_contract_findings[0]


@pytest.mark.parametrize(
    ("identity_obligation_index", "expects_finding"),
    [
        (1, False),
        (None, True),
        (2, True),
    ],
)
def test_authenticated_identifier_resolves_exact_identify_index(
    identity_obligation_index: int | None, expects_finding: bool,
) -> None:
    use_case = UseCaseItem(
        id="UC42", name="Select", primary_actor="User", supporting_actors=[],
        level="user_goal", goal="Select", requirement_ids=["R1"], nfr_ids=[],
    )
    proposal = UseCaseSpec.model_validate({
        "trigger": "A user proceeds.",
        "public_contract": {
            "identity_obligations": [
                {"obligation_ref": "ob_11111111111111111111", "subject": "subject",
                 "obligation": "identify", "source_authenticate_obligation_index": 2,
                 "requirement_ids": ["R1"]},
                {"obligation_ref": "ob_22222222222222222222", "subject": "session",
                 "obligation": "authenticate", "requirement_ids": ["R1"]},
            ],
            "required_values": [{
                "identity_obligation_index": identity_obligation_index,
                "name": "subject key", "source": "authenticated_actor_context",
                "value_type": "identifier", "usage": "control", "requirement_ids": ["R1"],
            }],
        },
    })
    normalized = normalize_specification(proposal, use_case)
    value = normalized["public_contract"]["required_values"][0]
    identify = normalized["public_contract"]["identity_obligations"][0]
    assert "identity_obligation_index" not in value
    if expects_finding:
        if identity_obligation_index is None:
            assert "identity_obligation_ref" not in value
        else:
            assert value.get("identity_obligation_ref") == normalized["public_contract"]["identity_obligations"][1]["obligation_ref"]
    else:
        assert value.get("identity_obligation_ref") == identify["obligation_ref"]
    findings = [item for item in validate_specification(normalized) if "public-contract-integrity" in item]
    assert bool(findings) is expects_finding


def test_required_value_source_prompt_distinguishes_origin_from_trust() -> None:
    source_description = UseCaseSpec.model_json_schema()["$defs"]["RequiredValue"]["properties"]["source"]["description"]

    assert "Classify where the value comes from" in source_description
    assert "explicitly obtained from the current authenticated session/actor context" in IDENTITY_SOURCE_INSTRUCTIONS
    assert "An ID the requirement says is read from the authenticated session is not caller_input" in IDENTITY_SOURCE_INSTRUCTIONS


def test_public_behavior_review_checks_value_source_against_requirement_evidence() -> None:
    statement = rules.rule("spec.public-behavior-completeness").statement

    assert "For every declared required value" in statement
    assert "matches the explicit origin stated in the linked requirements" in statement
    assert "direct correction of the `RequiredValue.source` field" in statement
    assert "`RequiredValue.identity_obligation_index`" in statement
    assert "`IdentityObligation.source_authenticate_obligation_index`" in statement
    assert "Do not direct repair of the derived accepted" in statement


def test_clean_broad_review_runs_focused_public_source_review_and_returns_finding() -> None:
    item = {
        "use_case_id": "UC1", "name": "Submit", "trigger": "A user submits.",
        "preconditions": [], "main_scenario": [], "extensions": [],
        "success_guarantee": [], "minimal_guarantee": [],
        "public_contract": {"identity_obligations": [], "required_values": []},
    }
    calls: list[tuple[object, bool]] = []

    def reviewer(stage, payload, *, prefix, source, subject, rule_ids=None, confirm_violations=False):
        calls.append((rule_ids, confirm_violations))
        if rule_ids != ("spec.public-behavior-completeness",):
            return validation.Review()
        return validation.Review(
            findings=["[semantic][spec.public-behavior-completeness] Required value source conflicts with requirement evidence."]
        )

    findings, status = _semantic_findings(
        item, requirements=[{"id": "R1", "text": "Session source evidence."}],
        review_call=reviewer,
    )

    assert status == validation.OK
    assert len(findings) == 1
    assert len(calls) == 2
    assert "spec.public-behavior-completeness" not in calls[0][0]
    assert calls[0][1] is True
    assert calls[1] == (("spec.public-behavior-completeness",), False)


def test_broad_semantic_finding_skips_focused_source_review() -> None:
    item = {
        "use_case_id": "UC1", "name": "Submit", "trigger": "A user submits.",
        "preconditions": [], "main_scenario": [], "extensions": [],
        "success_guarantee": [], "minimal_guarantee": [],
        "public_contract": {"identity_obligations": [], "required_values": []},
    }
    calls: list[object] = []

    def reviewer(stage, payload, *, prefix, source, subject, rule_ids=None, confirm_violations=False):
        calls.append(rule_ids)
        if rule_ids == ("spec.public-behavior-completeness",):
            return validation.Review()
        return validation.Review(findings=["[semantic] Existing broad finding."])

    findings, status = _semantic_findings(item, review_call=reviewer)

    assert status == validation.OK
    assert findings == ["[semantic] Existing broad finding."]
    assert len(calls) == 1
    assert "spec.public-behavior-completeness" not in calls[0]


def test_clean_broad_and_focused_reviews_remain_clean() -> None:
    item = {
        "use_case_id": "UC1", "name": "Submit", "trigger": "A user submits.",
        "preconditions": [], "main_scenario": [], "extensions": [],
        "success_guarantee": [], "minimal_guarantee": [],
        "public_contract": {"identity_obligations": [], "required_values": []},
    }
    calls: list[object] = []

    def reviewer(stage, payload, *, prefix, source, subject, rule_ids=None, confirm_violations=False):
        calls.append(rule_ids)
        return validation.Review()

    findings, status = _semantic_findings(item, review_call=reviewer)

    assert findings == []
    assert status == validation.OK
    assert len(calls) == 2
    assert "spec.public-behavior-completeness" not in calls[0]
    assert calls[1] == ("spec.public-behavior-completeness",)


def test_public_contract_allows_empty_obligations_and_missing_link_context() -> None:
    empty = validate_specification(
        {"requirement_ids": [], "public_contract": {"identity_obligations": [], "required_values": []}}
    )
    without_context = validate_specification(
        {
            "public_contract": {
                "identity_obligations": [],
                "required_values": [
                    {
                        "name": "account number",
                        "source": "caller_input",
                        "value_type": "identifier",
                        "usage": "control",
                        "requirement_ids": ["R1"],
                    }
                ],
            }
        }
    )

    assert not any("public-contract-integrity" in item for item in empty)
    assert not any("public-contract-integrity" in item for item in without_context)
