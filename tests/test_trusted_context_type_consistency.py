"""Authenticated required identifiers are the only trusted handoff values."""
from __future__ import annotations

from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.collaboration import _binding_candidates
from app.design.services.class_diagram.trusted_context import trusted_context_sources


def _use_case():
    index = build_scenario_index({
        "actors": [{"actor_ref": "actor", "name": "Student"}],
        "use_cases": [{"id": "UCX", "name": "View record", "primary_actor": "Student", "primary_actor_ref": "actor"}],
        "use_case_specs": [{
            "use_case_id": "UCX", "preconditions": [], "main_scenario": [], "extensions": [],
            "public_contract": {"identity_obligations": [{
                "obligation_ref": "auth-student", "subject_ref": "student", "subject": "Student",
                "obligation": "authenticate", "requirement_ids": ["R-auth"],
            }, {
                "obligation_ref": "identify-student", "subject_ref": "student", "subject": "Student",
                "obligation": "identify", "identity_source_kind": "authenticated_context",
                "source_authenticate_obligation_ref": "auth-student", "requirement_ids": ["R-view"],
            }], "required_values": []},
        }], "relationships": {"includes": [], "extends": []},
    })
    return index.use_case("UCX")


def test_authenticated_required_identifier_is_direct_scalar_handoff_only() -> None:
    use_case = _use_case()

    sources = trusted_context_sources(use_case, "studentId", "UUID", obligation_ref="identify-student")

    assert [source.source_ref for source in sources] == ["context#identify-student"]
    assert sources[0].evidence_refs == ("identify-student", "auth-student", "R-auth", "R-view")
    assert trusted_context_sources(use_case, "student", "StudentInfo", obligation_ref="identify-student") == ()
    assert trusted_context_sources(use_case, "context", "AuthContext", obligation_ref="identify-student") == ()
    assert trusted_context_sources(use_case, "studentId", "UUID", obligation_ref="wrong-identify") == ()


def test_absent_public_contract_has_no_authenticated_context_catalog() -> None:
    index = build_scenario_index({
        "actors": [{"actor_ref": "actor", "name": "Student"}],
        "use_cases": [{"id": "UC-empty", "name": "Browse", "primary_actor": "Student", "primary_actor_ref": "actor"}],
        "use_case_specs": [{
            "use_case_id": "UC-empty", "preconditions": [], "main_scenario": [], "extensions": [],
        }], "relationships": {"includes": [], "extends": []},
    })

    assert trusted_context_sources(
        index.use_case("UC-empty"), "studentId", "UUID", obligation_ref="identify-student",
    ) == ()


def test_catalog_rejects_wrong_identify_ref_but_keeps_prior_control_dto_result() -> None:
    use_case = _use_case()
    operations = {
        "control.lookup": {
            "returnType": "StudentInfo", "parameters": [], "stereotype": "control",
        },
        "control.use": {
            "returnType": "void", "parameters": [{"name": "student", "type": "StudentInfo"}],
            "stereotype": "control",
        },
    }
    calls = [{"stableId": "call-lookup", "receiverOperationId": "control.lookup"}, {
        "stableId": "call-use", "receiverOperationId": "control.use", "parentCallId": "call-lookup",
    }]
    model = {"Classes": [], "DataTypes": [{
        "name": "StudentInfo", "fields": ["name : String"], "fieldRefs": ["field-name"],
    }]}

    candidates = _binding_candidates(
        model, use_case, None, False, calls, 1,
        {"name": "student", "type": "StudentInfo"}, operations,
    )

    assert "context#other-student-id" not in candidates
    assert candidates == ["call-lookup#result"]
