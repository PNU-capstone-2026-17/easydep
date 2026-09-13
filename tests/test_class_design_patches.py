from __future__ import annotations

from copy import deepcopy

import pytest

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.patches import (
    DesignPatchError,
    apply_structured_patches,
)


def _model() -> dict:
    return {
        "Classes": [
            {
                "className": "EnrollmentBoundary",
                "stereotype": "Boundary",
                "fields": [],
                "use_case_ids": ["UC3", "UC5"],
                "operations": [{
                    "operationId": "EnrollmentBoundary::start()",
                    "name": "start",
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["UC3:main:1", "UC5:main:1"],
                }],
            },
            {
                "className": "EnrollmentControl",
                "stereotype": "Control",
                "fields": [],
                "use_case_ids": ["UC3", "UC5"],
                "operations": [{
                    "operationId": "EnrollmentControl::change()",
                    "name": "change",
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["UC3:main:2", "UC5:main:2"],
                }, {
                    "operationId": "EnrollmentControl::record(previous:String)",
                    "name": "record",
                    "parameters": [{"name": "previous", "type": "String"}],
                    "returnType": "void",
                    "stepRefs": ["UC3:main:3"],
                }],
            },
            {
                "className": "Registration",
                "stereotype": "Entity",
                "fields": [],
                "use_case_ids": ["UC3", "UC5"],
                "operations": [{
                    "operationId": "Registration::deleteById(id:UUID)",
                    "name": "deleteById",
                    "parameters": [{"name": "id", "type": "UUID"}],
                    "returnType": "void",
                    "stepRefs": ["UC3:main:3", "UC5:main:3"],
                }],
            },
            {
                "className": "CourseOffering",
                "stereotype": "Entity",
                "fields": [],
                "use_case_ids": ["UC3", "UC5"],
                "operations": [],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [
            {
                "collaborationId": "UC3:main:1",
                "useCaseIds": ["UC3"],
                "entryActor": "Student",
                "calls": [
                    {
                        "callId": "UC3:main:1::call:1",
                        "receiverOperationId": "EnrollmentBoundary::start()",
                        "stepRefs": ["UC3:main:1"],
                        "argumentBindings": [],
                    },
                    {
                        "callId": "UC3:main:1::call:2",
                        "parentCallId": "UC3:main:1::call:1",
                        "receiverOperationId": "EnrollmentControl::change()",
                        "stepRefs": ["UC3:main:2"],
                        "argumentBindings": [],
                    },
                    {
                        "callId": "UC3:main:1::call:3",
                        "parentCallId": "UC3:main:1::call:2",
                        "receiverOperationId": "Registration::deleteById(id:UUID)",
                        "stepRefs": ["UC3:main:3"],
                        "argumentBindings": [{"parameter": "id", "sourceRef": "UC3:main:1::call:2#result.id"}],
                    },
                    {
                        "callId": "UC3:main:1::call:4",
                        "parentCallId": "UC3:main:1::call:2",
                        "receiverOperationId": "EnrollmentControl::record(previous:String)",
                        "stepRefs": ["UC3:main:3"],
                        "argumentBindings": [{
                            "parameter": "previous",
                            "sourceRef": "UC3:main:1::call:3#result",
                        }],
                    },
                ],
            },
            {
                "collaborationId": "UC5:main:1",
                "useCaseIds": ["UC5"],
                "entryActor": "Student",
                "calls": [
                    {
                        "callId": "UC5:main:1::call:1",
                        "receiverOperationId": "EnrollmentBoundary::start()",
                        "stepRefs": ["UC5:main:1"],
                        "argumentBindings": [],
                    },
                    {
                        "callId": "UC5:main:1::call:2",
                        "parentCallId": "UC5:main:1::call:1",
                        "receiverOperationId": "EnrollmentControl::change()",
                        "stepRefs": ["UC5:main:2"],
                        "argumentBindings": [],
                    },
                    {
                        "callId": "UC5:main:1::call:3",
                        "parentCallId": "UC5:main:1::call:2",
                        "receiverOperationId": "Registration::deleteById(id:UUID)",
                        "stepRefs": ["UC5:main:3"],
                        "argumentBindings": [{"parameter": "id", "sourceRef": "UC5:main:1::call:2#result.id"}],
                    },
                ],
            },
        ],
    }


def _patches() -> list[dict]:
    return [
        {
            "operation": "add_operation",
            "target": "class_diagram:CourseOffering",
            "name": "decrementEnrolledCount",
            "returnType": "void",
            "stepRefs": ["UC3:main:3", "UC5:main:3"],
        },
        *[
            {
                "operation": "insert_call_after",
                "target": collaboration_id,
                "anchor": "Registration::deleteById(id:UUID)",
                "anchorOccurrence": None,
                "receiverOperationId": "CourseOffering::decrementEnrolledCount()",
                "stepRefs": [step_ref],
            }
            for collaboration_id, step_ref in (
                ("UC3:main:1", "UC3:main:3"),
                ("UC5:main:1", "UC5:main:3"),
            )
        ],
    ]


def test_structured_patches_add_only_named_operation_and_calls() -> None:
    before = _model()
    result = apply_structured_patches(before, _patches())

    assert before == _model()  # no caller-owned input is mutated
    assert result["Collaborations"][0]["collaborationId"] == "UC3:main:1"
    assert result["Collaborations"][1]["collaborationId"] == "UC5:main:1"
    operation = result["Classes"][3]["operations"]
    assert operation == [{
        "operationId": "CourseOffering::decrementEnrolledCount()",
        "name": "decrementEnrolledCount",
        "parameters": [],
        "returnType": "void",
        "stepRefs": ["UC3:main:3", "UC5:main:3"],
    }]
    uc3 = result["Collaborations"][0]["calls"]
    assert [call["receiverOperationId"] for call in uc3] == [
        "EnrollmentBoundary::start()",
        "EnrollmentControl::change()",
        "Registration::deleteById(id:UUID)",
        "CourseOffering::decrementEnrolledCount()",
        "EnrollmentControl::record(previous:String)",
    ]
    assert uc3[3]["parentCallId"] == "UC3:main:1::call:2"
    # The unchanged final call keeps its original parent and its call-result
    # source follows the same original call after position-based IDs are reindexed.
    assert uc3[4]["parentCallId"] == "UC3:main:1::call:2"
    assert uc3[4]["argumentBindings"] == [{
        "parameter": "previous", "sourceRef": "UC3:main:1::call:3#result",
    }]
    assert BCEModel.model_validate(result).model_dump(by_alias=True)["Collaborations"][0]["calls"][3]["parentCallId"] == "UC3:main:1::call:2"


def test_insert_before_preserves_anchor_parent_and_unmentioned_relative_order() -> None:
    model = _model()
    model["Classes"][3]["operations"] = [{
        "operationId": "CourseOffering::audit()",
        "name": "audit",
        "parameters": [],
        "returnType": "void",
        "stepRefs": ["UC3:main:3"],
    }]
    result = apply_structured_patches(model, [{
        "operation": "insert_call_before",
        "target": "UC3:main:1",
        "anchor": "Registration::deleteById(id:UUID)",
        "receiverOperationId": "CourseOffering::audit()",
        "stepRefs": ["UC3:main:3"],
    }])

    calls = result["Collaborations"][0]["calls"]
    assert [call["receiverOperationId"] for call in calls] == [
        "EnrollmentBoundary::start()", "EnrollmentControl::change()",
        "CourseOffering::audit()", "Registration::deleteById(id:UUID)",
        "EnrollmentControl::record(previous:String)",
    ]
    assert calls[2]["parentCallId"] == "UC3:main:1::call:2"
    assert calls[3]["parentCallId"] == "UC3:main:1::call:2"


def test_ambiguous_anchor_is_rejected_without_partial_mutation() -> None:
    model = _model()
    model["Collaborations"][0]["calls"].append({
        "callId": "UC3:main:1::call:5",
        "parentCallId": "UC3:main:1::call:1",
        "receiverOperationId": "EnrollmentControl::change()",
        "stepRefs": ["UC3:main:2"],
        "argumentBindings": [],
    })
    before = deepcopy(model)
    with pytest.raises(DesignPatchError, match="ambiguous"):
        apply_structured_patches(model, [{
            "operation": "insert_call_after",
            "target": "UC3:main:1",
            "anchor": "EnrollmentControl::change()",
            "receiverOperationId": "EnrollmentControl::change()",
        }])
    with pytest.raises(DesignPatchError, match="ambiguous"):
        apply_structured_patches(model, [{
            "operation": "remove_call",
            "target": "UC3:main:1",
            "receiverOperationId": "EnrollmentControl::change()",
        }])
    assert model == before


def test_remove_call_rejects_children_but_safely_removes_a_leaf() -> None:
    model = _model()
    before = deepcopy(model)
    with pytest.raises(DesignPatchError, match="child calls"):
        apply_structured_patches(model, [{
            "operation": "remove_call",
            "target": "UC3:main:1",
            "receiverOperationId": "EnrollmentControl::change()",
        }])
    assert model == before

    result = apply_structured_patches(model, [{
        "operation": "remove_call",
        "target": "UC5:main:1",
        "receiverOperationId": "Registration::deleteById(id:UUID)",
    }, {
        "operation": "preserve_existing_order",
        "target": "UC5:main:1",
    }])
    calls = result["Collaborations"][1]["calls"]
    assert [call["receiverOperationId"] for call in calls] == [
        "EnrollmentBoundary::start()", "EnrollmentControl::change()",
    ]
    assert calls[1]["parentCallId"] == "UC5:main:1::call:1"


def test_rename_operation_updates_only_its_canonical_references() -> None:
    result = apply_structured_patches(_model(), [{
        "operation": "rename_operation",
        "target": "class_diagram:Registration::deleteById(id:UUID)",
        "newName": "removeById",
    }])

    registration = result["Classes"][2]["operations"][0]
    assert registration["name"] == "removeById"
    assert registration["operationId"] == "Registration::removeById(id:UUID)"
    for collaboration in result["Collaborations"]:
        assert "Registration::removeById(id:UUID)" in {
            call["receiverOperationId"] for call in collaboration["calls"]
        }
    assert result["Collaborations"][0]["calls"][0]["receiverOperationId"] == (
        "EnrollmentBoundary::start()"
    )


def test_rename_operation_rejects_a_non_exact_class_target() -> None:
    with pytest.raises(DesignPatchError, match="exact operationId"):
        apply_structured_patches(_model(), [{
            "operation": "rename_operation",
            "target": "class_diagram:Registration",
            "newName": "removeById",
        }])


def test_semantic_receiver_aliases_require_one_candidate_and_keep_raw_spelling() -> None:
    model = _model()
    model["Classes"][3]["operations"] = [{
        "operationId": "CourseOffering::audit(id:uuid)",
        "name": "audit",
        "parameters": [{"name": "id", "type": "uuid"}],
        "returnType": "void",
        "stepRefs": ["UC3:main:3"],
    }]
    result = apply_structured_patches(model, [{
        "operation": "insert_call_after",
        "target": "UC3:main:1",
        "anchor": "Registration::deleteById(id:uuid)",
        "receiverOperationId": "CourseOffering::audit(id:UUID)",
        "stepRefs": ["UC3:main:3"],
        "argumentBindings": [{
            "parameter": "id", "sourceRef": "UC3:main:1::call:2#result.id",
        }],
    }])
    inserted = result["Collaborations"][0]["calls"][3]
    assert inserted["receiverOperationId"] == "CourseOffering::audit(id:uuid)"

    ambiguous = _model()
    ambiguous["Classes"][3]["operations"] = [{
        "operationId": "CourseOffering::audit()",
        "name": "audit",
        "parameters": [],
        "returnType": "void",
        "stepRefs": ["UC3:main:3"],
    }]
    ambiguous["Collaborations"][0]["calls"].append({
        "callId": "UC3:main:1::call:5",
        "parentCallId": "UC3:main:1::call:2",
        "receiverOperationId": "Registration::deleteById(id:uuid)",
        "stepRefs": ["UC3:main:3"],
        "argumentBindings": [{
            "parameter": "id", "sourceRef": "UC3:main:1::call:2#result.id",
        }],
    })
    with pytest.raises(DesignPatchError, match="ambiguous"):
        apply_structured_patches(ambiguous, [{
            "operation": "insert_call_after",
            "target": "UC3:main:1",
            "anchor": "Registration::deleteById(id:Uuid)",
            "receiverOperationId": "CourseOffering::audit()",
            "stepRefs": ["UC3:main:3"],
        }])
