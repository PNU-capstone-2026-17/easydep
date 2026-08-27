"""Deterministically project accepted BCE collaborations to sequence models."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.design.services.class_diagram.checks import (
    derived_value_parts,
    derived_value_source,
    operation_catalog,
)
from app.design.services.class_diagram.scenario import (
    ScenarioIndex,
    build_scenario_index,
    id_key,
    text,
)
from app.design.services.sequence_diagram.methods import (
    is_complete_method_call,
    is_return_value_label,
)


class SequenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SequenceParticipant(SequenceRecord):
    name: str = Field(min_length=1)
    alias: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    kind: Literal["actor", "boundary", "control", "entity", "database"]
    description: str = ""
    source_class: str = ""


class SequenceFragment(SequenceRecord):
    id: str = Field(min_length=1)
    type: Literal["alt", "opt", "loop"]
    branch: Literal["main", "else"] = "main"
    condition: str = Field(min_length=1)


class SequenceArgument(SequenceRecord):
    parameter: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)
    source_kind: Literal[
        "input", "precondition", "call_parameter", "call_result", "state", "literal",
    ]
    source_ref: str = Field(min_length=1)


class SequenceMessage(SequenceRecord):
    source: str
    target: str
    label: str
    type: Literal["sync", "async", "return", "self", "activate", "deactivate"]
    fragments: list[SequenceFragment] = Field(default_factory=list)
    use_case_ids: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    call_id: str = ""
    reply_to: str = ""
    arguments: list[SequenceArgument] = Field(default_factory=list)

    @model_validator(mode="after")
    def call_or_return_contract(self) -> "SequenceMessage":
        if self.type in {"sync", "self"}:
            if not is_complete_method_call(self.label):
                raise ValueError("call label must be a complete method signature")
            if not self.call_id or self.reply_to:
                raise ValueError("call requires call_id only")
        if self.type == "return":
            if not is_return_value_label(self.label):
                raise ValueError("return label must be a type identifier")
            if self.call_id or not self.reply_to:
                raise ValueError("return requires reply_to only")
        return self


class UseCaseSequence(SequenceRecord):
    use_case_id: str = Field(min_length=1)
    use_case_name: str = ""
    Participants: list[SequenceParticipant]
    Messages: list[SequenceMessage]
    UnresolvedSteps: list[dict[str, Any]] = Field(default_factory=list)
    NarrativeSteps: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def messages_reference_owner(self) -> "UseCaseSequence":
        for message in self.Messages:
            if message.use_case_ids != [self.use_case_id]:
                raise ValueError("every message must reference its diagram use case")
        return self


class SequenceCollection(SequenceRecord):
    Diagrams: list[UseCaseSequence]
    class_diagram_hash: str = ""
    MethodProposals: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("Diagrams")
    @classmethod
    def diagram_ids_are_unique(cls, values: list[UseCaseSequence]) -> list[UseCaseSequence]:
        identifiers = [diagram.use_case_id for diagram in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("sequence diagram use_case_ids must be unique")
        return values


def _alias(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not candidate:
        return "Participant"
    return f"P_{candidate}" if candidate[0].isdigit() else candidate


def _signature(operation: dict[str, Any]) -> str:
    parameters = ",".join(
        f"{text(parameter.get('name'))}:{text(parameter.get('type'))}"
        for parameter in operation.get("parameters") or [] if isinstance(parameter, dict)
    )
    return f"{text(operation.get('name'))}({parameters})"


def _parameter_type(operation: dict[str, Any], name: str) -> str:
    return next((
        text(parameter.get("type"))
        for parameter in operation.get("parameters") or []
        if isinstance(parameter, dict) and text(parameter.get("name")) == name
    ), "Object")


def _argument_kind(source_ref: str, call_ids: set[str], step_ids: set[str]) -> str:
    source_id, separator, path = source_ref.partition("#")
    if ":precondition:" in source_id:
        return "precondition"
    if source_id in call_ids and separator:
        return "call_result" if path == "result" or path.startswith("result.") else "call_parameter"
    if source_id in step_ids and separator:
        return "input"
    return "state"


def _extension_fragments(index: ScenarioIndex) -> dict[str, dict[str, dict[str, str]]]:
    result: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for use_case in index.use_cases:
        for step in use_case.steps:
            if step.branch == "main" or not step.condition:
                continue
            result[use_case.id][step.id] = {
                "id": f"{use_case.id}:extension:{step.branch}",
                "type": "opt",
                "branch": "main",
                "condition": step.condition,
            }
    return result


def _fragment_path(
    refs: list[str], fragments: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    if not refs or any(ref not in fragments for ref in refs):
        return []
    selected = [fragments[ref] for ref in refs]
    if not selected or len({item["id"] for item in selected}) != 1:
        return []
    return [dict(selected[0])]


def _project_collaboration(
    collaboration: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    use_case_id: str,
    fragments: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calls = [item for item in collaboration.get("calls") or [] if isinstance(item, dict)]
    if not calls:
        raise ValueError("accepted collaboration cannot be empty")
    call_by_id = {text(call.get("callId")): call for call in calls}
    if len(call_by_id) != len(calls) or "" in call_by_id:
        raise ValueError("collaboration call IDs must be nonblank and unique")
    children: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []
    seen: set[str] = set()
    for call in calls:
        call_id = text(call.get("callId"))
        parent_id = text(call.get("parentCallId"))
        operation_id = text(call.get("receiverOperationId"))
        if operation_id not in operations:
            raise ValueError(f"unknown receiver operation: {operation_id}")
        if parent_id:
            if parent_id not in seen:
                raise ValueError("parent call must precede its child")
            children[parent_id].append(call_id)
        else:
            roots.append(call_id)
        seen.add(call_id)
    if len(roots) != 1:
        raise ValueError("one execution collaboration requires one root call")

    participants: dict[str, dict[str, Any]] = {}
    class_aliases: dict[str, str] = {}
    actor = text(collaboration.get("entryActor"))
    actor_alias = _alias(actor) if actor else ""
    if actor:
        participants[actor_alias] = {
            "name": actor,
            "alias": actor_alias,
            "kind": "actor",
            "description": "",
            "source_class": "",
        }

    def participant(operation: dict[str, Any]) -> str:
        owner = operation["className"]
        if owner in class_aliases:
            return class_aliases[owner]
        candidate = _alias(owner)
        if candidate in participants:
            candidate = _alias(f"{owner}_{text(operation.get('stereotype')).title()}")
        suffix = 2
        alias = candidate
        while alias in participants:
            alias = f"{candidate}_{suffix}"
            suffix += 1
        class_aliases[owner] = alias
        participants[alias] = {
            "name": owner,
            "alias": alias,
            "kind": operation["stereotype"],
            "description": "",
            "source_class": owner,
        }
        return alias

    messages: list[dict[str, Any]] = []
    all_step_ids = {
        text(ref) for call in calls for ref in call.get("stepRefs") or []
    }

    def append(call_id: str, caller: str) -> None:
        call = call_by_id[call_id]
        operation = operations[text(call.get("receiverOperationId"))]
        callee = participant(operation)
        refs = [text(ref) for ref in call.get("stepRefs") or []]
        fragment_path = _fragment_path(refs, fragments)
        arguments = [
            {
                "parameter": text(binding.get("parameter")),
                "type": _parameter_type(operation, text(binding.get("parameter"))),
                "source_kind": _argument_kind(
                    text(binding.get("sourceRef")), set(call_by_id), all_step_ids,
                ),
                "source_ref": text(binding.get("sourceRef")),
            }
            for binding in call.get("argumentBindings") or []
            if isinstance(binding, dict)
        ]
        actual_caller = caller or callee
        messages.append({
            "source": actual_caller,
            "target": callee,
            "label": _signature(operation),
            "type": "self" if actual_caller == callee else "sync",
            "fragments": fragment_path,
            "use_case_ids": [use_case_id],
            "step_ids": refs,
            "call_id": call_id,
            "reply_to": "",
            "arguments": arguments,
        })
        for child_id in children.get(call_id, []):
            append(child_id, callee)
        messages.append({
            "source": callee,
            "target": actual_caller,
            "label": text(operation.get("returnType")) or "void",
            "type": "return",
            "fragments": fragment_path,
            "use_case_ids": [use_case_id],
            "step_ids": refs,
            "call_id": "",
            "reply_to": call_id,
            "arguments": [],
        })

    append(roots[0], actor_alias)
    return messages, list(participants.values())


def _merge_diagram(
    diagrams: dict[str, dict[str, Any]],
    use_case_id: str,
    name: str,
    messages: list[dict[str, Any]],
    participants: list[dict[str, Any]],
) -> None:
    if use_case_id not in diagrams:
        diagrams[use_case_id] = {
            "use_case_id": use_case_id,
            "use_case_name": name,
            "Participants": participants,
            "Messages": messages,
            "UnresolvedSteps": [],
            "NarrativeSteps": [],
        }
        return
    current = diagrams[use_case_id]
    aliases = {text(item.get("alias")) for item in current["Participants"]}
    current["Participants"].extend(
        item for item in participants if text(item.get("alias")) not in aliases
    )
    current["Messages"].extend(messages)


def _scoped_include_collaboration(
    owner: str,
    collaboration: dict[str, Any],
) -> dict[str, Any] | None:
    selected = [
        call for call in collaboration.get("calls") or []
        if isinstance(call, dict)
        and any(text(ref).startswith(f"{owner}:") for ref in call.get("stepRefs") or [])
    ]
    if not selected:
        return None
    id_map = {
        text(call.get("callId")): f"{owner}:scoped::call:{position}"
        for position, call in enumerate(selected, start=1)
    }
    first_step = next(
        (
            text(ref)
            for call in selected for ref in call.get("stepRefs") or []
            if text(ref).startswith(f"{owner}:")
        ),
        f"{owner}:root",
    )
    calls: list[dict[str, Any]] = []

    def remap_source(source_ref: str, fallback_name: str) -> str:
        derived_type, mappings = derived_value_parts(source_ref)
        if derived_type:
            return derived_value_source(
                derived_type,
                {
                    field: remap_source(nested, field)
                    for field, nested in mappings.items()
                },
            )
        if source_ref.startswith("runtime#"):
            return source_ref
        source_id, separator, suffix = source_ref.partition("#")
        if source_id in id_map:
            return id_map[source_id] + (f"#{suffix}" if separator else "")
        if separator:
            return f"{first_step}#{fallback_name}"
        return source_ref

    for call in selected:
        old_id = text(call.get("callId"))
        parent = id_map.get(text(call.get("parentCallId")))
        bindings: list[dict[str, str]] = []
        for binding in call.get("argumentBindings") or []:
            if not isinstance(binding, dict):
                continue
            source_ref = text(binding.get("sourceRef"))
            source_ref = remap_source(
                source_ref, text(binding.get("parameter")),
            )
            bindings.append({
                "parameter": text(binding.get("parameter")), "sourceRef": source_ref,
            })
        calls.append({
            "callId": id_map[old_id],
            "parentCallId": parent,
            "receiverOperationId": text(call.get("receiverOperationId")),
            "stepRefs": [
                text(ref) for ref in call.get("stepRefs") or []
                if text(ref).startswith(f"{owner}:")
            ],
            "argumentBindings": bindings,
        })
    return {
        "collaborationId": f"{owner}:scoped",
        "useCaseIds": [owner],
        "entryActor": None,
        "calls": calls,
    }


def _embed_extending_use_cases(
    index: ScenarioIndex,
    diagrams: dict[str, dict[str, Any]],
) -> None:
    for relationship in index.relationships:
        if relationship.kind != "extend":
            continue
        base = diagrams.get(relationship.base_id)
        extension = diagrams.get(relationship.child_id)
        if base is None or extension is None:
            continue
        raw_relationships = (index.raw.get("relationships") or {}).get("extends") or []
        raw = next((
            item for item in raw_relationships if isinstance(item, dict)
            and text(item.get("base_use_case_id")) == relationship.base_id
            and text(item.get("extending_use_case_id")) == relationship.child_id
        ), {})
        condition = text(raw.get("condition"))
        if not condition:
            continue
        aliases = {text(item.get("alias")) for item in base["Participants"]}
        base["Participants"].extend(
            deepcopy(item) for item in extension["Participants"]
            if text(item.get("alias")) not in aliases
        )
        fragment = {
            "id": f"{relationship.base_id}:extend:{relationship.child_id}",
            "type": "opt",
            "branch": "main",
            "condition": condition,
        }
        messages = deepcopy(extension["Messages"])
        for message in messages:
            message["fragments"] = [fragment, *(message.get("fragments") or [])]
            message["use_case_ids"] = [relationship.base_id]
        anchor_ids = set(relationship.anchor_step_ids)
        insertion = len(base["Messages"])
        if anchor_ids:
            positions = [
                position for position, message in enumerate(base["Messages"])
                if anchor_ids & {text(ref) for ref in message.get("step_ids") or []}
            ]
            if positions:
                insertion = max(positions) + 1
        base["Messages"][insertion:insertion] = messages


def project_sequence_model(
    scenario: dict[str, Any],
    class_model: dict[str, Any],
    class_diagram_puml: str,
) -> dict[str, Any]:
    """Project sequence diagrams without selecting or repairing operations."""

    index = build_scenario_index(scenario)
    operations = operation_catalog(class_model)
    collaborations = [
        item for item in class_model.get("Collaborations") or [] if isinstance(item, dict)
    ]
    if not collaborations:
        raise ValueError("class model has no accepted Collaborations")
    fragments = _extension_fragments(index)
    step_positions = {
        step.id: step.order for use_case in index.use_cases for step in use_case.steps
    }
    collaborations.sort(key=lambda collaboration: (
        id_key(text((collaboration.get("useCaseIds") or [""])[0])),
        min(
            (
                step_positions.get(text(ref), 10**9)
                for call in collaboration.get("calls") or [] if isinstance(call, dict)
                for ref in call.get("stepRefs") or []
            ),
            default=10**9,
        ),
        id_key(text(collaboration.get("collaborationId"))),
    ))
    diagrams: dict[str, dict[str, Any]] = {}
    for collaboration in collaborations:
        scope = [text(value) for value in collaboration.get("useCaseIds") or []]
        if not scope:
            raise ValueError("collaboration has no useCaseIds")
        owner = scope[0]
        messages, participants = _project_collaboration(
            collaboration, operations, owner, fragments.get(owner, {}),
        )
        _merge_diagram(
            diagrams, owner, index.use_case(owner).name, messages, participants,
        )
    for use_case in index.use_cases:
        if use_case.id in diagrams:
            continue
        source = next((
            collaboration for collaboration in collaborations
            if use_case.id in [text(value) for value in collaboration.get("useCaseIds") or []][1:]
        ), None)
        scoped = _scoped_include_collaboration(use_case.id, source) if source else None
        if scoped:
            messages, participants = _project_collaboration(
                scoped, operations, use_case.id, fragments.get(use_case.id, {}),
            )
            _merge_diagram(diagrams, use_case.id, use_case.name, messages, participants)
    missing = [use_case.id for use_case in index.use_cases if use_case.id not in diagrams]
    if missing:
        raise ValueError("missing accepted collaboration projection for " + ", ".join(missing))
    _embed_extending_use_cases(index, diagrams)
    ordered = [diagrams[use_case.id] for use_case in index.use_cases]
    return SequenceCollection(
        Diagrams=ordered,
        class_diagram_hash=hashlib.sha256(class_diagram_puml.encode("utf-8")).hexdigest(),
        MethodProposals=[],
    ).model_dump()


def sequence_findings(model: dict[str, Any]) -> list[str]:
    """Return deterministic contract defects without attempting a repair."""

    try:
        parsed = SequenceCollection.model_validate(model)
    except Exception as error:
        return [str(error)]
    findings: list[str] = []
    for diagram in parsed.Diagrams:
        calls = {message.call_id for message in diagram.Messages if message.call_id}
        replies = [message.reply_to for message in diagram.Messages if message.type == "return"]
        if set(replies) != calls or len(replies) != len(calls):
            findings.append(
                f"{diagram.use_case_id}: every call requires exactly one matching return"
            )
        participant_aliases = {participant.alias for participant in diagram.Participants}
        for message in diagram.Messages:
            if message.source not in participant_aliases or message.target not in participant_aliases:
                findings.append(
                    f"{diagram.use_case_id}: message references an undeclared participant"
                )
                break
    return findings


def normalize_sequence_model(model: dict[str, Any]) -> dict[str, Any]:
    """Validate the current persisted contract without legacy reconstruction."""

    return SequenceCollection.model_validate(model).model_dump()



