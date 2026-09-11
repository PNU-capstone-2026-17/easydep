from __future__ import annotations

from copy import deepcopy

import pytest

from app.design.services.class_diagram.proposals import OperationFragment
from app.design.services.executable_behavior import (
    OperationContext,
    OperationFragmentPatch,
    OperationPatchAction,
    OperationPatchEdit,
    OperationPatchError,
    apply_operation_fragment_patch,
    validate_operation_payload,
    validated_operation_fragment,
)
from app.design.services.executable_behavior.contracts import canonical_digest

SCENARIO = {
    "useCases": [
        {
            "id": "UC1",
            "steps": [{"id": "UC1:1"}, {"id": "UC1:2"}],
        }
    ]
}
INVENTORY = {
    "Classes": [
        {"className": "Boundary", "stereotype": "Boundary", "fields": []},
        {"className": "Control", "stereotype": "Control", "fields": []},
    ],
    "DataTypes": [],
    "Relationships": [],
}
PAYLOAD = {
    "Classes": [
        {
            "className": "Boundary",
            "operations": [
                {
                    "name": "submit",
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["UC1:1"],
                }
            ],
        },
        {
            "className": "Control",
            "operations": [
                {
                    "name": "process",
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["UC1:2"],
                }
            ],
        },
    ],
    "DataTypes": [],
}


def _context() -> OperationContext:
    return OperationContext.from_payload(
        "UC1",
        INVENTORY,
        scenario=SCENARIO,
        allowed_step_ids=("UC1:1", "UC1:2"),
    )


def _fragment():
    return validated_operation_fragment(PAYLOAD, _context())


def test_step_ref_shape_reaches_slice_aware_semantic_repair() -> None:
    candidate = deepcopy(PAYLOAD)
    candidate["Classes"][0]["operations"][0]["stepRefs"] = [
        "Boundary:main:UC1_main_1"
    ]

    parsed = OperationFragment.model_validate(candidate).model_dump(by_alias=True)
    findings = validate_operation_payload(parsed, _context())

    assert any("Boundary:main:UC1_main_1" in item for item in findings)
    assert any("allowedStepIds are [UC1:1, UC1:2]" in item for item in findings)


def _replacement_patch(*, base_digest: str | None = None, finding_id: str = "f-1"):
    fragment = _fragment()
    current = fragment.payload["Classes"][1]["operations"][0]
    replacement = {
        **current,
        "name": "coordinate",
    }
    return fragment, OperationFragmentPatch(
        baseDigest=base_digest or canonical_digest(fragment.payload),
        findingIds=(finding_id,),
        edits=(
            OperationPatchEdit(
                action=OperationPatchAction.REPLACE,
                owner="Control",
                operationRef="Control::process()",
                expectedDigest=canonical_digest(current),
                replacement=replacement,
            ),
        ),
    )


def test_confirmed_scoped_patch_preserves_unrelated_content_and_revalidates() -> None:
    fragment, patch = _replacement_patch()
    original = deepcopy(fragment.payload)

    attempt = apply_operation_fragment_patch(
        fragment, patch, confirmed_finding_ids=("f-1",)
    )
    revalidated = validated_operation_fragment(attempt.after, _context())

    assert fragment.payload == original
    assert attempt.before["Classes"][0] == attempt.after["Classes"][0]
    assert attempt.after["Classes"][1]["operations"][0]["name"] == "coordinate"
    assert revalidated.payload == attempt.after
    assert attempt.before_digest != attempt.after_digest


def test_patch_rejects_stale_base_or_stale_expected_value() -> None:
    fragment, stale_base = _replacement_patch(base_digest="stale")
    with pytest.raises(OperationPatchError, match="baseDigest is stale"):
        apply_operation_fragment_patch(
            fragment, stale_base, confirmed_finding_ids=("f-1",)
        )

    current = fragment.payload["Classes"][1]["operations"][0]
    stale_edit = OperationFragmentPatch(
        baseDigest=canonical_digest(fragment.payload),
        findingIds=("f-1",),
        edits=(
            OperationPatchEdit(
                action=OperationPatchAction.REPLACE,
                owner="Control",
                operationRef="Control::process()",
                expectedDigest="stale",
                replacement={**current, "name": "coordinate"},
            ),
        ),
    )
    with pytest.raises(OperationPatchError, match="expectedDigest is stale"):
        apply_operation_fragment_patch(
            fragment, stale_edit, confirmed_finding_ids=("f-1",)
        )


def test_patch_rejects_unconfirmed_finding() -> None:
    fragment, patch = _replacement_patch(finding_id="reviewer-claim")

    with pytest.raises(OperationPatchError, match="unconfirmed findings"):
        apply_operation_fragment_patch(
            fragment, patch, confirmed_finding_ids=("confirmed-defect",)
        )


def test_add_patch_uses_absence_as_the_expected_prior_value() -> None:
    fragment = _fragment()
    operation = {
        "name": "cancel",
        "parameters": [],
        "returnType": "void",
        "stepRefs": ["UC1:2"],
    }
    patch = OperationFragmentPatch(
        baseDigest=canonical_digest(fragment.payload),
        findingIds=("f-add",),
        edits=(
            OperationPatchEdit(
                action=OperationPatchAction.ADD,
                owner="Control",
                operationRef="Control::cancel()",
                expectedDigest=canonical_digest(None),
                replacement=operation,
            ),
        ),
    )

    attempt = apply_operation_fragment_patch(
        fragment, patch, confirmed_finding_ids=("f-add",)
    )

    assert [
        item["name"] for item in attempt.after["Classes"][1]["operations"]
    ] == ["process", "cancel"]
