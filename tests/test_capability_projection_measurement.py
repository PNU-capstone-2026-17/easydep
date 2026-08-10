from evaluation.research_protocol.commands.measure_capability_projection import (
    CASES,
    _positive_capability_requirements,
    _read,
    ablate,
)


def test_measurement_selects_existing_positive_capability_clauses_only():
    for filename in (
        "ps-treatment-aws.json",
        "lb-treatment-aws.json",
        "tls-treatment-aws.json",
    ):
        source = _read(CASES / filename)
        selected = _positive_capability_requirements(source)

        assert selected
        assert all(item in source["requirements"][1] for item in selected)
        assert not any("do not provision" in item.casefold() for item in selected)


def test_ablation_holds_stored_llm_output_fixed(monkeypatch):
    calls = []

    def project(requirements_result, provider, region, *, use_cloud_kb=True):
        calls.append((requirements_result, provider, region, use_cloud_kb))
        return {
            "provider": provider,
            "anchors": ["vm", "disk"] if use_cloud_kb else [],
            "modeledOutcomes": ["disk"] if use_cloud_kb else [],
            "unmodeledAcceptedNeeds": [],
            "realizationIds": [f"{provider}-disk"] if use_cloud_kb else [],
            "kbUsed": ["depkb"] if use_cloud_kb else [],
            "deferred": [] if use_cloud_kb else ["dependencies"],
        }

    monkeypatch.setattr(
        "evaluation.research_protocol.commands.measure_capability_projection._project",
        project,
    )
    source = {
        "configuration": {"model": "fixed"},
        "cells": [
            {
                "axis": "persistent-storage",
                "condition": "treatment",
                "deploymentNeeds": {"storage": {"required": True}},
            }
        ],
    }

    result = ablate(source)

    assert result["configuration"]["fixedInputAcrossArms"] is True
    assert result["summary"]["providerCellCount"] == 3
    assert result["summary"]["fullRealizationCount"] == 3
    assert result["summary"]["noDepkbRealizationCount"] == 0
    for index in range(0, len(calls), 2):
        assert calls[index][0] == calls[index + 1][0]
        assert calls[index][1:3] == calls[index + 1][1:3]
        assert calls[index][3] is True
        assert calls[index + 1][3] is False
