"""One normalized source of use-case and execution-group identities."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def id_key(value: str) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    )


@dataclass(frozen=True)
class Step:
    id: str
    use_case_id: str
    subject: str
    sentence: str
    order: int
    branch: str
    condition: str = ""


@dataclass(frozen=True)
class UseCase:
    id: str
    name: str
    primary_actor: str
    specification: dict[str, Any]
    steps: tuple[Step, ...]
    precondition_refs: tuple[str, ...]


@dataclass(frozen=True)
class Relationship:
    kind: str
    base_id: str
    child_id: str
    anchor_step_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionGroup:
    id: str
    use_case_id: str
    step_ids: tuple[str, ...]
    actor_step: str | None
    entry_actor: str | None
    trace_use_case_ids: tuple[str, ...]
    required_step_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioIndex:
    raw: dict[str, Any]
    use_cases: tuple[UseCase, ...]
    relationships: tuple[Relationship, ...]
    groups: tuple[ExecutionGroup, ...]

    def use_case(self, use_case_id: str) -> UseCase:
        for use_case in self.use_cases:
            if use_case.id == use_case_id:
                return use_case
        raise KeyError(use_case_id)

    @property
    def step_ids(self) -> frozenset[str]:
        return frozenset(step.id for use_case in self.use_cases for step in use_case.steps)


def _steps(use_case_id: str, specification: dict[str, Any]) -> tuple[Step, ...]:
    result: list[Step] = []
    order = 0
    for raw in specification.get("main_scenario") or []:
        if not isinstance(raw, dict) or raw.get("step_number") is None:
            continue
        result.append(Step(
            id=f"{use_case_id}:main:{raw['step_number']}",
            use_case_id=use_case_id,
            subject=text(raw.get("subject_ref")),
            sentence=text(raw.get("sentence") or raw.get("description")),
            order=order,
            branch="main",
        ))
        order += 1
    for extension in specification.get("extensions") or []:
        if not isinstance(extension, dict):
            continue
        label = text(extension.get("label"))
        condition = text(extension.get("condition"))
        for raw in extension.get("handling_steps") or []:
            if not label or not isinstance(raw, dict) or raw.get("sub_step") is None:
                continue
            result.append(Step(
                id=f"{use_case_id}:extension:{label}:{raw['sub_step']}",
                use_case_id=use_case_id,
                subject=text(raw.get("subject_ref")),
                sentence=text(raw.get("sentence") or raw.get("description")),
                order=order,
                branch=label,
                condition=condition,
            ))
            order += 1
    return tuple(result)


def _preconditions(use_case_id: str, specification: dict[str, Any]) -> tuple[str, ...]:
    raw = specification.get("preconditions") or []
    values = list(raw.values()) if isinstance(raw, dict) else list(raw)
    return tuple(
        f"{use_case_id}:precondition:{index}"
        for index, value in enumerate(values, start=1)
        if text(value)
    )


def _actor_steps(use_case: UseCase) -> set[str]:
    actor = use_case.primary_actor.casefold()
    if not actor:
        return set()
    return {
        step.id for step in use_case.steps
        if step.subject.casefold() == actor
        or (
            not step.subject
            and re.match(rf"^(?:the )?{re.escape(actor)}\b", step.sentence.casefold())
        )
    }


def _aliases(use_cases: tuple[UseCase, ...]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for use_case in use_cases:
        for alias in (use_case.id, use_case.name):
            if alias:
                candidates.setdefault(alias.casefold(), set()).add(use_case.id)
    return {alias: next(iter(ids)) for alias, ids in candidates.items() if len(ids) == 1}


def _relationships(
    raw: dict[str, Any], use_cases: tuple[UseCase, ...],
) -> tuple[Relationship, ...]:
    source = raw.get("relationships")
    if not isinstance(source, dict):
        return ()
    aliases = _aliases(use_cases)
    result: list[Relationship] = []
    for kind, collection, child_keys in (
        ("include", "includes", ("included_use_case_id", "included_use_case", "includedUseCase")),
        ("extend", "extends", ("extending_use_case_id", "extending_use_case", "extendingUseCase")),
    ):
        for item in source.get(collection) or []:
            if not isinstance(item, dict):
                continue
            base_raw = text(
                item.get("base_use_case_id")
                or item.get("base_use_case")
                or item.get("baseUseCase")
            )
            child_raw = text(next((item.get(key) for key in child_keys if item.get(key)), ""))
            base_id = aliases.get(base_raw.casefold(), "")
            child_id = aliases.get(child_raw.casefold(), "")
            if not base_id or not child_id or base_id == child_id:
                continue
            anchors = {
                f"{base_id}:{text(ref.get('step_ref'))}"
                for ref in item.get("step_refs") or []
                if isinstance(ref, dict)
                and text(ref.get("use_case_id")) == base_id
                and text(ref.get("step_ref"))
            }
            if kind == "extend" and not anchors and text(item.get("extension_point")):
                anchors.add(f"{base_id}:{text(item.get('extension_point'))}")
            result.append(Relationship(kind, base_id, child_id, tuple(sorted(anchors))))
    unique = {(item.kind, item.base_id, item.child_id, item.anchor_step_ids): item for item in result}
    return tuple(unique[key] for key in sorted(unique))


def _groups(
    use_cases: tuple[UseCase, ...], relationships: tuple[Relationship, ...],
) -> tuple[ExecutionGroup, ...]:
    by_id = {use_case.id: use_case for use_case in use_cases}
    internal_includes = {
        relation.child_id for relation in relationships
        if relation.kind == "include" and not _actor_steps(by_id[relation.child_id])
    }
    groups: list[ExecutionGroup] = []
    for use_case in use_cases:
        if not use_case.steps or use_case.id in internal_includes:
            continue
        actor_steps = _actor_steps(use_case)
        main_steps = [step for step in use_case.steps if step.branch == "main"]
        active: str | None = None
        grouped: dict[str, list[str]] = {}
        owner_by_step: dict[str, str] = {}
        for step in main_steps:
            if step.id in actor_steps:
                active = step.id
                grouped.setdefault(active, [])
            if active:
                grouped[active].append(step.id)
                owner_by_step[step.id] = active
        if not grouped:
            grouped[f"{use_case.id}:root"] = [step.id for step in main_steps]
        for extension in use_case.specification.get("extensions") or []:
            if not isinstance(extension, dict):
                continue
            branch_step = text(extension.get("branch_step"))
            label = text(extension.get("label"))
            owner = owner_by_step.get(f"{use_case.id}:main:{branch_step}")
            if owner and label:
                grouped[owner].extend(
                    step.id for step in use_case.steps if step.branch == label
                )
        for group_id, base_steps in grouped.items():
            actor_step = group_id if group_id in actor_steps else None
            trace_ids = [use_case.id]
            required = list(base_steps)
            for relation in relationships:
                if relation.kind != "include" or relation.base_id != use_case.id:
                    continue
                if relation.anchor_step_ids and not set(relation.anchor_step_ids) & set(base_steps):
                    continue
                child = by_id.get(relation.child_id)
                if child:
                    trace_ids.append(child.id)
                    required.extend(step.id for step in child.steps)
            groups.append(ExecutionGroup(
                id=group_id,
                use_case_id=use_case.id,
                step_ids=tuple(base_steps),
                actor_step=actor_step,
                entry_actor=use_case.primary_actor if actor_step else None,
                trace_use_case_ids=tuple(dict.fromkeys(trace_ids)),
                required_step_ids=tuple(dict.fromkeys(required)),
            ))
    return tuple(groups)


def build_scenario_index(raw: dict[str, Any]) -> ScenarioIndex:
    if not isinstance(raw, dict):
        raise TypeError("use-case specification must be an object")
    summaries = {
        text(item.get("id")): item
        for item in raw.get("use_cases") or []
        if isinstance(item, dict) and text(item.get("id"))
    }
    use_cases: list[UseCase] = []
    seen: set[str] = set()
    specifications = sorted(
        (
            item for item in raw.get("use_case_specs") or []
            if isinstance(item, dict) and text(item.get("use_case_id"))
        ),
        key=lambda item: id_key(text(item.get("use_case_id"))),
    )
    for specification in specifications:
        use_case_id = text(specification.get("use_case_id"))
        if use_case_id in seen:
            raise ValueError(f"duplicate use-case specification: {use_case_id}")
        seen.add(use_case_id)
        summary = summaries.get(use_case_id, {})
        use_cases.append(UseCase(
            id=use_case_id,
            name=text(summary.get("name") or specification.get("use_case_name")),
            primary_actor=text(
                specification.get("primary_actor") or summary.get("primary_actor")
            ),
            specification=specification,
            steps=_steps(use_case_id, specification),
            precondition_refs=_preconditions(use_case_id, specification),
        ))
    accepted = tuple(use_cases)
    relationships = _relationships(raw, accepted)
    return ScenarioIndex(raw, accepted, relationships, _groups(accepted, relationships))



