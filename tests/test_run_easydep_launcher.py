from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-easydep.ps1"


def test_backend_reload_is_explicit_and_independent_from_frontend_hmr() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "[switch]$BackendReload" in source
    assert 'if ($BackendReload) {\n    $backendArguments += "--reload"' in source
    assert 'if (-not $ProductionLike) {\n    $backendArguments += "--reload"' not in source
    assert "-BackendReload cannot be combined with -ProductionLike." in source


def test_child_processes_do_not_inherit_the_launching_terminal_input() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "-RedirectStandardInput $backendStdinPath" in source
    assert "-RedirectStandardInput $frontendStdinPath" in source
