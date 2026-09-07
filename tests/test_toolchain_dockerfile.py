from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run-easydep.ps1"
TOOLCHAIN_DOCKERFILE = ROOT / "docker" / "Dockerfile.toolchain"


def test_launcher_builds_and_hashes_the_dedicated_toolchain_recipe() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert '$toolchainDockerfile = Join-Path $repoRoot "docker\\Dockerfile.toolchain"' in source
    assert '"build", "--file", $toolchainDockerfile' in source
    assert '"docker/Dockerfile.toolchain"' in source
    assert '"Dockerfile",' not in source[source.index("function Get-ToolchainBuildHash") :]
    assert (
        '"builder", "prune", "--force",\n'
        '            "--max-used-space", $toolchainBuildCacheLimit'
    ) in source


def test_large_tool_layers_precede_changeable_python_dependencies() -> None:
    source = TOOLCHAIN_DOCKERFILE.read_text(encoding="utf-8")

    system_stage = source.index("FROM python:3.13-slim-bookworm AS toolchain-system")
    browser_stage = source.index("FROM toolchain-system AS toolchain-browser")
    common_stage = source.index("FROM toolchain-browser AS toolchain")
    common_requirements = source.index("COPY requirements-common.txt")

    assert system_stage < browser_stage < common_stage < common_requirements
    assert source.index("COPY --from=jdk-runtime") < browser_stage
    assert source.index("python -m playwright install") < common_stage
    assert source.index("USER appuser", common_stage) < source.index(
        "RUN sh ./scripts/bootstrap-implementation-tools.sh", common_stage
    )
