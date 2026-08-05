"""사용자 피드백을 배포 토폴로지 모델(진실의 원천)에 적용한다.

LLM은 PlantUML 텍스트를 만지지 않고 구조화된 배포 모델만 편집한다. 다이어그램은 그 뒤
결정론적 변환으로 재렌더되므로 모델과 그림이 어긋나지 않는다.
"""
from __future__ import annotations

from typing import Any

from app.design.services.common.structured import parse_structured, revision_messages
from app.design.services.deployment_diagram.extractor import DeploymentModel

DEPLOYMENT_REVISION_SYSTEM_PROMPT = """
You edit an existing UML deployment model. You are given the current model (as JSON),
the design artifacts it was derived from, and the user's natural-language feedback.

Apply the feedback to the model and return the FULL revised model, following the
same schema. Rules:
- Change only what the feedback asks for; leave everything else intact.
- Keep the model grounded in the inputs — do not invent infrastructure the feedback
  and artifacts do not support.
- Every artifact's `deployed_on`, every connection's `source`/`target`, and every
  node's `parent` must name a node you return (`parent` may also be empty).
- `parent` nesting must not form a cycle.
- Keep the traceability fields (source_classes) accurate. Carry them over unchanged for
  elements you did not touch; update them for elements you changed; fill them
  in for elements you added. Never invent a reference — an empty list is
  honest, a made-up one is a lie the trace matrix will believe.
Return the revised model strictly according to the provided schema. Do not include
markdown, code fences, or any prose outside the schema fields.
"""


def revise_deployment_model(
    current_model: dict[str, Any],
    feedback: str,
    context_text: str = "",
    targets: set[str] | None = None,
) -> dict[str, Any]:
    """현재 모델 + 피드백 → 수정된 모델. 피드백이 없으면 원본을 그대로 둔다."""
    if not current_model or not feedback:
        return current_model or {}

    return parse_structured(
        revision_messages(
            DEPLOYMENT_REVISION_SYSTEM_PROMPT,
            "Design Artifacts",
            context_text,
            "Current Deployment Model",
            current_model,
            feedback,
            targets,
        ),
        DeploymentModel,
    )
