"""배포 capability 판정·calibration·의존 링크의 공개 계약을 검증한다."""

from __future__ import annotations

import pytest

from app.requirements.resources.capability_contract import (
    CalibrationPoint,
    apply_capability_answers,
    calibrated_score,
    capability_resource_questions,
    decide,
    fit_policy,
    link_dependency_capability,
    load_policy,
    requires_persistent_storage,
    validate_policy,
)


def _pending_persistent_storage_contract() -> dict:
    return {
        "schemaVersion": "CapabilityContract/v1",
        "capabilities": [
            {
                "id": "persistent_storage",
                "statement": "Keep application state in durable storage.",
                "requirementIds": ["RR1"],
                "evidenceSpans": ["record registrations"],
                "origin": "inferred",
                "necessity": "required",
                "decision": "needsQuestion",
                "decisionReason": "calibrated-threshold-not-met",
                "rawConfidence": 1.0,
                "calibratedConfidence": None,
                "thresholdVersion": "test-v1",
                "confirmation": "pending",
                "alternatives": [],
                "unresolvedFields": [],
                "dependencyCapabilityIds": [],
            }
        ],
        "questions": [
            {
                "capabilityId": "persistent_storage",
                "reason": "calibrated-threshold-not-met",
                "question": "Should storage be durable?",
            }
        ],
    }


def test_pending_capability_projects_to_a_finite_workspace_choice() -> None:
    questions = capability_resource_questions(_pending_persistent_storage_contract())

    assert questions[0]["field"] == "capability:persistent_storage"
    assert "service restarts" in questions[0]["question"]
    assert [choice["value"] for choice in questions[0]["choices"]] == [
        "accepted",
        "abstained",
    ]
    assert questions[0]["choices"][0]["recommended"] is True


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("accepted", "accepted"), ("abstained", "abstained")],
)
def test_capability_choice_updates_the_contract_and_deployment_projection(
    answer: str, expected: str
) -> None:
    state = {
        "capability_contract": _pending_persistent_storage_contract(),
        "deployment_needs": {
            "persistent_storage": {"required": True, "decision": "needsQuestion"}
        },
    }

    patch = apply_capability_answers(
        state, {"capability:persistent_storage": answer}
    )

    capability = patch["capability_contract"]["capabilities"][0]
    assert capability["decision"] == expected
    assert capability["confirmation"] == "userConfirmed"
    assert patch["capability_contract"]["questions"] == []
    assert patch["deployment_needs"]["persistent_storage"]["decision"] == expected
    assert patch["capability_answers"] == {"persistent_storage": expected}


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


def test_default_policy_loads_the_frozen_current_resource() -> None:
    """resource 경계 이동 뒤에도 기본 동결 정책 파일을 읽는다."""
    policy = load_policy()

    assert policy["status"] == "frozen"
    assert policy["version"] == "development-no-inference-v1"


def test_policy_boundary_rejects_missing_required_shape() -> None:
    """필수 키가 빠진 정책은 판정의 보수적 결과로 조용히 흡수하지 않는다."""
    with pytest.raises(ValueError):
        validate_policy({
            "schemaVersion": "easydep-capability-threshold/v1",
            "status": "frozen",
            "autoAcceptEnabled": False,
            "acceptThreshold": None,
            "mapping": [],
        })


def test_enabled_policy_requires_threshold_and_calibration_mapping() -> None:
    """자동 수락을 켠 정책에는 임계값과 보정 구간이 모두 필요하다."""
    with pytest.raises(ValueError):
        validate_policy({
            "schemaVersion": "easydep-capability-threshold/v1",
            "status": "frozen",
            "version": "invalid-enabled-policy",
            "autoAcceptEnabled": True,
            "acceptThreshold": None,
            "mapping": [],
        })


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


def test_unused_invalid_policy_does_not_override_hard_decisions() -> None:
    """hard gate와 explicit 결정은 사용하지 않는 policy 검증보다 먼저 끝난다."""
    invalid_policy = {"schemaVersion": "easydep-capability-threshold/v1"}

    impossible = decide(
        raw_score=0,
        origin="inferred",
        evidence_valid=False,
        unresolved_fields=[],
        impossible=True,
        policy=invalid_policy,
    )
    explicit = decide(
        raw_score=1,
        origin="explicit",
        evidence_valid=True,
        unresolved_fields=[],
        policy=invalid_policy,
    )

    assert impossible[:2] == ("abstained", "logically-impossible")
    assert explicit[:2] == ("accepted", "explicit-grounded-constraint")


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


def test_plain_https_need_links_to_recognized_out_of_scope_capability():
    assert link_dependency_capability(
        "secure_ingress",
        "provide external HTTPS ingress",
    ) == "https-ingress"


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
