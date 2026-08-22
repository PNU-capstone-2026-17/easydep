"""유스케이스·클래스·시퀀스 다이어그램에서 API 엔드포인트 모델을 도출한다.

**왜 OpenAPI를 바로 만들지 않나.** OpenAPI 3.1은 중첩이 깊고 규칙이 많아서, LLM에게
그것을 직접 쓰게 하면 필드 누락·스키마 참조 오류가 나고 그때마다 수리 루프가 필요했다.
여기서는 훨씬 단순한 평평한 모델(엔드포인트 목록 + 스키마 목록)만 받고, OpenAPI 문서
조립은 openapi.build_openapi_from_model이 결정론적으로 한다. 그래서 openapi/paths 같은
필수 필드가 빠질 수 없고, $ref도 항상 실제 스키마를 가리킨다.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.design.services.common.structured import parse_structured


class ApiField(BaseModel):
    name: str
    #: string | integer | number | boolean | array | object, 또는 Schemas의 이름.
    type: str = Field(default="string")
    required: bool = Field(default=True)
    description: str = Field(default="")


class ApiResponse(BaseModel):
    status: int = Field(default=200)
    description: str = Field(default="")
    #: 본문 스키마 이름(Schemas 중 하나). 본문이 없으면 빈 문자열.
    schema_name: str = Field(default="")
    #: 배열로 돌려주는지. schema_name이 있을 때만 의미가 있다.
    is_array: bool = Field(default=False)


class ApiControlArgument(BaseModel):
    """One explicit value flow from the HTTP request to a Control parameter."""

    #: Exact parameter name in the BCE Control method.
    name: str
    #: ``$path.id``, ``$query.filter``, ``$body.field`` or ``$body``.
    source: str


class ApiControlOutcome(BaseModel):
    """The named Control outcome that produces one documented HTTP status."""

    status: int
    outcome: str


class ApiControlBinding(BaseModel):
    """The executable application contract behind one HTTP operation.

    This belongs in the API design model rather than an implementation prompt:
    it is the single, reviewable answer to which Control operation receives an
    endpoint and how every documented HTTP outcome is produced.
    """

    control: str
    method: str
    arguments: list[ApiControlArgument] = Field(default_factory=list)
    outcomes: list[ApiControlOutcome] = Field(default_factory=list)


class ApiEndpoint(BaseModel):
    #: "/orders/{orderId}" 처럼 중괄호로 경로 변수를 표기한다.
    path: str = Field(default="/")
    method: str = Field(default="get")
    summary: str = Field(default="")
    operation_id: str = Field(default="")
    path_params: list[ApiField] = Field(default_factory=list)
    query_params: list[ApiField] = Field(default_factory=list)
    #: 요청 본문 스키마 이름(Schemas 중 하나). 본문이 없으면 빈 문자열.
    request_schema: str = Field(default="")
    responses: list[ApiResponse] = Field(default_factory=list)
    #: 이 엔드포인트를 낳은 Boundary/Control 클래스 이름.
    source_classes: list[str] = Field(default_factory=list)
    #: 이 엔드포인트가 실현하는 유스케이스 id.
    use_case_ids: list[str] = Field(default_factory=list)
    #: Endpoint-to-Control mapping used by design validation and implementation.
    control_binding: ApiControlBinding | None = None


class ApiSchema(BaseModel):
    name: str
    description: str = Field(default="")
    fields: list[ApiField] = Field(default_factory=list)
    #: 이 스키마가 나온 Entity 클래스 이름. 요청 전용 스키마면 비울 수 있다.
    source_class: str = Field(default="")


class ApiSpecModel(BaseModel):
    title: str = Field(default="API")
    version: str = Field(default="1.0.0")
    Endpoints: list[ApiEndpoint] = Field(default_factory=list)
    Schemas: list[ApiSchema] = Field(default_factory=list)


API_SPEC_EXTRACTION_SYSTEM_PROMPT = """
You are an API designer deriving a REST API model from a use-case specification,
the analysis-level class diagram, and the sequence diagram derived from them.

## Input
A use-case specification, a class diagram in PlantUML using Boundary-Control-Entity
stereotypes, and a sequence diagram in PlantUML. Do not invent endpoints or fields
the inputs do not support.

## Endpoints
- Derive endpoints from the Boundary classes and from the messages that cross from
  an actor into the system in the sequence diagram. One endpoint per distinct
  operation the system exposes — not one per class and not one per scenario step.
- `path` uses plural resource nouns and braces for variables: /orders/{orderId}.
- `method` follows REST semantics: get (read), post (create), put (full replace),
  patch (partial update), delete (remove). Choose from the operation's intent, not
  from the method name in the class diagram.
- `operation_id` is a unique camelCase verbNoun, e.g. createOrder, listOrders.
- `path_params` must contain exactly the variables that appear in braces in `path`,
  with the same names. `query_params` are filters and pagination only.
- `request_schema` is set only for methods that carry a body (post, put, patch),
  and must name one of the Schemas you return.
- `responses` must include the success case and every failure the specification's
  Extensions describe (e.g. 400 validation, 404 not found, 409 conflict).
  Set `schema_name` only when the response carries a body; set `is_array` for
  collection responses.

## Schemas
- Derive schemas from the Entity classes in the class diagram — their fields are the
  schema's fields. Add request-shaped schemas (e.g. OrderCreateRequest) where the
  request body is a subset of an entity.
- `type` is one of string, integer, number, boolean, array, object — or the name of
  another schema you return, for nested objects.
- `name` is PascalCase and unique.

## Traceability
- `source_classes` on each endpoint: the Boundary/Control classes it came from,
  copied exactly from the class diagram.
- `use_case_ids` on each endpoint: the use case(s) it realizes, copied exactly
  from the specification.
- `source_class` on each schema: the Entity class it mirrors. Leave it empty for
  request-shaped schemas that do not correspond to one entity.
- `control_binding` on every endpoint is mandatory. Set its `control` and
  `method` to the exact BCE Control class and method that implement the endpoint.
  Map every Control parameter once in `arguments`, using only `$path.<name>`,
  `$query.<name>`, `$body.<field>`, or `$body`. Map every documented response
  status once in `outcomes` with a meaningful named result such as `found`,
  `not_found`, `created`, or `validation_error`. Do not use fabricated values,
  implicit defaults, or an untyped `Object` result.
- **Never invent a name or an id.** An empty list is honest; a made-up
  reference is a lie the trace matrix will believe.

## Self-check before finalizing
(a) every `request_schema` and every response `schema_name` names a schema you returned,
(b) every brace variable in every `path` has a matching entry in `path_params`,
(c) `operation_id` values are unique,
(d) every use-case step where the actor asks the system to do something is reachable
    through at least one endpoint,
(e) every `source_classes` / `source_class` entry names a class in the given class
    diagram, and every `use_case_ids` entry appears in the given specification.
(f) every endpoint has an exact Control binding; its argument sources and outcomes
    cover the endpoint contract, and the same Control call appears in the sequence.
(g) when the inputs describe user-visible system behavior, Endpoints is not empty.
    A schema-only API model is incomplete and cannot be implemented.

Populate the response strictly according to the provided schema. Do not include
markdown, code fences, or any prose outside the schema fields.
"""


def api_spec_messages(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
) -> list[dict[str, str]]:
    """운영 호출과 지연 프로브가 공유하는 API 설계 메시지 계약."""
    return [
        {"role": "system", "content": API_SPEC_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"[Use Case Specification]\n{scenario_text}\n\n"
                f"[Class Diagram PlantUML]\n{class_diagram_puml}\n\n"
                f"[Sequence Diagrams PlantUML]\n{sequence_diagram_puml}"
            ),
        },
    ]


def extract_api_spec_model(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
) -> dict[str, Any]:
    """유스케이스 + 클래스 + 시퀀스 → 구조화된 API 엔드포인트 모델."""
    if not scenario_text:
        return {}
    messages = api_spec_messages(
        scenario_text, class_diagram_puml, sequence_diagram_puml
    )
    model = parse_structured(messages, ApiSpecModel)
    return normalize_api_spec_model(model, class_diagram_puml)


def _control_return_types(class_diagram_puml: str) -> dict[tuple[str, str], str]:
    """Read explicit Control method return types for API contract alignment."""
    result: dict[tuple[str, str], str] = {}
    class_pattern = re.compile(
        r"(?ms)^\s*class\s+(?P<class>[A-Za-z_]\w*)[^\{]*\{(?P<body>.*?)^\s*\}"
    )
    method_pattern = re.compile(
        r"^\s*[+\-#]\s*(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)"
        r"\s*(?::\s*(?P<return>[A-Za-z_]\w*(?:<[^>]+>)?))?\s*$",
        re.MULTILINE,
    )
    for match in class_pattern.finditer(class_diagram_puml or ""):
        if not re.search(r"<<\s*Control\s*>>", match.group(0), re.IGNORECASE):
            continue
        for method in method_pattern.finditer(match.group("body")):
            result[(match.group("class"), method.group("name"))] = (
                method.group("return") or "void"
            )
    return result


def normalize_api_spec_model(
    model: dict[str, Any], class_diagram_puml: str = ""
) -> dict[str, Any]:
    """Repair mechanical traceability omissions without inventing API behavior.

    The structured model frequently contains a correct ``control_binding`` but
    omits the redundant ``source_classes`` entry, or leaves request DTO fields
    empty even though the binding explicitly names ``$body.<field>``.  These
    are representation omissions, not design decisions, so fill them
    deterministically before validation and OpenAPI rendering.
    """
    if not isinstance(model, dict):
        return model
    control_returns = _control_return_types(class_diagram_puml)
    for endpoint in model.get("Endpoints", []) or []:
        if not isinstance(endpoint, dict):
            continue
        binding = endpoint.get("control_binding")
        if isinstance(binding, dict):
            control = str(binding.get("control") or "").strip()
            source_classes = endpoint.setdefault("source_classes", [])
            if control and isinstance(source_classes, list) and control not in source_classes:
                source_classes.append(control)
            request_schema = str(endpoint.get("request_schema") or "").strip()
            if request_schema:
                schema = next(
                    (
                        item for item in model.get("Schemas", []) or []
                        if isinstance(item, dict) and item.get("name") == request_schema
                    ),
                    None,
                )
                if isinstance(schema, dict):
                    fields = schema.setdefault("fields", [])
                    known = {
                        str(item.get("name") or "").strip()
                        for item in fields if isinstance(item, dict)
                    }
                    for argument in binding.get("arguments", []) or []:
                        if not isinstance(argument, dict):
                            continue
                        source = str(argument.get("source") or "")
                        name = source.removeprefix("$body.").strip()
                        if not name or name == source or name in known:
                            continue
                        fields.append({
                            "name": name,
                            "type": "string",
                            "required": True,
                            "description": "",
                        })
                        known.add(name)
            return_type = control_returns.get((control, str(binding.get("method") or "").strip()))
            if return_type and return_type.lower() == "void":
                success_responses = [
                    response for response in endpoint.get("responses", []) or []
                    if isinstance(response, dict)
                    and 200 <= int(response.get("status", 0) or 0) < 300
                    and int(response.get("status", 0) or 0) != 204
                ]
                if len(success_responses) == 1:
                    response = success_responses[0]
                    previous_status = int(response.get("status", 0) or 0)
                    response["status"] = 204
                    response["schema_name"] = ""
                    response["is_array"] = False
                    for outcome in binding.get("outcomes", []) or []:
                        if isinstance(outcome, dict) and int(outcome.get("status", 0) or 0) == previous_status:
                            outcome["status"] = 204
    return model
