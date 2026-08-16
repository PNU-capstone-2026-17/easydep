from __future__ import annotations

from evaluation.dependency_audit.sample_app_postgres_e1_common import (
    app_build_script,
    baseline_script,
    blocked_script,
    restored_script,
)


def test_common_build_script_embeds_domain_neutral_sample() -> None:
    script = app_build_script("install-docker")

    assert "install-docker" in script
    assert "dependency-sample:postgres-e1" in script
    assert "course" not in script.lower()
    assert "student" not in script.lower()


def test_common_oracles_cover_success_loss_and_restore() -> None:
    baseline = baseline_script()
    blocked = blocked_script()
    restored = restored_script()

    assert "/health/ready" in baseline
    assert "/records/evidence" in baseline
    assert '"message":"kept"' in baseline
    assert '"$health" = 503' in blocked
    assert '"$business" = 502' in blocked
    assert '"$status" = 200' in restored
    assert '"message": "kept"' in restored
