from __future__ import annotations

from app.workspace.service import WorkspaceService


def _testing_job(blockers: list[dict[str, object]]) -> dict[str, object]:
    return {
        "job_id": "testing-command",
        "implementation_job_id": "implementation-command",
        "result": {
            "passed": False,
            "repair_state": {"status": "ACTIVE", "attempt_count": 1},
            "blocking_findings": blockers,
        },
    }


def test_exhausted_test_plan_authoring_defect_is_terminal_not_user_input() -> None:
    result = WorkspaceService._testing_result(
        _testing_job(
            [
                {
                    "defect_class": "TEST_DEFECT",
                    "repair_owner": "testing",
                    "repairable": True,
                    "message": "A workflow failed its authoring schema.",
                }
            ]
        )
    )

    assert result["kind"] == "platform_diagnostic"
    assert "awaiting_input" not in result
    assert result["requires_revision"] is False
    assert result["internal_diagnostic"] == {
        "category": "TEST_PLAN_AUTHORING_DEFECT",
        "owner": "EasyDep",
    }
    assert result["repair_state"]["status"] == "STALLED"


def test_sut_defect_keeps_existing_implementation_repair_path() -> None:
    result = WorkspaceService._testing_result(
        _testing_job(
            [
                {
                    "defect_class": "SUT_DEFECT",
                    "repair_owner": "implementation",
                    "repairable": True,
                    "message": "The generated application returned 500.",
                }
            ]
        )
    )

    assert result["awaiting_input"] is True
    assert result["kind"] == "action_required"


def test_identical_candidate_stall_does_not_reenter_automatic_repair() -> None:
    job = _testing_job(
        [
            {
                "defect_class": "SUT_DEFECT",
                "repair_owner": "implementation",
                "repairable": True,
                "message": "The generated application returned 500.",
            }
        ]
    )
    job["result"]["repair_state"] = {
        "status": "STALLED",
        "stall_reason": "Same candidate and findings repeated.",
    }

    result = WorkspaceService._testing_result(job)

    assert result["kind"] == "platform_diagnostic"
    assert result["requires_revision"] is False
    assert "awaiting_input" not in result
    assert result["internal_diagnostic"]["category"] == "AUTOMATIC_REPAIR_STALLED"
    assert result["blocking_findings"] == job["result"]["blocking_findings"]
    assert WorkspaceService._active_semantic_repair_input(result) is None
    assert WorkspaceService._technical_retry_stage({"stage": "testing"}, result) is None


def test_mixed_test_and_sut_defects_are_not_terminal_plan_defect() -> None:
    result = WorkspaceService._testing_result(
        _testing_job(
            [
                {
                    "defect_class": "TEST_DEFECT",
                    "repair_owner": "testing",
                    "repairable": True,
                },
                {
                    "defect_class": "SUT_DEFECT",
                    "repair_owner": "implementation",
                    "repairable": True,
                },
            ]
        )
    )

    assert result["awaiting_input"] is True
