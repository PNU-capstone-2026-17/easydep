"""Regression coverage: names and prose cannot authorize trusted context."""
from __future__ import annotations

from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.trusted_context import trusted_context_sources


def _use_case(precondition: str, obligations: list[dict] | None = None):
    return build_scenario_index({
        "use_cases": [{
            "id": "UC2",
            "name": "Process a generic request",
            "primary_actor": "Requester",
        }],
        "use_case_specs": [{
            "use_case_id": "UC2",
            "preconditions": [precondition],
            "public_contract": {
                "identity_obligations": obligations or [],
                "required_values": [],
            },
            "main_scenario": [],
            "extensions": [],
        }],
        "relationships": {"includes": [], "extends": []},
    }).use_case("UC2")


def test_uc2_like_auth_context_name_and_prose_are_not_admitted():
    sources = trusted_context_sources(
        _use_case("An authenticated server session is trusted for this request."),
        "authContext",
        "AuthContext",
    )

    assert sources == ()


def test_context_type_rename_does_not_change_explicit_obligation_binding():
    sources = trusted_context_sources(
        _use_case("A different subject is authenticated and trusted for this request.", [{
            "obligation_ref": "ob:2", "subject_ref": "sub:2", "subject": "changed label",
            "obligation": "authenticate", "requirement_ids": ["REQ-2"],
        }]),
        "authContext",
        "CompletelyRenamedEnvelope",
        obligation_ref="ob:2",
    )

    assert len(sources) == 1
    assert sources[0].source_ref == "context#ob:2"
    assert sources[0].evidence_refs == ("ob:2", "REQ-2")
