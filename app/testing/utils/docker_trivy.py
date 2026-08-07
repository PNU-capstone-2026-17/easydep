import json
import subprocess
from typing import Any

def run_trivy_scan(target_dir: str) -> list[str]:
    """
    Runs Trivy config scan on a given directory via Docker and parses the results.
    Returns a list of issue strings.
    """
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{target_dir}:/src",
        "aquasec/trivy", "config", "/src",
        "--format", "json",
        "--severity", "HIGH,CRITICAL"
    ]
    
    try:
        # trivy may exit with non-zero if issues are found, so check=False is used
        # We capture stdout and stderr separately, because older trivy versions might mix logs.
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        # Trivy output might be empty or invalid JSON if docker fails to run
        if not result.stdout.strip():
            return [f"Trivy scan failed or returned no output. Stderr: {result.stderr}"]

        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            return [f"Failed to parse Trivy JSON output. Output: {result.stdout[:200]}"]

        issues = []
        # 'Results' contains the findings per file
        results = parsed.get("Results", [])
        for r in results:
            target = r.get("Target", "Unknown File")
            misconfigs = r.get("Misconfigurations", [])
            for misconf in misconfigs:
                msg = f"[{target}] {misconf.get('Title', 'Unknown Issue')} ({misconf.get('Severity', 'UNKNOWN')}): {misconf.get('Message', '')}"
                issues.append(msg)
                
        return issues
        
    except Exception as e:
        return [f"Error running Trivy container: {str(e)}"]
