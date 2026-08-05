from __future__ import annotations

import json
import os
from typing import Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from openai import OpenAI


class APIProperty(BaseModel):
    name: str = Field(..., description="Property name in camelCase")
    type: str = Field(
        default="string",
        description="string | integer | number | boolean | array | object",
    )
    format: str = Field(default="", description="float, double, date-time, etc.")
    description: str = Field(default="")
    required: bool = Field(default=False)
    items_ref: str = Field(
        default="",
        description="If type is array, the reference schema name for array items",
    )
    example: str = Field(default="", description="Example value")


class APISchema(BaseModel):
    name: str = Field(..., description="Schema / DTO name (e.g. PurchaseRequest, PurchaseRecord)")
    description: str = Field(default="")
    type: str = Field(default="object")
    properties: list[APIProperty] = Field(default_factory=list)


class APIParameter(BaseModel):
    name: str = Field(..., description="Parameter name")
    in_location: str = Field(
        default="path",
        description="path | query | header",
    )
    type: str = Field(default="string")
    required: bool = Field(default=True)
    description: str = Field(default="")


class APIResponse(BaseModel):
    status_code: str = Field(
        ...,
        description="HTTP status code string, e.g., '200', '201', '202', '400', '404', '502'",
    )
    description: str = Field(...)
    schema_ref: str = Field(
        default="",
        description="Schema name for response body (e.g. PurchaseRecord, ErrorResponse)",
    )


class APIEndpoint(BaseModel):
    path: str = Field(..., description="API Path (e.g., /purchases, /portfolio)")
    method: str = Field(..., description="HTTP Method: get | post | put | delete | patch")
    summary: str = Field(default="")
    description: str = Field(default="")
    tag: str = Field(default="Default", description="Logical tag for grouping endpoints")
    parameters: list[APIParameter] = Field(default_factory=list)
    request_body_schema_ref: str = Field(
        default="", description="Schema name for request body payload"
    )
    request_body_required: bool = Field(default=False)
    responses: list[APIResponse] = Field(default_factory=list)


class APISpecElements(BaseModel):
    title: str = Field(default="System API", description="API Specification Title")
    description: str = Field(default="", description="API Specification Overview")
    version: str = Field(default="1.0.0", description="API Version")
    endpoints: list[APIEndpoint] = Field(default_factory=list)
    schemas: list[APISchema] = Field(default_factory=list)


API_ELEMENT_EXTRACTION_SYSTEM_PROMPT = """
You are an expert Software Architect and Backend Developer specializing in RESTful API design and Model-Driven Architecture (MDA).
Your task is to analyze the provided [Class Diagram] and [Sequence Diagram] to extract structured elements required to build a production-ready OpenAPI specification.

## Execution Steps & Rules

**Step 1: Identify Inbound Messages and Endpoints**
- Analyze the Sequence Diagram to identify API requests. Specifically, identify messages sent from Boundary components (UI/Frontend) to Control components (Backend Controllers/Managers).
- Filter out purely frontend UI rendering actions.
- Map inbound messages to REST Endpoints (URIs) and HTTP Methods (GET, POST, PUT, DELETE, PATCH).
- Group endpoints logically using tags based on Control classes.

**Step 2: Define Request and Response Schemas (DTOs)**
- Analyze message parameters and cross-reference the Class Diagram to identify data types, property names (use camelCase), relationships (e.g. Portfolio holding Holdings), and payload DTOs.
- Create schemas for Request bodies, Response bodies, and Error responses (e.g., ErrorResponse).

**Step 3: Determine State Transitions and Exception Scenarios (Status Codes)**
- Trace control flows and `alt`/`opt` conditional branches in the Sequence Diagram (e.g., validation failures, missing data, external gateway errors).
- Map these branches to appropriate HTTP status codes:
  - 200 OK / 201 Created: Successful execution / creation.
  - 202 Accepted: Asynchronous or delayed processing (e.g. delayed purchase).
  - 400 Bad Request: Missing info, invalid input, or unsupported site.
  - 404 Not Found: Resource not found.
  - 502 Bad Gateway: Connection or remote gateway failure.

**Step 4: Output Structured Elements**
- Return the extracted endpoints and component schemas strictly according to the provided JSON schema. Do not include markdown code blocks or prose outside the schema fields.
"""


def run_api_elements_parse(messages: list[dict[str, str]]) -> dict[str, Any]:
    """LLM에서 APISpecElements 구조체 파싱."""
    load_dotenv()

    api_key = os.getenv("API_KEY") or os.getenv("GEMINI_API_KEY")
    base_url = os.getenv("BASE_URL")
    if not base_url and os.getenv("GEMINI_API_KEY"):
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"

    model_name = os.getenv("DESIGN_AGENT_MODEL", "openai/gpt-oss-120b")

    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "0")),
    )

    response = client.chat.completions.parse(
        model=model_name,
        messages=messages,
        temperature=0,
        response_format=APISpecElements,
    )
    return response.choices[0].message.parsed.model_dump()


def extract_api_elements_from_diagrams(
    class_diagram_puml: str,
    sequence_diagram_puml: str,
) -> dict[str, Any]:
    if not class_diagram_puml and not sequence_diagram_puml:
        return {}

    user_prompt = f"""[Sequence Diagram Information]
{sequence_diagram_puml}

[Class Diagram Information]
{class_diagram_puml}
"""

    messages = [
        {"role": "system", "content": API_ELEMENT_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    return run_api_elements_parse(messages)
