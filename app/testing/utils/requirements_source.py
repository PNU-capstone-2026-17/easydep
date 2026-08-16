"""The functional requirements the dynamic test suite is written against.

The requirements agent stores its classified requirement list as the
``REFINE_REQ`` artifact, so the testing agent reads that rather than re-deriving
requirements from the generated code — testing code against itself proves
nothing.

The stored value is the classified list the requirements graph emits
(``[{"id": "FR1", "text": ..., "type": "FR"}, ...]``, see
``app/requirements/agent/state.py``).  Older payloads wrapped that list in a
``{"requirements": [...]}`` object, so both shapes are accepted.
"""

from __future__ import annotations

from typing import Any

from app.repositories.artifact_repository import AppNotFound, load_state


class RequirementsUnavailable(Exception):
    """The app has no stored requirements analysis to test against."""


def _as_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("requirements")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("text")]


def functional_requirements(app_id: str) -> list[dict[str, Any]]:
    """Return the stored FR items for an app, newest stored version.

    NFRs are dropped: they are the dynamic NFR stage's input, and mixing them in
    makes the generated acceptance suite assert load and latency properties that
    a functional run cannot decide.  An item with no ``type`` is kept, because an
    unclassified requirement is still a requirement.
    """
    try:
        state = load_state(app_id)
    except AppNotFound as error:
        raise RequirementsUnavailable(f"Unknown app id: {app_id}") from error

    items = _as_items(state.get("refined_requirements"))
    if not items:
        raise RequirementsUnavailable(
            f"App {app_id} has no stored requirements analysis (REFINE_REQ)."
        )

    functional = [
        item
        for item in items
        if str(item.get("type") or "FR").upper() != "NFR"
    ]
    return functional
