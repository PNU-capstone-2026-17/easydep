import json
from pathlib import Path

import evaluation.implementation as implementation
from evaluation.baselines.cot import _extract_object
from evaluation.implementation import (
    _iac_engine_result,
    evaluate_repository,
    inspect_repository,
    normalize_tool_graph,
    resolve_oracle,
    score_graph,
    write_evaluation,
)
from evaluation.terraform_semantics import analyze_terraform_semantics


def test_baseline_inspection_separates_repository_and_cloud_artifacts(tmp_path):
    files = {
        "src/main/App.java": "class App {}",
        "src/test/AppTest.java": "class AppTest {}",
        "build.gradle": "plugins {}",
        "Dockerfile": "FROM eclipse-temurin:21",
        "terraform/main.tf": """
resource "aws_vpc" "main" {}
resource "aws_subnet" "public" {
  vpc_id = aws_vpc.main.id
}
resource "aws_instance" "app" {
  subnet_id = aws_subnet.public.id
}
""",
        "deployment.mmd": "flowchart LR",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    result = inspect_repository(tmp_path)

    assert result["requiredPassed"] is True
    assert result["cloudNativePassed"] is True
    assert result["checks"]["iac_present"] is True
    assert result["checks"]["deployment_diagram_present"] is True


def test_cot_keeps_implementation_when_auxiliary_bundle_fields_are_missing():
    bundle = _extract_object('{"files":{"Dockerfile":"FROM scratch"}}')

    assert bundle["files"] == {"Dockerfile": "FROM scratch"}
    assert bundle["traceability"] == []
    assert "traceability" in bundle["_missingBundleFields"]


def test_re_evaluation_preserves_previous_result(tmp_path):
    path = tmp_path / "evaluation.json"
    previous = {"schemaVersion": "v1", "experimentEligible": False}
    current = {"schemaVersion": "v1", "experimentEligible": True}
    path.write_text(json.dumps(previous), encoding="utf-8")

    history = write_evaluation(path, current)

    assert history is not None
    assert json.loads(history.read_text(encoding="utf-8")) == previous
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["experimentEligible"] is True
    assert saved["evaluationMetadata"]["schema"] == implementation.EVALUATOR_SCHEMA


def test_iac_formatting_is_reported_without_overriding_validity():
    status, format_compliant = _iac_engine_result([{
        "format": {"status": "failed"},
        "initialize": {"status": "passed"},
        "validate": {"status": "passed", "json": {"valid": True}},
        "graph": {"status": "passed"},
    }])

    assert status == "passed"
    assert format_compliant is False


def test_iac_non_json_validation_output_is_a_failure_not_an_exception():
    status, _ = _iac_engine_result([{
        "format": {"status": "failed"},
        "initialize": {"status": "passed"},
        "validate": {"status": "passed", "json": None},
        "graph": {"status": "passed"},
    }])

    assert status == "failed"


def test_missing_normalized_graph_is_not_an_evaluator_exception(tmp_path, monkeypatch):
    (tmp_path / "main.tf").write_text('resource "aws_instance" "app" {}', encoding="utf-8")
    monkeypatch.setattr(implementation, "run_iac_tools", lambda _root: {
        "iacEngine": {"status": "failed", "modules": [{"normalizedGraph": None}]},
        "trivy": {"status": "unavailable"},
    })
    monkeypatch.setattr(
        implementation, "run_container_tools", lambda *_args: {"status": "failed"}
    )

    result = evaluate_repository(tmp_path, run_tools=True)

    assert result["resourceGraph"]["status"] == "not-run"
    assert result["experimentEligible"] is False


def test_final_implementation_evaluation_uses_iac_not_diagram(tmp_path):
    files = {
        "src/main/App.java": """
class App {
  int classify(int value) {
    if (value > 10) return 2;
    if (value > 0) return 1;
    return 0;
  }
}
""",
        "src/test/AppTest.java": "class AppTest {}",
        "build.gradle": "plugins {}",
        "Dockerfile": "FROM eclipse-temurin:21",
        "terraform/main.tf": """
resource "google_compute_network" "main" {}
resource "google_compute_subnetwork" "public" {
  network = google_compute_network.main.id
}
resource "google_compute_instance" "app" {
  network_interface { subnetwork = google_compute_subnetwork.public.id }
}
""",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    result = evaluate_repository(tmp_path, {
        "requiredResourceTypes": ["network", "subnet", "vm"],
        "requiredDependencyTypes": [
            {"from": "subnet", "to": "network"},
            {"from": "vm", "to": "subnet"},
        ],
        "forbiddenResourceTypes": ["loadBalancer"],
    })

    assert result["staticValidationPassed"] is True
    assert result["repository"]["checks"]["deployment_diagram_present"] is False
    assert result["score"]["status"] == "not-run"
    assert result["experimentEligible"] is False
    assert result["codeQuality"]["complexity"]["status"] == "available"
    assert result["codeQuality"]["complexity"]["functionCount"] == 1
    assert result["codeQuality"]["complexity"]["cyclomaticComplexity"]["max"] == 3
    assert result["codeQuality"]["complexity"]["decisionPointDensityPer100Nloc"] > 0
    assert result["codeQuality"]["coverage"]["status"] == "unavailable"


def test_generated_package_name_does_not_hide_production_code(tmp_path):
    path = tmp_path / "src/main/java/com/example/generated/App.java"
    path.parent.mkdir(parents=True)
    path.write_text(
        "class App { int choose(boolean flag) { return flag ? 1 : 0; } }",
        encoding="utf-8",
    )

    result = evaluate_repository(tmp_path)

    assert result["codeQuality"]["complexity"]["fileCount"] == 1
    assert result["codeQuality"]["complexity"]["functionCount"] == 1


def test_opentofu_dot_graph_is_normalized_without_easydep_artifacts():
    dot = r'''
digraph {
  "[root] aws_vpc.main (expand)" [label = "aws_vpc.main", shape = "box"]
  "[root] aws_subnet.public (expand)" [label = "aws_subnet.public", shape = "box"]
  "[root] aws_instance.app (expand)" [label = "aws_instance.app", shape = "box"]
  "[root] aws_subnet.public (expand)" -> "[root] aws_vpc.main (expand)"
  "[root] aws_instance.app (expand)" -> "[root] aws_subnet.public (expand)"
}
'''

    graph = normalize_tool_graph(dot, "terraform")

    assert {node["type"] for node in graph["nodes"]} == {"network", "subnet", "vm"}
    assert {
        (edge["fromType"], edge["toType"])
        for edge in graph["edges"]
    } == {("subnet", "network"), ("vm", "subnet")}
    assert graph["extractionMethod"] == "opentofu-or-terraform-graph"
    score = score_graph(graph, {
        "requiredResourceTypes": ["network", "subnet", "vm"],
        "requiredDependencyTypes": [
            {"from": "subnet", "to": "network"},
            {"from": "vm", "to": "subnet"},
        ],
        "forbiddenResourceTypes": ["loadBalancer"],
    })
    assert score["resourceTypes"]["f1"] == 1.0
    assert score["dependencyTypes"]["f1"] == 1.0
    assert score["forbiddenResourceTypes"] == []


def test_jacoco_branch_and_complexity_coverage_are_read_quantitatively(tmp_path):
    for name, content in {
        "src/main/App.java": "class App { int value() { return 1; } }",
        "src/test/AppTest.java": "class AppTest {}",
        "build.gradle": "plugins {}",
        "Dockerfile": "FROM eclipse-temurin:21",
        "main.tf": 'resource "aws_instance" "app" {}',
        "build/reports/jacoco/test/jacocoTestReport.xml": """
<report name="demo">
  <counter type="LINE" missed="2" covered="8"/>
  <counter type="BRANCH" missed="1" covered="3"/>
  <counter type="COMPLEXITY" missed="1" covered="2"/>
</report>
""",
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    result = evaluate_repository(tmp_path)

    coverage = result["codeQuality"]["coverage"]
    assert coverage["status"] == "available"
    assert coverage["counters"]["line"]["ratio"] == 0.8
    assert coverage["counters"]["branch"]["ratio"] == 0.75
    assert coverage["counters"]["complexity"]["ratio"] == 0.666667


def test_external_tool_graph_is_required_for_experiment_score(tmp_path, monkeypatch):
    for name, content in {
        "src/main/App.java": "class App { int value() { return 1; } }",
        "src/test/AppTest.java": "class AppTest {}",
        "build.gradle": "plugins {}",
        "Dockerfile": "FROM eclipse-temurin:21",
        "main.tf": 'resource "aws_instance" "app" {}',
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    graph = normalize_tool_graph(
        '"[root] aws_instance.app (expand)" [label = "aws_instance.app"]',
        ".",
    )
    monkeypatch.setattr(implementation, "run_iac_tools", lambda _root: {
        "iacEngine": {
            "status": "passed",
            "modules": [{"normalizedGraph": graph}],
        },
        "trivy": {"status": "unavailable"},
    })
    monkeypatch.setattr(
        implementation, "run_container_tools", lambda *_args: {"status": "passed"}
    )

    result = evaluate_repository(
        tmp_path,
        {"requiredResourceTypes": ["vm"], "requiredDependencyTypes": []},
        run_tools=True,
    )

    assert result["experimentEligible"] is True
    assert result["score"]["resourceTypes"]["f1"] == 1.0


def test_explicit_tool_path_works_without_path_lookup(tmp_path, monkeypatch):
    executable = tmp_path / "tofu.exe"
    executable.write_text("placeholder", encoding="utf-8")
    monkeypatch.setenv("EVALUATION_TOFU_PATH", str(executable))
    monkeypatch.setattr(implementation.shutil, "which", lambda _name: None)

    assert implementation._tool_path("tofu", "EVALUATION_TOFU_PATH") == str(
        executable.resolve()
    )


def test_container_check_reports_unavailable_daemon_without_building(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    monkeypatch.setattr(implementation, "_tool_path", lambda *_args: "docker")
    calls = []

    def command(arguments, _cwd, timeout=180):
        calls.append((arguments, timeout))
        return {"status": "failed", "stderr": "daemon unavailable"}

    monkeypatch.setattr(implementation, "_command", command)

    result = implementation.run_container_tools(tmp_path)

    assert result["status"] == "unavailable"
    assert calls == [(["docker", "info", "--format", "{{json .ServerVersion}}"], 180)]


def test_container_diagnostics_are_captured_before_cleanup(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    monkeypatch.setattr(implementation, "_tool_path", lambda *_args: "docker")
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(implementation.time, "monotonic", lambda: next(ticks))
    calls = []

    def command(arguments, _cwd, timeout=180):
        calls.append(arguments)
        if arguments[1] == "run":
            return {"status": "passed", "stdout": "container-id\n", "stderr": ""}
        if arguments[1] == "port":
            return {"status": "passed", "stdout": "127.0.0.1:12345\n", "stderr": ""}
        return {"status": "passed", "stdout": "diagnostic", "stderr": ""}

    monkeypatch.setattr(implementation, "_command", command)

    result = implementation.run_container_tools(tmp_path)

    assert result["health"]["status"] == "failed"
    assert result["containerInspect"]["stdout"] == "diagnostic"
    assert result["containerLogs"]["stdout"] == "diagnostic"
    assert [call[1] for call in calls[-4:]] == ["inspect", "logs", "rm", "image"]


def test_open_firewall_port_443_is_not_mistaken_for_https(tmp_path):
    (tmp_path / "main.tf").write_text(
        '''resource "aws_security_group" "web" {
  ingress { from_port = 443 to_port = 443 protocol = "tcp" }
}
resource "aws_instance" "app" {}
''',
        encoding="utf-8",
    )
    script = tmp_path / "cloud-init.sh"
    script.write_text("docker run -p 8080:8080 example/app", encoding="utf-8")

    result = analyze_terraform_semantics(tmp_path)

    assert result["capabilities"]["publicHttps"] is False


def test_https_listener_counts_as_real_tls_entry(tmp_path):
    (tmp_path / "main.tf").write_text(
        '''resource "aws_lb" "web" {}
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.web.arn
  port = 443
  protocol = "HTTPS"
  certificate_arn = "certificate-placeholder"
}
''',
        encoding="utf-8",
    )

    result = analyze_terraform_semantics(tmp_path)

    assert result["capabilities"]["publicHttps"] is True


def test_end_to_end_oracle_keeps_capabilities_separate_from_tool_graph():
    oracle = json.loads(
        Path("evaluation/baselines/cases/oracle.json").read_text(encoding="utf-8")
    )

    expected = resolve_oracle(oracle, "P2-gcp")

    assert expected["requiredCapabilities"]["persistentData"] is True
    assert expected["requiredCapabilities"]["dataDiskGiB"]["min"] == 20
    assert {item["to"] for item in expected["requiredDependencies"]} >= {"disk", "nic"}
    assert [item["name"] for item in expected["functionalAcceptance"]] == [
        "create note",
        "list notes",
    ]


def test_json_acceptance_matches_fragments_and_numeric_values():
    assert implementation._matches_json(
        {"id": "generated", "result": 100, "unit": "centimeter"},
        {"result": 100.0, "unit": "centimeter"},
    )
    assert implementation._matches_json(
        [{"id": 1, "title": "alpha", "content": "first"}],
        [{"title": "alpha", "content": "first"}],
    )
    assert not implementation._matches_json(
        {"result": None, "unit": "centimeter"},
        {"result": 100.0, "unit": "centimeter"},
    )
