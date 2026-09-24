import pytest

from app.requirements.contracts.state import UseCaseItem
from app.requirements.modeling.specifications import (
    normalize_specification,
    spec_review_payload,
    validate_specification,
)
from app.requirements.schemas import UseCaseSpec


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
        "name": "request reference",
        "source": "system_result",
        "value_type": "identifier",
        "usage": "result",
        "requirement_ids": ["R1"],
    }

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
            {"subject": "customer", "obligation": "identify", "requirement_ids": ["R1"]},
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


def test_identify_authenticated_source_does_not_bind_to_sole_different_subject_authenticate():
    proposal = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": [
            {"subject": "renamed person", "obligation": "identify",
             "identity_source_kind": "authenticated_context", "requirement_ids": ["R1"]},
            {"subject": "other label", "obligation": "authenticate", "requirement_ids": ["R2"]},
        ]},
    })
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1", "R2"], nfr_ids=[],
    )
    items = normalize_specification(proposal, use_case)["public_contract"]["identity_obligations"]

    assert items[0]["identity_source_kind"] == "unresolved"
    assert "source_authenticate_obligation_ref" not in items[0]
    assert items[1]["obligation"] == "authenticate"


def test_identify_authenticated_source_accepts_explicit_exact_same_use_case_ref():
    chosen_ref = "ob_11111111111111111111"
    proposal = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": [
            {"subject": "customer", "obligation": "identify",
             "identity_source_kind": "authenticated_context",
             "source_authenticate_obligation_ref": chosen_ref, "requirement_ids": ["R1"]},
            {"obligation_ref": chosen_ref, "subject": "different label", "obligation": "authenticate",
             "requirement_ids": ["R2"]},
        ]},
    })
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1", "R2"], nfr_ids=[],
    )

    items = normalize_specification(proposal, use_case)["public_contract"]["identity_obligations"]
    assert items[0]["identity_source_kind"] == "authenticated_context"
    assert items[0]["source_authenticate_obligation_ref"] == chosen_ref


@pytest.mark.parametrize("auth_count", [0, 2])
def test_identify_authenticated_source_stays_unresolved_without_unique_authenticate(auth_count):
    obligations = [{"subject": "subject", "obligation": "identify",
                    "identity_source_kind": "authenticated_context", "requirement_ids": ["R1"]}]
    obligations.extend(
        {"subject": f"auth label {index}", "obligation": "authenticate", "requirement_ids": ["R1"]}
        for index in range(auth_count)
    )
    spec = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": obligations},
    })
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1"], nfr_ids=[],
    )

    identified = normalize_specification(spec, use_case)["public_contract"]["identity_obligations"][0]
    assert identified["identity_source_kind"] == "unresolved"
    assert "source_authenticate_obligation_ref" not in identified


def test_identify_authenticated_source_accepts_explicit_choice_among_multiple_authenticators():
    chosen_ref = "ob_11111111111111111111"
    spec = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": [
            {"subject": "customer", "obligation": "identify",
             "identity_source_kind": "authenticated_context",
             "source_authenticate_obligation_ref": chosen_ref, "requirement_ids": ["R1"]},
            {"obligation_ref": chosen_ref, "subject": "customer account", "obligation": "authenticate",
             "requirement_ids": ["R1"]},
            {"obligation_ref": "ob_22222222222222222222", "subject": "staff", "obligation": "authenticate",
             "requirement_ids": ["R1"]},
        ]},
    })
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1"], nfr_ids=[],
    )

    items = normalize_specification(spec, use_case)["public_contract"]["identity_obligations"]
    assert items[0]["identity_source_kind"] == "authenticated_context"
    assert items[0]["source_authenticate_obligation_ref"] == chosen_ref


def test_identify_authenticated_source_rejects_explicit_foreign_ref():
    spec = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": [
            {"subject": "customer", "obligation": "identify",
             "identity_source_kind": "authenticated_context",
             "source_authenticate_obligation_ref": "ob_33333333333333333333", "requirement_ids": ["R1"]},
            {"obligation_ref": "ob_11111111111111111111", "subject": "clerk", "obligation": "authenticate",
             "requirement_ids": ["R1"]},
        ]},
    })
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1"], nfr_ids=[],
    )

    identified = normalize_specification(spec, use_case)["public_contract"]["identity_obligations"][0]
    assert identified["identity_source_kind"] == "unresolved"
    assert "source_authenticate_obligation_ref" not in identified


@pytest.mark.parametrize("kind", ["caller_input", "system_result", "unresolved"])
def test_identify_source_kinds_are_preserved_without_auth_link(kind):
    spec = UseCaseSpec.model_validate({
        "trigger": "A clerk submits.",
        "public_contract": {"identity_obligations": [{
            "subject": "customer", "obligation": "identify",
            "identity_source_kind": kind, "requirement_ids": ["R1"],
        }]},
    })
    use_case = UseCaseItem(
        id="UC7", name="Submit", primary_actor="Clerk", supporting_actors=[],
        level="user_goal", goal="Submit", requirement_ids=["R1"], nfr_ids=[],
    )

    identified = normalize_specification(spec, use_case)["public_contract"]["identity_obligations"][0]
    assert identified["identity_source_kind"] == kind
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
        finding for finding in conflicting_findings if "public-contract-integrity" in finding
    ]
    assert len(duplicate_contract_findings) == 1
    assert "declared more than once" in duplicate_contract_findings[0]
    assert len(conflict_contract_findings) == 1
    assert "conflicting" in conflict_contract_findings[0]


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
