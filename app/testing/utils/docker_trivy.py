import json
import shutil
import subprocess

from app.implementation.runtime.process import run_process_tree


def run_trivy_scan(target_dir: str) -> list[str]:
    """Trivy 구성 검사를 실행하고 발견한 문제를 읽기 쉬운 문자열로 반환한다.

    공용 툴체인 안에서는 설치된 ``trivy``를 바로 실행한다. 로컬 개발자가 아직 툴체인
    이미지를 사용하지 않는 경우에만 기존 Docker 이미지 실행을 예비 경로로 남긴다.
    """
    executable = shutil.which("trivy")
    command = (
        [
            executable,
            "config",
            target_dir,
            "--format",
            "json",
            "--severity",
            "HIGH,CRITICAL",
        ]
        if executable
        else [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{target_dir}:/src:ro",
            "aquasec/trivy:0.74.0",
            "config",
            "/src",
            "--format",
            "json",
            "--severity",
            "HIGH,CRITICAL",
        ]
    )

    try:
        result = run_process_tree(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=300,
        )
        if not result.stdout.strip():
            return [f"Trivy가 결과를 반환하지 않았습니다: {result.stderr[-2000:]}"]

        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            return [f"Trivy JSON 결과를 읽을 수 없습니다: {result.stdout[:500]}"]

        issues: list[str] = []
        results = parsed.get("Results", [])
        for result_item in results:
            target = result_item.get("Target", "Unknown File")
            misconfigs = result_item.get("Misconfigurations", [])
            for misconf in misconfigs:
                issues.append(
                    f"[{target}] {misconf.get('Title', 'Unknown Issue')} "
                    f"({misconf.get('Severity', 'UNKNOWN')}): "
                    f"{misconf.get('Message', '')}"
                )
        return issues

    except (OSError, subprocess.SubprocessError) as error:
        return [f"Trivy 실행 실패: {error}"]
