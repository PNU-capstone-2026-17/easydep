from __future__ import annotations

from types import SimpleNamespace

from app.design import cascade
from app.design.nodes.artifact import DesignArtifactSpec


def _validation(_content):
    return {"syntax_valid": True, "syntax_errors": []}


def test_apply_uses_state_revision_and_preserves_untargeted_diagrams() -> None:
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

    def revise_state(_current, _feedback, _state, _targets):
        return {
            "sequence_model": revised,
            "class_model": {"Collaborations": [{"collaborationId": "UC9"}]},
            "revised_upstream_stages": ["class_diagram"],
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
        revise=lambda *_args: (_ for _ in ()).throw(AssertionError("wrong revision path")),
        revise_state=revise_state,
        render=lambda model: str(model),
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
    assert patch["class_model"] == {
        "Collaborations": [{"collaborationId": "UC9"}]
    }
    assert patch["revised_upstream_stages"] == ["class_diagram"]


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
        render=lambda model: str(model),
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


def test_upstream_owned_sequence_revision_skips_second_reverse_class_edit(
    monkeypatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        cascade,
        "DESIGN_SPECS",
        {
            "class_diagram": SimpleNamespace(
                stage="class_diagram", model_key="class_model", elements={}
            ),
            "sequence_diagram": SimpleNamespace(
                stage="sequence_diagram",
                model_key="sequence_model",
                elements={"Diagrams": lambda item: item.get("use_case_id", "")},
            ),
            "api_spec": SimpleNamespace(
                stage="api_spec", model_key="api_model", elements={}
            ),
            "deployment_diagram": SimpleNamespace(
                stage="deployment_diagram", model_key="deployment_model", elements={}
            ),
        },
    )
    monkeypatch.setattr(
        cascade,
        "build_design_rtm",
        lambda _state: {
            "rows": [{"stage": "sequence_diagram", "element": "UC9"}],
            "links": [],
        },
    )
    monkeypatch.setattr(
        cascade,
        "linked_elements",
        lambda _rtm, stage, _element: (
            ["class_diagram:UserBoundary"] if stage == "sequence_diagram" else []
        ),
    )
    monkeypatch.setattr(cascade, "affected_by_element", lambda *_args: [])

    def apply(spec, _state, _feedback, _targets):
        calls.append(spec.stage)
        if spec.stage == "sequence_diagram":
            return {
                "sequence_model": {"Diagrams": [{"use_case_id": "UC9"}]},
                "class_model": {"Collaborations": [{"collaborationId": "UC9"}]},
                "revised_upstream_stages": ["class_diagram"],
            }
        raise AssertionError("class diagram must not be revised a second time")

    monkeypatch.setattr(cascade, "_apply", apply)

    result = cascade.revise_and_cascade(
        {"sequence_model": {}, "class_model": {}},
        "sequence_diagram:UC9",
        "Place extension 1a at its branch",
    )

    assert calls == ["sequence_diagram"]
    assert result["changed"] == ["class_diagram", "sequence_diagram"]
    assert result["state"]["revised_upstream_stages"] == []
