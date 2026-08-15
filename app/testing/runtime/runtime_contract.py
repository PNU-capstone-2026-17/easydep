"""Small, testing-owned projection of an application runtime contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def test_environment(
    runtime_contract: dict[str, Any] | None, temporary_directory: Path
) -> dict[str, str]:
    """Return the isolated test environment declared in a runtime contract.

    Only the test namespace is accepted so generated contracts cannot replace
    inherited process controls such as PATH or JAVA_TOOL_OPTIONS.
    """
    environment: dict[str, str] = {}
    for fact in (runtime_contract or {}).get("facts") or []:
        if not isinstance(fact, dict) or fact.get("kind") != "runtime.environment":
            continue
        attributes = fact.get("attributes") or {}
        if not isinstance(attributes, dict):
            continue
        name = str(attributes.get("name") or "")
        template = str(attributes.get("testValueTemplate") or "")
        if name.startswith("EASYDEP_") and template:
            environment[name] = template.replace("{temp}", temporary_directory.as_posix())
    return environment
