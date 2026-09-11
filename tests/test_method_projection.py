from __future__ import annotations

from copy import deepcopy

from app.design.schemas.class_model import BCEModel
from app.design.services.sequence_diagram.projection import SequenceCollection
from app.implementation.generation.method_skeleton import (
    render_backend_method_skeletons,
)
from app.implementation.planning.method_projection import project_method_calls


def _bce() -> BCEModel:
    return BCEModel.model_validate(
        {
            "Classes": [
                _control("RootControl", "run", [("value", "int")], "void"),
                _control("LookupControl", "lookup", [("value", "int")], "String"),
                _control("SaveControl", "save", [("name", "String")], "void"),
            ],
            "DataTypes": [],
            "Relationships": [],
            "Collaborations": [],
        }
    )


def _control(
    owner: str,
    method: str,
    parameters: list[tuple[str, str]],
    return_type: str,
) -> dict[str, object]:
    return {
        "className": owner,
        "stereotype": "Control",
        "use_case_ids": ["UC1", "UC2"],
        "operations": [
            {
                "operationId": "stale",
                "name": method,
                "parameters": [
                    {"name": name, "type": value_type}
                    for name, value_type in parameters
                ],
                "returnType": return_type,
                "stepRefs": [],
            }
        ],
    }


def _diagram(use_case_id: str = "UC1") -> dict[str, object]:
    root = f"{use_case_id}-flow::call:1"
    lookup = f"{use_case_id}-flow::call:2"
    save = f"{use_case_id}-flow::call:3"
    return {
        "use_case_id": use_case_id,
        "use_case_name": "Synthetic flow",
        "Participants": [
            {"name": "Actor", "alias": "Actor", "kind": "actor", "source_class": ""},
            {
                "name": "RootControl",
                "alias": "Root",
                "kind": "control",
                "source_class": "RootControl",
            },
            {
                "name": "LookupControl",
                "alias": "Lookup",
                "kind": "control",
                "source_class": "LookupControl",
            },
            {
                "name": "SaveControl",
                "alias": "Save",
                "kind": "control",
                "source_class": "SaveControl",
            },
        ],
        "Messages": [
            _call(
                use_case_id,
                root,
                "Actor",
                "Root",
                "run(value:int)",
                [("value", "int", "input", f"{use_case_id}:main:1#value")],
            ),
            _call(
                use_case_id,
                lookup,
                "Root",
                "Lookup",
                "lookup(value:int)",
                [("value", "int", "call_parameter", f"{root}#value")],
            ),
            _return(use_case_id, lookup, "Lookup", "Root", "String"),
            _call(
                use_case_id,
                save,
                "Root",
                "Save",
                "save(name:String)",
                [("name", "String", "call_result", f"{lookup}#result")],
            ),
            _return(use_case_id, save, "Save", "Root", "void"),
            _return(use_case_id, root, "Root", "Actor", "void"),
        ],
        "UnresolvedSteps": [],
        "NarrativeSteps": [],
    }


def _call(
    use_case_id: str,
    call_id: str,
    source: str,
    target: str,
    label: str,
    arguments: list[tuple[str, str, str, str]],
) -> dict[str, object]:
    return {
        "source": source,
        "target": target,
        "label": label,
        "type": "sync",
        "use_case_ids": [use_case_id],
        "step_ids": [f"{use_case_id}:main:1"],
        "call_id": call_id,
        "arguments": [
            {
                "parameter": parameter,
                "type": value_type,
                "source_kind": source_kind,
                "source_ref": source_ref,
            }
            for parameter, value_type, source_kind, source_ref in arguments
        ],
    }


def _return(
    use_case_id: str, call_id: str, source: str, target: str, return_type: str
) -> dict[str, object]:
    return {
        "source": source,
        "target": target,
        "label": return_type,
        "type": "return",
        "use_case_ids": [use_case_id],
        "step_ids": [f"{use_case_id}:main:1"],
        "reply_to": call_id,
    }


def _project(*diagrams: dict[str, object]):
    sequence = SequenceCollection.model_validate(
        {"Diagrams": list(diagrams), "MethodProposals": []}
    )
    return project_method_calls(bce_model=_bce(), sequence_model=sequence)


def _method(result, owner: str):
    return next(item for item in result.methods if item.method.class_name == owner)


def test_projects_ordered_typed_calls_and_prior_result_wiring() -> None:
    root = _method(_project(_diagram()), "RootControl")

    assert root.generation == "code"
    assert [item.target.class_name for item in root.slices[0].outgoing] == [
        "LookupControl",
        "SaveControl",
    ]
    assert root.slices[0].outgoing[0].arguments[0].expression == "value"
    assert root.slices[0].outgoing[1].arguments[0].expression == "lookupResult1"


def test_deduplicates_identical_slices_across_use_cases() -> None:
    root = _method(_project(_diagram("UC1"), _diagram("UC2")), "RootControl")

    assert len(root.slices) == 1
    assert root.slices[0].use_case_ids == ("UC1", "UC2")


def test_conflicting_scenario_slices_are_hints_not_concatenated() -> None:
    second = _diagram("UC2")
    second["Messages"] = [
        item
        for item in second["Messages"]
        if not (isinstance(item, dict) and "lookup" in str(item.get("label")))
        and not (
            isinstance(item, dict)
            and str(item.get("reply_to", "")).endswith("call:2")
        )
    ]
    save_call = next(
        item
        for item in second["Messages"]
        if isinstance(item, dict) and "save" in str(item.get("label"))
    )
    save_call["arguments"][0].update(
        {"source_kind": "input", "source_ref": "UC2:main:1#name"}
    )
    root = _method(_project(_diagram("UC1"), second), "RootControl")

    assert root.generation == "hint"
    assert "conflicting_scenario_slices" in root.reasons
    assert len(root.slices) == 2


def test_natural_language_fragment_condition_never_becomes_code() -> None:
    diagram = _diagram()
    lookup = next(
        item
        for item in diagram["Messages"]
        if isinstance(item, dict) and "lookup" in str(item.get("label"))
    )
    lookup["fragments"] = [
        {
            "id": "UC1:extension:2a",
            "type": "opt",
            "branch": "main",
            "condition": "The value is accepted",
        }
    ]
    root = _method(_project(diagram), "RootControl")
    call = next(item.outgoing[0] for item in root.slices if item.outgoing)

    assert call.generation == "hint"
    assert call.reasons == ("fragment_condition_not_typed",)


def test_unresolved_target_and_return_stack_are_structured_diagnostics() -> None:
    diagram = _diagram()
    diagram["Messages"][1]["target"] = "Missing"
    diagram["Messages"][2]["reply_to"] = "unknown-call"
    result = _project(diagram)

    reasons = {item.reason for item in result.diagnostics}
    assert "unresolved_target_operation" in reasons
    assert "return_stack_mismatch" in reasons


def test_type_mismatch_preserves_the_exact_target_as_a_hint() -> None:
    diagram = _diagram()
    lookup = diagram["Messages"][1]
    lookup["arguments"][0]["type"] = "String"

    result = _project(diagram)
    root = _method(result, "RootControl")
    call = root.slices[0].outgoing[0]

    assert call.target is not None
    assert call.target.class_name == "LookupControl"
    assert call.generation == "hint"
    assert "argument_contract_mismatch" in call.reasons
    assert "argument_contract_mismatch" in {item.reason for item in result.diagnostics}


def test_argument_bindings_are_validated_by_name_and_rendered_in_signature_order() -> None:
    bce = _bce().model_copy(deep=True)
    root_contract = next(
        item for item in bce.Classes if item.class_name == "RootControl"
    ).operations[0]
    root_contract.parameters.append(
        root_contract.parameters[0].model_copy(
            update={"name": "mode", "type": "String"}
        )
    )
    lookup_contract = next(
        item for item in bce.Classes if item.class_name == "LookupControl"
    ).operations[0]
    lookup_contract.parameters.append(
        lookup_contract.parameters[0].model_copy(
            update={"name": "mode", "type": "String"}
        )
    )
    diagram = _diagram()
    root_call = diagram["Messages"][0]
    root_call["label"] = "run(value:int,mode:String)"
    root_call["arguments"] = [
        {
            "parameter": "mode",
            "type": "String",
            "source_kind": "input",
            "source_ref": "UC1:main:1#mode",
        },
        root_call["arguments"][0],
    ]
    lookup_call = diagram["Messages"][1]
    lookup_call["label"] = "lookup(value:int,mode:String)"
    lookup_call["arguments"] = [
        {
            "parameter": "mode",
            "type": "String",
            "source_kind": "call_parameter",
            "source_ref": "UC1-flow::call:1#mode",
        },
        lookup_call["arguments"][0],
    ]

    result = project_method_calls(
        bce_model=bce,
        sequence_model=SequenceCollection.model_validate(
            {"Diagrams": [diagram], "MethodProposals": []}
        ),
    )
    root = _method(result, "RootControl")

    assert "argument_contract_mismatch" not in root.reasons
    assert [item.parameter for item in root.slices[0].outgoing[0].arguments] == [
        "value",
        "mode",
    ]
    assert [item.expression for item in root.slices[0].outgoing[0].arguments] == [
        "value",
        "mode",
    ]


def test_exact_signature_selects_one_overloaded_operation() -> None:
    bce = _bce().model_copy(deep=True)
    lookup = next(item for item in bce.Classes if item.class_name == "LookupControl")
    lookup.operations.append(
        lookup.operations[0].model_copy(
            update={
                "operation_id": "LookupControl.lookup(String)",
                "stable_id": "lookup-string",
                "parameters": [
                    lookup.operations[0].parameters[0].model_copy(
                        update={"type": "String"}
                    )
                ],
            }
        )
    )
    result = project_method_calls(
        bce_model=bce,
        sequence_model=SequenceCollection.model_validate(
            {"Diagrams": [_diagram()], "MethodProposals": []}
        ),
    )
    root = _method(result, "RootControl")

    assert root.slices[0].outgoing[0].target is not None
    assert root.slices[0].outgoing[0].target.parameters == (("value", "int"),)


def test_recursive_call_is_a_hint() -> None:
    diagram = _diagram()
    root_call = diagram["Messages"][0]
    recursive = deepcopy(root_call)
    recursive.update(
        {
            "source": "Root",
            "target": "Root",
            "type": "self",
            "call_id": "UC1-flow::call:4",
            "arguments": [
                {
                    "parameter": "value",
                    "type": "int",
                    "source_kind": "call_parameter",
                    "source_ref": "UC1-flow::call:1#value",
                }
            ],
        }
    )
    diagram["Messages"] = [
        root_call,
        recursive,
        _return("UC1", "UC1-flow::call:4", "Root", "Root", "void"),
        _return("UC1", "UC1-flow::call:1", "Root", "Actor", "void"),
    ]
    root = _method(_project(diagram), "RootControl")
    call = next(item.outgoing[0] for item in root.slices if item.outgoing)

    assert call.generation == "hint"
    assert "cyclic_method_call" in call.reasons


def test_renders_compile_safe_service_calls() -> None:
    bce = _bce()
    projection = project_method_calls(
        bce_model=bce,
        sequence_model=SequenceCollection.model_validate(
            {"Diagrams": [_diagram()], "MethodProposals": []}
        ),
    )
    files = render_backend_method_skeletons(bce, projection, "com.example.app")
    root = files["com/example/app/application/impl/RootControlService.java"]

    assert "private final LookupControl lookupControl;" in root
    assert "private final SaveControl saveControl;" in root
    assert root.index("lookupControl.lookup(value)") < root.index(
        "saveControl.save(lookupResult1)"
    )
    assert "EASYDEP-IMPLEMENT" not in root
    leaf = files["com/example/app/application/impl/LookupControlService.java"]
    assert "EASYDEP-IMPLEMENT:" in leaf
    assert "reports/implementation-tasks/method-context/" in leaf
