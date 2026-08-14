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
    Runs inside the docker runner if configured, otherwise locally.
    """
    # Create a temporary directory in the workspace to store the test file
    # so that docker can mount and access it.
    workspace_test_dir = repository_root / ".easydep" / "testing" / "generated"
    workspace_test_dir.mkdir(parents=True, exist_ok=True)
    
    with tempfile.NamedTemporaryFile(
        dir=workspace_test_dir, prefix="test_dynamic_", suffix=".py", delete=False, mode="w", encoding="utf-8"
    ) as temp_file:
        temp_file.write(code)
        temp_path = Path(temp_file.name)

    report_path = temp_path.with_suffix(".json")

    environment = os.environ.copy()
    environment["TARGET_URL"] = target_url

    runner_image = configured_runner_image(environment)
    
    if runner_image:
        # Run inside Docker
        container_test_path = to_container_path(temp_path, repository_root)
        container_report_path = to_container_path(report_path, repository_root)
        
        arguments = [
            "-m", "pytest", str(container_test_path),
            "--json-report", f"--json-report-file={container_report_path}"
        ]
        command = runner_command(
            image=runner_image,
            repository_root=repository_root,
            operation="python", # Assume the image's entrypoint allows running python
            arguments=arguments,
            environment=environment,
        )
    else:
        # Run locally
        import sys
        command = [
            sys.executable, "-m", "pytest", str(temp_path),
            "--json-report", f"--json-report-file={report_path}"
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
