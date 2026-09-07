"""Serializable OpenHands definition for the legacy focused-check tool."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Self

from openhands.sdk.tool import (
    Action,
    DeclaredResources,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)

from .task_check import TASK_CHECK_TOOL_NAME, TaskCheckSession


class TaskCheckAction(Action):
    """A focused verification request with no model-controlled arguments."""


class TaskCheckObservation(Observation):
    """Text returned by the assigned compile or test check."""


class TaskCheckExecutor(ToolExecutor):
    def __init__(
        self,
        sandbox: Path,
        task_type: str,
        allowed_write_paths: list[str],
        verification_profile: dict[str, object] | None = None,
    ) -> None:
        self.session = TaskCheckSession(
            sandbox,
            task_type,
            list(allowed_write_paths),
            dict(verification_profile) if verification_profile else None,
        )

    def __call__(self, _action, conversation=None):  # noqa: ANN001, ARG002
        passed, output = self.session.run()
        return TaskCheckObservation.from_text(text=output, is_error=not passed)


class TaskCheckTool(ToolDefinition[TaskCheckAction, TaskCheckObservation]):
    name = TASK_CHECK_TOOL_NAME

    def declared_resources(self, _action: Action) -> DeclaredResources:
        workspace = str((self.meta or {}).get("workspace", "unknown"))
        return DeclaredResources(
            keys=(f"implementation-check:{workspace}",),
            declared=True,
        )

    @classmethod
    def create(
        cls,
        conv_state,
        *,
        task_type: str,
        allowed_write_paths: list[str],
        verification_profile: dict[str, object] | None = None,
    ) -> Sequence[Self]:
        sandbox = Path(conv_state.workspace.working_dir).resolve()
        return [
            cls(
                description=(
                    "Run the focused compile or test already assigned to this "
                    "implementation task. This tool takes no arguments and cannot "
                    "run arbitrary shell commands. Read a failed result, edit the "
                    "source, and run this check again. Call finish only after it passes."
                ),
                action_type=TaskCheckAction,
                observation_type=TaskCheckObservation,
                executor=TaskCheckExecutor(
                    sandbox,
                    task_type,
                    allowed_write_paths,
                    verification_profile,
                ),
                annotations=ToolAnnotations(
                    title=TASK_CHECK_TOOL_NAME,
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                meta={"workspace": str(sandbox)},
            )
        ]
