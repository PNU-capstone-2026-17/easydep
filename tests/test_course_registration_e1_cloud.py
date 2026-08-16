from pathlib import Path

from evaluation.dependency_audit.course_registration_e1_cloud import (
    APP_IMAGE,
    _app_setup,
    _oracle_outputs,
    _state_setup,
)


def test_state_setup_binds_postgres_to_child_directory_of_data_disk() -> None:
    script = _state_setup("/dev/example-data", "test-password")

    assert "test -b '/dev/example-data'" in script
    assert "/var/lib/easydep-postgres/data:/var/lib/postgresql/data" in script
    assert "--restart unless-stopped" in script
    assert "POSTGRES_DB=appdb" in script


def test_app_setup_uses_frozen_image_and_private_database_endpoint() -> None:
    script = _app_setup(
        "10.0.0.4",
        "test-password",
        "certificate",
        "private-key",
    )

    assert "jdbc:postgresql://10.0.0.4:5432/appdb" in script
    assert APP_IMAGE in script
    assert "127.0.0.1:8080:8080" in script
    assert "listen 443 ssl" in script


def test_oracle_outputs_are_provider_specific_and_adjacent(tmp_path: Path) -> None:
    result = _oracle_outputs(tmp_path / "result.json", "gcp")

    assert set(result) == {"business", "databaseUnavailable", "persistence"}
    assert all(path.parent == tmp_path for path in result.values())
    assert all("gcp" in path.name for path in result.values())


def test_campaign_image_is_digest_pinned() -> None:
    assert APP_IMAGE.startswith("public.ecr.aws/")
    assert "@sha256:" in APP_IMAGE
    assert ":v1" not in APP_IMAGE
