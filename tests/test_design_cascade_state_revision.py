from __future__ import annotations

from app.design import cascade
from app.design.nodes.artifact import DesignArtifactSpec


def _validation(_content):
    return {"syntax_valid": True, "syntax_errors": []}


def test_apply_uses_local_reviser_and_preserves_untargeted_diagrams() -> None:
    original = {
        "Diagrams": [
            {"use_case_id": "UC9", "Messages": [{"label": "old"}]},
            {"use_case_id": "UC10", "Messages": [{"label": "stable"}]},
        ]
    }
    revised = {
        "Diagrams": [
            {"use_case_id": "UC9", "Messages": [{"label": "fixed"}]},
            {"use_case_id": "UC10", "Messages": [{"label": "should-not-enter"}]},
        ]
    }

    spec = DesignArtifactSpec(
        stage="sequence_diagram",
        model_key="sequence_model",
        content_key="sequence_puml",
        valid_key="sequence_valid",
        errors_key="sequence_errors",
        feedback_key="sequence_feedback",
        empty="",
        extract=lambda _state: {},
        revise=lambda *_args: revised,
        render=str,
        validate=_validation,
        elements={"Diagrams": lambda item: item.get("use_case_id", "")},
    )

    patch = cascade._apply(
        spec,
        {"sequence_model": original, "class_model": {"Collaborations": []}},
        "Place extension 1a at its branch",
        {"UC9"},
    )

    assert patch["sequence_model"]["Diagrams"] == [
        {"use_case_id": "UC9", "Messages": [{"label": "fixed"}]},
        {"use_case_id": "UC10", "Messages": [{"label": "stable"}]},
    ]
    assert "class_model" not in patch


def test_deterministic_projection_refreshes_provenance_without_replacing_siblings() -> None:
    original = {
        "Diagrams": [
            {"use_case_id": "UC1", "Messages": [{"label": "old-target"}]},
            {"use_case_id": "UC2", "Messages": [{"label": "approved-sibling"}]},
        ],
        "class_diagram_hash": "old-class-hash",
        "MethodProposals": [{"operation": "old-proposal"}],
    }
    projected = {
        "Diagrams": [
            {"use_case_id": "UC1", "Messages": [{"label": "new-target"}]},
            {"use_case_id": "UC2", "Messages": [{"label": "regenerated-sibling"}]},
        ],
        "class_diagram_hash": "current-class-hash",
        "MethodProposals": [],
    }
    spec = DesignArtifactSpec(
        stage="sequence_diagram",
        model_key="sequence_model",
        content_key="sequence_puml",
        valid_key="sequence_valid",
        errors_key="sequence_errors",
        feedback_key="sequence_feedback",
        empty="",
        extract=lambda _state: projected,
        revise=lambda *_args: projected,
        render=str,
        validate=_validation,
        elements={"Diagrams": lambda item: item.get("use_case_id", "")},
    )

    patch = cascade._apply_projection(
        spec,
        {"sequence_model": original},
        {"UC1"},
    )

    assert patch["sequence_model"] == {
        "Diagrams": [
            {"use_case_id": "UC1", "Messages": [{"label": "new-target"}]},
            {"use_case_id": "UC2", "Messages": [{"label": "approved-sibling"}]},
        ],
        "class_diagram_hash": "current-class-hash",
        "MethodProposals": [],
    }


def test_targeted_class_merge_updates_dependent_collaboration() -> None:
    old_operation = "UserBoundary::login(request:LoginRequest)"
    new_operation = "UserBoundary::login(credentials:LoginRequest)"
    original = {
        "Classes": [{
            "className": "UserBoundary",
            "operations": [{"operationId": old_operation}],
        }],
        "DataTypes": [],
        "Collaborations": [{
            "collaborationId": "UC9",
            "calls": [{"receiverOperationId": old_operation}],
        }],
    }
    revised = {
        "Classes": [{
            "className": "UserBoundary",
            "operations": [{"operationId": new_operation}],
        }],
        "DataTypes": [],
        "Collaborations": [{
            "collaborationId": "UC9",
            "calls": [{"receiverOperationId": new_operation}],
        }],
    }
    spec = DesignArtifactSpec(
        stage="class_diagram",
        model_key="class_model",
        content_key="class_puml",
        valid_key="class_valid",
        errors_key="class_errors",
        feedback_key="class_feedback",
        empty="",
        extract=lambda _state: {},
        revise=lambda *_args: revised,
        render=str,
        validate=_validation,
        elements={
            "Classes": lambda item: item.get("className", ""),
            "DataTypes": lambda item: item.get("name", ""),
            "Collaborations": lambda item: item.get("collaborationId", ""),
        },
    )

    patch = cascade._apply(
        spec,
        {"class_model": original},
        "Revise login",
        {"UserBoundary"},
    )

    assert patch["class_model"]["Classes"][0]["operations"][0]["operationId"] == new_operation
    assert (
        patch["class_model"]["Collaborations"][0]["calls"][0]["receiverOperationId"]
        == new_operation
    )
