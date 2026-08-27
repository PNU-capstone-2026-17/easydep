"""Compare class-design artifacts against one frozen course-registration input.

This module is deliberately offline: it loads frozen requirements and
specifications checkpoints, then checks supplied artifacts.  It never invokes
an LLM and it does not regard a stored class diagram as the only valid
answer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.scenario import ScenarioIndex, build_scenario_index

CASE_ID = "e1-aws"
SCHEMA_VERSION = "easydep-class-design-evaluation/v1"
ROOT = Path(__file__).resolve().parents[1]
GOLD_ROOT = ROOT / "evaluation" / "baselines" / "course-registration-cases" / "goldset" / CASE_ID

# These are reviewer prompts, not a score or a generator contract.  A reviewer
# records observations for both artifacts and explains trade-offs in prose.
QUALITATIVE_RUBRIC = (
    "requirement_fidelity",
    "responsibility_cohesion",
    "giant_or_redundant_classes",
    "entity_and_datatype_choices",
    "operation_parameter_return_clarity",
    "relationship_economy",
    "call_and_return_readability",
)


def _checkpoint_context() -> tuple[dict[str, Any], dict[str, str]]:
    """Load and digest-check only the frozen upstream checkpoints."""
    requirements = _load_checkpoint("requirements")
    specifications = _load_checkpoint("specifications")
    specification_context = {
        key: specifications.get(key)
        for key in ("actors", "use_cases", "use_case_specs", "classified")
    }
    specification_context["relationships"] = {}
    state = {**specifications, "usecase_spec": specification_context}
    checkpoints = {
        "requirements": _checkpoint_digest(requirements),
        "specifications": _checkpoint_digest(specifications),
    }
    return state, checkpoints


def frozen_e1_scenario_index() -> ScenarioIndex:
    """Load the digest-verified E1 specification as the class service input."""

    state, _checkpoints = _checkpoint_context()
    specification = state.get("usecase_spec")
    if not isinstance(specification, dict):
        raise TypeError("frozen E1 usecase_spec must be an object")
    return build_scenario_index(specification)


def _checkpoint_digest(state: dict[str, Any]) -> str:
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_checkpoint(checkpoint: str) -> dict[str, Any]:
    manifest = _read_json(GOLD_ROOT / "manifest.json")
    entry = next(
        (item for item in manifest.get("checkpoints") or [] if item.get("id") == checkpoint),
        None,
    )
    if not isinstance(entry, dict):
        raise KeyError(f"Frozen checkpoint is absent: {checkpoint}")
    state = _read_json(GOLD_ROOT / "snapshots" / checkpoint / "state.json")
    if _checkpoint_digest(state) != entry.get("sha256"):
        raise ValueError(f"Frozen checkpoint digest mismatch: {checkpoint}")
    return state


def _gate(status: str, findings: list[dict[str, str | None]]) -> dict[str, Any]:
    return {"status": status, "findings": findings, "findingCount": len(findings)}


def _known_steps(state: dict[str, Any]) -> set[str]:
    """Derive canonical step ids from the frozen specification, not a diagram."""
    steps: set[str] = set()
    for specification in state.get("use_case_specs") or []:
        if not isinstance(specification, dict):
            continue
        use_case_id = str(specification.get("use_case_id") or "").strip()
        if not use_case_id:
            continue
        raw_preconditions = specification.get("preconditions") or []
        preconditions = (
            list(raw_preconditions.values())
            if isinstance(raw_preconditions, dict)
            else list(raw_preconditions)
        )
        for index, _precondition in enumerate(preconditions, start=1):
            steps.add(f"{use_case_id}:precondition:{index}")
        for step in specification.get("main_scenario") or []:
            if isinstance(step, dict) and step.get("step_number") is not None:
                steps.add(f"{use_case_id}:main:{step['step_number']}")
        for extension in specification.get("extensions") or []:
            if not isinstance(extension, dict):
                continue
            label = str(extension.get("label") or "").strip()
            for step in extension.get("handling_steps") or []:
                if label and isinstance(step, dict) and step.get("sub_step") is not None:
                    steps.add(f"{use_case_id}:extension:{label}:{step['sub_step']}")
    return steps


def _integrity_findings(
    model: dict[str, Any], state: dict[str, Any]
) -> tuple[list[dict[str, str | None]], list[dict[str, str | None]]]:
    """Check finite collaboration references without reenacting generation.

    The first list contains structural/type issues and the second covers ordered
    calls and their argument sources.  Values are only checked against already
    accepted operations, calls, and specification step ids.
    """
    structure: list[dict[str, str | None]] = []
    calls: list[dict[str, str | None]] = []
    classes = {
        str(item.get("className") or ""): item
        for item in model.get("Classes") or [] if isinstance(item, dict)
    }
    data_types = {
        str(item.get("name") or "")
        for item in model.get("DataTypes") or [] if isinstance(item, dict)
    }
    known_use_cases = {
        str(item.get("id") or "")
        for item in state.get("use_cases") or [] if isinstance(item, dict)
    }
    known_steps = _known_steps(state)
    operations: dict[str, tuple[str, dict[str, Any]]] = {}
    for class_name, class_item in classes.items():
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "")
            if operation_id in operations:
                structure.append({"ruleId": "class.operation-id-unique", "message": "operationId is duplicated", "location": operation_id})
            operations[operation_id] = (class_name, operation)
        for use_case_id in class_item.get("use_case_ids") or []:
            if str(use_case_id) not in known_use_cases:
                structure.append({"ruleId": "class.usecase-reference", "message": "class references an unknown use case", "location": class_name})
    for relationship in model.get("Relationships") or []:
        if not isinstance(relationship, dict):
            continue
        for end in ("source", "target"):
            value = str(relationship.get(end) or "")
            if value not in classes and value not in data_types:
                structure.append({"ruleId": "class.relationship-reference", "message": f"{end} is not a declared class or data type", "location": value or None})

    for collaboration in model.get("Collaborations") or []:
        if not isinstance(collaboration, dict):
            continue
        collaboration_id = str(collaboration.get("collaborationId") or "")
        use_case_ids = {str(value) for value in collaboration.get("useCaseIds") or []}
        if not use_case_ids <= known_use_cases:
            structure.append({"ruleId": "class.collaboration-usecase-reference", "message": "collaboration references an unknown use case", "location": collaboration_id})
        seen_calls: dict[str, tuple[str, dict[str, Any]]] = {}
        for position, call in enumerate(collaboration.get("calls") or [], start=1):
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("callId") or "")
            operation_id = str(call.get("receiverOperationId") or "")
            target = operations.get(operation_id)
            if target is None:
                calls.append({"ruleId": "class.call-operation-reference", "message": "call references an unknown operation", "location": call_id or collaboration_id})
                continue
            parent_id = str(call.get("parentCallId") or "")
            if parent_id and parent_id not in seen_calls:
                calls.append({"ruleId": "class.call-order", "message": "parent call is missing or appears later", "location": call_id})
            step_refs = {str(value) for value in call.get("stepRefs") or []}
            if not step_refs or not step_refs <= known_steps:
                calls.append({"ruleId": "class.call-step-reference", "message": "call must cite accepted specification steps", "location": call_id})
            parameters = {
                str(parameter.get("name") or ""): str(parameter.get("type") or "")
                for parameter in target[1].get("parameters") or [] if isinstance(parameter, dict)
            }
            bindings = {
                str(binding.get("parameter") or ""): str(binding.get("sourceRef") or "")
                for binding in call.get("argumentBindings") or [] if isinstance(binding, dict)
            }
            if set(bindings) != set(parameters):
                calls.append({"ruleId": "class.call-argument-contract", "message": "argument bindings must exactly match receiver parameters", "location": call_id})
            for parameter, source_ref in bindings.items():
                # Runtime clocks and deterministic DTO derivations are checked
                # against exact parameter/field types by the product contract.
                if source_ref.startswith(("runtime#", "derived#")):
                    continue
                source_id, separator, source_parameter = source_ref.rpartition("#")
                source = seen_calls.get(source_ref)
                source_parameter_call = seen_calls.get(source_id) if separator else None
                source_step = source_id if separator else source_ref
                if source is None and source_parameter_call is None and source_step not in known_steps:
                    calls.append({"ruleId": "class.call-source-reference", "message": "argument source is not an earlier call or accepted step", "location": f"{call_id}#{parameter}"})
                elif source is not None:
                    source_return = str(source[1].get("returnType") or "void")
                    if source_return == "void" or source_return != parameters.get(parameter):
                        calls.append({"ruleId": "class.call-source-type", "message": "earlier call return type is incompatible", "location": f"{call_id}#{parameter}"})
                elif source_parameter_call is not None:
                    source_parameters = {
                        str(item.get("name") or ""): str(item.get("type") or "")
                        for item in source_parameter_call[1].get("parameters") or []
                        if isinstance(item, dict)
                    }
                    if source_parameters.get(source_parameter) != parameters.get(parameter):
                        calls.append({"ruleId": "class.call-source-type", "message": "earlier call parameter type is incompatible", "location": f"{call_id}#{parameter}"})
            seen_calls[call_id] = target
    return structure, calls


def _sequence_integrity_findings(
    sequence_model: dict[str, Any], class_model: dict[str, Any]
) -> list[dict[str, str | None]]:
    """Confirm that a provided sequence only projects accepted call ids."""
    expected = {
        str(call.get("callId") or "")
        for collaboration in class_model.get("Collaborations") or [] if isinstance(collaboration, dict)
        for call in collaboration.get("calls") or [] if isinstance(call, dict)
    }
    actual = {
        str(message.get("call_id") or message.get("callId") or "")
        for diagram in sequence_model.get("Diagrams") or [sequence_model] if isinstance(diagram, dict)
        for message in diagram.get("Messages") or [] if isinstance(message, dict)
        if str(message.get("type") or "") != "return"
    }
    actual.discard("")
    if expected == actual:
        return []
    return [{
        "ruleId": "class.downstream-call-projection",
        "message": "sequence calls must match the accepted collaboration calls",
        "location": None,
    }]


def evaluate_candidate(
    class_model: dict[str, Any],
    *,
    sequence_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a supplied candidate without generating or selecting a model.

    ``sequence_model`` is optional so a class-only experiment can be inspected;
    when supplied, it becomes a downstream machine gate.
    """
    state, checkpoints = _checkpoint_context()
    try:
        normalized = BCEModel.model_validate(class_model).model_dump(by_alias=True)
    except ValidationError as error:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "caseId": CASE_ID,
            "upstreamCheckpoints": checkpoints,
            "machineGates": {
                "schema": _gate("failed", [{"ruleId": "schema", "message": str(error), "location": None}]),
                "referencesAndTypes": _gate("not_assessed", []),
                "callsAndSources": _gate("not_assessed", []),
                "downstreamSequence": _gate("not_assessed", []),
            },
            "status": "failed",
        }

    class_records, call_records = _integrity_findings(normalized, state)
    reference_records = class_records
    class_status = "passed" if not class_records else "failed"
    reference_status = "passed" if not reference_records else "failed"
    call_status = "passed" if not call_records else "failed"

    sequence_gate: dict[str, Any]
    if sequence_model is None:
        sequence_gate = _gate("not_assessed", [])
    else:
        sequence_records = _sequence_integrity_findings(sequence_model, normalized)
        sequence_gate = _gate(
            "passed" if not sequence_records else "failed", sequence_records
        )

    gates = {
        "schema": _gate("passed", []),
        "classStructure": _gate(class_status, class_records),
        "referencesAndTypes": _gate(reference_status, reference_records),
        "callsAndSources": _gate(call_status, call_records),
        "downstreamSequence": sequence_gate,
    }
    required_gates = [gate for gate in gates.values() if gate["status"] != "not_assessed"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "caseId": CASE_ID,
        "upstreamCheckpoints": checkpoints,
        "machineGates": gates,
        "qualitativeRubric": list(QUALITATIVE_RUBRIC),
        "status": "passed" if all(gate["status"] == "passed" for gate in required_gates) else "failed",
    }


def compare(
    baseline_class: dict[str, Any],
    candidate_class: dict[str, Any],
    *,
    baseline_sequence: dict[str, Any] | None = None,
    candidate_sequence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return side-by-side evidence without ranking diagrams by exact shape."""
    baseline = evaluate_candidate(baseline_class, sequence_model=baseline_sequence)
    candidate = evaluate_candidate(candidate_class, sequence_model=candidate_sequence)
    baseline_gates = baseline["machineGates"]
    candidate_gates = candidate["machineGates"]
    gate_delta = {
        name: candidate_gates[name]["findingCount"] - baseline_gates[name]["findingCount"]
        for name in baseline_gates
        if name in candidate_gates
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "caseId": CASE_ID,
        "upstreamCheckpoints": baseline["upstreamCheckpoints"],
        "baseline": baseline,
        "candidate": candidate,
        "machineGateFindingDelta": gate_delta,
        "comparisonNote": (
            "Finding deltas describe deterministic integrity checks only; they do not "
            "select a winner or require matching class names, counts, topology, or text."
        ),
        "qualitativeRubric": list(QUALITATIVE_RUBRIC),
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-class", required=True, type=Path)
    parser.add_argument("--candidate-class", required=True, type=Path)
    parser.add_argument("--baseline-sequence", type=Path)
    parser.add_argument("--candidate-sequence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if bool(args.baseline_sequence) != bool(args.candidate_sequence):
        parser.error("supply both sequence artifacts or neither")
    report = compare(
        _read_json(args.baseline_class),
        _read_json(args.candidate_class),
        baseline_sequence=_read_json(args.baseline_sequence) if args.baseline_sequence else None,
        candidate_sequence=_read_json(args.candidate_sequence) if args.candidate_sequence else None,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["candidate"]["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
