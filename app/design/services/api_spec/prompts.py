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


def proposal_messages(
    scenario_text: str,
    bce_model: BCEModel,
) -> list[dict[str, str]]:
    """유스케이스와 유한 interaction 후보만 API 제안 입력으로 만든다."""

    payload = {
        "useCaseSpecification": scenario_text,
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
            "useCaseSpecification": scenario_text,
            "interactionCandidates": interaction_context(bce_model),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
