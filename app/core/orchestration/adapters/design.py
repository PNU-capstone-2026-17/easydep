"""Design-agent boundary."""

from __future__ import annotations

from typing import Any


class DesignContractError(RuntimeError):
    pass


class DesignAdapter:
    def start(self, *, app_id: str) -> dict[str, Any]:
        from app.design.graphs.design_graph import reset_design, start_design
        from app.repositories import artifact_repository

        state = artifact_repository.load_state(app_id)
        if not state.get("usecase_spec"):
            raise DesignContractError(
                "Requirements analysis did not persist usecase_spec"
            )
        reset_design(app_id)
        return start_design(app_id, state)

    def resume(self, *, app_id: str, feedback: str) -> dict[str, Any]:
        from app.design.graphs.design_graph import resume_design

        return resume_design(app_id, feedback)
