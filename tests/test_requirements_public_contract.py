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
