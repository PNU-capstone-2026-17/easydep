from evaluation.dependency_audit.sample_app_direct_tls_intervention import (
    run_local_tls_experiment,
)


def test_domain_neutral_direct_tls_path_is_removed_restored_and_cleaned(tmp_path):
    result = run_local_tls_experiment(tmp_path / "result.json")

    assert result["outcome"] == "passed"
    assert [step["status"] for step in result["steps"]] == [
        "passed",
        "passed",
        "passed",
    ]
    assert result["cleanup"] == {
        "passed": True,
        "temporaryDirectoryRemoved": True,
        "listeningPortClosed": True,
    }
    assert "course" not in str(result).lower()
    assert "enrollment" not in str(result).lower()
