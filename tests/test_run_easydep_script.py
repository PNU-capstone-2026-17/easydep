from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-easydep.ps1"


def test_dev_runner_connects_frontend_database_backend_and_health_checks() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "& $npmCommand run build" in source
    assert '"mysql:8.4"' in source
    assert '"uvicorn", "server:app"' in source
    assert "/api/health" in source
    assert "/workspace/" in source
    assert "/api/workspace/apps?limit=1" in source


def test_dev_runner_reuses_only_a_matching_successful_frontend_build() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"frontend-build.sha256"' in source
    assert "Get-FrontendBuildHash" in source
    assert '@("src", "static")' in source
    assert '"build\\index.html"' in source
    assert "$buildHash -eq $builtHash" in source
    assert "Set-Content -LiteralPath $frontendBuildHashPath" in source
    assert "[switch]$ForceFrontendBuild" in source


def test_dev_runner_only_stops_recorded_launcher_and_listener_processes() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "Get-OwnedServerProcesses" in source
    assert "listenerPid" in source
    assert "listenerStartedAt" in source
    assert "$process.StartTime.ToUniversalTime()" in source
    assert "Stop-Process -Id $process.Id" in source
    assert "Get-Process python" not in source
