from __future__ import annotations

from dataclasses import replace

from app.design import service
from app.design.graphs import subgraphs
from app.design.knowledge.detectors import Finding
from app.design.nodes.artifact import DesignArtifactSpec, check_node, merge_model
from app.validation import stable_digest


def _finding(model, _state):
    item = next(entry for entry in model["Endpoints"] if entry["name"] == "create")
    if item.get("broken"):
        return [Finding("api.contract", "contract mismatch", "create")]
    return []


def test_batch_aware_repair_receives_the_exact_repair_batch() -> None:
    received: list[list[Finding]] = []

    spec = DesignArtifactSpec(
        stage="api_spec",
        model_key="api_spec_model",
        content_key="api_spec",
        valid_key="api_spec_syntax_valid",
        errors_key="api_spec_syntax_errors",
        feedback_key="api_spec_feedback",
        empty={},
        extract=lambda _state: {},
        revise=lambda model, _feedback, _state, _targets: model,
        render=str,
        validate=lambda _content: {"syntax_valid": True, "syntax_errors": []},
        check=lambda model, _state: (
            [] if model.get("broken")
            else [Finding("api.contract", "contract mismatch", "create")]
        ),
        repair=lambda model, _feedback, _state, _targets: model,
        repair_batch=lambda model, _feedback, _state, _targets, batch: (
            received.append(list(batch)) or {**model, "broken": True}
        ),
        check_key="api_spec_check",
    )

    check_node(spec)({"api_spec_model": {"broken": False}})

    assert [[item.rule_id for item in batch] for batch in received] == [["api.contract"]]


def test_batch_repair_splits_class_findings_by_mapped_use_case_without_diagrams() -> None:
    received: list[tuple[set[str], list[str]]] = []

    def check(model, _state):
        return [
            Finding("class.public-contract-semantic", "missing contract", use_case_id)
            for use_case_id, broken in model["broken"].items()
            if broken
        ]

    def repair(model, _feedback, _state, targets, batch):
        received.append((set(targets), [item.location for item in batch]))
        return {
            **model,
            "broken": {
                use_case_id: False if use_case_id in targets else broken
                for use_case_id, broken in model["broken"].items()
            },
        }

    spec = DesignArtifactSpec(
        stage="class_diagram",
        model_key="class_diagram_model",
        content_key="class_diagram",
        valid_key="class_diagram_syntax_valid",
        errors_key="class_diagram_syntax_errors",
        feedback_key="class_diagram_feedback",
        empty={},
        extract=lambda _state: {},
        revise=lambda model, _feedback, _state, _targets: model,
        render=str,
        validate=lambda _content: {"syntax_valid": True, "syntax_errors": []},
        check=check,
        repair=lambda model, _feedback, _state, _targets: model,
        repair_batch=repair,
        repair_target_mapper=lambda _model, _state, findings: {
            item.location for item in findings
        },
        check_key="class_diagram_check",
    )

    check_node(spec)({"class_diagram_model": {"broken": {"UC5": True, "UC9": True}}})

    assert received == [({"UC5"}, ["UC5"]), ({"UC9"}, ["UC9"])]


def test_batch_repair_keeps_its_operation_change_while_legacy_repair_still_merges() -> None:
    original = {
        "Classes": [{"className": "RegistrationControl", "broken": True}],
        "Collaborations": [{"collaborationId": "UC5"}],
    }
    revised = {
        "Classes": [{"className": "RegistrationControl", "broken": False}],
        "Collaborations": [{"collaborationId": "UC5"}],
    }
    spec = DesignArtifactSpec(
        stage="class_diagram",
        model_key="class_diagram_model",
        content_key="class_diagram",
        valid_key="class_diagram_syntax_valid",
        errors_key="class_diagram_syntax_errors",
        feedback_key="class_diagram_feedback",
        empty={},
        extract=lambda _state: {},
        revise=lambda model, _feedback, _state, _targets: model,
        render=str,
        validate=lambda _content: {"syntax_valid": True, "syntax_errors": []},
        elements={
            "Classes": lambda item: item["className"],
            "Collaborations": lambda item: item["collaborationId"],
        },
    )

    batch_spec = replace(
        spec,
        check=lambda model, _state: [
            Finding("class.public-contract-semantic", "missing contract", "UC5")
        ] if model["Classes"][0]["broken"] else [],
        repair=lambda model, _feedback, _state, _targets: model,
        repair_batch=lambda *_args: revised,
        repair_target_mapper=lambda _model, _state, _findings: {"UC5"},
    )
    result = check_node(batch_spec)({"class_diagram_model": original})

    assert result["class_diagram_model"]["Classes"] == revised["Classes"]
    assert merge_model(spec, original, revised, {"UC5"})["Classes"] == original["Classes"]


def test_targeted_revision_repairs_only_matching_technical_finding(monkeypatch):
    original = {
        "api_spec_model": {
            "Endpoints": [
                {"name": "create", "broken": False},
                {"name": "list", "untouched": True},
            ],
            "Schemas": [],
        },
        "api_spec": "original",
        "api_spec_syntax_valid": True,
        "api_spec_syntax_errors": [],
    }
    broken_revision = {
        **original,
        "api_spec_model": {
            "Endpoints": [
                {"name": "create", "broken": True},
                {"name": "list", "untouched": True},
            ],
            "Schemas": [],
        },
        "api_spec": "revised",
    }
    repairs: list[set[str]] = []

    def repair(model, _feedback, _state, targets):
        repairs.append(set(targets))
        return {
            **model,
            "Endpoints": [
                {**item, "broken": False} if item["name"] in targets else item
                for item in model["Endpoints"]
            ],
        }

    spec = DesignArtifactSpec(
        stage="api_spec",
        model_key="api_spec_model",
        content_key="api_spec",
        valid_key="api_spec_syntax_valid",
        errors_key="api_spec_syntax_errors",
        feedback_key="api_spec_feedback",
        empty={},
        extract=lambda _state: {},
        revise=lambda model, _feedback, _state, _targets: model,
        render=str,
        validate=lambda _content: {"syntax_valid": True, "syntax_errors": []},
        elements={"Endpoints": lambda item: item["name"]},
        check=_finding,
        repair=repair,
        check_key="api_spec_check",
    )
    monkeypatch.setitem(
        subgraphs.DESIGN_SPECS,
        "api_spec",
        spec,
    )
    monkeypatch.setattr(service, "_load_app", lambda _app_id: original)
    monkeypatch.setattr(
        service,
        "revise_and_cascade",
        lambda *_args, **_kwargs: {
            "state": broken_revision,
            "changed": ["api_spec"],
            "touched": {"api_spec": ["create"]},
            "related": [],
            "regenerated": {},
        },
    )
    monkeypatch.setattr(service, "sync_design_state", lambda *_args: None)
    monkeypatch.setattr(service, "persist_cascade", lambda *_args: None)
    monkeypatch.setattr(service, "to_web_response", dict)

    response = service.revise_design_elements(
        "00000000-0000-0000-0000-000000000001",
        service.BatchReviseRequest(revisions=[
            service.ReviseRequest(target="api_spec:create", feedback="revise")
        ]),
    )

    assert repairs == [{"create"}]
    assert response["api_spec_check"]["stopped"] == "clean", response["api_spec_check"]
    assert response["api_spec_model"]["Endpoints"] == [
        {"name": "create", "broken": False},
        {"name": "list", "untouched": True},
    ]
    assert response["api_spec_check"]["finding_details"] == []
    assert response["api_spec_check"]["repair_iters"] == 1

    decision_spec = replace(
        spec,
        check=lambda _model, _state: [Finding(
            "api.decision", "needs a user choice", "create",
            requires_user_input=True,
        )],
    )
    monkeypatch.setitem(subgraphs.DESIGN_SPECS, "api_spec", decision_spec)
    decision_response = service.revise_design_elements(
        "00000000-0000-0000-0000-000000000001",
        service.BatchReviseRequest(revisions=[
            service.ReviseRequest(target="api_spec:create", feedback="revise")
        ]),
    )
    assert repairs == [{"create"}]
    assert decision_response["revision_status"] == "stalled"
    assert decision_response["revision_validation"]["api_spec"]["repair_iters"] == 0
    assert decision_response["revision_validation"]["api_spec"]["finding_details"][0]["requires_user_input"] is True


def test_finding_metadata_keeps_legacy_text_and_resolution_hint():
    spec = replace(
        subgraphs.DESIGN_SPECS["api_spec"],
        check=lambda _model, _state: [Finding(
            "api.contract",
            "choice needed",
            "create",
            requires_user_input=True,
            origin="semantic",
        )],
        check_evidence=None,
        repair=None,
    )
    result = check_node(spec)({"api_spec_model": {"Endpoints": [], "Schemas": []}})
    report = result["api_spec_check"]

    assert report["findings"] == [
        "create: choice needed [api.contract · 알 수 없는 규칙]"
    ]
    assert report["finding_details"] == [{
        "rule_id": "api.contract",
        "message": "choice needed",
        "location": "create",
        "requires_user_input": True,
        "origin": "semantic",
        "authority_ref": "api_spec:create",
    }]


def test_unresolved_candidate_is_reported_without_persisting_or_syncing(monkeypatch):
    original = {
        "api_spec_model": {"Endpoints": [{"name": "create"}], "Schemas": []},
        "api_spec": "accepted",
        "api_spec_syntax_valid": True,
        "api_spec_syntax_errors": [],
    }
    candidate = {
        **original,
        "api_spec_model": {"Endpoints": [{"name": "create", "changed": True}], "Schemas": []},
        "api_spec": "candidate",
    }
    persisted: list[dict] = []
    synced: list[dict] = []
    spec = DesignArtifactSpec(
        stage="api_spec",
        model_key="api_spec_model",
        content_key="api_spec",
        valid_key="api_spec_syntax_valid",
        errors_key="api_spec_syntax_errors",
        feedback_key="api_spec_feedback",
        empty={},
        extract=lambda _state: {},
        revise=lambda model, _feedback, _state, _targets: model,
        render=str,
        validate=lambda _content: {"syntax_valid": True, "syntax_errors": []},
        elements={"Endpoints": lambda item: item["name"]},
        check=lambda _model, _state: [Finding(
            "api.contract", "candidate contract is incomplete", "create"
        )],
        check_key="api_spec_check",
    )
    monkeypatch.setitem(subgraphs.DESIGN_SPECS, "api_spec", spec)
    monkeypatch.setattr(service, "_load_app", lambda _app_id: original)
    monkeypatch.setattr(
        service,
        "revise_and_cascade",
        lambda *_args, **_kwargs: {
            "state": candidate,
            "changed": ["api_spec"],
            "touched": {"api_spec": ["create"]},
            "related": [],
            "regenerated": {},
        },
    )
    monkeypatch.setattr(service, "sync_design_state", lambda _app, state: synced.append(state))
    monkeypatch.setattr(service, "persist_cascade", lambda _app, result: persisted.append(result))

    def web_response(state):
        check = state.get("api_spec_check") or {}
        return {
            "artifacts": state.get("api_spec_model"),
            "validation": {
                "api_spec": {
                    "errors": list(state.get("api_spec_syntax_errors") or []),
                    "findings": list(check.get("findings") or []),
                    "finding_details": list(check.get("finding_details") or []),
                    "check_status": check.get("stopped"),
                }
            },
        }

    monkeypatch.setattr(service, "to_web_response", web_response)

    response = service.revise_design_elements(
        "00000000-0000-0000-0000-000000000001",
        service.BatchReviseRequest(revisions=[
            service.ReviseRequest(target="api_spec:create", feedback="revise")
        ]),
    )

    assert response["revision_status"] == "stalled"
    assert response["changed"] == []
    assert response["touched"] == {}
    assert response["artifacts"] == original["api_spec_model"]
    assert response["revision_validation"]["api_spec"]["findings"] == [
        "create: candidate contract is incomplete [api.contract · 알 수 없는 규칙]"
    ]
    assert synced == []
    assert persisted == []


def test_nested_class_call_finding_merges_its_collaboration_owner(monkeypatch):
    original = {
        "extracted_bce_classes": {
            "Classes": [],
            "Collaborations": [{
                "collaborationId": "UC8",
                "calls": [{"callId": "UC8::call:1", "broken": False}],
            }],
        },
        "class_diagram_puml": "original",
        "class_diagram_syntax_valid": True,
        "class_diagram_syntax_errors": [],
    }
    revised = {
        **original,
        "extracted_bce_classes": {
            "Classes": [],
            "Collaborations": [{
                "collaborationId": "UC8",
                "calls": [{"callId": "UC8::call:1", "broken": True}],
            }],
        },
        "class_diagram_puml": "revised",
    }
    reviser_targets: list[set[str]] = []
    evidence_digests: list[str] = []

    def check(model, _state):
        call = model["Collaborations"][0]["calls"][0]
        return [Finding(
            "class.collaboration.contract", "bad call contract", "UC8::call:1"
        )] if call.get("broken") else []

    def repair(model, _feedback, _state, targets):
        reviser_targets.append(set(targets))
        return {
            **model,
            "Collaborations": [
                {
                    **collaboration,
                    "calls": [
                        {**call, "broken": False}
                        if collaboration["collaborationId"] in targets
                        or call["callId"] in targets
                        else call
                        for call in collaboration["calls"]
                    ],
                }
                for collaboration in model["Collaborations"]
            ],
        }

    def evidence(model, _state):
        digest = stable_digest(model)
        evidence_digests.append(digest)
        return {"modelDigest": digest}

    spec = DesignArtifactSpec(
        stage="class_diagram",
        model_key="extracted_bce_classes",
        content_key="class_diagram_puml",
        valid_key="class_diagram_syntax_valid",
        errors_key="class_diagram_syntax_errors",
        feedback_key="class_diagram_feedback",
        empty="",
        extract=lambda _state: {},
        revise=lambda model, _feedback, _state, _targets: model,
        render=str,
        validate=lambda _content: {"syntax_valid": True, "syntax_errors": []},
        elements={
            "Classes": lambda item: item.get("className", ""),
            "Collaborations": lambda item: item.get("collaborationId", ""),
        },
        check=check,
        check_evidence=evidence,
        repair=repair,
        repair_target_mapper=subgraphs._class_repair_targets,
        check_key="class_diagram_check",
    )
    monkeypatch.setitem(subgraphs.DESIGN_SPECS, "class_diagram", spec)
    monkeypatch.setattr(service, "_load_app", lambda _app_id: original)
    monkeypatch.setattr(
        service,
        "revise_and_cascade",
        lambda *_args, **_kwargs: {
            "state": revised,
            "changed": ["class_diagram"],
            "touched": {"class_diagram": ["UC8"]},
            "related": [],
            "regenerated": {},
        },
    )
    monkeypatch.setattr(service, "sync_design_state", lambda *_args: None)
    monkeypatch.setattr(service, "persist_cascade", lambda *_args: None)
    monkeypatch.setattr(service, "to_web_response", dict)

    response = service.revise_design_elements(
        "00000000-0000-0000-0000-000000000001",
        service.BatchReviseRequest(revisions=[
            service.ReviseRequest(target="class_diagram:UC8", feedback="revise")
        ]),
    )

    assert reviser_targets == [{"UC8"}]
    assert response["extracted_bce_classes"]["Collaborations"][0]["calls"][0]["broken"] is False
    assert response["class_diagram_check"]["finding_details"] == []
    assert response["class_diagram_check"]["semanticEvidence"]["modelDigest"] == stable_digest(
        response["extracted_bce_classes"]
    )
    assert len(evidence_digests) == 2

    decision_spec = replace(
        spec,
        check=lambda _model, _state: [Finding(
            "class.collaboration.decision",
            "a user choice is required",
            "UC8::call:1",
            requires_user_input=True,
        )],
    )
    monkeypatch.setitem(subgraphs.DESIGN_SPECS, "class_diagram", decision_spec)
    decision_response = service.revise_design_elements(
        "00000000-0000-0000-0000-000000000001",
        service.BatchReviseRequest(revisions=[
            service.ReviseRequest(target="class_diagram:UC8", feedback="revise")
        ]),
    )
    assert reviser_targets == [{"UC8"}]
    assert decision_response["revision_status"] == "stalled"
    assert decision_response["revision_validation"]["class_diagram"]["repair_iters"] == 0
    assert len(evidence_digests) == 3
