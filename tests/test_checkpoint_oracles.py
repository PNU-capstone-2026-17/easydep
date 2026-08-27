from __future__ import annotations

from evaluation.checkpoint_e2e.evidence import validate_state
from evaluation.checkpoint_e2e.oracles import case_expectation_issues, product_contract_issues


def _e1_state() -> dict:
    state = {
        "_case": {"caseId": "e1-aws"},
        "actors": [
            {"name": "University User", "parent_actor": None},
            {"name": "Student", "parent_actor": "University User"},
            {"name": "Professor", "parent_actor": "University User"},
            {"name": "Academic Administrator", "parent_actor": "University User"},
        ],
        "use_cases": [
            {"id": "UC-register", "name": "Register"},
            {"id": "UC-roster", "name": "Open roster"},
            {"id": "UC-view", "name": "View registrations"},
            {"id": "UC-export", "name": "Export schedule"},
            {"id": "UC-waitlist", "name": "Join waitlist"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC-view",
                "main_scenario": [{"step_number": 2, "sentence": "Present registrations."}],
            }
        ],
        "relationships": {
            "generalizations": [
                {"parent": "University User", "child": "Student", "kind": "actor"},
                {"parent": "University User", "child": "Professor", "kind": "actor"},
                {"parent": "University User", "child": "Academic Administrator", "kind": "actor"},
            ],
            "derived_use_cases": [{"use_case_id": "UC-eligibility"}],
            "includes": [
                {
                    "base_use_case_id": "UC-register",
                    "included_use_case_id": "UC-eligibility",
                    "step_refs": [{"sentence": "Validate enrollment eligibility."}],
                },
                {
                    "base_use_case_id": "UC-swap",
                    "included_use_case_id": "UC-eligibility",
                    "step_refs": [{"sentence": "Validate enrollment eligibility."}],
                },
            ],
            "extends": [
                {
                    "base_use_case_id": "UC-view",
                    "extending_use_case_id": "UC-export",
                    "extension_point": "main:2",
                    "extension_point_name": "registrations presented",
                    "condition": "The user chooses export.",
                },
                {
                    "base_use_case_id": "UC-register",
                    "extending_use_case_id": "UC-waitlist",
                    "extension_point": "main:3",
                    "extension_point_name": "after eligibility validation",
                    "condition": "The offering is full.",
                }
            ],
        },
    }
    return state


def test_e1_expectations_use_model_semantics_without_fixed_generated_ids() -> None:
    state = _e1_state()

    assert case_expectation_issues("usecase_diagram", state) == []


def test_e1_expectations_reject_wrong_relationship_kind_and_infrastructure_actor() -> None:
    state = _e1_state()
    state["relationships"]["extends"] = []
    state["actors"].append({"name": "PostgreSQL DBMS", "parent_actor": None})

    issues = case_expectation_issues("usecase_diagram", state)

    assert any("optional export extend" in issue for issue in issues)
    assert any("DBMS is infrastructure" in issue for issue in issues)


def test_e1_rejects_authentication_as_an_include() -> None:
    state = _e1_state()
    state["use_cases"].append({"id": "UC-auth", "name": "Sign in", "requirement_ids": []})
    state["relationships"]["includes"].append(
        {"base_use_case_id": "UC-register", "included_use_case_id": "UC-auth"}
    )

    issues = case_expectation_issues("usecase_diagram", state)

    assert any("authentication" in issue for issue in issues)


def test_product_contract_adapter_uses_existing_readiness_findings(monkeypatch) -> None:
    seen = []

    def readiness(_state, stages):
        seen.append(tuple(stages))
        return {"findings": [{"stage": "api_spec", "finding": "[api.trace] missing binding"}]}

    monkeypatch.setattr("evaluation.checkpoint_e2e.oracles.design_readiness_report", readiness)

    issues = product_contract_issues("deployment_diagram", {})

    assert seen == [("class_diagram", "sequence_diagram", "api_spec", "erd")]
    assert issues == ["product contract [api_spec]: [api.trace] missing binding"]


def test_validation_consumes_e1_expectations(monkeypatch) -> None:
    state = _e1_state() | {"diagram": "@startuml\n@enduml"}
    monkeypatch.setattr("evaluation.checkpoint_e2e.evidence.blocking_issues", lambda *_args, **_kwargs: [])

    report = validate_state("usecase_diagram", state)

    assert report["status"] == "passed"
