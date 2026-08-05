from __future__ import annotations

import json
from typing import Any
from typing_extensions import TypedDict


class ArchitectureState(TypedDict, total=False):
    app_id: str

    # User input, stored on the app row.
    requirements_text: str
    resource_constraints_text: str

    # Requirements analysis artifacts.
    refined_requirements: dict[str, Any]
    usecase_spec: dict[str, Any]
    usecase_diagram_puml: str
    usecase_diagram_syntax_valid: bool
    usecase_diagram_syntax_errors: list[str]
    resource_spec: dict[str, Any]

    # Every design artifact is stored twice: the structured model the LLM produces
    # and edits (*_model / *_bce_classes) and the rendered artifact derived from it
    # (*_puml / api_spec). The model is the source of truth — feedback edits the
    # model and the artifact is re-rendered deterministically, so the two cannot
    # drift apart and the rendered form is valid by construction.
    # The *_feedback keys are transient: they carry one round of user feedback into
    # the stage's feedback subgraph and are not part of the stored artifact.
    extracted_bce_classes: dict[str, Any]
    class_diagram_feedback: str
    class_diagram_puml: str
    class_diagram_syntax_valid: bool
    class_diagram_syntax_errors: list[str]
    # Deterministic rule check on the BCE model, produced before the render.
    # {findings: list[str], repair_iters: int, stopped: str, error?: str}
    #
    # This is a different question from *_syntax_valid. The syntax keys ask whether the
    # rendered PlantUML parses — and it always does, because the renderer sanitises its
    # input and is valid by construction. These keys ask whether the model the LLM
    # produced obeys the rules in app/design/knowledge/rules.py, which is the question
    # nothing used to ask. `stopped` says why the repair loop ended, so "no violations"
    # and "the budget ran out" are never the same value.
    class_diagram_check: dict[str, Any]

    sequence_diagram_model: dict[str, Any]
    sequence_diagram_feedback: str
    sequence_diagram_puml: str
    sequence_diagram_syntax_valid: bool
    sequence_diagram_syntax_errors: list[str]
    sequence_diagram_check: dict[str, Any]

    api_spec_model: dict[str, Any]
    api_spec_feedback: str
    api_spec: dict[str, Any]
    api_spec_syntax_valid: bool
    api_spec_syntax_errors: list[str]
    api_spec_check: dict[str, Any]

    # The ERD keeps its own BCE entity copy so ERD feedback edits it without
    # touching the class diagram's model.
    erd_bce_classes: dict[str, Any]
    erd_feedback: str
    erd_puml: str
    erd_syntax_valid: bool
    erd_syntax_errors: list[str]

    deployment_diagram_model: dict[str, Any]
    deployment_diagram_feedback: str
    deployment_diagram_puml: str
    deployment_diagram_syntax_valid: bool
    deployment_diagram_syntax_errors: list[str]

    # Transient, pipeline graph only (app/design/graphs/design_graph.py).
    # gate_route is the marker a gate leaves for its conditional edge:
    # "advance" (next stage) or "loop" (revise this stage and ask again).
    # stage_origin records what produced the current stage state, so the persist
    # node can label the version generated vs feedback-revised.
    gate_route: str
    stage_origin: str

    artifact_status: dict[str, str]


def usecase_spec_text(state: ArchitectureState) -> str:
    """The use case specification as prompt text.

    Every design artifact is derived from the use case specification produced by
    the requirements analysis agent, so this is the context the LLM gets.
    """
    spec = state.get("usecase_spec")
    if not spec:
        return ""
    if isinstance(spec, str):
        return spec
    return json.dumps(spec, ensure_ascii=False, indent=2)
