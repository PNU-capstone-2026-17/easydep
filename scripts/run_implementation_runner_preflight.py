"""Run the fixed Linux implementation environment checks without an LLM call."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.implementation.runtime.linux_runner_transport import (
    configured_runner_image,
    runner_command,
)
from app.llm_connection import llm_subprocess_environment


def main() -> int:
    image = configured_runner_image()
    if not image:
        raise SystemExit("EASYDEP_TOOLCHAIN_IMAGE is not configured")
    llm_environment = llm_subprocess_environment()
    environment = os.environ.copy()
    environment.update(llm_environment)
    command = runner_command(
        image=image,
        repository_root=REPO_ROOT,
        operation="preflight",
        arguments=[],
        environment=environment,
        llm_environment=llm_environment,
    )
    result = subprocess.run(command, env=environment, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
