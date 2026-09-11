"""CAS-style, operation-scoped repair patches for validated fragments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.design.schemas.class_model import ClassParameter, canonical_operation_id

from .contracts import Contract, ValidatedOperationFragment, canonical_digest


class OperationPatchAction(StrEnum):
    ADD = "ADD"
    REPLACE = "REPLACE"
    REMOVE = "REMOVE"


class OperationPatchReplacement(Contract):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameters: tuple[ClassParameter, ...] = ()
    return_type: str = Field(alias="returnType", min_length=1)
    step_refs: tuple[str, ...] = Field(alias="stepRefs", min_length=1)

    @field_validator("step_refs")
    @classmethod
    def unique_step_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


class OperationPatchEdit(Contract):
    action: OperationPatchAction
    owner: str = Field(min_length=1)
    operation_ref: str = Field(alias="operationRef", min_length=1)
    expected_digest: str = Field(alias="expectedDigest", min_length=1)
    replacement: OperationPatchReplacement | None = None

    @model_validator(mode="after")
    def replacement_matches_action(self) -> OperationPatchEdit:
        if self.action in {OperationPatchAction.ADD, OperationPatchAction.REPLACE}:
            if self.replacement is None:
                raise ValueError("ADD/REPLACE patch requires replacement")
        elif self.replacement is not None:
            raise ValueError("REMOVE patch cannot include replacement")
        return self


class OperationFragmentPatch(Contract):
    base_digest: str = Field(alias="baseDigest", min_length=1)
    finding_ids: tuple[str, ...] = Field(alias="findingIds", min_length=1)
    edits: tuple[OperationPatchEdit, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def targets_are_unique(self) -> OperationFragmentPatch:
        targets = [(item.owner, item.operation_ref) for item in self.edits]
        if len(targets) != len(set(targets)):
            raise ValueError("a patch cannot edit the same operation twice")
        return self


class OperationPatchAttempt(Contract):
    patch_digest: str = Field(alias="patchDigest", min_length=1)
    before_digest: str = Field(alias="beforeDigest", min_length=1)
    after_digest: str = Field(alias="afterDigest", min_length=1)
    finding_ids: tuple[str, ...] = Field(alias="findingIds", min_length=1)
    touched_refs: tuple[str, ...] = Field(alias="touchedRefs", min_length=1)
    before: dict[str, Any]
    after: dict[str, Any]


class OperationPatchError(ValueError):
    pass


def _operation_ref(owner: str, operation: Mapping[str, Any]) -> str:
    return canonical_operation_id(
        owner,
        str(operation.get("name") or "").strip(),
        operation.get("parameters", []) or [],
    )


def _owner_operations(
    payload: dict[str, Any], owner: str
) -> list[dict[str, Any]]:
    matches = [
        class_set
        for class_set in payload.get("Classes", []) or []
        if isinstance(class_set, dict)
        and str(class_set.get("className") or class_set.get("name") or "").strip()
        == owner
    ]
    if len(matches) != 1:
        raise OperationPatchError(f"patch owner must exist exactly once: {owner}")
    values = matches[0].get("operations")
    if not isinstance(values, list):
        raise OperationPatchError(f"patch owner has no operation list: {owner}")
    if not all(isinstance(item, dict) for item in values):
        raise OperationPatchError(f"patch owner has an invalid operation list: {owner}")
    return values


def apply_operation_fragment_patch(
    fragment: ValidatedOperationFragment,
    patch: OperationFragmentPatch | Mapping[str, Any],
    *,
    confirmed_finding_ids: Iterable[str],
) -> OperationPatchAttempt:
    """Apply only exact, confirmed, compare-and-swap operation edits."""
    value = (
        patch
        if isinstance(patch, OperationFragmentPatch)
        else OperationFragmentPatch.model_validate(patch)
    )
    before = deepcopy(fragment.payload)
    before_digest = canonical_digest(before)
    if value.base_digest != before_digest:
        raise OperationPatchError("patch baseDigest is stale")
    confirmed = set(confirmed_finding_ids)
    unconfirmed = set(value.finding_ids) - confirmed
    if unconfirmed:
        raise OperationPatchError(
            "patch cites unconfirmed findings: " + ", ".join(sorted(unconfirmed))
        )

    after = deepcopy(before)
    touched: list[str] = []
    for edit in value.edits:
        operations = _owner_operations(after, edit.owner)
        indexes = [
            index
            for index, operation in enumerate(operations)
            if _operation_ref(edit.owner, operation) == edit.operation_ref
        ]
        if len(indexes) > 1:
            raise OperationPatchError(
                f"patch target is not unique: {edit.operation_ref}"
            )
        current = operations[indexes[0]] if indexes else None
        if canonical_digest(current) != edit.expected_digest:
            raise OperationPatchError(
                f"patch expectedDigest is stale: {edit.operation_ref}"
            )

        if edit.action is OperationPatchAction.ADD:
            if current is not None:
                raise OperationPatchError(f"ADD target already exists: {edit.operation_ref}")
            replacement = (
                edit.replacement.model_dump(by_alias=True)
                if edit.replacement is not None
                else None
            )
            if replacement is None:
                raise OperationPatchError("ADD replacement must be an operation object")
            if _operation_ref(edit.owner, replacement) != edit.operation_ref:
                raise OperationPatchError("ADD replacement identity differs from target")
            operations.append(replacement)
        elif edit.action is OperationPatchAction.REPLACE:
            if current is None:
                raise OperationPatchError(
                    f"REPLACE target does not exist: {edit.operation_ref}"
                )
            replacement = (
                edit.replacement.model_dump(by_alias=True)
                if edit.replacement is not None
                else None
            )
            if replacement is None:
                raise OperationPatchError("REPLACE replacement must be an operation object")
            operations[indexes[0]] = replacement
        else:
            if current is None:
                raise OperationPatchError(
                    f"REMOVE target does not exist: {edit.operation_ref}"
                )
            operations.pop(indexes[0])
        touched.append(edit.operation_ref)

    after_digest = canonical_digest(after)
    if after_digest == before_digest:
        raise OperationPatchError("patch made no change")
    return OperationPatchAttempt(
        patchDigest=canonical_digest(value.model_dump(by_alias=True)),
        beforeDigest=before_digest,
        afterDigest=after_digest,
        findingIds=value.finding_ids,
        touchedRefs=tuple(touched),
        before=before,
        after=after,
    )


__all__ = [
    "OperationFragmentPatch",
    "OperationPatchAction",
    "OperationPatchAttempt",
    "OperationPatchEdit",
    "OperationPatchError",
    "apply_operation_fragment_patch",
]
