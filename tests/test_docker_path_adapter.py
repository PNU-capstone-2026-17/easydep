from app.core.orchestration.docker_path_adapter import translate_docker_command


def test_translate_docker_command_maps_only_workspace_paths():
    root = r"C:\repo\easydep"
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        root + ":" + root,
        "-w",
        root,
        "node:20",
        "node",
        root + r"\tools\generator.js",
        "--external",
        r"C:\other\input.json",
    ]

    translated = translate_docker_command(command, root)

    assert translated[4] == root + ":/easydep-workspace"
    assert translated[6] == "/easydep-workspace"
    assert translated[9] == "/easydep-workspace/tools/generator.js"
    assert translated[11] == r"C:\other\input.json"


def test_translate_docker_command_leaves_other_commands_unchanged():
    command = ["python", r"C:\repo\easydep\script.py"]
    assert translate_docker_command(command, r"C:\repo\easydep") == command


def test_translate_docker_command_handles_subdirectory_volumes():
    root = r"C:\repo\easydep"
    application = root + r"\.easydep\run\application"
    cache = root + r"\.easydep\cache\gradle"
    translated = translate_docker_command(
        [
            "docker",
            "run",
            "-v",
            application + ":" + application,
            "-v",
            cache + ":/home/gradle/.gradle",
            "-w",
            application,
            "gradle:8.14.2-jdk21",
        ],
        root,
    )

    assert translated[3] == application + ":/easydep-workspace/.easydep/run/application"
    assert translated[5] == cache + ":/home/gradle/.gradle"
    assert translated[7] == "/easydep-workspace/.easydep/run/application"


def test_translate_docker_command_maps_runner_source_back_to_host():
    translated = translate_docker_command(
        [
            "docker",
            "run",
            "-v",
            "/easydep-workspace/.easydep/run:/easydep-workspace/.easydep/run",
            "-w",
            "/easydep-workspace/.easydep/run",
            "node:20",
        ],
        "/easydep-workspace",
        host_workspace=r"C:\repo\easydep",
    )

    assert translated[3] == (
        r"C:\repo\easydep\.easydep\run:/easydep-workspace/.easydep/run"
    )
    assert translated[5] == "/easydep-workspace/.easydep/run"
