"""Focused safety tests for one-shot provenance-graph Arazzo planning."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.testing.nodes import dynamic_functional as dynamic
from app.testing.utils import arazzo_executor, arazzo_planner


def _output(name: str, *, format: str | None = "uuid") -> dict:
    return {
        "slot": f"body:{name}", "outputName": name,
        "outputExpression": f"$response.body#/{name}",
        "type": "string", "format": format, "cardinality": "single",
    }


def _body_input(name: str) -> dict:
    return {
        "inputSlot": f"body:{name}", "pointerParts": (name,),
        "type": "string", "cardinality": "single",
    }


def _path_input(name: str, *, type_: str = "string", format: str | None = "uuid") -> dict:
    return {
        "inputSlot": f"path:{name}", "type": type_, "format": format,
        "cardinality": "single",
    }


def _candidate() -> dict:
    return {
        "workflowId": "workflow-UC-1",
        "trace": {"requirementIds": [], "useCaseIds": ["UC-1"], "evidenceRefs": []},
        "operations": [{"operationId": "register"}],
        "planningModel": {
            "targetOperationIds": ["register"],
            "availableSteps": [
                {
                    "stepId": "createOffering", "operationId": "createOffering", "method": "POST",
                    "successStatuses": ["201"], "inputs": [_body_input("name")],
                    "outputs": [_output("offeringId"), _output("registrationId")],
                },
                {
                    "stepId": "register", "operationId": "register", "method": "POST",
                    "successStatuses": ["201"], "inputs": [_path_input("offeringId")], "outputs": [],
                },
            ],
        },
    }


def _graph(*, inputs: list[dict], occurrences: list[dict] | None = None, distinct: list[dict] | None = None) -> dict:
    return {
        "workflowId": "workflow-UC-1",
        "occurrences": occurrences or [
            {"occurrenceId": "o1", "operationId": "createOffering"},
            {"occurrenceId": "o2", "operationId": "register"},
        ],
        "requiredInputs": inputs,
        "distinctResourcePairs": distinct or [],
    }


def _candidate_workflow_with_projection_results(
    monkeypatch, projection_results, graph_calls=None, graph_results=None, validation_results=None,
):
    graph_calls = graph_calls if graph_calls is not None else []
    graphs = iter(graph_results or ({"graph": index} for index in range(len(projection_results))))
    results = iter(projection_results)
    document_results = iter(validation_results) if validation_results is not None else None
    authoring = {"workflowId": "workflow-UC-1", "planningModel": {}}

    monkeypatch.setattr(dynamic, "_emit_plan_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dynamic, "_authoring_candidate", lambda candidate, _steps: authoring)
    monkeypatch.setattr(dynamic, "_select_semantic_producers", lambda *_args: [])

    def generate_graph(_client, _candidate, correction_context=None, **_kwargs):
        graph_calls.append(correction_context)
        result = next(graphs)
        if isinstance(result, Exception):
            raise result
        return result

    def project_graph(_candidate, graph):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return {}, {"graph": graph}, []

    monkeypatch.setattr(dynamic, "_generate_workflow_graph", generate_graph)
    monkeypatch.setattr(dynamic, "_validated_graph_projection", project_graph)
    monkeypatch.setattr(dynamic, "_select_literal_values", lambda *_args: [])
    monkeypatch.setattr(dynamic, "_compile_workflow_decision", lambda *_args: {"workflowId": "workflow-UC-1"})
    def validate_document(*_args):
        if document_results is not None:
            result = next(document_results)
            if isinstance(result, Exception):
                raise result
            return result
        return {"workflows": [{"workflowId": "workflow-UC-1"}]}

    monkeypatch.setattr(dynamic, "_validate_document", validate_document)
    result = dynamic._generate_candidate_workflow(object(), {"workflowId": "workflow-UC-1"}, {}, 1, [])
    return result, graph_calls


def test_candidate_workflow_uses_one_graph_call_when_projection_succeeds(monkeypatch) -> None:
    result, graph_calls = _candidate_workflow_with_projection_results(monkeypatch, [({}, {}, [])])

    assert result["workflowId"] == "workflow-UC-1"
    assert graph_calls == [None]


def test_candidate_workflow_corrects_one_rejected_graph_then_succeeds(monkeypatch) -> None:
    result, graph_calls = _candidate_workflow_with_projection_results(
        monkeypatch,
        [dynamic.ArazzoPlanningError("missing required input"), ({}, {}, [])],
    )

    assert result["workflowId"] == "workflow-UC-1"
    assert len(graph_calls) == 2
    assert graph_calls[0] is None
    assert graph_calls[1] == {
        "rejectedGraph": {"graph": 0},
        "validationError": "missing required input",
    }


def test_candidate_workflow_stops_after_second_graph_projection_fails(monkeypatch) -> None:
    graph_calls = []
    with pytest.raises(dynamic.ArazzoPlanningError, match="still invalid"):
        _candidate_workflow_with_projection_results(
            monkeypatch,
            [
                dynamic.ArazzoPlanningError("first invalid"),
                dynamic.ArazzoPlanningError("still invalid"),
            ],
            graph_calls,
        )
    assert len(graph_calls) == 2
    assert graph_calls[0] is None
    assert graph_calls[1]["validationError"] == "first invalid"


def test_candidate_workflow_corrects_one_document_validation_failure(monkeypatch) -> None:
    result, graph_calls = _candidate_workflow_with_projection_results(
        monkeypatch,
        [({}, {}, []), ({}, {}, [])],
        validation_results=[
            dynamic.ArazzoValidationError("indexed list output is not guaranteed"),
            {"workflows": [{"workflowId": "workflow-UC-1"}]},
        ],
    )

    assert result["workflowId"] == "workflow-UC-1"
    assert graph_calls == [
        None,
        {
            "rejectedGraph": {"graph": 0},
            "validationError": "indexed list output is not guaranteed",
        },
    ]


def test_candidate_workflow_stops_after_second_document_validation_failure(monkeypatch) -> None:
    graph_calls = []
    with pytest.raises(dynamic.ArazzoValidationError, match="second invalid document"):
        _candidate_workflow_with_projection_results(
            monkeypatch,
            [({}, {}, []), ({}, {}, [])],
            graph_calls,
            validation_results=[
                dynamic.ArazzoValidationError("first invalid document"),
                dynamic.ArazzoValidationError("second invalid document"),
            ],
        )

    assert len(graph_calls) == 2
    assert graph_calls[1]["validationError"] == "first invalid document"


def test_candidate_workflow_corrects_first_graph_response_schema_failure(monkeypatch) -> None:
    schema_error = dynamic.jsonschema.ValidationError(
        "'knownAlias' is not one of ['availableAlias']",
        validator="enum",
        path=["requiredInputs", 0, "sourceInputSlot"],
    )
    result, graph_calls = _candidate_workflow_with_projection_results(
        monkeypatch,
        [({}, {}, [])],
        graph_results=[schema_error, {"graph": "corrected"}],
    )

    assert result["workflowId"] == "workflow-UC-1"
    assert len(graph_calls) == 2
    assert graph_calls[0] is None
    assert graph_calls[1]["validationError"] == (
        "requiredInputs.0.sourceInputSlot: enum validation failed "
        "('knownAlias' is not one of ['availableAlias'])"
    )


def test_candidate_workflow_stops_after_second_graph_response_schema_failure(monkeypatch) -> None:
    first_error = dynamic.PydanticValidationError.from_exception_data(
        "WorkflowGraph",
        [{"type": "missing", "loc": ("occurrences",), "input": {}}],
    )
    second_error = dynamic.jsonschema.ValidationError("second schema failure", validator="type")
    graph_calls = []
    with pytest.raises(dynamic.jsonschema.ValidationError, match="second schema failure"):
        _candidate_workflow_with_projection_results(
            monkeypatch,
            [({}, {}, [])],
            graph_calls,
            graph_results=[first_error, second_error],
        )

    assert len(graph_calls) == 2
    assert graph_calls[0] is None
    assert graph_calls[1]["validationError"] == "occurrences: Field required"


def test_graph_requires_exact_closure_of_every_selected_required_slot() -> None:
    candidate = _candidate()
    incomplete = _graph(inputs=[
        {"targetOccurrenceId": "o1", "targetInputSlot": "body:name", "literalNeeded": True},
    ])
    with pytest.raises(dynamic.ArazzoPlanningError, match="cover every required input exactly once"):
        dynamic._validated_graph_projection(candidate, incomplete)

    complete = _graph(inputs=[
        {"targetOccurrenceId": "o1", "targetInputSlot": "body:name", "literalNeeded": True},
        {"targetOccurrenceId": "o2", "targetInputSlot": "path:offeringId", "literalNeeded": False,
         "sourceOccurrenceId": "o1", "sourceOutputName": "offeringId"},
    ])
    selected, decision, literal_slots = dynamic._validated_graph_projection(candidate, complete)
    decision["fixedInputs"] = [
        {"targetStepId": "o1", "targetInputSlot": "body:name", "value": "Algorithms"}
    ]

    workflow = dynamic._compile_workflow_decision(decision, selected)
    assert len(literal_slots) == 1
    assert workflow["steps"][1]["parameters"] == [
        {"name": "offeringId", "in": "path", "value": "$steps.o1.outputs.offeringId"}
    ]


def test_graph_schema_and_prompt_require_explicit_exclusive_input_binding_fields() -> None:
    candidate = _candidate()
    item_schema = dynamic._graph_response_format(candidate)["json_schema"]["schema"][
        "properties"]["requiredInputs"]["items"]
    assert set(item_schema["required"]) == {
        "targetOccurrenceId", "targetInputSlot", "literalNeeded",
        "sourceOccurrenceId", "sourceOutputName",
        "sourceInputOccurrenceId", "sourceInputSlot",
    }
    prompt = dynamic._workflow_graph_prompt(candidate)
    assert "set unused source fields explicitly to null" in prompt
    assert "Never combine binding shapes" in prompt
    assert "for a fixed literal" in prompt
    assert "for an output binding" in prompt
    assert "for fixed-input reuse" in prompt


@pytest.mark.parametrize(
    "bad_input, message",
    [
        ({"targetOccurrenceId": "o2", "targetInputSlot": "path:offeringId", "literalNeeded": False,
          "sourceOccurrenceId": "missing", "sourceOutputName": "offeringId"}, "incomplete producer binding"),
        ({"targetOccurrenceId": "o1", "targetInputSlot": "body:name", "literalNeeded": False,
          "sourceOccurrenceId": "o2", "sourceOutputName": "offeringId"}, "topologically ordered"),
    ],
)
def test_graph_rejects_unknown_or_cyclic_provenance(bad_input: dict, message: str) -> None:
    inputs = [
        {"targetOccurrenceId": "o1", "targetInputSlot": "body:name", "literalNeeded": True},
        {"targetOccurrenceId": "o2", "targetInputSlot": "path:offeringId", "literalNeeded": False,
         "sourceOccurrenceId": "o1", "sourceOutputName": "offeringId"},
    ]
    inputs[0 if bad_input["targetOccurrenceId"] == "o1" else 1] = bad_input
    with pytest.raises(dynamic.ArazzoPlanningError, match=message):
        dynamic._validated_graph_projection(_candidate(), _graph(inputs=inputs))


def test_graph_preserves_the_exact_model_selected_finite_catalog_output() -> None:
    graph = _graph(inputs=[
        {"targetOccurrenceId": "o1", "targetInputSlot": "body:name", "literalNeeded": True},
        {"targetOccurrenceId": "o2", "targetInputSlot": "path:offeringId", "literalNeeded": False,
         "sourceOccurrenceId": "o1", "sourceOutputName": "offeringId"},
    ])
    selected, decision, _ = dynamic._validated_graph_projection(_candidate(), graph)
    assert decision["connectionIds"] == ["o1.offeringId->o2.path:offeringId"]
    assert selected["planningModel"]["availableSteps"][1]["inputs"][0]["connections"][0]["outputName"] == "offeringId"

    graph["requiredInputs"][1]["sourceOutputName"] = "unknownId"
    with pytest.raises(dynamic.ArazzoPlanningError, match="unknown or incompatible output"):
        dynamic._validated_graph_projection(_candidate(), graph)


def test_repeated_occurrences_preserve_exact_bindings_and_compile_distinct_assertion() -> None:
    graph = _graph(
        occurrences=[
            {"occurrenceId": "o1", "operationId": "createOffering"},
            {"occurrenceId": "o2", "operationId": "createOffering"},
            {"occurrenceId": "o3", "operationId": "register"},
        ],
        inputs=[
            {"targetOccurrenceId": "o1", "targetInputSlot": "body:name", "literalNeeded": True},
            {"targetOccurrenceId": "o2", "targetInputSlot": "body:name", "literalNeeded": True},
            {"targetOccurrenceId": "o3", "targetInputSlot": "path:offeringId", "literalNeeded": False,
             "sourceOccurrenceId": "o2", "sourceOutputName": "offeringId"},
        ],
        distinct=[{
            "leftOccurrenceId": "o1", "leftOutputName": "offeringId",
            "rightOccurrenceId": "o2", "rightOutputName": "offeringId",
            "relation": "distinct_resource_identity",
        }],
    )
    selected, decision, _ = dynamic._validated_graph_projection(_candidate(), graph)
    decision["fixedInputs"] = [
        {"targetStepId": "o1", "targetInputSlot": "body:name", "value": "A"},
        {"targetStepId": "o2", "targetInputSlot": "body:name", "value": "B"},
    ]
    workflow = dynamic._compile_workflow_decision(decision, selected)

    assert [step["stepId"] for step in workflow["steps"]] == ["o1", "o2", "o3"]
    assert workflow["steps"][2]["parameters"][0]["value"] == "$steps.o2.outputs.offeringId"
    assert workflow["steps"][2]["successCriteria"] == [
        {"condition": "$steps.o1.outputs.offeringId != $steps.o2.outputs.offeringId"}
    ]


def test_literal_prompt_receives_graph_provenance_and_explicit_alias_mapping(monkeypatch) -> None:
    candidate = _candidate()
    candidate["setupOperations"] = [{
        "operationId": "createOffering",
        "linkedUseCaseEvidence": [{"precondition": "the selected offering is not yet present"}],
    }]
    graph = _graph(
        occurrences=[
            {"occurrenceId": "o1", "operationId": "createOffering"},
            {"occurrenceId": "o2", "operationId": "createOffering"},
            {"occurrenceId": "o3", "operationId": "register"},
        ],
        inputs=[
            {"targetOccurrenceId": "o1", "targetInputSlot": "body:name", "literalNeeded": True},
            {"targetOccurrenceId": "o2", "targetInputSlot": "body:name", "literalNeeded": True},
            {"targetOccurrenceId": "o3", "targetInputSlot": "path:offeringId", "literalNeeded": False,
             "sourceOccurrenceId": "o2", "sourceOutputName": "offeringId"},
        ],
        distinct=[{
            "leftOccurrenceId": "o1", "leftOutputName": "offeringId",
            "rightOccurrenceId": "o2", "rightOutputName": "offeringId",
            "relation": "distinct_resource_identity",
        }],
    )
    selected, decision, literal_slots = dynamic._validated_graph_projection(candidate, graph)
    captured: dict = {}

    def create(**request):
        captured.update(request)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content='{"v1":"Algorithms","v2":"Databases"}')
        )])

    monkeypatch.setattr(dynamic, "build_arazzo_llm_connection", lambda: SimpleNamespace(model="fake-model"))
    monkeypatch.setattr(dynamic, "profile_for", lambda *args, **kwargs: SimpleNamespace(
        temperature=0, top_p=None, completion_limit=lambda _limit: 100,
        resolve_reasoning=lambda _requested: None,
    ))
    monkeypatch.setattr(dynamic, "_structured_output_extra_body", lambda *_args: None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    fixed_inputs = dynamic._select_literal_values(client, selected, literal_slots, decision)

    user_prompt = captured["messages"][1]["content"]
    provenance_text = user_prompt.split("workflowInputProvenance:\n", 1)[1].split(
        "\nselectedSetupUseCaseEvidence:", 1
    )[0]
    provenance = json.loads(provenance_text)
    assert provenance["inputBindings"] == [{
        "sourceStepId": "o2", "sourceOutputName": "offeringId",
        "targetStepId": "o3", "targetInputSlot": "path:offeringId",
    }]
    assert provenance["distinctResourcePairs"] == decision["distinctResourcePairs"]
    assert provenance["occurrences"] == [
        {"stepId": "o1", "operationId": "createOffering"},
        {"stepId": "o2", "operationId": "createOffering"},
        {"stepId": "o3", "operationId": "register"},
    ]
    assert "selectedSetupUseCaseEvidence" in user_prompt
    assert "v1, v2" in user_prompt
    literal_text = user_prompt.split("requiredLiteralInputs:\n", 1)[1]
    assert json.loads(literal_text) == [
        {"key": "v1", "targetStepId": "o1", "targetInputSlot": "body:name",
         "type": "string", "format": None, "description": ""},
        {"key": "v2", "targetStepId": "o2", "targetInputSlot": "body:name",
         "type": "string", "format": None, "description": ""},
    ]
    assert fixed_inputs == [
        {"targetStepId": "o1", "targetInputSlot": "body:name", "value": "Algorithms"},
        {"targetStepId": "o2", "targetInputSlot": "body:name", "value": "Databases"},
    ]


def test_numeric_calculator_path_literals_remain_valid_graph_inputs() -> None:
    candidate = {
        "workflowId": "workflow-calc",
        "trace": {"requirementIds": [], "useCaseIds": [], "evidenceRefs": []},
        "operations": [{"operationId": "sum"}],
        "planningModel": {
            "targetOperationIds": ["sum"],
            "availableSteps": [{
                "stepId": "sum", "operationId": "sum", "successStatuses": ["200"], "inputs": [
                    _path_input("firstNumber", type_="number", format=None),
                    _path_input("secondNumber", type_="number", format=None),
                ], "outputs": [],
            }],
        },
    }
    graph = {
        "workflowId": "workflow-calc", "occurrences": [{"occurrenceId": "o1", "operationId": "sum"}],
        "requiredInputs": [
            {"targetOccurrenceId": "o1", "targetInputSlot": "path:firstNumber", "literalNeeded": True},
            {"targetOccurrenceId": "o1", "targetInputSlot": "path:secondNumber", "literalNeeded": True},
        ], "distinctResourcePairs": [],
    }
    selected, decision, _ = dynamic._validated_graph_projection(candidate, graph)
    decision["fixedInputs"] = [
        {"targetStepId": "o1", "targetInputSlot": "path:firstNumber", "value": 42},
        {"targetStepId": "o1", "targetInputSlot": "path:secondNumber", "value": 7},
    ]
    workflow = dynamic._compile_workflow_decision(decision, selected)
    assert workflow["steps"][0]["parameters"] == [
        {"name": "firstNumber", "in": "path", "value": 42},
        {"name": "secondNumber", "in": "path", "value": 7},
    ]


def test_literal_response_schema_preserves_projected_leaf_types_and_string_format() -> None:
    slots = [
        ("o1", "body:count", {"type": "integer"}),
        ("o1", "body:ratio", {"type": "number"}),
        ("o1", "body:enabled", {"type": "boolean"}),
        ("o1", "body:offeringId", {"type": "string", "format": "uuid"}),
        ("o1", "body:metadata", {"type": "object"}),
        ("o1", "body:tags", {"type": "array"}),
    ]

    schema = dynamic._literal_value_response_format(slots)["json_schema"]["schema"]

    assert schema["properties"] == {
        "v1": {"type": "integer"},
        "v2": {"type": "number"},
        "v3": {"type": "boolean"},
        "v4": {"type": "string", "format": "uuid"},
        "v5": {"type": "object"},
        "v6": {"type": "array"},
    }
    with pytest.raises(dynamic.jsonschema.ValidationError):
        dynamic.jsonschema.Draft202012Validator(schema).validate({
            "v1": "1", "v2": 2.5, "v3": True, "v4": "not-checked-here",
            "v5": {}, "v6": [],
        })


def test_explicit_source_format_can_fill_unformatted_target_but_not_the_inverse() -> None:
    uuid_output = _output("resourceId", format="uuid")
    plain_output = {**_output("resourceId", format=None), "outputExpression": "$response.body#"}
    plain_input = _path_input("resourceId", format=None)
    uuid_input = _path_input("resourceId", format="uuid")

    assert dynamic._connection_types_compatible(plain_input, uuid_output)
    assert not dynamic._connection_types_compatible(uuid_input, plain_output)
    assert not dynamic._connection_types_compatible(
        _path_input("resourceId", format="date-time"), uuid_output
    )


def test_object_query_literal_uses_the_projected_value_schema(monkeypatch) -> None:
    value_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "term": {"type": "string"},
        },
        "required": ["name"],
    }
    slots = [(
        "search", "query:filter", {
            "type": "object", "format": "", "cardinality": "one",
            "parameterStyle": "deepObject", "parameterExplode": True,
            "valueSchema": value_schema,
        },
    )]
    captured: dict = {}

    def create(**request):
        captured.update(request)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(
                content='{"v1":{"name":"Algorithms","term":"2026-spring"}}'
            )
        )])

    monkeypatch.setattr(dynamic, "build_arazzo_llm_connection", lambda: SimpleNamespace(model="fake-model"))
    monkeypatch.setattr(dynamic, "profile_for", lambda *args, **kwargs: SimpleNamespace(
        temperature=0, top_p=None, completion_limit=lambda _limit: 100,
        resolve_reasoning=lambda _requested: None,
    ))
    monkeypatch.setattr(dynamic, "_structured_output_extra_body", lambda *_args: None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    fixed = dynamic._select_literal_values(client, {"planningModel": {"availableSteps": []}}, slots, {})

    response_schema = captured["response_format"]["json_schema"]["schema"]
    assert response_schema["properties"]["v1"] == value_schema
    descriptors = json.loads(captured["messages"][1]["content"].split("requiredLiteralInputs:\n", 1)[1])
    assert descriptors[0]["valueSchema"] == value_schema
    assert fixed == [{
        "targetStepId": "search", "targetInputSlot": "query:filter",
        "value": {"name": "Algorithms", "term": "2026-spring"},
    }]


def test_read_only_setup_outputs_need_earlier_state_changing_setup() -> None:
    candidate = {
        "workflowId": "workflow-read",
        "operations": [{"operationId": "targetLookup"}],
        "planningModel": {},
    }
    steps = [
        {"stepId": "setupLookup", "operationId": "setupLookup", "method": "GET",
         "successStatuses": ["200"], "inputs": [], "outputs": [_output("resourceId")]},
        {"stepId": "targetLookup", "operationId": "targetLookup", "method": "GET",
         "successStatuses": ["200"], "inputs": [_path_input("resourceId")],
         "outputs": [_output("resultId")]},
    ]
    candidate["setupOperations"] = [
        {"operationId": "createResource", "method": "POST"},
        {"operationId": "setupLookup", "method": "GET"},
    ]
    steps.insert(0, {"stepId": "createResource", "operationId": "createResource", "method": "POST",
                     "successStatuses": ["201"], "inputs": [], "outputs": []})
    projected = dynamic._authoring_candidate(candidate, steps)
    available = projected["planningModel"]["availableSteps"]
    assert available[1]["outputs"] == [_output("resourceId")]
    assert available[2]["outputs"] == [_output("resultId")]
    assert [item["operationId"] for item in dynamic._graph_catalog(projected)] == [
        "createResource", "setupLookup", "targetLookup"
    ]

    graph = {
        "workflowId": "workflow-read",
        "occurrences": [
            {"occurrenceId": "o0", "operationId": "createResource"},
            {"occurrenceId": "o1", "operationId": "setupLookup"},
            {"occurrenceId": "o2", "operationId": "targetLookup"},
        ],
        "requiredInputs": [{
            "targetOccurrenceId": "o2", "targetInputSlot": "path:resourceId",
            "literalNeeded": False, "sourceOccurrenceId": "o1", "sourceOutputName": "resourceId",
        }],
        "distinctResourcePairs": [],
    }
    selected, _, _ = dynamic._validated_graph_projection(projected, graph)
    assert selected["planningModel"]["availableSteps"][2]["inputs"][0]["connections"][0]["sourceStepId"] == "o1"

    graph["occurrences"] = graph["occurrences"][1:]
    with pytest.raises(dynamic.ArazzoPlanningError, match="state-changing setup occurrence"):
        dynamic._validated_graph_projection(projected, graph)


def test_graph_prompt_scopes_setup_use_case_flow_to_linked_operation() -> None:
    candidate = {
        "workflowId": "workflow-target",
        "operations": [{"operationId": "target"}],
        "planningModel": {
            "intent": {},
            "targetOperationIds": ["target"],
            "producerSelections": [{
                "targetOperationId": "target", "targetInputSlot": "path:itemId",
                "sourceOperationId": "setup-linked", "sourceOutputName": "bodyItemId",
                "creatorOperationId": "setup-linked",
            }],
            "availableSteps": [
                {"operationId": "setup-linked", "method": "POST", "inputs": [], "outputs": []},
                {"operationId": "setup-unrelated", "method": "POST", "inputs": [], "outputs": []},
                {"operationId": "target", "method": "GET", "inputs": [], "outputs": []},
            ],
        },
        "setupOperations": [
            {"operationId": "setup-linked", "linkedUseCaseEvidence": [{
                "useCaseId": "UC-linked", "preconditions": ["The requested item is absent."],
                "main_scenario": ["Create the requested item."],
            }]},
            {"operationId": "setup-unrelated"},
        ],
    }

    prompt = dynamic._workflow_graph_prompt(candidate)

    assert '"operationId":"setup-linked"' in prompt
    assert '"main_scenario":["Create the requested item."]' in prompt
    assert '"operationId":"setup-unrelated"' in prompt
    assert '"main_scenario":["Delete an unrelated item."]' not in prompt
    assert "UC-unrelated" not in prompt
    assert '"creatorOperationId":"setup-linked"' in prompt
    assert "include that exact state-changing operation before its selected read-only resource producer" in prompt


def test_plain_response_body_cannot_claim_uuid_but_json_pointer_leaf_uses_runtime_gate() -> None:
    uuid_input = _path_input("resourceId", format="uuid")
    whole_body = {**_output("bodyValue", format=None), "outputExpression": "$response.body#"}
    nested_id = {**_output("registrationId", format=None), "outputExpression": "$response.body#/registrationList/0/registrationId"}

    assert not dynamic._connection_types_compatible(uuid_input, whole_body)
    assert not arazzo_planner._connection_types_compatible(uuid_input, whole_body)
    assert dynamic._connection_types_compatible(uuid_input, nested_id)
    assert arazzo_planner._connection_types_compatible(uuid_input, nested_id)
    assert arazzo_executor._validated_schema_value(
        {"type": "string", "format": "uuid"}, "11111111-1111-1111-1111-111111111111",
        location="path.resourceId", openapi={},
    )
    with pytest.raises(arazzo_executor._ExecutionError, match="is not a 'uuid'"):
        arazzo_executor._validated_schema_value(
            {"type": "string", "format": "uuid"}, "not-a-uuid",
            location="path.resourceId", openapi={},
        )
