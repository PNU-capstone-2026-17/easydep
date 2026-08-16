from evaluation.dependency_audit.gcp_sample_app_postgres_e3 import _wait_state_ready
from evaluation.dependency_audit.sample_app_postgres_e3_common import (
    app_rebind_from_variable_script,
    app_rebind_script,
    state_setup_script,
)


def test_state_setup_returns_to_provider_controller_after_postgres_is_ready() -> None:
    script = state_setup_script("install-docker", "device=/dev/example")

    assert 'test "$ready" = 1' in script
    assert "pg_isready -U postgres && exit 0" not in script


def test_app_rebind_reuses_local_image_and_does_not_build() -> None:
    script = app_rebind_script("10.0.0.12")

    assert "docker image inspect" in script
    assert 'test "$before" = "$after"' in script
    assert "docker build" not in script
    assert "10.0.0.12:5432" in script


def test_app_runtime_variable_rebind_expands_endpoint_safely() -> None:
    script = app_rebind_from_variable_script("replacement_ip")

    assert "@${replacement_ip}:5432" in script
    assert '-e DATABASE_URL="$database_url"' in script
    assert "docker build" not in script


def test_gcp_state_readiness_requires_postgres_and_normal_startup(monkeypatch) -> None:
    observations = iter([
        "/var/run/postgresql:5432 - accepting connections",
        (
            "/var/run/postgresql:5432 - accepting connections\n"
            "Finished running startup scripts"
        ),
    ])
    monkeypatch.setattr(
        "evaluation.dependency_audit.gcp_sample_app_postgres_e3._run",
        lambda *args, **kwargs: next(observations),
    )
    monkeypatch.setattr(
        "evaluation.dependency_audit.gcp_sample_app_postgres_e3.time.sleep",
        lambda _seconds: None,
    )

    assert "startup script completed" in _wait_state_ready("project", "state")


def test_aws_bootstrap_limit_is_avoided_by_building_after_guest_ready() -> None:
    from evaluation.dependency_audit.aws_sample_app_postgres_e3 import (
        _app_bootstrap_script,
    )

    script = _app_bootstrap_script()
    assert len(script.encode("utf-8")) < 16_384
    assert "service.py" not in script
