"""Requirements-agent boundary.

No requirements internals are reproduced here. The adapter invokes its public
session functions and persists the artifacts through the same function used by
the requirements HTTP API.
"""

from __future__ import annotations

from typing import Any


class RequirementsAdapter:
    def start(
        self,
        *,
        app_id: str,
        thread_id: str,
        requirements: list[str],
        constraints_text: str,
    ) -> dict[str, Any]:
        from app.requirements.agent import start_analysis
        from app.requirements.api import persist_analysis
        from app.requirements.config import settings

        result = start_analysis(
            requirements,
            thread_id,
            feedback_gates=settings.enable_feedback_gates,
            persist=settings.enable_session_persistence,
            constraints_text=constraints_text,
        )
        result["saved_stages"] = persist_analysis(app_id, result)
        return result

    def resume(
        self, *, app_id: str, thread_id: str, answer: Any
    ) -> dict[str, Any]:
        from app.requirements.agent import resume_analysis
        from app.requirements.api import persist_analysis
        from app.requirements.config import settings

        result = resume_analysis(
            answer,
            thread_id,
            persist=settings.enable_session_persistence,
        )
        result["saved_stages"] = persist_analysis(app_id, result)
        return result
