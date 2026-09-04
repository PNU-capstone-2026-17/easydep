"""API LLM에는 HTTP 설계만 맡기고 실행 연결은 코드가 채우게 한다."""

from __future__ import annotations

import json

from app.design.schemas.class_model import BCEModel
from app.design.services.api_spec.normalization import interaction_context

API_SPEC_EXTRACTION_SYSTEM_PROMPT = """
Design the HTTP surface for the supplied finite interaction candidates.
Return one endpoint for each distinct interaction the application exposes and
copy its interactionId exactly. Decide only the HTTP path, method, short summary,
and useful response status descriptions.

- Use resource-oriented absolute paths and standard HTTP method semantics.
- Fill path, method, summary, and responses for every endpoint; do not rely on defaults.
- Choose a concrete resource path such as /registrations or /offerings/{offeringId};
  never use the API root path by itself.
- Every method and path pair must be unique because duplicate pairs overwrite each other.
- Include identifiers in path placeholders when they make a resource path clearer.
- Include the successful status and failures stated by the use-case extensions.
- Do not return operation IDs, parameters, schemas, Control bindings, argument sources,
  result names, class traces, or use-case traces. The application derives all of them
  from the selected interaction.
- Do not invent an interactionId or add an endpoint without a supplied candidate.

Return only the structured response.
""".strip()

API_SPEC_REVISION_SYSTEM_PROMPT = """
Revise only the HTTP contract requested by the feedback. Keep interactionId values
grounded in the supplied candidates and return the full minimal API proposal.
Return only path, method, summary, and response statuses in addition to interactionId.
The application derives operation IDs, parameters, schemas, Control bindings, argument
mappings, outcomes, and trace fields from the accepted class collaboration.
""".strip()


def _api_use_case_context(scenario_text: str) -> object:
    """전체 요구사항 상태에서 HTTP 판단에 쓰는 유스케이스 내용만 남긴다.

    API 경로·메서드·상태 코드를 고르는 데 actor 설명, 추적 ID, repair 상태와 UML 관계는
    필요하지 않다. JSON이 아닌 이전 호출 형식은 그대로 전달해 입력 호환성을 유지한다.
    """

    try:
        source = json.loads(scenario_text)
    except (TypeError, json.JSONDecodeError):
        return scenario_text
    if not isinstance(source, dict):
        return source
    use_cases = {
        str(item.get("id")): item
        for item in source.get("use_cases", [])
        if isinstance(item, dict) and item.get("id")
    }
    compact = []
    for spec in source.get("use_case_specs", []):
        if not isinstance(spec, dict):
            continue
        use_case_id = str(spec.get("use_case_id") or "")
        use_case = use_cases.get(use_case_id, {})
        compact.append(
            {
                "id": use_case_id,
                "name": spec.get("name") or use_case.get("name") or "",
                "goal": use_case.get("goal") or "",
                "trigger": spec.get("trigger") or "",
                "steps": [
                    step.get("sentence")
                    for step in spec.get("main_scenario", [])
                    if isinstance(step, dict) and step.get("sentence")
                ],
                "extensions": [
                    {
                        "condition": extension.get("condition") or "",
                        "outcome": extension.get("outcome") or "",
                    }
                    for extension in spec.get("extensions", [])
                    if isinstance(extension, dict)
                ],
            }
        )
    return compact or scenario_text


def proposal_messages(
    scenario_text: str,
    bce_model: BCEModel,
) -> list[dict[str, str]]:
    """유스케이스와 유한 interaction 후보만 API 제안 입력으로 만든다."""

    payload = {
        "useCases": _api_use_case_context(scenario_text),
        "interactionCandidates": interaction_context(bce_model),
    }
    return [
        {"role": "system", "content": API_SPEC_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def revision_context(scenario_text: str, bce_model: BCEModel) -> str:
    """수정에도 최초 제안과 같은 작은 입력만 제공한다."""

    return json.dumps(
        {
            "useCases": _api_use_case_context(scenario_text),
            "interactionCandidates": interaction_context(bce_model),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
