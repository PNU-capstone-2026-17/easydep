from __future__ import annotations

import json
import os
import re
import queue
import threading
from typing import Any


def call_artifact_llm(system_prompt: str, user_prompt: str) -> str:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()

    client = OpenAI(
        base_url=os.getenv("BASE_URL"),
        api_key=os.getenv("API_KEY"),
        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "0")),
    )

    response = run_with_wall_timeout(
        lambda: client.chat.completions.create(
            model=os.getenv(
                "ARTIFACT_MODEL",
                os.getenv(
                    "CLASS_EXTRACTOR_MODEL",
                    os.getenv("DESIGN_AGENT_MODEL", "openai/gpt-oss-120b"),
                ),
            ),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            seed=42,
        )
    )
    return response.choices[0].message.content.strip()


def run_with_wall_timeout(callable_obj):
    timeout_seconds = float(os.getenv("LLM_WALL_TIMEOUT_SECONDS", "150"))
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def target():
        try:
            result_queue.put((True, callable_obj()))
        except Exception as error:
            result_queue.put((False, error))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    try:
        ok, result = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as error:
        raise TimeoutError(
            f"LLM request timed out after {timeout_seconds:g} seconds."
        ) from error

    if ok:
        return result

    raise result


def generate_sequence_diagram_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
) -> str:
    system_prompt = """
You generate PlantUML sequence diagrams from use case scenarios and class
diagrams. Return only PlantUML code from @startuml to @enduml.
"""
    user_prompt = f"""
[Use Case Scenario]
{scenario_text}

[Class Diagram PlantUML]
{class_diagram_puml}

Generate the sequence diagram after the class diagram has been completed.
"""
    return extract_puml(call_artifact_llm(system_prompt, user_prompt))


def generate_api_spec_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
) -> dict[str, Any]:
    system_prompt = """
You generate OpenAPI 3.1 JSON specifications. Return only a valid JSON object.
Do not wrap it in markdown.
"""
    user_prompt = f"""
[Use Case Scenario]
{scenario_text}

[Class Diagram PlantUML]
{class_diagram_puml}

[Sequence Diagram PlantUML]
{sequence_diagram_puml}

Generate an OpenAPI 3.1 JSON specification after the sequence diagram has been
completed.
"""
    return extract_json(call_artifact_llm(system_prompt, user_prompt))


def generate_erd_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
    api_spec: dict[str, Any],
) -> str:
    system_prompt = """
You generate PlantUML ERD diagrams. Return only PlantUML code from @startuml to
@enduml.
"""
    user_prompt = f"""
[Use Case Scenario]
{scenario_text}

[Class Diagram PlantUML]
{class_diagram_puml}

[API Spec JSON]
{json.dumps(api_spec, ensure_ascii=False, indent=2)}

Generate an ERD after the API specification has been completed.
"""
    return extract_puml(call_artifact_llm(system_prompt, user_prompt))


def generate_deployment_diagram_with_llm(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
    api_spec: dict[str, Any],
    erd_puml: str,
) -> str:
    system_prompt = """
You generate PlantUML deployment diagrams. Return only PlantUML code from
@startuml to @enduml.
"""
    user_prompt = f"""
[Use Case Scenario]
{scenario_text}

[Class Diagram PlantUML]
{class_diagram_puml}

[Sequence Diagram PlantUML]
{sequence_diagram_puml}

[API Spec JSON]
{json.dumps(api_spec, ensure_ascii=False, indent=2)}

[ERD PlantUML]
{erd_puml}

Generate a deployment diagram after the ERD has been completed.
"""
    return extract_puml(call_artifact_llm(system_prompt, user_prompt))


def revise_puml_with_llm(
    artifact_name: str,
    current_puml: str,
    feedback: str,
    syntax_errors: list[str] | None = None,
    context: str = "",
) -> str:
    system_prompt = """
You revise PlantUML artifacts. Apply the user's natural-language feedback and
fix any PlantUML syntax errors. Return only PlantUML code from @startuml to
@enduml. Do not explain.
"""
    user_prompt = f"""
[Artifact]
{artifact_name}

[Context]
{context}

[Current PlantUML]
{current_puml}

[User Feedback]
{feedback or "(none)"}

[Syntax Errors]
{chr(10).join(syntax_errors or []) or "(none)"}
"""
    return extract_puml(call_artifact_llm(system_prompt, user_prompt))


def revise_json_with_llm(
    artifact_name: str,
    current_json: dict[str, Any],
    feedback: str,
    errors: list[str] | None = None,
    context: str = "",
) -> dict[str, Any]:
    system_prompt = """
You revise JSON artifacts. Apply the user's natural-language feedback and fix
validation errors. Return only a valid JSON object. Do not explain.
"""
    user_prompt = f"""
[Artifact]
{artifact_name}

[Context]
{context}

[Current JSON]
{json.dumps(current_json, ensure_ascii=False, indent=2)}

[User Feedback]
{feedback or "(none)"}

[Errors]
{chr(10).join(errors or []) or "(none)"}
"""
    return extract_json(call_artifact_llm(system_prompt, user_prompt))


def extract_puml(content: str) -> str:
    match = re.search(r"(@startuml.*?@enduml)", content, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    cleaned = strip_code_fence(content)
    if not cleaned.startswith("@startuml"):
        cleaned = "@startuml\n" + cleaned
    if not cleaned.rstrip().endswith("@enduml"):
        cleaned = cleaned.rstrip() + "\n@enduml"
    return cleaned.strip()


def extract_json(content: str) -> dict[str, Any]:
    cleaned = strip_code_fence(content)
    return json.loads(cleaned)


def strip_code_fence(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```plantuml"):
        cleaned = cleaned[11:]
    elif cleaned.startswith("```puml"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    return cleaned.strip()
