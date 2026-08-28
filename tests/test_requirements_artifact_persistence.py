from app.repositories import artifact_repository
from app.requirements.orchestration.api import persist_analysis


def test_first_review_gate_persists_all_three_reviewable_artifacts(monkeypatch) -> None:
    saved = []
    monkeypatch.setattr(artifact_repository, "load_state", lambda _app_id: {})
    monkeypatch.setattr(
        artifact_repository,
        "save_stage",
        lambda app_id, stage, state: saved.append((app_id, stage, state)),
    )

    stages = persist_analysis(
        "app-1",
        {
            "status": "need_feedback",
            "requirements": [{"id": "FR1", "type": "FR", "text": "Browse courses."}],
            "capability_contract": {
                "schemaVersion": "CapabilityContract/v1",
                "capabilities": [{"id": "public_ingress"}],
                "questions": [],
            },
            "resource_intake": {
                "draft": {"provider": "aws", "region": "ap-northeast-2"},
                "valid": False,
                "questions": [{"field": "monthlyBudgetUSD"}],
            },
        },
    )

    assert stages == [
        "refined_requirements",
        "capability_contract",
        "resource_intake",
    ]
    assert [stage for _app_id, stage, _state in saved] == stages


def test_use_case_review_gate_persists_the_model_before_detailed_specs(
    monkeypatch,
) -> None:
    saved = []
    monkeypatch.setattr(artifact_repository, "load_state", lambda _app_id: {})
    monkeypatch.setattr(
        artifact_repository,
        "save_stage",
        lambda app_id, stage, state: saved.append((app_id, stage, state)),
    )

    stages = persist_analysis(
        "app-1",
        {
            "status": "need_feedback",
            "phase": "use_cases",
            "actors": [{"id": "ACT1", "name": "Student"}],
            "use_cases": [{"id": "UC1", "name": "Enroll in a course"}],
        },
    )

    assert stages == ["usecase_spec"]
    assert saved == [
        (
            "app-1",
            "usecase_spec",
            {
                "usecase_spec": {
                    "actors": [{"id": "ACT1", "name": "Student"}],
                    "use_cases": [{"id": "UC1", "name": "Enroll in a course"}],
                    "use_case_specs": [],
                }
            },
        )
    ]
