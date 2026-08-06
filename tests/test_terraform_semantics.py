import json
from pathlib import Path

from evaluation.implementation import evaluate_repository, resolve_oracle
from evaluation.terraform_semantics import analyze_terraform_semantics


def _write(tmp_path: Path, name: str, content: str) -> None:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_gcp_nested_blocks_and_attached_disk_are_normalized(tmp_path):
    _write(tmp_path, "Dockerfile", "FROM eclipse-temurin:21\nEXPOSE 8080\n")
    _write(tmp_path, "src/main/App.java", 'class App { String health = "/health"; }')
    _write(tmp_path, "config/application.yml", "data: /var/lib/notes\n")
    _write(tmp_path, "terraform/main.tf", """
resource "google_compute_network" "main" {}
resource "google_compute_subnetwork" "app" {
  network = google_compute_network.main.id
}
resource "google_compute_disk" "data" { size = 20 }
resource "google_compute_instance" "app" {
  count = 1
  boot_disk { initialize_params { image = "debian-cloud/debian-12" } }
  attached_disk { source = google_compute_disk.data.id }
  network_interface {
    subnetwork = google_compute_subnetwork.app.id
    access_config {}
  }
}
resource "google_compute_firewall" "https" {
  network = google_compute_network.main.id
  allow { protocol = "tcp" ports = ["443"] }
}
""")

    result = analyze_terraform_semantics(tmp_path)

    assert result["status"] == "available"
    assert result["capabilities"] == {
        "vmCount": 1,
        "availabilityZones": None,
        "loadBalancer": False,
        "persistentData": True,
        "dataDiskGiB": 20,
        "volumeMount": "/var/lib/notes",
        "publicHttps": True,
        "healthPath": "/health",
        "applicationPort": 8080,
    }
    assert {(edge["from"], edge["to"]) for edge in result["edges"]} >= {
        ("vm", "disk"),
        ("vm", "nic"),
        ("nic", "subnet"),
        ("subnet", "network"),
    }


def test_end_to_end_oracle_is_scored_from_final_artifacts(tmp_path):
    _write(tmp_path, "Dockerfile", "FROM eclipse-temurin:21\nEXPOSE 8080\n")
    _write(tmp_path, "src/main/App.java", 'class App { String health = "/health"; }')
    _write(tmp_path, "src/test/AppTest.java", "class AppTest {}")
    _write(tmp_path, "build.gradle", "plugins {}")
    _write(tmp_path, "config/application.yml", "data: /var/lib/notes\n")
    _write(tmp_path, "main.tf", """
resource "google_compute_network" "main" {}
resource "google_compute_subnetwork" "app" { network = google_compute_network.main.id }
resource "google_compute_disk" "data" { size = 20 }
resource "google_compute_instance" "app" {
  boot_disk { initialize_params { image = "debian-cloud/debian-12" } }
  attached_disk { source = google_compute_disk.data.id }
  network_interface { subnetwork = google_compute_subnetwork.app.id }
}
resource "google_compute_firewall" "https" {
  network = google_compute_network.main.id
  allow { protocol = "tcp" ports = ["443"] }
}
""")
    oracle = json.loads(
        Path("evaluation/baselines/cases/oracle.json").read_text(encoding="utf-8")
    )

    result = evaluate_repository(tmp_path, oracle, case_id="P2-gcp")

    assert result["score"]["status"] == "completed"
    assert result["score"]["failed"] == 0
    assert result["score"]["unknown"] == 0
    assert result["score"]["passRate"] == 1.0


def test_custom_network_dependency_is_not_forced_when_target_is_absent(tmp_path):
    _write(tmp_path, "main.tf", """
resource "aws_instance" "app" {}
resource "aws_security_group" "https" {
  ingress { from_port = 443 to_port = 443 protocol = "tcp" }
}
""")
    oracle = json.loads(
        Path("evaluation/baselines/cases/oracle.json").read_text(encoding="utf-8")
    )
    expected = resolve_oracle(oracle, "P1-aws")

    result = evaluate_repository(tmp_path, oracle, case_id="P1-aws")

    dependency = next(
        check for check in result["score"]["checks"] if check["kind"] == "dependency"
    )
    assert expected["requiredDependencies"][0]["condition"]
    assert dependency["status"] == "not-applicable"


def test_every_edge_of_absent_conditional_path_is_not_applicable(tmp_path):
    _write(tmp_path, "main.tf", """
resource "google_compute_network" "main" { auto_create_subnetworks = true }
resource "google_compute_instance" "app" {
  boot_disk { initialize_params { image = "debian-cloud/debian-12" } }
  network_interface { network = google_compute_network.main.id }
}
""")
    oracle = json.loads(
        Path("evaluation/baselines/cases/oracle.json").read_text(encoding="utf-8")
    )

    result = evaluate_repository(tmp_path, oracle, case_id="P1-gcp")
    conditional = [
        check
        for check in result["score"]["checks"]
        if check["kind"] == "dependency" and check["status"] == "not-applicable"
    ]

    assert {(item["from"], item["to"]) for item in conditional} == {
        ("nic", "subnet"),
        ("subnet", "network"),
    }


def test_unknown_required_capability_reduces_pass_rate(tmp_path):
    _write(tmp_path, "main.tf", 'resource "google_compute_instance" "app" {}')
    oracle = json.loads(
        Path("evaluation/baselines/cases/oracle.json").read_text(encoding="utf-8")
    )

    result = evaluate_repository(tmp_path, oracle, case_id="P1-gcp")

    assert result["score"]["unknown"] > 0
    assert result["score"]["passRate"] < 1.0


def test_vm_bootstrap_template_contributes_tls_and_application_port(tmp_path):
    _write(tmp_path, "infra/main.tf", 'resource "aws_instance" "app" {}')
    _write(
        tmp_path,
        "infra/user_data.sh.tftpl",
        """server { listen 443 ssl; ssl_certificate /etc/cert; }
docker run -p 127.0.0.1:8080:8080 image
location = /health {}
""",
    )

    result = analyze_terraform_semantics(tmp_path)

    assert result["capabilities"]["publicHttps"] is True
    assert result["capabilities"]["applicationPort"] == 8080
    assert result["capabilities"]["healthPath"] == "/health"
