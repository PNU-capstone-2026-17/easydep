"""Typed source graph and finite argument-binding selection."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.design.services.executable_behavior.calls import catalog_operations
from app.design.services.executable_behavior.contracts import (
    ValidatedBindingPlan,
    ValidatedCallStructure,
    ValidatedCatalogDraft,
    canonical_digest,
    make_provenance,
    provenance_matches_subject,
)
from app.design.services.executable_behavior.operations import canonical_type

SOURCE_KINDS = frozenset({
    "actor_input",
    "precondition",
    "runtime",
    "derived",
    "call_result",
})
BINDING_VALIDATOR_VERSION = "executable-behavior.bindings.v2"


@dataclass(frozen=True)
class Source:
    source_ref: str
    type_name: str
    kind: str = "actor_input"
    scope: str = "global"
    semantic_role: str = ""
    producer_call_id: str | None = None
    optional: bool = False
    guard_ref: str | None = None
    exported: bool = False
    required_fields: tuple[str, ...] = ()
    field_sources: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "sourceRef": self.source_ref,
            "type": self.type_name,
            "kind": self.kind,
            "scope": self.scope,
            "semanticRole": self.semantic_role,
            "producerCallId": self.producer_call_id,
            "optional": self.optional,
            "guardRef": self.guard_ref,
            "exported": self.exported,
            "requiredFields": list(self.required_fields),
            "fieldSources": list(self.field_sources),
        }


@dataclass(frozen=True)
class BindingCandidate:
    source: Source
    score: int = 0


@dataclass(frozen=True)
class BindingTarget:
    call_id: str
    parameter_name: str
    parameter: dict[str, Any]
    candidates: tuple[BindingCandidate, ...]


class BindingSelectionError(ValueError):
    """A required source is absent, ambiguous, or outside the finite candidates."""


class BindingSelector(Protocol):
    def select(
        self,
        parameter: Mapping[str, Any],
        candidates: tuple[BindingCandidate, ...],
    ) -> BindingCandidate: ...


class UniqueBindingSelector:
    def select(
        self,
        parameter: Mapping[str, Any],
        candidates: tuple[BindingCandidate, ...],
    ) -> BindingCandidate:
        if len(candidates) != 1:
            raise BindingSelectionError("binding requires exactly one candidate")
        return candidates[0]


def _source(value: Source | Mapping[str, Any]) -> Source:
    if isinstance(value, Source):
        return value
    return Source(
        source_ref=str(value.get("sourceRef") or value.get("source_ref") or ""),
        type_name=str(value.get("type") or value.get("type_name") or ""),
        kind=str(value.get("kind") or "actor_input"),
        scope=str(value.get("scope") or "global"),
        semantic_role=str(
            value.get("semanticRole") or value.get("semantic_role") or ""
        ),
        producer_call_id=(
            str(producer)
            if (producer := value.get("producerCallId") or value.get("producer_call_id"))
            else None
        ),
        optional=bool(value.get("optional") or False),
        guard_ref=(
            str(guard)
            if (guard := value.get("guardRef") or value.get("guard_ref"))
            else None
        ),
        exported=bool(value.get("exported") or False),
        required_fields=tuple(
            str(item)
            for item in value.get("requiredFields") or value.get("required_fields") or ()
        ),
        field_sources=tuple(
            str(item)
            for item in value.get("fieldSources") or value.get("field_sources") or ()
        ),
    )


def _calls(structure: ValidatedCallStructure) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in structure.payload.get("calls", []) or []
        if isinstance(item, Mapping)
    ]


def _ancestors(call_id: str, parent_by_call: Mapping[str, str | None]) -> set[str]:
    result: set[str] = set()
    current = parent_by_call.get(call_id)
    while current is not None:
        if current in result:
            break
        result.add(current)
        current = parent_by_call.get(current)
    return result


def _binding_context(calls: list[dict[str, Any]]) -> tuple[
    dict[str, int], dict[str, str | None]
]:
    call_order = {
        str(call.get("callId")): index for index, call in enumerate(calls)
    }
    parent_by_call = {
        str(call.get("callId")): (
            str(call.get("parentCallId")) if call.get("parentCallId") else None
        )
        for call in calls
    }
    return call_order, parent_by_call


def _validate_sources(
    sources: tuple[Source, ...],
    calls: list[dict[str, Any]],
    operations: Mapping[str, Mapping[str, Any]],
) -> None:
    expected_results: dict[str, tuple[str, str, bool]] = {}
    for call in calls:
        operation = operations.get(str(call.get("receiverOperationId") or ""))
        if operation is None:
            continue
        return_type = canonical_type(operation.get("returnType", "void"))
        if return_type != "void":
            call_id = str(call.get("callId") or "")
            expected_results[f"call:{call_id}.return"] = (
                return_type,
                str(call.get("groupKey") or ""),
                bool(call.get("exportResult") or False),
            )

    actual_results: set[str] = set()
    for source in sources:
        if (
            not source.source_ref
            or not canonical_type(source.type_name)
            or not source.scope
            or source.kind not in SOURCE_KINDS
        ):
            raise BindingSelectionError("binding source declaration is invalid")
        if source.kind != "call_result":
            if source.producer_call_id is not None or source.source_ref.startswith("call:"):
                raise BindingSelectionError(
                    "only canonical call-result sources may name a producer call"
                )
            continue
        expected = expected_results.get(source.source_ref)
        if expected is None or source.producer_call_id is None:
            raise BindingSelectionError("call-result source is not produced by this plan")
        expected_ref = f"call:{source.producer_call_id}.return"
        if source.source_ref != expected_ref:
            raise BindingSelectionError("call-result source identity is not canonical")
        expected_type, expected_scope, expected_exported = expected
        if (
            canonical_type(source.type_name) != expected_type
            or source.scope != expected_scope
            or source.exported != expected_exported
        ):
            raise BindingSelectionError(
                "call-result source does not match its producer contract"
            )
        actual_results.add(source.source_ref)
    if actual_results != set(expected_results):
        raise BindingSelectionError("binding source graph omits a call result")


class BindingResolver:
    """Compute finite compatible sources before invoking an injected selector."""

    def __init__(
        self,
        selector: BindingSelector
        | Callable[[Mapping[str, Any], tuple[BindingCandidate, ...]], BindingCandidate]
        | None = None,
    ) -> None:
        self.selector = selector or UniqueBindingSelector()

    def candidates(
        self,
        parameter: Mapping[str, Any],
        sources: Iterable[Source],
        *,
        call_id: str,
        group_key: str,
        guard_refs: Iterable[str],
        call_order: Mapping[str, int],
        parent_by_call: Mapping[str, str | None],
    ) -> tuple[BindingCandidate, ...]:
        expected = canonical_type(parameter.get("type"))
        expected_role = str(
            parameter.get("semanticRole") or parameter.get("semantic_role") or ""
        ).casefold()
        consumer_index = call_order[call_id]
        ancestors = _ancestors(call_id, parent_by_call)
        guards = set(guard_refs)
        result: list[BindingCandidate] = []
        for source in sources:
            if not source.source_ref or canonical_type(source.type_name) != expected:
                continue
            if expected_role and source.semantic_role.casefold() != expected_role:
                continue
            if source.scope not in {"global", group_key} and not source.exported:
                continue
            if source.optional and source.guard_ref not in guards:
                continue
            if source.required_fields and not set(source.required_fields) <= set(
                source.field_sources
            ):
                continue
            if source.producer_call_id is not None:
                producer_index = call_order.get(source.producer_call_id)
                if (
                    producer_index is None
                    or producer_index >= consumer_index
                    or source.producer_call_id in ancestors
                ):
                    continue
            score = 2 if expected_role else 0
            if source.scope == group_key:
                score += 1
            result.append(BindingCandidate(source, score))
        return tuple(sorted(result, key=lambda item: (-item.score, item.source.source_ref)))

    def resolve(
        self,
        parameter: Mapping[str, Any],
        sources: Iterable[Source],
        **context: Any,
    ) -> Source:
        choices = self.candidates(parameter, sources, **context)
        if not choices:
            raise BindingSelectionError(
                f"no source for parameter {parameter.get('name', '')}"
            )
        selector = self.selector
        selected = (
            selector.select(parameter, choices)
            if hasattr(selector, "select")
            else selector(parameter, choices)
        )
        if selected not in choices:
            raise BindingSelectionError("selector returned a source outside candidate set")
        return selected.source


def _result_source(
    call: Mapping[str, Any], operation: Mapping[str, Any]
) -> Source | None:
    return_type = canonical_type(operation.get("returnType", "void"))
    if return_type == "void":
        return None
    call_id = str(call.get("callId") or "")
    return Source(
        source_ref=f"call:{call_id}.return",
        type_name=return_type,
        kind="call_result",
        scope=str(call.get("groupKey") or ""),
        producer_call_id=call_id,
        exported=bool(call.get("exportResult") or False),
    )


def binding_candidate_sets(
    catalog: ValidatedCatalogDraft,
    call_structure: ValidatedCallStructure,
    *,
    scenario: Mapping[str, Any],
    sources: Iterable[Source | Mapping[str, Any]],
) -> tuple[BindingTarget, ...]:
    """Expose the finite source sets so one UC-level selector call can choose."""
    if not provenance_matches_subject(catalog.provenance, catalog.payload):
        raise BindingSelectionError("catalog content changed after validation")
    if not provenance_matches_subject(
        call_structure.provenance, call_structure.payload
    ):
        raise BindingSelectionError("call structure changed after validation")
    scenario_digest = canonical_digest(scenario)
    if catalog.provenance.input_digests.get("scenario") != scenario_digest:
        raise BindingSelectionError("catalog and binding candidates use different scenarios")
    if call_structure.provenance.input_digests.get("scenario") != scenario_digest:
        raise BindingSelectionError(
            "call structure and binding candidates use different scenarios"
        )
    if call_structure.provenance.input_digests.get("catalog") != canonical_digest(
        catalog.model_dump(by_alias=True)
    ):
        raise BindingSelectionError("call structure uses a different catalog snapshot")

    calls = _calls(call_structure)
    operations = catalog_operations(catalog.payload)
    available_sources = [_source(item) for item in sources]
    if len({item.source_ref for item in available_sources}) != len(available_sources):
        raise BindingSelectionError("source references must be unique")
    if any(
        item.kind == "call_result" or item.producer_call_id is not None
        for item in available_sources
    ):
        raise BindingSelectionError(
            "call-result sources are derived from the validated call structure"
        )
    call_order, parent_by_call = _binding_context(calls)
    resolver = BindingResolver()
    targets: list[BindingTarget] = []
    for call in calls:
        call_id = str(call.get("callId") or "")
        operation_id = str(call.get("receiverOperationId") or "")
        operation = operations.get(operation_id)
        if operation is None:
            raise BindingSelectionError(
                f"unknown operation for binding: {operation_id}"
            )
        for parameter in operation.get("parameters", []) or []:
            if not isinstance(parameter, Mapping):
                continue
            normalized_parameter = dict(parameter)
            targets.append(BindingTarget(
                call_id=call_id,
                parameter_name=str(parameter.get("name") or ""),
                parameter=normalized_parameter,
                candidates=resolver.candidates(
                    normalized_parameter,
                    available_sources,
                    call_id=call_id,
                    group_key=str(call.get("groupKey") or ""),
                    guard_refs=tuple(call.get("guardRefs") or ()),
                    call_order=call_order,
                    parent_by_call=parent_by_call,
                ),
            ))
        result_source = _result_source(call, operation)
        if result_source is not None:
            available_sources.append(result_source)
    _validate_sources(tuple(available_sources), calls, operations)
    return tuple(targets)


def validate_binding_plan(
    catalog: ValidatedCatalogDraft,
    call_structure: ValidatedCallStructure,
    binding_plan: ValidatedBindingPlan,
) -> tuple[dict[str, Any], ...]:
    """Recheck a complete binding plan instead of trusting its status label."""
    if not provenance_matches_subject(catalog.provenance, catalog.payload):
        raise BindingSelectionError("catalog content changed after validation")
    if not provenance_matches_subject(
        call_structure.provenance, call_structure.payload
    ):
        raise BindingSelectionError("call structure changed after validation")
    if not provenance_matches_subject(binding_plan.provenance, binding_plan.payload):
        raise BindingSelectionError("binding plan changed after validation")
    scenario_digest = catalog.provenance.input_digests.get("scenario")
    if not scenario_digest:
        raise BindingSelectionError("catalog does not pin a scenario snapshot")
    if call_structure.provenance.input_digests.get("scenario") != scenario_digest:
        raise BindingSelectionError("call structure uses a different scenario snapshot")
    if binding_plan.provenance.input_digests.get("scenario") != scenario_digest:
        raise BindingSelectionError("binding plan uses a different scenario snapshot")
    catalog_snapshot = catalog.model_dump(by_alias=True)
    call_snapshot = call_structure.model_dump(by_alias=True)
    if call_structure.provenance.input_digests.get("catalog") != canonical_digest(
        catalog_snapshot
    ):
        raise BindingSelectionError("call structure uses a different catalog snapshot")
    if binding_plan.provenance.input_digests.get("catalog") != canonical_digest(
        catalog_snapshot
    ):
        raise BindingSelectionError("binding plan uses a different catalog snapshot")
    if binding_plan.provenance.input_digests.get("calls") != canonical_digest(
        call_snapshot
    ):
        raise BindingSelectionError("binding plan uses a different call snapshot")

    calls = _calls(call_structure)
    operations = catalog_operations(catalog.payload)
    source_values = binding_plan.payload.get("sources", []) or []
    source_list = tuple(
        _source(item) for item in source_values if isinstance(item, (Source, Mapping))
    )
    source_by_ref = {item.source_ref: item for item in source_list}
    if len(source_by_ref) != len(source_list):
        raise BindingSelectionError("binding sources must be explicit and unique")
    _validate_sources(source_list, calls, operations)
    call_order, parent_by_call = _binding_context(calls)
    resolver = BindingResolver()
    raw_bindings = [
        dict(item)
        for item in binding_plan.payload.get("bindings", []) or []
        if isinstance(item, Mapping)
    ]
    by_target: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_binding in raw_bindings:
        target = (
            str(raw_binding.get("callId") or ""),
            str(raw_binding.get("parameter") or ""),
        )
        if not all(target) or target in by_target:
            raise BindingSelectionError("binding targets must be explicit and unique")
        by_target[target] = raw_binding

    expected_targets: set[tuple[str, str]] = set()
    selected_sources_by_call: dict[str, set[str]] = {}
    for call in calls:
        call_id = str(call.get("callId") or "")
        operation_id = str(call.get("receiverOperationId") or "")
        operation = operations.get(operation_id)
        if operation is None:
            raise BindingSelectionError(f"unknown operation for binding: {operation_id}")
        for parameter in operation.get("parameters", []) or []:
            if not isinstance(parameter, Mapping):
                continue
            parameter_name = str(parameter.get("name") or "")
            target = (call_id, parameter_name)
            expected_targets.add(target)
            selected_binding = by_target.get(target)
            if selected_binding is None:
                raise BindingSelectionError(
                    f"missing binding for {call_id}#{parameter_name}"
                )
            source_ref = str(selected_binding.get("sourceRef") or "")
            used_sources = selected_sources_by_call.setdefault(call_id, set())
            if source_ref in used_sources:
                raise BindingSelectionError(
                    f"distinct parameters in {call_id} reuse source: {source_ref}"
                )
            used_sources.add(source_ref)
            choices = resolver.candidates(
                parameter,
                source_list,
                call_id=call_id,
                group_key=str(call.get("groupKey") or ""),
                guard_refs=tuple(call.get("guardRefs") or ()),
                call_order=call_order,
                parent_by_call=parent_by_call,
            )
            if source_ref not in {item.source.source_ref for item in choices}:
                raise BindingSelectionError(
                    f"source is not a compatible finite candidate: {source_ref}"
                )
    if set(by_target) != expected_targets:
        raise BindingSelectionError("binding plan contains an unknown target")
    return tuple(raw_bindings)


def validated_binding_plan(
    catalog: ValidatedCatalogDraft,
    call_structure: ValidatedCallStructure,
    *,
    scenario: Mapping[str, Any],
    sources: Iterable[Source | Mapping[str, Any]],
    selector: BindingSelector
    | Callable[[Mapping[str, Any], tuple[BindingCandidate, ...]], BindingCandidate]
    | None = None,
    selections: Mapping[tuple[str, str], str] | None = None,
    revision: int = 1,
) -> ValidatedBindingPlan:
    """Resolve every call parameter from explicit, typed finite sources."""
    if not provenance_matches_subject(catalog.provenance, catalog.payload):
        raise BindingSelectionError("catalog content changed after validation")
    if not provenance_matches_subject(
        call_structure.provenance, call_structure.payload
    ):
        raise BindingSelectionError("call structure changed after validation")
    scenario_digest = canonical_digest(scenario)
    if catalog.provenance.input_digests.get("scenario") != scenario_digest:
        raise BindingSelectionError("catalog and binding plan use different scenarios")
    if call_structure.provenance.input_digests.get("scenario") != scenario_digest:
        raise BindingSelectionError("call structure and binding plan use different scenarios")
    if call_structure.provenance.input_digests.get("catalog") != canonical_digest(
        catalog.model_dump(by_alias=True)
    ):
        raise BindingSelectionError("call structure uses a different catalog snapshot")
    calls = _calls(call_structure)
    operations = catalog_operations(catalog.payload)
    source_list = tuple(_source(item) for item in sources)
    if len({item.source_ref for item in source_list}) != len(source_list):
        raise BindingSelectionError("source references must be unique")
    if any(
        item.kind == "call_result" or item.producer_call_id is not None
        for item in source_list
    ):
        raise BindingSelectionError(
            "call-result sources are derived from the validated call structure"
        )
    call_order, parent_by_call = _binding_context(calls)
    resolver = BindingResolver(selector)
    bindings: list[dict[str, Any]] = []
    selected_sources_by_call: dict[str, set[str]] = {}
    available_sources = list(source_list)
    requested_selections = dict(selections or {})
    used_selections: set[tuple[str, str]] = set()
    for call in calls:
        call_id = str(call.get("callId") or "")
        operation_id = str(call.get("receiverOperationId") or "")
        operation = operations.get(operation_id)
        if operation is None:
            raise BindingSelectionError(f"unknown operation for binding: {operation_id}")
        for parameter in operation.get("parameters", []) or []:
            if not isinstance(parameter, Mapping):
                continue
            target = (call_id, str(parameter.get("name") or ""))
            choices = resolver.candidates(
                parameter,
                available_sources,
                call_id=call_id,
                group_key=str(call.get("groupKey") or ""),
                guard_refs=tuple(call.get("guardRefs") or ()),
                call_order=call_order,
                parent_by_call=parent_by_call,
            )
            if target in requested_selections:
                selected_ref = requested_selections[target]
                matches = [
                    item.source for item in choices
                    if item.source.source_ref == selected_ref
                ]
                if len(matches) != 1:
                    raise BindingSelectionError(
                        f"selection is outside finite candidates: {call_id}#{target[1]}"
                    )
                chosen = matches[0]
                used_selections.add(target)
            else:
                chosen = resolver.resolve(
                    parameter,
                    available_sources,
                    call_id=call_id,
                    group_key=str(call.get("groupKey") or ""),
                    guard_refs=tuple(call.get("guardRefs") or ()),
                    call_order=call_order,
                    parent_by_call=parent_by_call,
                )
            used_sources = selected_sources_by_call.setdefault(call_id, set())
            if chosen.source_ref in used_sources:
                raise BindingSelectionError(
                    f"distinct parameters in {call_id} reuse source: "
                    f"{chosen.source_ref}"
                )
            used_sources.add(chosen.source_ref)
            bindings.append({
                "callId": call_id,
                "parameter": str(parameter.get("name") or ""),
                "sourceRef": chosen.source_ref,
            })
        result_source = _result_source(call, operation)
        if result_source is not None:
            available_sources.append(result_source)

    if used_selections != set(requested_selections):
        raise BindingSelectionError("binding selections contain an unknown target")

    _validate_sources(tuple(available_sources), calls, operations)

    payload = {
        "sources": [item.as_payload() for item in available_sources],
        "bindings": bindings,
    }
    return ValidatedBindingPlan(
        useCaseId=call_structure.use_case_id,
        payload=payload,
        provenance=make_provenance(
            revision=revision,
            inputs={
                "scenario": scenario,
                "catalog": catalog.model_dump(by_alias=True),
                "calls": call_structure.model_dump(by_alias=True),
            },
            subject=payload,
            validator_version=BINDING_VALIDATOR_VERSION,
        ),
    )


__all__ = [
    "BINDING_VALIDATOR_VERSION",
    "BindingCandidate",
    "BindingResolver",
    "BindingSelectionError",
    "BindingSelector",
    "BindingTarget",
    "Source",
    "UniqueBindingSelector",
    "binding_candidate_sets",
    "validate_binding_plan",
    "validated_binding_plan",
]
