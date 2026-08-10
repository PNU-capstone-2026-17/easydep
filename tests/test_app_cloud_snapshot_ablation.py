from pathlib import Path

import evaluation.research_protocol.commands.evaluate_app_cloud_snapshot_ablation as subject
from evaluation.research_protocol.core.snapshot_support import (
    apply_mutations,
    portable_result,
    terraform_files,
)


def _write(root: Path, name: str, content: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_same_snapshot_is_copied_to_both_arms_and_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    application = tmp_path / "snapshot"
    _write(
        application,
        "src/main/java/example/App.java",
        "package example; class App {}",
    )
    _write(application, "build.gradle", "plugins { id 'java' }\ndependencies {}\n")
    result = subject.evaluate(
        {
            "cases": [
                {
                    "id": "dependency",
                    "group": "build-runtime-dependency",
                    "sourceApplication": "snapshot",
                    "boundary": "application",
                    "expectedDiagnostic": "APP-DEP-001",
                    "expectedRepairOwner": "implementation.logic",
                    "mutations": [
                        {
                            "operation": "write",
                            "path": "src/main/java/example/Entity.java",
                            "content": (
                                "package example; import jakarta.persistence.Entity; "
                                "@Entity class RecordEntity {}"
                            ),
                        }
                    ],
                }
            ]
        },
        run_downstream=False,
    )
    row = result["cases"][0]
    assert row["sameInputAcrossArms"] is True
    assert row["sourceQualification"]["eligible"] is True
    assert row["fullDecisionCorrect"] is True
    assert row["arms"]["full"]["blockedBeforeDownstream"] is True
    assert row["arms"]["noConsistencyValidator"]["blockedBeforeDownstream"] is False


def test_mutation_requires_exact_precondition_and_cannot_escape_snapshot(tmp_path):
    application = tmp_path / "application"
    application.mkdir()
    _write(application, "infra/main.tf", "port = 8080\n")

    try:
        apply_mutations(
            application,
            [{"operation": "replace", "path": "infra/main.tf", "old": "9090", "new": "8080"}],
        )
    except ValueError as error:
        assert "변경 전제 불일치" in str(error)
    else:
        raise AssertionError("precondition mismatch must fail")

    try:
        apply_mutations(
            application,
            [{"operation": "write", "path": "../outside", "content": "bad"}],
        )
    except ValueError as error:
        assert "스냅샷 밖" in str(error)
    else:
        raise AssertionError("path escape must fail")


def test_portable_result_removes_windows_and_uri_path_forms(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    temporary = tmp_path / "temp"
    monkeypatch.setattr(subject, "ROOT", repository)
    value = {
        "native": str(temporary / "application"),
        "uri": f"file:///{(temporary / 'build/report.html').as_posix()}",
        "command": str(repository / "gradlew.bat"),
    }

    result = portable_result(
        value, temporary=temporary, repository_root=repository
    )

    assert result["native"].startswith("<temporary>")
    assert "<temporary>" in result["uri"]
    assert result["command"].startswith("<repository>")


def test_terraform_files_include_standard_template_extensions(tmp_path):
    application = tmp_path / "application"
    _write(application, "infra/main.tf", "resource \"x\" \"y\" {}")
    _write(application, "infra/cloud-init.yaml.tftpl", "mount /dev/data /srv/state")

    files = terraform_files(application)

    assert set(files) == {"main.tf", "cloud-init.yaml.tftpl"}
