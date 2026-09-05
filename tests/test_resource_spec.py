"""Docker-on-VM 요구사항 경로의 공개 RESOURCE_SPEC 계약을 검증한다."""

from __future__ import annotations

import re

from app.requirements.resources import service as sr
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
    return sr.build_resource_spec(
        {
            "classified": [],
            "resource_constraints_text": text,
        },
        proposal_call=lambda _briefing: extraction,
    )


def test_explicit_required_constraints_produce_vm_resource_spec(monkeypatch):
    result = _run(monkeypatch, _extraction())

    assert result["resource_spec"] == {
        "schemaVersion": "4",
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
        "schemaVersion": "4",
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


def test_cloud_coordinate_questions_include_only_control_and_dependency_metadata(monkeypatch):
    missing = _run(monkeypatch, CloudConstraintExtraction(), text="")["resource_intake"]
    questions = {question["field"]: question for question in missing["questions"]}

    assert questions["provider"]["ui"] == {"control": "cloudProvider"}
    assert questions["region"]["ui"] == {
        "control": "cloudRegion",
        "dependsOn": "provider",
    }

    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    unresolved = sr.build_resource_spec(
        {
            "classified": [],
            "initial_cloud_constraints": {"provider": "aws", "region": "Unknown place"},
        }
    )["resource_intake"]
    region = next(question for question in unresolved["questions"] if question["field"] == "region")

    assert region["ui"] == {
        "control": "cloudRegion",
        "dependsOn": "provider",
        "knownProvider": "aws",
    }


def test_normalized_values_suppress_stale_ambiguity_questions(monkeypatch):
    result = _run(
        monkeypatch,
        _extraction(ambiguous_fields=["provider", "region", "monthlyBudgetUSD"]),
    )

    assert result["resource_spec"]["provider"] == "aws"
    assert result["resource_spec"]["region"] == "ap-northeast-2"
    assert result["resource_spec"]["monthlyBudgetUSD"] == 100.0
    assert not {
        question["field"] for question in result["resource_intake"]["questions"]
    }.intersection({"provider", "region", "monthlyBudgetUSD"})


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


def test_structured_single_selection_prevents_duplicate_cloud_questions(monkeypatch):
    """전용 입력 칸의 단일 CSP·리전·예산은 자유문장 제안이 다시 묻지 못한다."""

    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    extracted = CloudConstraintExtraction(
        provider="aws",
        provider_evidence="AWS",
        region_as_written="Seoul",
        region_evidence="Seoul",
        monthly_budget_amount=900,
        monthly_budget_currency="USD",
        monthly_budget_evidence="900 USD",
        ambiguous_fields=["provider", "region", "monthlyBudgetUSD"],
        understanding="The free text contains an ambiguous deployment description.",
    )
    result = sr.build_resource_spec(
        {
            "classified": [],
            "resource_constraints_text": "AWS Seoul with a separate free-form budget.",
            "initial_cloud_constraints": {
                "provider": "aws",
                "region": "ap-northeast-2",
                "monthly_budget_amount": 500,
                "monthly_budget_currency": "USD",
            },
            "resource_constraint_extraction": {
                "status": "completed",
                "result": extracted.model_dump(mode="json"),
            },
        }
    )

    assert result["resource_spec"]["provider"] == "aws"
    assert result["resource_spec"]["region"] == "ap-northeast-2"
    assert result["resource_spec"]["monthlyBudgetUSD"] == 500
    assert not {
        question["field"] for question in result["resource_intake"]["questions"]
    }.intersection({"provider", "region", "monthlyBudgetUSD"})


def test_resource_answer_overrides_cached_ambiguous_region(monkeypatch):
    """되묻기 답변은 이전 LLM 추출을 재사용해도 즉시 region에 반영된다."""

    monkeypatch.setattr(sr.settings, "resource_agent_llm", False)
    extracted = CloudConstraintExtraction(
        region_as_written="somewhere in Asia",
        region_evidence="somewhere in Asia",
        ambiguous_fields=["region"],
        understanding="The original region was ambiguous.",
    )
    result = sr.build_resource_spec(
        {
            "classified": [],
            "initial_cloud_constraints": {"provider": "aws"},
            "resource_answers": {"region": "ap-northeast-2"},
            "resource_constraint_extraction": {
                "status": "completed",
                "result": extracted.model_dump(mode="json"),
            },
        }
    )

    assert result["resource_spec"]["provider"] == "aws"
    assert result["resource_spec"]["region"] == "ap-northeast-2"
    assert not any(
        question["field"] == "region"
        for question in result["resource_intake"]["questions"]
    )


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

    assert intake["draft"] == {"schemaVersion": "4", "workloads": ["vm"]}
    assert intake["degraded"].startswith("The resource constraint LLM is disabled")


def test_structured_user_answers_ground_cached_extraction_through_public_stage():
    """구조화 답변의 근거 인정을 private briefing helper 없이 결과로 확인한다."""

    result = sr.build_resource_spec(
        {
            "classified": [],
            "resource_answers": {
                "provider": "azure",
                "monthlyBudgetUSD": "100",
            },
            "resource_constraint_extraction": {
                "status": "completed",
                "result": CloudConstraintExtraction(
                    provider="azure",
                    provider_evidence="provider: azure",
                    monthly_budget_amount=100,
                    monthly_budget_currency="USD",
                    monthly_budget_evidence="monthlyBudgetUSD: 100",
                ).model_dump(mode="json"),
            },
        }
    )

    assert result["resource_intake"]["draft"] == {
        "schemaVersion": "4",
        "workloads": ["vm"],
        "provider": "azure",
        "monthlyBudgetUSD": 100.0,
    }


def test_structured_topology_preferences_are_not_copied_into_resource_spec(monkeypatch):
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

    assert "computeProfile" not in result["resource_spec"]
    assert "replicaCount" not in result["resource_spec"]
    assert "publicIngress" not in result["resource_spec"]
    assert result["resource_spec"]["deploymentTargets"][0]["zones"] == [
        "ap-northeast-2a",
        "ap-northeast-2c",
    ]
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
