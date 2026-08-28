"""Relationship/diagram modeling stage의 기존 import 경로를 보존하는 얇은 facade다."""

from __future__ import annotations

from functools import wraps

from app.requirements.contracts.state import AgentState
from app.requirements.modeling import diagram as _diagram
from app.requirements.modeling import relationships as _canonical
from app.requirements.modeling import validation as validator  # noqa: F401
from app.requirements.runtime.structured_llm import invoke_structured

render_diagram = _diagram.render_diagram
select_relationship_parts = _canonical.select_relationship_parts

_clean_text = _canonical._clean_text  # noqa: SLF001
_existing_include_options = _canonical._existing_include_options  # noqa: SLF001
_humanize_name = _canonical._humanize_name  # noqa: SLF001
_include_candidates = _canonical._include_candidates  # noqa: SLF001
_materialize_existing_includes = _canonical._materialize_existing_includes  # noqa: SLF001
_extend_label = _diagram._extend_label  # noqa: SLF001
_san = _diagram._san  # noqa: SLF001


@wraps(_canonical.identify_relationships)
def identify_relationships(
    state: AgentState, feedback: str = ""
) -> dict[str, object]:
    return _canonical.identify_relationships(
        state,
        feedback,
        proposal_call=invoke_structured,
    )


check_relationships = _canonical.check_relationships

__all__ = [
    "check_relationships",
    "identify_relationships",
    "render_diagram",
    "select_relationship_parts",
]
