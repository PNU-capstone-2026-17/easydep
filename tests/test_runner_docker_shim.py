from app.core.orchestration.runner_docker_shim import translate


def test_runner_shim_translates_declared_gradle_tool_only():
    command, cwd, _ = translate(
        [
            "run",
            "--rm",
            "-v",
            "/easydep-workspace/app:/easydep-workspace/app",
            "-w",
            "/easydep-workspace/app",
            "gradle:21-jdk",
            "gradle",
            "test",
            "--no-daemon",
        ]
    )

    assert command[-2:] == ["test", "--no-daemon"]
    assert command[0] == "java"
    assert "-Dorg.gradle.caching=true" in command
    assert "-classpath" in command
    assert "org.gradle.wrapper.GradleWrapperMain" in command
    assert cwd.as_posix() == "/easydep-workspace/app"


def test_runner_shim_rejects_undeclared_image():
    try:
        translate(["run", "--rm", "alpine:latest", "sh"])
    except ValueError as error:
        assert "허용하지 않은" in str(error)
    else:
        raise AssertionError("undeclared image was accepted")


def test_runner_shim_maps_container_target_paths_to_local_bind_sources():
    command, _, _ = translate(
        [
            "run",
            "--rm",
            "-v",
            "/easydep-workspace:/workspace",
            "openapitools/openapi-generator-cli:v7.14.0",
            "generate",
            "-i",
            "/workspace/design/openapi.json",
            "-o",
            "/workspace/generated/client",
        ]
    )

    assert "/easydep-workspace/design/openapi.json" in command
    assert "/easydep-workspace/generated/client" in command
