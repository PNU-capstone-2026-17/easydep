from app.implementation.runtime.runner_docker_shim import translate


def test_runner_shim_translates_declared_gradle_tool_only():
    command, cwd, _ = translate(
        [
            "run",
            "--rm",
            "-v",
            "/easydep-workspace/app:/easydep-workspace/app",
            "-w",
            "/easydep-workspace/app",
            "gradle:8.14.2-jdk21",
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
            "openapitools/openapi-generator-cli:v7.24.0",
            "generate",
            "-i",
            "/workspace/design/openapi.json",
            "-o",
            "/workspace/generated/client",
        ]
    )

    assert "/easydep-workspace/design/openapi.json" in command
    assert "/easydep-workspace/generated/client" in command


def test_runner_shim_accepts_the_pinned_backend_openapi_image():
    command, _, _ = translate(
        [
            "run",
            "--rm",
            "openapitools/openapi-generator-cli:v7.24.0",
            "generate",
            "-g",
            "spring",
        ]
    )

    assert command[:3] == [
        "java",
        "-jar",
        "/opt/easydep/openapi-generator-7.24.0.jar",
    ]


def test_runner_shim_keeps_frontend_node_tool():
    command, cwd, _ = translate(
        [
            "run",
            "--rm",
            "-v",
            "/easydep-workspace/frontend:/easydep-workspace/frontend",
            "-w",
            "/easydep-workspace/frontend",
            "node:20",
            "npm",
            "install",
        ]
    )

    assert command == ["npm", "install"]
    assert cwd is not None
    assert cwd.as_posix() == "/easydep-workspace/frontend"


def test_runner_shim_rejects_removed_puml2code_image():
    try:
        translate(
            [
                "run",
                "--rm",
                "easydep/puml2code-bce:0.2.0",
                "-i",
                "/workspace/design/class.puml",
            ]
        )
    except ValueError as error:
        assert "허용하지 않은" in str(error)
    else:
        raise AssertionError("removed puml2code image was accepted")
