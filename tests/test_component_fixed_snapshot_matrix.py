from evaluation.research_protocol.commands.run_component_fixed_snapshot_matrix import (
    _needs_for_condition,
    _selected_cases,
)


def _measurement():
    return {
        "cells": [
            {
                "axis": "base",
                "deploymentNeeds": {
                    "lb": {"dependencyCapabilityIds": ["load-balanced-ingress"]}
                },
            },
            {
                "axis": "specific",
                "deploymentNeeds": {
                    "tls": {"dependencyCapabilityIds": ["https-ingress"]},
                    "runtime": {"role": "container"},
                },
            },
        ]
    }


def test_control_can_use_a_predecessor_measurement_axis():
    pair = {
        "measurementAxis": "specific",
        "controlMeasurementAxis": "base",
        "capabilityIds": ["https-ingress"],
    }

    control = _needs_for_condition(_measurement(), pair, "control")
    treatment = _needs_for_condition(_measurement(), pair, "treatment")

    assert set(control) == {"lb"}
    assert set(treatment) == {"tls", "runtime"}


def test_control_removes_only_configured_capability_links():
    pair = {"measurementAxis": "specific", "capabilityIds": ["https-ingress"]}

    control = _needs_for_condition(_measurement(), pair, "control")

    assert set(control) == {"runtime"}


def test_case_selection_defaults_to_frozen_suite_order(monkeypatch):
    cases = {
        "a.json": {"caseId": "A", "scope": {"condition": "control"}},
        "b.json": {"caseId": "B", "scope": {"condition": "treatment"}},
    }
    monkeypatch.setattr(
        "evaluation.research_protocol.commands.run_component_fixed_snapshot_matrix.read_protocol_json",
        lambda path: cases[path.name],
    )

    selected = _selected_cases(
        {"development": ["a.json", "b.json"]},
        conditions={"treatment"},
        case_ids=set(),
    )

    assert [item["caseId"] for item in selected] == ["B"]
