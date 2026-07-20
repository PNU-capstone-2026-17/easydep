from __future__ import annotations

from app.schemas.architecture_state import ArchitectureState
from app.services.bce_class_extractor import (
    extract_bce_classes_from_scenario,
    load_scenario_from_json,
)
from app.services.plantuml_class_diagram import (
    compile_plantuml_to_image,
    generate_plantuml_from_bce_json,
    save_plantuml_file,
)
from app.services.plantuml_error import (
    extract_plantuml_error_hint,
)
from app.services.llm_artifacts import revise_puml_with_llm


MAX_CLASS_DIAGRAM_FEEDBACK_ATTEMPTS = 3


def extract_class_elements(state: ArchitectureState) -> ArchitectureState:
    scenario_text = state.get("scenario_text") or state.get("source", "")
    scenario_file_path = state.get("scenario_file_path", "")

    if not scenario_text and scenario_file_path:
        scenario_text = load_scenario_from_json(scenario_file_path)

    return {
        "scenario_text": scenario_text,
        "extracted_bce_classes": extract_bce_classes_from_scenario(scenario_text),
    }


def convert_to_class_diagram_code(state: ArchitectureState) -> ArchitectureState:
    class_json = state.get("extracted_bce_classes", {})
    diagram_puml = generate_plantuml_from_bce_json(class_json)
    output_path = state.get("class_diagram_output_path")

    if output_path and diagram_puml:
        save_plantuml_file(diagram_puml, output_path)

    return {
        "class_diagram_puml": diagram_puml,
        "class_diagram_feedback_attempts": state.get(
            "class_diagram_feedback_attempts", 0
        ),
        "class_diagram_max_feedback_attempts": state.get(
            "class_diagram_max_feedback_attempts",
            MAX_CLASS_DIAGRAM_FEEDBACK_ATTEMPTS,
        ),
        "class_diagram_syntax_valid": False,
        "class_diagram_syntax_errors": [],
    }


def feedback_class_diagram(state: ArchitectureState) -> ArchitectureState:
    attempts = state.get("class_diagram_feedback_attempts", 0) + 1
    feedback_parts: list[str] = []

    requested_feedback = state.get("class_diagram_requested_feedback", "").strip()
    if requested_feedback:
        feedback_parts.append(requested_feedback)

    syntax_errors = state.get("class_diagram_syntax_errors", [])
    if syntax_errors:
        feedback_parts.append("Fix syntax errors: " + "; ".join(syntax_errors))

    if not feedback_parts:
        feedback_parts.append("Review the class diagram for completeness and clarity.")
        
    feedback_text = "\n".join(feedback_parts)
    puml_text = state.get("class_diagram_puml", "").strip()

    if not puml_text:
        return {
            "class_diagram_feedback": feedback_text,
            "class_diagram_feedback_attempts": attempts,
            "class_diagram_puml": puml_text
        }

    # LLM을 통해 피드백을 반영하여 다이어그램 수정
    revised = revise_puml_with_llm(
        artifact_name="class diagram",
        current_puml=puml_text,
        feedback=feedback_text,
        syntax_errors=state.get("class_diagram_syntax_errors", []),
        context=state.get("scenario_text", ""),
    )
    
    output_path = state.get("class_diagram_output_path")
    if output_path:
        save_plantuml_file(revised, output_path)

    return {
        "class_diagram_feedback": feedback_text,
        "class_diagram_feedback_attempts": attempts,
        "class_diagram_puml": revised,
    }


def validate_class_diagram_syntax(state: ArchitectureState) -> ArchitectureState:
    output_path = state.get("class_diagram_output_path")
    puml_text = state.get("class_diagram_puml", "")
    plantuml_jar_path = state.get("plantuml_jar_path", "plantuml.jar")

    if not puml_text:
        errors = ["PlantUML class diagram code is empty."]
        return {
            "class_diagram_compile_result": {
                "success": False,
                "error_message": errors[0],
            },
            "class_diagram_syntax_valid": False,
            "class_diagram_syntax_errors": errors,
        }

    if not output_path:
        output_path = "outputs/bce_class_diagram.puml"
        save_plantuml_file(puml_text, output_path)

    compile_result = compile_plantuml_to_image(output_path, plantuml_jar_path)
    error_message = compile_result.get("error_message")
    errors = [error_message] if error_message else []

    if errors:
        try:
            hint = extract_plantuml_error_hint(puml_text, plantuml_jar_path)
        except Exception:
            hint = ""
        if hint:
            errors.append(hint)

    return {
        "class_diagram_compile_result": compile_result,
        "class_diagram_syntax_valid": not errors,
        "class_diagram_syntax_errors": errors,
    }


