"""Current RESOURCE_SPEC behavior for the Docker-on-VM requirements flow."""
from __future__ import annotations

import re

from app.requirements.agent.steps import step_resource as sr
from app.requirements.schemas import CloudConstraintExtraction

CONSTRAINTS = (
    "Deploy to AWS in Seoul with a monthly budget of at most 100 USD. "
    "A single availability zone is acceptable."
)


def _extraction(**changes) -> CloudConstraintExtraction:
    values = {
        "provider": "aws",
        "provider_evidence": "AWS",
        "region_as_written": "Seoul",
        "region_evidence": "Seoul",
        "monthly_budget_amount": 100,
        "monthly_budget_currency": "USD",
        "monthly_budget_evidence": "100 USD",
        "multi_zone": False,
        "multi_zone_evidence": "A single availability zone is acceptable",
        "understanding": "AWS Seoul, up to 100 USD per month, single zone accepted.",
    }
    values.update(changes)
    return CloudConstraintExtraction(**values)


def _run(monkeypatch, extraction: CloudConstraintExtraction, text: str = CONSTRAINTS):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", True)
    monkeypatch.setattr(sr, "_extract_once", lambda _briefing: extraction)
    return sr.build_resource_spec({
        "classified": [],
        "resource_constraints_text": text,
    })


def test_explicit_required_constraints_produce_vm_resource_spec(monkeypatch):
    result = _run(monkeypatch, _extraction())

    assert result["resource_spec"] == {
        "schemaVersion": "2",
        "workloads": ["vm"],
        "provider": "aws",
        "multiZone": False,
        "regionAsWritten": "Seoul",
        "region": "ap-northeast-2",
        "monthlyBudgetUSD": 100.0,
    }
    intake = result["resource_intake"]
    assert intake["valid"] is True
    assert {item["field"] for item in intake["provenance"]} >= {
        "provider", "region", "monthlyBudgetUSD", "multiZone"
    }


def test_missing_required_constraints_are_asked_in_english(monkeypatch):
    result = _run(monkeypatch, CloudConstraintExtraction(), text="")

    assert "resource_spec" not in result
    intake = result["resource_intake"]
    assert intake["valid"] is False
    questions = intake["questions"]
    assert {q["field"] for q in questions if q["kind"] == "missing"} == {
        "provider", "region", "monthlyBudgetUSD"
    }
    assert not re.search(r"[가-힣]", " ".join(
        f"{q['question']} {q['why']}" for q in questions
    ))


def test_unsupported_or_ungrounded_values_do_not_enter_the_spec(monkeypatch):
    result = _run(
        monkeypatch,
        _extraction(provider="oracle", provider_evidence="AWS"),
    )

    assert "resource_spec" not in result
    assert "provider" not in result["resource_intake"]["draft"]


def test_optional_sizing_constraints_are_preserved_when_explicit(monkeypatch):
    text = CONSTRAINTS + " The service needs at least 2 vCPUs and 4 GiB of memory."
    result = _run(monkeypatch, _extraction(
        min_vcpu=2,
        min_vcpu_evidence="2 vCPUs",
        min_memory_gib=4,
        min_memory_evidence="4 GiB",
    ), text=text)

    assert result["resource_spec"]["minVCpu"] == 2
    assert result["resource_spec"]["minMemoryGiB"] == 4.0


def test_disabled_llm_reports_degradation_without_fabricating_values(monkeypatch):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    intake = sr.build_resource_spec({
        "classified": [],
        "resource_constraints_text": CONSTRAINTS,
    })["resource_intake"]

    assert intake["draft"] == {"schemaVersion": "2", "workloads": ["vm"]}
    assert intake["degraded"].startswith("The resource constraint LLM is disabled")
