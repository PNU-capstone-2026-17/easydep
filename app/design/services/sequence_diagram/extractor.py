from __future__ import annotations

import json
import os
from typing import Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from openai import OpenAI


class Participant(BaseModel):
    type: str = Field(
        default="participant",
        description="actor | boundary | control | entity | database | participant",
    )
    label: str = Field(default="", description="Display Name")
    alias: str = Field(default="", description="Short alias identifier")


class SequenceStep(BaseModel):
    type: str = Field(
        default="message",
        description=(
            "message | self_message | return_message | activate | deactivate | "
            "fragment_start | fragment_else | fragment_end"
        ),
    )
    source: str = Field(default="", description="Caller alias for messages")
    target: str = Field(
        default="",
        description="Receiver alias for messages or target for activate/deactivate",
    )
    text: str = Field(
        default="", description="methodName() or return value text"
    )
    fragment_type: str = Field(
        default="", description="alt | opt | loop (only for fragment_start)"
    )
    condition: str = Field(
        default="", description="Condition text for fragment_start and fragment_else"
    )


class SequenceDiagramElements(BaseModel):
    participants: list[Participant] = Field(default_factory=list)
    sequence: list[SequenceStep] = Field(default_factory=list)


SEQUENCE_ELEMENT_EXTRACTION_SYSTEM_PROMPT = """
You are a Principal Software Architect proficient in Object-Oriented Analysis and Design (OOAD) and UML sequence modeling.
Your goal is to analyze the provided [Use Case Specification] and [Class Diagram] to extract the structured logical elements required to build a sequence diagram.

## Objective & Workflow
You must derive the sequence diagram elements strictly following the 5-step analysis process below.

**Step 1: Target Identification**
- Fully understand the Main Success Scenario, Alternative Flows, and Exception Flows of the provided [Use Case Specification].

**Step 2: Constrained Object Extraction**
- Identify the actors and system components appearing in the use case scenario.
- [IMPORTANT] System components (objects) MUST be identified EXCLUSIVELY from the classes existing in the provided [Class Diagram]. Do not arbitrarily invent or create new classes.
- Categorize each object type as one of: `actor`, `boundary`, `control`, `entity`, `database`, or `participant`.

**Step 3: Object Layout**
- Define participants with a descriptive `label` and a unique PascalCase `alias`.
- Layout ordering principle: Arrange participants in logical order from left to right: Actor -> Boundary (UI/Controller) -> Control (Service/Manager) -> Entity / Database (Repository/DB).

**Step 4: Strict Message Mapping & Directional Rules**
- Translate each action step of the use case into a sequence step:
  - **[RULE 4-1: Receiver Ownership]** When creating a synchronous call (`message`), the method specified in `text` (e.g. `methodName()`) MUST explicitly exist within the **Receiver's** class definition in the [Class Diagram]. NEVER call a method on a Receiver if it belongs to the Caller or another class.
  - **[RULE 4-2: Self-Messages]** If an object triggers its own internal event or method, use `self_message` where `source` and `target` are the same component alias.
  - **[RULE 4-3: Return Messages]** Use `return_message` EXCLUSIVELY for returning data or control back to the caller. Format `text` as return value (e.g. `memberId`, `success`). NEVER put method names with parentheses (e.g., `getMember()`) on a return message.
  - **[RULE 4-4: Activations]** Emit `activate` and `deactivate` steps to represent object execution lifelines accurately.

**Step 5: Combined Fragments Integration**
- Identify logical branches and iterations in the use case scenario and encapsulate them using fragment steps:
  - Mutually exclusive branching (alt flow / exception flow): `fragment_start` (fragment_type="alt", condition="..."), `fragment_else` (condition="..."), `fragment_end`.
  - Single conditional flow (optional flow): `fragment_start` (fragment_type="opt", condition="..."), `fragment_end`.
  - Iterative loop: `fragment_start` (fragment_type="loop", condition="..."), `fragment_end`.

Return the extracted elements strictly matching the response schema. Do not include markdown code fences or conversational text outside the schema fields.
"""


def run_sequence_parse(messages: list[dict[str, str]]) -> dict[str, Any]:
    """LLM에서 SequenceDiagramElements 구조체 파싱."""
    load_dotenv()

    client = OpenAI(
        base_url=os.getenv("BASE_URL"),
        api_key=os.getenv("API_KEY"),
        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "0")),
    )

    response = client.chat.completions.parse(
        model=os.getenv("DESIGN_AGENT_MODEL", "openai/gpt-oss-120b"),
        messages=messages,
        temperature=0,
        response_format=SequenceDiagramElements,
    )
    return response.choices[0].message.parsed.model_dump()


def extract_sequence_elements_from_scenario(
    scenario_text: str,
    class_diagram_puml: str,
) -> dict[str, Any]:
    if not scenario_text:
        return {}

    user_prompt = f"""[Use Case Specification]
{scenario_text}

[Class Diagram Information]
{class_diagram_puml}
"""

    messages = [
        {"role": "system", "content": SEQUENCE_ELEMENT_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return run_sequence_parse(messages)
