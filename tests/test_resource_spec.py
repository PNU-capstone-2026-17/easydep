"""Current RESOURCE_SPEC behavior for the Docker-on-VM requirements flow."""

from __future__ import annotations

import re

from app.requirements.agent.steps import step_resource as sr
from app.requirements.schemas import CloudConstraintExtraction

CONSTRAINTS = "Deploy to AWS in Seoul with a monthly budget of at most 100 USD."


def _extraction(**changes) -> CloudConstraintExtraction:
    values = {
        "provider": "aws",
        "provider_evidence": "AWS",
        "region_as_written": "Seoul",
        "region_evidence": "Seoul",
        "monthly_budget_amount": 100,
        "monthly_budget_currency": "USD",
        "monthly_budget_evidence": "100 USD",
        "understanding": "AWS Seoul, up to 100 USD per month.",
    }
    values.update(changes)
    return CloudConstraintExtraction(**values)


def _run(monkeypatch, extraction: CloudConstraintExtraction, text: str = CONSTRAINTS):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", True)
    monkeypatch.setattr(sr, "_extract_once", lambda _briefing: extraction)
    return sr.build_resource_spec(
        {
            "classified": [],
            "resource_constraints_text": text,
        }
    )


def test_explicit_required_constraints_produce_vm_resource_spec(monkeypatch):
    result = _run(monkeypatch, _extraction())

    assert result["resource_spec"] == {
        "schemaVersion": "3",
        "workloads": ["vm"],
        "provider": "aws",
        "regionAsWritten": "Seoul",
        "region": "ap-northeast-2",
        "monthlyBudgetUSD": 100.0,
    }
    intake = result["resource_intake"]
    assert intake["valid"] is True
    assert {item["field"] for item in intake["provenance"]} >= {
        "provider",
        "region",
        "monthlyBudgetUSD",
    }


def test_structured_intake_constraints_do_not_depend_on_llm(monkeypatch):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    result = sr.build_resource_spec(
        {
            "classified": [],
            "initial_cloud_constraints": {
                "provider": "aws",
                "region": "Seoul",
                "monthly_budget_amount": 100,
                "monthly_budget_currency": "USD",
            },
        }
    )

    assert result["resource_spec"] == {
        "schemaVersion": "3",
        "workloads": ["vm"],
        "provider": "aws",
        "regionAsWritten": "Seoul",
        "region": "ap-northeast-2",
        "monthlyBudgetUSD": 100.0,
    }


def test_missing_required_constraints_are_asked_in_english(monkeypatch):
    result = _run(monkeypatch, CloudConstraintExtraction(), text="")

    assert "resource_spec" not in result
    intake = result["resource_intake"]
    assert intake["valid"] is False
    questions = intake["questions"]
    assert {q["field"] for q in questions if q["kind"] == "missing"} == {
        "provider",
        "region",
    }
    assert not re.search(r"[가-힣]", " ".join(f"{q['question']} {q['why']}" for q in questions))
    sizing = [q for q in questions if q["field"] in {"minVCpu", "minMemoryGiB"}]
    assert len(sizing) == 1
    assert "either the minimum vCPU or minimum memory" in sizing[0]["question"]


def test_structured_deployment_alternatives_are_preserved_without_budget(monkeypatch):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    result = sr.build_resource_spec(
        {
            "classified": [],
            "initial_cloud_constraints": {
                "mode": "alternatives",
                "targets": [
                    {
                        "provider": "aws",
                        "region": "ap-northeast-2",
                        "zones": ["ap-northeast-2a"],
                    },
                    {
                        "provider": "gcp",
                        "region": "asia-northeast3",
                        "zones": ["asia-northeast3-a"],
                    },
                ],
            },
        }
    )

    spec = result["resource_spec"]
    assert spec["provider"] == "aws"
    assert spec["region"] == "ap-northeast-2"
    assert spec["deploymentTargets"] == [
        {
            "provider": "aws",
            "region": "ap-northeast-2",
            "zones": ["ap-northeast-2a"],
        },
        {
            "provider": "gcp",
            "region": "asia-northeast3",
            "zones": ["asia-northeast3-a"],
        },
    ]
    assert "monthlyBudgetUSD" not in spec


def test_structured_map_selection_prevents_duplicate_provider_and_region_questions(monkeypatch):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    extracted = CloudConstraintExtraction(
        provider="azure",
        provider_evidence="Azure",
        region_as_written="West Europe",
        region_evidence="West Europe",
        ambiguous_fields=["provider", "region"],
        understanding="The free text mentions Azure West Europe.",
    )
    result = sr.build_resource_spec(
        {
            "classified": [],
            "resource_constraints_text": "Deploy to Azure in West Europe.",
            "initial_cloud_constraints": {
                "mode": "alternatives",
                "targets": [
                    {
                        "provider": "aws",
                        "region": "ap-northeast-2",
                        "zones": ["ap-northeast-2a", "ap-northeast-2c"],
                    }
                ],
            },
            "resource_constraint_extraction": {
                "status": "completed",
                "result": extracted.model_dump(mode="json"),
            },
        }
    )

    assert result["resource_spec"]["provider"] == "aws"
    assert result["resource_spec"]["region"] == "ap-northeast-2"
    assert not {
        question["field"] for question in result["resource_intake"]["questions"]
    }.intersection({"provider", "region"})


def test_unsupported_or_ungrounded_values_do_not_enter_the_spec(monkeypatch):
    result = _run(
        monkeypatch,
        _extraction(provider="oracle", provider_evidence="AWS"),
    )

    assert "resource_spec" not in result
    assert "provider" not in result["resource_intake"]["draft"]


def test_optional_sizing_constraints_are_preserved_when_explicit(monkeypatch):
    text = CONSTRAINTS + " The service needs at least 2 vCPUs and 4 GiB of memory."
    result = _run(
        monkeypatch,
        _extraction(
            min_vcpu=2,
            min_vcpu_evidence="2 vCPUs",
            min_memory_gib=4,
            min_memory_evidence="4 GiB",
        ),
        text=text,
    )

    assert result["resource_spec"]["minVCpu"] == 2
    assert result["resource_spec"]["minMemoryGiB"] == 4.0


def test_disabled_llm_reports_degradation_without_fabricating_values(monkeypatch):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    intake = sr.build_resource_spec(
        {
            "classified": [],
            "resource_constraints_text": CONSTRAINTS,
        }
    )["resource_intake"]

    assert intake["draft"] == {"schemaVersion": "3", "workloads": ["vm"]}
    assert intake["degraded"].startswith("The resource constraint LLM is disabled")


def test_structured_user_answers_are_grounded_in_the_rendered_briefing():
    seen, briefing = sr._perception(
        {
            "classified": [],
            "resource_answers": {
                "provider": "azure",
                "monthlyBudgetUSD": "100",
            },
        }
    )

    assert "provider: azure" in briefing
    assert sr._ground("provider: azure", seen)
    assert sr._ground("monthlyBudgetUSD: 100", seen)


def test_structured_multi_zone_topology_is_recorded_without_availability_question(monkeypatch):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    state = {
        "classified": [],
        "initial_cloud_constraints": {
            "provider": "aws",
            "region": "Seoul",
            "monthly_budget_amount": 100,
            "compute_profile": "managedGroupManyMultiZone",
            "replica_count": 2,
            "public_ingress": "loadBalanced",
            "targets": [
                {
                    "provider": "aws",
                    "region": "ap-northeast-2",
                    "zones": ["ap-northeast-2a", "ap-northeast-2c"],
                }
            ],
        },
    }

    result = sr.build_resource_spec(state)

    assert result["resource_spec"]["computeProfile"] == "managedGroupManyMultiZone"
    assert result["resource_spec"]["replicaCount"] == 2
    assert result["resource_spec"]["publicIngress"] == "loadBalanced"
    assert not any(
        item["field"] == "availabilityIntent"
        for item in result["resource_intake"]["questions"]
    )


def test_legacy_availability_answer_is_ignored(monkeypatch):
    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    result = sr.build_resource_spec(
        {
            "classified": [],
            "resource_answers": {"availabilityIntent": "failureContinuity"},
        }
    )

    assert "availabilityIntent" not in result["resource_intake"]["draft"]
