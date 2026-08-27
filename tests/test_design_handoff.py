import pytest

from app.orchestration.adapters.design import (
    DesignAdapter,
    DesignContractError,
)


def _requirements_result(**overrides):
    result = {
        "requirements": [
            {"id": "FR1", "text": "A member shall place an order.", "type": "FR"},
            {"id": "NFR1", "text": "Order data shall survive restarts.", "type": "NFR"},
        ],
        "actors": [{"name": "Member", "parent_actor": None}],
        "use_cases": [
            {
                "id": "UC1",
                "name": "Place order",
                "primary_actor": "Member",
                "requirement_ids": ["FR1"],
                "nfr_ids": [],
            }
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "name": "Place order",
                "requirement_ids": ["FR1"],
                "nfr_ids": [],
                "preconditions": [],
                "trigger": "Member requests an order",
                "main_scenario": [
                    {
                        "step_number": 1,
                        "sentence": "System records the order",
                        "covered_req_ids": ["FR1"],
                    }
                ],
                "extensions": [],
                "success_guarantee": ["The order is recorded"],
                "minimal_guarantee": [],
                "issues": [],
                "repair_iters": 0,
            }
        ],
        "coverage": {
            "orphan_fr_ids": [],
            "unattached_nfr_ids": ["NFR1"],
            "unknown_requirement_refs": [],
        },
        "relationships": {
            "associations": [{"actor": "Member", "use_case_id": "UC1"}],
            "includes": [],
            "extends": [],
            "generalizations": [],
            "derived_use_cases": [],
        },
    }
    result.update(overrides)
    return result


def test_handoff_preserves_global_requirements_and_relationships():
    source = _requirements_result()
    source["traceability"] = {
        "requirements": {
            "NFR1": {
                "text": "Order data shall survive restarts.",
                "constrains_use_cases": ["UC1"],
            }
        }
    }

    state = DesignAdapter._state(source)

    assert state["refined_requirements"] == source["requirements"]
    assert state["relationships"] == source["relationships"]
    assert state["usecase_spec"]["relationships"] == source["relationships"]
    assert state["usecase_spec"]["traceability"] == source["traceability"]


def test_handoff_allows_unattached_global_nfr_and_empty_preconditions():
    state = DesignAdapter._state(_requirements_result())

    assert state["usecase_spec"]["use_case_specs"][0]["preconditions"] == []


def test_handoff_rejects_existing_requirements_report_blockers():
    source = _requirements_result(
        spec_report={"total_issues": 1, "failed_ucs": [], "unvalidated_ucs": []}
    )

    with pytest.raises(DesignContractError, match="specification report"):
        DesignAdapter._state(source)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (
            {"requirement_source_issues": ["RR1 has no RAW source"]},
            "requirement source mapping",
        ),
        (
            {
                "resource_intake": {
                    "valid": False,
                    "errors": ["[required] provider missing"],
                    "questions": [
                        {"field": "provider", "kind": "missing"},
                    ],
                }
            },
            "resource contract is missing",
        ),
        (
            {
                "capability_contract": {
                    "capabilities": [
                        {"id": "zone_placement", "decision": "needsQuestion"},
                    ]
                }
            },
            "capability contract needs answers",
        ),
    ],
)
def test_handoff_rejects_incomplete_requirement_contracts(update, message):
    with pytest.raises(DesignContractError, match=message):
        DesignAdapter._state(_requirements_result(**update))


def test_handoff_allows_suggested_capacity_questions():
    state = DesignAdapter._state(
        _requirements_result(
            resource_intake={
                "valid": True,
                "errors": [],
                "questions": [
                    {"field": "minMemoryGiB", "kind": "suggested"},
                ],
            }
        )
    )

    assert state["resource_intake"]["questions"][0]["kind"] == "suggested"


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (
            {"use_case_specs": []},
            "did not produce use_case_specs",
        ),
        (
            {
                "use_case_specs": [
                    {
                        "use_case_id": "UC2",
                        "trigger": "Member acts",
                        "main_scenario": [{"step_number": 1, "covered_req_ids": []}],
                        "success_guarantee": ["Done"],
                    }
                ]
            },
            "id mismatch",
        ),
        (
            {
                "use_cases": [
                    {
                        "id": "UC1",
                        "primary_actor": "Member",
                        "requirement_ids": ["GHOST"],
                        "nfr_ids": [],
                    }
                ]
            },
            "unknown requirement",
        ),
    ],
)
def test_handoff_rejects_structurally_unsafe_input(update, message):
    with pytest.raises(DesignContractError, match=message):
        DesignAdapter._state(_requirements_result(**update))
