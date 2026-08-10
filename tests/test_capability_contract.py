from __future__ import annotations

from app.requirements.capability_contract import (
    CalibrationPoint,
    calibrated_score,
    decide,
    fit_policy,
    link_dependency_capability,
    requires_persistent_storage,
)


def test_calibration_qualifies_only_when_precision_and_wilson_floor_hold():
    points = [CalibrationPoint(1.0, True) for _ in range(20)]
    points += [CalibrationPoint(0.6, value) for value in (True, False, False, False)]

    policy = fit_policy(points, version="development-v1")

    assert policy["autoAcceptEnabled"] is True
    assert policy["acceptThreshold"] == 1.0
    assert policy["qualification"]["precision"] == 1.0
    assert policy["qualification"]["wilsonLower95"] >= 0.8


def test_unqualified_calibration_disables_inferred_auto_accept():
    policy = fit_policy(
        [CalibrationPoint(1.0, value) for value in (True, False, True, False)],
        version="small-development-set",
    )

    decision, reason, _score = decide(
        raw_score=1.0,
        origin="inferred",
        evidence_valid=True,
        unresolved_fields=[],
        policy=policy,
    )

    assert policy["autoAcceptEnabled"] is False
    assert (decision, reason) == ("needsQuestion", "calibrated-threshold-not-met")


def test_hard_question_gates_override_confidence_and_explicit_origin():
    decision = decide(
        raw_score=1.0,
        origin="explicit",
        evidence_valid=True,
        unresolved_fields=["security"],
    )
    ungrounded = decide(
        raw_score=1.0,
        origin="explicit",
        evidence_valid=False,
        unresolved_fields=[],
    )

    assert decision[:2] == ("needsQuestion", "realization-changing-ambiguity")
    assert ungrounded[:2] == ("needsQuestion", "missing-or-ungrounded-evidence")


def test_impossible_and_out_of_scope_are_abstentions_not_questions():
    assert decide(
        raw_score=0, origin="inferred", evidence_valid=False,
        unresolved_fields=[], impossible=True,
    )[:2] == ("abstained", "logically-impossible")
    assert decide(
        raw_score=0, origin="inferred", evidence_valid=False,
        unresolved_fields=[], out_of_scope=True,
    )[:2] == ("abstained", "model-out-of-scope")


def test_isotonic_mapping_is_monotonic():
    policy = fit_policy([
        CalibrationPoint(0.2, True),
        CalibrationPoint(0.4, False),
        CalibrationPoint(0.8, True),
    ], version="v1")
    values = [block["value"] for block in policy["mapping"]]
    assert values == sorted(values)
    assert calibrated_score(0.2, policy["mapping"]) is not None


def test_open_need_links_to_supported_capability_without_case_aliases():
    assert link_dependency_capability(
        "persistent_storage_notes",
        "provide durable block storage that survives VM replacement",
    ) == "persistent-block-storage"


def test_more_specific_https_capability_wins_over_plain_load_balancing():
    assert link_dependency_capability(
        "https_load_balanced_ingress",
        "provide HTTPS TLS termination through load balanced ingress",
    ) == "https-load-balanced-ingress"


def test_unrelated_or_incomplete_need_abstains():
    assert link_dependency_capability("durable_notes", "keep note data") is None


def test_persistent_storage_is_detected_from_state_semantics_without_fixed_need_id():
    needs = {
        "arbitrary_storage_name": {
            "required": True,
            "decision": "accepted",
            "metadata": {
                "applicationState": {
                    "durability": "persistent",
                    "accessScope": "node-filesystem",
                    "accessPath": "/srv/catalog-data",
                }
            },
            "dependencyCapabilityIds": [],
        }
    }

    assert requires_persistent_storage(needs) is True
