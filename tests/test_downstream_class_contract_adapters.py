from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path

import pytest

from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.extractor import _control_parameter_types
from app.design.services.erd.mapping import build_logical_model
from app.design.services.sequence_diagram.extractor import extract_sequence_diagrams

_PUML2CODE_ROOT = Path("app/implementation/tools/puml2code-bce")
_PUML2CODE_READY = (_PUML2CODE_ROOT / "src/parser/plantuml.js").is_file()


def _collaboration_model() -> dict:
    return BCEModel.model_validate({
        "Classes": [
            {
                "className": "OrderBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored",
                    "name": "submit",
                    "parameters": [{"name": "request", "type": "OrderRequest"}],
                    "returnType": "Receipt",
                    "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "OrderControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored",
                    "name": "place",
                    "parameters": [{"name": "request", "type": "OrderRequest"}],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
            {
                "className": "Order",
                "stereotype": "Entity",
                "fields": ["id : UUID"],
                "identifier": ["id"],
                "use_case_ids": ["UC1"],
            },
        ],
        "DataTypes": [
            {"name": "OrderRequest", "kind": "valueObject", "fields": ["sku : String"]},
            {"name": "Receipt", "kind": "enumeration", "values": ["ACCEPTED", "REJECTED"]},
        ],
        "Relationships": [],
        "Collaborations": [{
            "collaborationId": "place-order",
            "useCaseIds": ["UC1", "UC_INCLUDED"],
            "entryActor": "Buyer",
            "calls": [
                {
                    "callId": "ignored",
                    "receiverOperationId": "OrderBoundary::submit(request:OrderRequest)",
                    "stepRefs": ["UC1:main:1"],
                    "argumentBindings": [{
                        "parameter": "request", "sourceRef": "UC1:main:1#request",
                    }],
                },
                {
                    "callId": "ignored",
                    "parentCallId": "place-order::call:1",
                    "receiverOperationId": "OrderControl::place(request:OrderRequest)",
                    "stepRefs": ["UC1:main:2"],
                    "argumentBindings": [{
                        "parameter": "request", "sourceRef": "place-order::call:1#request",
                    }],
                },
            ],
        }],
    }).model_dump(by_alias=True)


def _use_case_spec() -> dict:
    return {
        "use_cases": [{"id": "UC1", "name": "Place order"}, {"id": "UC_INCLUDED"}],
        "use_case_specs": [{"use_case_id": "UC1"}, {"use_case_id": "UC_INCLUDED"}],
    }


def test_sequence_projects_collaboration_without_mutating_class_contract():
    class_model = _collaboration_model()
    before = copy.deepcopy(class_model)

    sequence = extract_sequence_diagrams(_use_case_spec(), "", class_model)

    assert class_model == before
    assert [diagram["use_case_id"] for diagram in sequence["Diagrams"]] == ["UC1"]
    calls = [
        message for message in sequence["Diagrams"][0]["Messages"]
        if message["type"] in {"sync", "self", "async"}
    ]
    assert [(call["call_id"], call["target"], call["label"]) for call in calls] == [
        ("place-order::call:1", "OrderBoundary", "submit(request:OrderRequest)"),
        ("place-order::call:2", "OrderControl", "place(request:OrderRequest)"),
    ]
    assert calls[1]["arguments"][0]["source_ref"] == "place-order::call:1#request"


def test_sequence_combines_multiple_execution_groups_for_one_use_case():
    class_model = _collaboration_model()
    class_model["Collaborations"].insert(0, {
        "collaborationId": "confirm-order",
        "useCaseIds": ["UC1"],
        "entryActor": "Buyer",
        "calls": [{
            "callId": "confirm-order::call:1",
            "receiverOperationId": "OrderBoundary::submit(request:OrderRequest)",
            "stepRefs": ["UC1:main:3"],
            "argumentBindings": [{
                "parameter": "request", "sourceRef": "UC1:main:3#request",
            }],
        }],
    })

    specification = _use_case_spec()
    specification["use_case_specs"][0]["main_scenario"] = [
        {"step_number": 1}, {"step_number": 2}, {"step_number": 3},
    ]
    sequence = extract_sequence_diagrams(specification, "", class_model)

    assert len(sequence["Diagrams"]) == 1
    calls = [
        message for message in sequence["Diagrams"][0]["Messages"]
        if message["type"] in {"sync", "self", "async"}
    ]
    assert [call["call_id"] for call in calls] == [
        "place-order::call:1", "place-order::call:2", "confirm-order::call:1",
    ]


def test_sequence_keeps_actor_and_same_named_entity_as_distinct_lifelines():
    class_model = _collaboration_model()
    class_model["Classes"].append({
        "className": "Buyer",
        "stereotype": "Entity",
        "fields": ["id : UUID"],
        "use_case_ids": ["UC1"],
        "operations": [{
            "operationId": "ignored",
            "name": "canPlace",
            "parameters": [],
            "returnType": "boolean",
            "stepRefs": ["UC1:main:2"],
        }],
    })
    class_model["Collaborations"][0]["calls"].append({
        "callId": "ignored",
        "parentCallId": "place-order::call:2",
        "receiverOperationId": "Buyer::canPlace()",
        "stepRefs": ["UC1:main:2"],
        "argumentBindings": [],
    })
    class_model = BCEModel.model_validate(class_model).model_dump(by_alias=True)

    sequence = extract_sequence_diagrams(_use_case_spec(), "", class_model)

    participants = {
        item["alias"]: (item["kind"], item["source_class"])
        for item in sequence["Diagrams"][0]["Participants"]
    }
    assert participants["Buyer"] == ("actor", "")
    assert participants["Buyer_Entity"] == ("entity", "Buyer")
    entity_call = next(
        message for message in sequence["Diagrams"][0]["Messages"]
        if message.get("call_id") == "place-order::call:3"
    )
    assert entity_call["target"] == "Buyer_Entity"


def test_sequence_projects_extension_only_call_and_return_as_opt_fragment():
    class_model = _collaboration_model()
    order = next(item for item in class_model["Classes"] if item["className"] == "Order")
    order["operations"] = [{
        "operationId": "ignored",
        "name": "requiresReview",
        "parameters": [],
        "returnType": "boolean",
        "stepRefs": ["UC1:extension:2a:2a1"],
    }]
    class_model["Collaborations"][0]["calls"].append({
        "callId": "ignored",
        "parentCallId": "place-order::call:2",
        "receiverOperationId": "Order::requiresReview()",
        "stepRefs": ["UC1:extension:2a:2a1"],
        "argumentBindings": [],
    })
    class_model = BCEModel.model_validate(class_model).model_dump(by_alias=True)
    specification = _use_case_spec()
    specification["use_case_specs"][0]["extensions"] = [{
        "label": "2a",
        "branch_step": 2,
        "condition": "The order needs manual review",
        "handling_steps": [{"sub_step": "2a1", "sentence": "System flags it."}],
    }]

    sequence = extract_sequence_diagrams(specification, "", class_model)

    messages = sequence["Diagrams"][0]["Messages"]
    call = next(message for message in messages if message.get("call_id") == "place-order::call:3")
    reply = next(message for message in messages if message.get("reply_to") == "place-order::call:3")
    expected = [{
        "id": "UC1:extension:2a",
        "type": "opt",
        "branch": "main",
        "condition": "The order needs manual review",
    }]
    assert call["fragments"] == expected
    assert reply["fragments"] == expected


@pytest.mark.parametrize("missing_key", [False, True])
def test_sequence_reports_stale_collaboration_model_instead_of_reconstructing_calls(
    missing_key,
):
    class_model = _collaboration_model()
    if missing_key:
        class_model.pop("Collaborations")
    else:
        class_model["Collaborations"] = []

    sequence = extract_sequence_diagrams(_use_case_spec(), "", class_model)

    assert sequence["Diagrams"][0]["Messages"] == []
    assert "stale" in sequence["Diagrams"][0]["UnresolvedSteps"][0]["reason"].lower()


def test_erd_maps_entities_but_never_data_types_to_tables():
    logical = build_logical_model(_collaboration_model())

    assert [table["name"] for table in logical["Tables"]] == ["Order"]


def test_api_adapter_reads_typed_control_signature_from_structured_contract():
    types = _control_parameter_types("", _collaboration_model())

    assert types[("OrderControl", "place")] == {"request": "OrderRequest"}


@pytest.mark.skipif(
    shutil.which("node") is None or not _PUML2CODE_READY,
    reason="Node.js and generated puml2code parser are required",
)
def test_puml2code_generates_value_object_and_enum_java_sources():
    tool_root = _PUML2CODE_ROOT
    script = r'''
const Puml = require('./src');
const source = `@startuml
package "Data Types" {
class Money <<ValueObject>> {
  amount : BigDecimal
}
enum PaymentStatus { PENDING, PAID }
}
@enduml`;
Puml.fromString(source).generate('java', {basePackage: 'example.types'})
  .then(output => output.print(value => process.stdout.write(value + '\n')))
  .catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(
        ["node", "-e", script], cwd=tool_root, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "public final class Money" in result.stdout
    assert "private final BigDecimal amount" in result.stdout
    assert "public enum PaymentStatus" in result.stdout


@pytest.mark.skipif(
    shutil.which("node") is None or not _PUML2CODE_READY,
    reason="Node.js and generated puml2code parser are required",
)
def test_puml2code_blocks_unknown_class_placeholder_source():
    tool_root = _PUML2CODE_ROOT
    script = r'''
const Puml = require('./src');
Puml.fromString('@startuml\nclass UnknownClass {\n}\n@enduml').generate('java')
  .then(() => { process.exitCode = 2; })
  .catch(error => { console.log(error.message); });
'''
    result = subprocess.run(
        ["node", "-e", script], cwd=tool_root, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0
    assert "unresolved placeholder" in result.stdout
