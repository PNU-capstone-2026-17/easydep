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


def test_unapproved_reverse_authority_stops_before_any_reviser(monkeypatch) -> None:
    rtm = _rtm({
        "from": "sequence_diagram:UC1",
        "to": "class_diagram:OrderControl",
        "relation": "invokes",
    })
    calls: list[str] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: rtm)
    monkeypatch.setattr(cascade, "_apply", lambda spec, *_args, **_kwargs: calls.append(spec.stage))

    with pytest.raises(cascade.UnapprovedScopeExpansion, match="approved class authority"):
        cascade.revise_and_cascade(_state(), "sequence_diagram:UC1", "Change contract")

    assert calls == []


def test_approved_reverse_authority_is_bounded_to_exact_class(monkeypatch) -> None:
    rtm = _rtm({
        "from": "api_spec:createOrder",
        "to": "class_diagram:OrderControl",
        "relation": "binds",
    })
    calls: list[tuple[str, set[str]]] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: rtm)
    monkeypatch.setattr(cascade, "affected_by_element", lambda *_args: [])

    def apply(spec, state, _feedback, targets, **_kwargs):
        calls.append((spec.stage, set(targets)))
        return {spec.model_key: state.get(spec.model_key) or {}}

    monkeypatch.setattr(cascade, "_apply", apply)
    result = cascade.revise_and_cascade(
        _state(),
        "api_spec:createOrder",
        "Change contract",
        approved_authority_targets={"class_diagram:OrderControl"},
        approved_downstream_targets=set(),
    )

    assert calls == [
        ("class_diagram", {"OrderControl"}),
        ("api_spec", {"createOrder"}),
    ]
    assert result["touched"] == {
        "api_spec": ["createOrder"],
        "class_diagram": ["OrderControl"],
    }


def test_sequence_without_exact_link_never_guesses_or_calls_reviser(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cascade, "build_design_rtm", lambda _state: _rtm())
    monkeypatch.setattr(cascade, "_apply", lambda spec, *_args, **_kwargs: calls.append(spec.stage))

    with pytest.raises(cascade.UnapprovedScopeExpansion, match="no exact class contract link"):
        cascade.revise_and_cascade(_state(), "sequence_diagram:UC1", "Change call")

    assert calls == []


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
