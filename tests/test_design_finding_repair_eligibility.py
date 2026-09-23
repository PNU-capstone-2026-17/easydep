from __future__ import annotations

from app.design.graphs import subgraphs
from app.design.knowledge.detectors import erd_source_entity_consistency
from app.design.nodes.artifact import _repairable_findings


def test_erd_source_projection_mismatch_is_technical_and_repairable():
    findings = erd_source_entity_consistency(
        {"Classes": []},
        {
            "_sourceEntityNames": ["Order"],
            "_sourceEntityRelationships": [],
        },
    )

    assert len(findings) == 1
    assert findings[0].requires_user_input is False
    assert findings[0] in _repairable_findings(findings)


def test_deployment_finding_input_flag_uses_issue_classification(monkeypatch):
    issues = [
        {"field": "invalid", "classification": "invalid", "reason": "bad shape"},
        {"field": "unsupported", "classification": "unsupported", "reason": "out of scope"},
        {"field": "missing", "classification": "needsInput", "reason": "choose value"},
        {"field": "unjustified", "classification": "unjustified", "reason": "no source"},
    ]
    monkeypatch.setattr(subgraphs, "extract_planning_facts", lambda **_kwargs: {})
    monkeypatch.setattr(
        subgraphs,
        "normalize_workload_graph",
        lambda _model, **_kwargs: {"issues": issues},
    )

    findings = subgraphs._deployment_model_findings({}, {})
    by_location = {finding.location: finding for finding in findings}

    assert by_location["invalid"].requires_user_input is False
    assert by_location["unsupported"].requires_user_input is False
    assert by_location["missing"].requires_user_input is True
    # Keep the existing decision gate for missing justification pending a
    # product-level distinction between absent evidence and a technical defect.
    assert by_location["unjustified"].requires_user_input is False
    assert by_location["invalid"] in _repairable_findings(findings)
    assert by_location["unsupported"] in _repairable_findings(findings)
    assert by_location["missing"] not in _repairable_findings(findings)
    assert by_location["unjustified"] in _repairable_findings(findings)
