from experiments.baselines.verify import inspect_repository


def test_baseline_inspection_separates_repository_and_cloud_artifacts(tmp_path):
    files = {
        "src/main/App.java": "class App {}",
        "src/test/AppTest.java": "class AppTest {}",
        "build.gradle": "plugins {}",
        "Dockerfile": "FROM eclipse-temurin:21",
        "requirements.md": "# Requirements",
        "architecture.md": "# Design",
        "deployment.mmd": "flowchart LR",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    result = inspect_repository(tmp_path)

    assert result["requiredPassed"] is True
    assert result["cloudNativePassed"] is False
    assert result["checks"]["iac_present"] is False
