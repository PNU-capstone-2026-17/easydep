"""A narrow, whole-source replacement tool for editor-only implementation owners."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Self

from openhands.sdk.tool import Action, Observation, ToolAnnotations, ToolDefinition, ToolExecutor
from pydantic import Field

from .harness import render_harness_error
from .workspace import grant_owner_file_access

SOURCE_REPLACE_TOOL_NAME = "replace_source"
_REGISTERED = False
_REGISTRATION_LOCK = threading.Lock()


class SourceReplaceAction(Action):
    """Replace one supplied writable source file with a complete UTF-8 body."""

    path: str = Field(min_length=1)
    source: str = Field(min_length=1)


class SourceReplaceObservation(Observation):
    """Result of a bounded source replacement."""


class SourceReplaceExecutor(ToolExecutor):
    def __init__(self, workspace: Path, allowed_files: list[str]) -> None:
        self.workspace = workspace.resolve()
        self.allowed_files = {Path(path).resolve() for path in allowed_files}

    def __call__(self, action, conversation=None):  # noqa: ANN001, ARG002
        if not action.source.strip():
            return SourceReplaceObservation.from_text(
                text="SOURCE_REPLACE_EMPTY: source must contain a complete non-empty file body.",
                is_error=True,
            )
        supplied = Path(action.path)
        target = (
            supplied.resolve() if supplied.is_absolute() else (self.workspace / supplied).resolve()
        )
        try:
            target.relative_to(self.workspace)
        except ValueError:
            return SourceReplaceObservation.from_text(
                text=render_harness_error(
                    "PATH_OUTSIDE_WORKSPACE",
                    "The path is outside the assigned workspace.",
                    retryable=False,
                    workspace=str(self.workspace),
                ),
                is_error=True,
            )
        if target not in self.allowed_files:
            return SourceReplaceObservation.from_text(
                text=render_harness_error(
                    "WRITE_OUTSIDE_OWNER_SCOPE",
                    "replace_source accepts only an exact supplied writable source file.",
                    retryable=False,
                    workspace=str(self.workspace),
                ),
                is_error=True,
            )
        if not target.is_file():
            return SourceReplaceObservation.from_text(
                text=render_harness_error(
                    "WRITE_OUTSIDE_OWNER_SCOPE",
                    "replace_source cannot create a missing source file.",
                    retryable=False,
                    workspace=str(self.workspace),
                ),
                is_error=True,
            )
        try:
            target.write_text(action.source, encoding="utf-8")
            grant_owner_file_access(target, self.workspace)
        except OSError as error:
            return SourceReplaceObservation.from_text(text=str(error), is_error=True)
        return SourceReplaceObservation.from_text(
            text=f"SOURCE_REPLACED: {target.relative_to(self.workspace).as_posix()}"
        )


class SourceReplaceTool(ToolDefinition[SourceReplaceAction, SourceReplaceObservation]):
    name = SOURCE_REPLACE_TOOL_NAME

    @classmethod
    def create(cls, conv_state, allowed_files: list[str]) -> Sequence[Self]:  # noqa: ARG003
        workspace = Path(conv_state.workspace.working_dir)
        allowed = "\n".join(
            f"- {Path(path).resolve().relative_to(workspace.resolve()).as_posix()}"
            for path in allowed_files
        )
        return [
            cls(
                description=(
                    "Replace the complete UTF-8 body of exactly one already-existing writable source. "
                    "Do not use partial patches, shell commands, or repository search. The complete source body must be non-empty. Allowed paths:\n"
                    + allowed
                ),
                action_type=SourceReplaceAction,
                observation_type=SourceReplaceObservation,
                executor=SourceReplaceExecutor(workspace, allowed_files),
                annotations=ToolAnnotations(
                    title=SOURCE_REPLACE_TOOL_NAME,
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


def register_source_replace_tool() -> str:
    """Register the typed editor-only tool once per process."""

    global _REGISTERED
    if _REGISTERED:
        return SOURCE_REPLACE_TOOL_NAME
    with _REGISTRATION_LOCK:
        if not _REGISTERED:
            from openhands.sdk.tool import register_tool

            register_tool(SOURCE_REPLACE_TOOL_NAME, SourceReplaceTool)
            _REGISTERED = True
    return SOURCE_REPLACE_TOOL_NAME
