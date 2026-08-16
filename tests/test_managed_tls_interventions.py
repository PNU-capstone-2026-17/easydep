import uuid
from pathlib import Path

from evaluation.dependency_audit.sample_app_managed_tls_common import startup_oracle


def test_managed_tls_oracle_is_domain_neutral_and_exposes_two_gates():
    script = startup_oracle()

    assert "'/readyz'" in script
    assert "'/business'" in script
    assert "0.0.0.0', 8080" in script
    assert "course" not in script.lower()
    assert "enrollment" not in script.lower()


def test_managed_tls_material_is_written_only_to_caller_owned_directory(tmp_path: Path):
    from evaluation.dependency_audit.sample_app_managed_tls_common import (
        generate_test_certificate,
    )

    material = generate_test_certificate(tmp_path, "easydep-neutral.invalid")

    assert set(material) == {"certificate", "privateKey"}
    assert all(path.parent == tmp_path for path in material.values())


def test_azure_managed_tls_material_includes_a_pfx_in_the_same_directory(tmp_path: Path):
    from evaluation.dependency_audit.sample_app_managed_tls_common import (
        generate_test_certificate,
    )

    material = generate_test_certificate(
        tmp_path, "easydep-neutral.invalid", pfx_password=uuid.uuid4().hex
    )

    assert set(material) == {"certificate", "privateKey", "pfx"}
    assert all(path.parent == tmp_path for path in material.values())
