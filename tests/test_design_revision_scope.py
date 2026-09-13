"""Frozen authority/downstream bounds for targeted design revisions."""
from __future__ import annotations

import pytest

from app.design import cascade
from app.design.rtm import exact_contract_links


def _rtm(*links: dict[str, str]) -> dict:
    return {
        "rows": [
            {"stage": "sequence_diagram", "element": "UC1"},
            {"stage": "api_spec", "element": "createOrder"},
            {"stage": "class_diagram", "element": "OrderControl"},
        ],
        "links": list(links),
        "impact": {},
    }


def _state() -> dict:
    return {
        "extracted_bce_classes": {
            "Classes": [{"className": "OrderControl", "operations": []}],
            "Collaborations": [],
        },
        "sequence_diagram_model": {"Diagrams": []},
        "api_spec_model": {"Endpoints": [], "Schemas": []},
    }


def test_exact_contract_links_preserve_direction_and_relation() -> None:
    rtm = _rtm(
        {"from": "api_spec:createOrder", "to": "class_diagram:OrderControl", "relation": "binds"},
        {"from": "api_spec:createOrder", "to": "sequence_diagram:UC1", "relation": "implements"},
    )

    assert exact_contract_links(
        rtm, "api_spec", "createOrder", direction="outgoing", relations={"binds"}
    ) == [{
        "from": "api_spec:createOrder",
        "to": "class_diagram:OrderControl",
        "relation": "binds",
    }]
    assert exact_contract_links(rtm, "sequence_diagram", "UC1", direction="incoming") == [{
        "from": "api_spec:createOrder",
        "to": "sequence_diagram:UC1",
        "relation": "implements",
    }]


def test_direct_sequence_revision_is_rejected_before_any_reviser(monkeypatch) -> None:
    rtm = _rtm({
        "from": "sequence_diagram:UC1",
        "to": "class_diagram:OrderControl",
        "relation": "invokes",
    })
    calls: list[str] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: rtm)
    monkeypatch.setattr(cascade, "_apply", lambda spec, *_args, **_kwargs: calls.append(spec.stage))

    with pytest.raises(cascade.UnapprovedScopeExpansion, match="deterministic class projections"):
        cascade.revise_and_cascade(
            _state(),
            "sequence_diagram:UC1",
            "Change contract",
            approved_authority_targets={"class_diagram:OrderControl"},
        )

    assert calls == []


def test_api_revision_rejects_extra_class_authority_and_stays_local(monkeypatch) -> None:
    rtm = _rtm({
        "from": "api_spec:createOrder",
        "to": "class_diagram:OrderControl",
        "relation": "binds",
    }, {
        "from": "api_spec:createOrder",
        "to": "sequence_diagram:UC1",
        "relation": "implements",
    })
    calls: list[tuple[str, set[str]]] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: rtm)
    monkeypatch.setattr(cascade, "affected_by_element", lambda *_args: [])

    def apply(spec, state, _feedback, targets, **_kwargs):
        calls.append((spec.stage, set(targets)))
        return {spec.model_key: state.get(spec.model_key) or {}}

    def project(spec, state, targets):
        calls.append((spec.stage, set(targets)))
        return {spec.model_key: state.get(spec.model_key) or {}}

    monkeypatch.setattr(cascade, "_apply", apply)
    monkeypatch.setattr(cascade, "_apply_projection", project)
    with pytest.raises(cascade.UnapprovedScopeExpansion, match="additional authority"):
        cascade.revise_and_cascade(
            _state(),
            "api_spec:createOrder",
            "Change contract",
            approved_authority_targets={"class_diagram:OrderControl"},
        )

    assert calls == []

    result = cascade.revise_and_cascade(
        _state(),
        "api_spec:createOrder",
        "Change contract",
        approved_authority_targets={"api_spec:createOrder"},
    )

    assert calls == [("api_spec", {"createOrder"})]
    assert result["touched"] == {"api_spec": ["createOrder"]}


def test_frozen_downstream_scope_rejects_an_untargeted_edit_before_reviser(monkeypatch) -> None:
    rtm = _rtm()
    rtm["impact"] = {"class:OrderControl": ["api_spec:createOrder"]}
    calls: list[str] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: rtm)
    monkeypatch.setattr(cascade, "_apply", lambda spec, *_args, **_kwargs: calls.append(spec.stage))

    with pytest.raises(cascade.UnapprovedScopeExpansion, match="downstream scope excludes"):
        cascade.revise_and_cascade(
            _state(),
            "class_diagram:OrderControl",
            "Add field",
            approved_downstream_targets=set(),
        )

    assert calls == []


def test_operation_scope_uses_exact_contract_links_not_its_whole_boundary() -> None:
    state = _state()
    state["extracted_bce_classes"] = {
        "Classes": [
            {
                "className": "OrderBoundary",
                "operations": [
                    {
                            "operationId": "OrderBoundary::createOrder(orderId:UUID)",
                            "name": "createOrder",
                    },
                    {
                            "operationId": "OrderBoundary::cancelOrder(orderId:UUID)",
                            "name": "cancelOrder",
                    },
                ],
            },
            {
                "className": "OrderControl",
                "operations": [
                    {
                        "operationId": "OrderControl::createOrder(orderId:UUID)",
                        "name": "createOrder",
                    },
                    {
                        "operationId": "OrderControl::cancelOrder(orderId:UUID)",
                        "name": "cancelOrder",
                    },
                ],
            },
        ],
        "Collaborations": [
            {
                "collaborationId": use_case,
                "calls": [
                    {
                        "receiverOperationId": (
                            f"OrderBoundary::{operation}Order(orderId:UUID)"
                        )
                    },
                    {
                        "receiverOperationId": (
                            f"OrderControl::{operation}Order(orderId:UUID)"
                        )
                    },
                ],
            }
            for use_case, operation in (("UC1", "create"), ("UC2", "cancel"))
        ],
    }
    state["sequence_diagram_model"] = {
        "Diagrams": [{"use_case_id": "UC1"}, {"use_case_id": "UC2"}]
    }
    state["api_spec_model"] = {
        "Endpoints": [
            {
                "operation_id": operation,
                "interaction_id": (
                        f"OrderBoundary::{operation}Order(orderId:UUID) -> "
                        f"OrderControl::{operation}Order(orderId:UUID)"
                ),
                "source_classes": ["OrderBoundary", "OrderControl"],
                "use_case_ids": [use_case],
                "control_binding": {
                    "control": "OrderControl",
                    "method": f"{operation}Order",
                },
            }
            for use_case, operation in (("UC1", "create"), ("UC2", "cancel"))
        ],
        "Schemas": [],
    }

    scope = cascade._frozen_cascade_scope(
        state,
        cascade.build_design_rtm(state),
        "class_diagram",
        "OrderBoundary::createOrder(orderId:UUID)",
        {"sequence_diagram:UC1", "api_spec:create"},
    )

    assert scope == {
        "sequence_diagram": {"UC1"},
        "api_spec": {"create"},
    }


def test_class_operation_uses_owning_class_merge_unit_without_widening_reviser(monkeypatch) -> None:
    operation = "OrderControl::createOrder(): void"
    state = _state()
    state["extracted_bce_classes"]["Classes"][0]["operations"] = [{"operationId": operation}]
    rtm = _rtm()
    rtm["rows"].append({"stage": "class_diagram", "element": operation})
    observed: list[tuple[set[str], set[str]]] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: rtm)
    monkeypatch.setattr(cascade, "affected_by_element", lambda *_args: [])

    def apply(spec, current, _feedback, targets, **kwargs):
        observed.append((set(targets), set(kwargs["revision_targets"])))
        return {spec.model_key: current.get(spec.model_key) or {}}

    monkeypatch.setattr(cascade, "_apply", apply)
    cascade.revise_and_cascade(state, f"class_diagram:{operation}", "Revise operation")

    assert observed == [({"OrderControl"}, {operation})]


def test_direct_collaboration_uses_its_own_merge_unit(monkeypatch) -> None:
    state = _state()
    state["extracted_bce_classes"]["Collaborations"] = [
        {"collaborationId": "UC1", "calls": []}
    ]
    rtm = _rtm()
    rtm["rows"].append({"stage": "class_diagram", "element": "UC1"})
    observed: list[tuple[set[str], set[str]]] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: rtm)
    monkeypatch.setattr(cascade, "affected_by_element", lambda *_args: [])

    def apply(spec, current, _feedback, targets, **kwargs):
        observed.append((set(targets), set(kwargs["revision_targets"])))
        return {spec.model_key: current.get(spec.model_key) or {}}

    monkeypatch.setattr(cascade, "_apply", apply)
    cascade.revise_and_cascade(state, "class_diagram:UC1", "Revise collaboration")

    assert observed == [({"UC1"}, {"UC1"})]


def test_class_revision_receives_companion_collaborations_as_read_only_context(
    monkeypatch,
) -> None:
    state = _state()
    state["extracted_bce_classes"] = {
        "Classes": [{"className": "CourseOffering", "operations": []}],
        "Collaborations": [
            {"collaborationId": "UC3:main:1", "useCaseIds": ["UC3"], "calls": []},
            {"collaborationId": "UC5:main:1", "useCaseIds": ["UC5"], "calls": []},
        ],
    }
    rtm = _rtm()
    rtm["rows"] = [
        {"stage": "class_diagram", "element": "CourseOffering"},
        {"stage": "class_diagram", "element": "UC3:main:1"},
        {"stage": "class_diagram", "element": "UC5:main:1"},
    ]
    observed: list[tuple[set[str], set[str]]] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: rtm)
    monkeypatch.setattr(cascade, "affected_by_element", lambda *_args: [])

    def apply(spec, current, _feedback, targets, **kwargs):
        observed.append((set(targets), set(kwargs["revision_targets"])))
        return {spec.model_key: current.get(spec.model_key) or {}}

    monkeypatch.setattr(cascade, "_apply", apply)
    cascade.revise_and_cascade(
        state,
        "class_diagram:CourseOffering",
        "Add a decrement operation.",
        revision_context_targets={
            "class_diagram:UC3:main:1",
            "class_diagram:UC5:main:1",
        },
    )

    assert observed == [
        (
            {"CourseOffering"},
            {"CourseOffering", "UC3:main:1", "UC5:main:1"},
        )
    ]
