from app.artifact_trace import TraceRef
from app.artifact_trace_projection import project_artifact_trace
from app.implementation.planning.design_context import _method_context_evidence
from app.implementation.planning.method_projection import (
    MethodProjection,
    MethodRef,
    MethodSlice,
)


def _extension_spec(label: str, condition: str) -> dict:
    return {
        "use_case_id": "UC1",
        "extensions": [{
            "label": label,
            "branch_step": 2,
            "condition": condition,
            "handling_steps": [{
                "sub_step": "2a1",
                "sentence": "System records the alternate result.",
                "sourceRefs": ["requirement:R1"],
            }],
        }],
    }


def test_artifact_trace_uses_structural_extension_step_ref():
    step_ref = "UC1:extension:2:1:2a1"

    def project(label: str):
        return project_artifact_trace({
            "usecase_spec": {
                "use_cases": [{"id": "UC1"}],
                "use_case_specs": [_extension_spec(label, f"condition {label}")],
            },
            "extracted_bce_classes": {
                "Classes": [{
                    "className": "OrderControl",
                    "operations": [{
                        "operationId": "OrderControl::handle()",
                        "name": "handle",
                        "stepRefs": [step_ref],
                    }],
                }],
            },
        })

    first = project("2a")
    renamed = project("alternate")
    step = TraceRef("step", step_ref)
    assert step in first.refs and step in renamed.refs
    assert TraceRef("requirement", "R1") in first.sources(step)


def test_method_context_matches_structural_extension_ref_after_label_rename():
    method = MethodRef(
        class_name="OrderControl", stereotype="Control",
        operation_id="OrderControl::handle()", stable_id="operation_handle",
        name="handle", parameters=(), return_type="void",
    )
    step_ref = "UC1:extension:2:1:2a1"
    method_slice = MethodSlice(
        use_case_ids=("UC1",), incoming_call_id="call_1", incoming_source="Buyer",
        method=method, outgoing=(), return_type="void", step_refs=(step_ref,), reasons=(),
    )
    projection = MethodProjection(
        method=method, slices=(method_slice,), generation="hint", reasons=(),
    )
    evidence = _method_context_evidence(
        projection,
        requirements_by_id={},
        use_cases=[_extension_spec("renamed", "A changed condition")],
        endpoints=[],
    )

    assert evidence["scenarioSteps"] == [{
        "ref": step_ref,
        "branchCondition": "A changed condition",
        "outcome": None,
        "sub_step": "2a1",
        "sentence": "System records the alternate result.",
        "sourceRefs": ["requirement:R1"],
    }]
