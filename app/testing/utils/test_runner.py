import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.orchestration.linux_runner_transport import (
    configured_runner_image,
    runner_command,
    to_container_path,
)
from app.core.orchestration.process import run_process_tree


def run_dynamic_test(code: str, target_url: str, repository_root: Path) -> dict[str, Any]:
    """
    Executes the dynamically generated pytest+playwright code.
    Runs unconditionally inside the official Playwright Docker container to ensure browser dependencies are met.
    """
    workspace_test_dir = repository_root / ".easydep" / "testing" / "generated"
    workspace_test_dir.mkdir(parents=True, exist_ok=True)
    
    with tempfile.NamedTemporaryFile(
        dir=workspace_test_dir, prefix="test_dynamic_", suffix=".py", delete=False, mode="w", encoding="utf-8"
    ) as temp_file:
        temp_file.write(code)
        temp_path = Path(temp_file.name)

    report_path = temp_path.with_suffix(".json")

    environment = os.environ.copy()
    
    # Auto-rewrite localhost to host.docker.internal so the container can reach the host network
    if "localhost" in target_url or "127.0.0.1" in target_url:
        target_url = target_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
        
    environment["TARGET_URL"] = target_url

    container_test_path = to_container_path(temp_path, repository_root)
    container_report_path = to_container_path(report_path, repository_root)
    
    # Run inside isolated Playwright container
    command = [
        "docker", "run", "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-v", f"{repository_root.resolve()}:/easydep-workspace",
        "-w", "/easydep-workspace",
        "-e", f"TARGET_URL={target_url}",
        "mcr.microsoft.com/playwright/python:v1.40.0-jammy",
        "bash", "-c",
        f"pip install --quiet pytest pytest-json-report httpx requests playwright && "
        f"python3 -m pytest {container_test_path} --json-report --json-report-file={container_report_path}"
    ]

    try:
        completed = run_process_tree(
            command,
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        
        report_data = {}
        if report_path.exists():
            try:
                report_data = json.loads(report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
            finally:
                report_path.unlink(missing_ok=True)

        return {
            "status": "passed" if completed.returncode == 0 else "failed",
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "report": report_data
        }
    finally:
        # Cleanup the temporary test file
        temp_path.unlink(missing_ok=True)

