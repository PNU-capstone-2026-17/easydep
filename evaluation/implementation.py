"""Method-neutral evaluation of a generated Docker-on-VM implementation.

The evaluator reads final repository artifacts. It does not consume EasyDep plans or
deployment diagrams. Terraform references are treated as the implementation's declared
resource dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.cloudkb.depkb.provider_cache import (
    provider_cache_environment,
    provider_mirror_configuration,
)
from evaluation.component_projection import derive_component_dependency_expectations
from evaluation.terraform_semantics import analyze_terraform_semantics, score_semantics

SOURCE_SUFFIXES = (".java", ".kt", ".py", ".ts", ".js", ".go", ".rs")
GENERATED_ARTIFACT_SUFFIXES = SOURCE_SUFFIXES + (
    ".gradle",
    ".kts",
    ".xml",
    ".tf",
    ".yml",
    ".yaml",
)

EVALUATOR_SCHEMA = "easydep-implementation-evaluation/v1"


def write_evaluation(path: Path, result: dict[str, Any]) -> Path | None:
    """Write the current evaluation while preserving any previous result verbatim."""
    history: Path | None = None
    if path.is_file():
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        history = path.with_name(f"{path.stem}.{timestamp}.{uuid.uuid4().hex[:6]}{path.suffix}")
        history.write_bytes(path.read_bytes())
    value = dict(result)
    value.setdefault(
        "evaluationMetadata",
        {
            "schema": EVALUATOR_SCHEMA,
            "evaluatedAt": datetime.now(UTC).isoformat(),
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return history


RESOURCE_TYPES = {
    # AWS
    "aws_vpc": "network",
    "aws_subnet": "subnet",
    "aws_security_group": "firewall",
    "aws_instance": "vm",
    "aws_ebs_volume": "disk",
    "aws_volume_attachment": "diskAttachment",
    "aws_lb": "loadBalancer",
    "aws_alb": "loadBalancer",
    "aws_lb_target_group": "backendPool",
    "aws_lb_target_group_attachment": "backendAttachment",
    "aws_lb_listener": "listener",
    "aws_eip": "publicIp",
    "aws_internet_gateway": "internetGateway",
    # Azure
    "azurerm_virtual_network": "network",
    "azurerm_subnet": "subnet",
    "azurerm_network_security_group": "firewall",
    "azurerm_linux_virtual_machine": "vm",
    "azurerm_managed_disk": "disk",
    "azurerm_virtual_machine_data_disk_attachment": "diskAttachment",
    "azurerm_lb": "loadBalancer",
    "azurerm_lb_backend_address_pool": "backendPool",
    "azurerm_network_interface_backend_address_pool_association": "backendAttachment",
    "azurerm_lb_rule": "listener",
    "azurerm_public_ip": "publicIp",
    "azurerm_network_interface": "networkInterface",
    # GCP
    "google_compute_network": "network",
    "google_compute_subnetwork": "subnet",
    "google_compute_firewall": "firewall",
    "google_compute_instance": "vm",
    "google_compute_disk": "disk",
    "google_compute_attached_disk": "diskAttachment",
    "google_compute_forwarding_rule": "loadBalancer",
    "google_compute_global_forwarding_rule": "loadBalancer",
    "google_compute_backend_service": "backendPool",
    "google_compute_region_backend_service": "backendPool",
    "google_compute_instance_group": "backendAttachment",
    "google_compute_health_check": "healthCheck",
    "google_compute_address": "publicIp",
    "google_compute_global_address": "publicIp",
}

DOT_LABEL = re.compile(r'^\s*"(?P<node>[^"]+)"\s+\[label\s*=\s*"(?P<label>[^"]+)"')
DOT_EDGE = re.compile(r'^\s*"(?P<from>[^"]+)"\s*->\s*"(?P<to>[^"]+)"')
ADDRESS = re.compile(r"(?P<type>(?:aws|azurerm|google)_[A-Za-z0-9_]+)\.(?P<name>[A-Za-z0-9_-]+)")


def _files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(
            part in {".git", ".gradle", "node_modules", "build", "logs"} for part in path.parts
        )
    ]


def inspect_repository(root: Path) -> dict[str, Any]:
    files = _files(root)
    relative = [path.relative_to(root).as_posix().lower() for path in files]

    def contains(*tokens: str) -> bool:
        return any(all(token in name for token in tokens) for name in relative)

    markdown_contaminated: list[str] = []
    for path in files:
        if (
            path.suffix.lower() not in GENERATED_ARTIFACT_SUFFIXES
            and path.name.lower() != "dockerfile"
        ):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        nonblank = [line.strip() for line in lines if line.strip()]
        if nonblank and (
            nonblank[0].startswith("```")
            or re.match(r"^#{2,6}\s+\S", nonblank[0])
            or any(line.startswith("```") for line in nonblank)
        ):
            markdown_contaminated.append(path.relative_to(root).as_posix())

    checks = {
        "source_present": any(name.endswith(SOURCE_SUFFIXES) for name in relative),
        "test_present": any("test" in name and name.endswith(SOURCE_SUFFIXES) for name in relative),
        "build_present": contains("build.gradle") or contains("pom.xml"),
        "dockerfile_present": any(name.endswith("dockerfile") for name in relative),
        "iac_present": any(name.endswith(".tf") for name in relative),
        "generated_files_clean": not markdown_contaminated,
        "deployment_manifest_present": any(
            name.endswith(("compose.yml", "compose.yaml")) or "cloud-init" in name
            for name in relative
        ),
        # These are descriptive only and never gate the implementation score.
        "requirements_documented": contains("requirement") or contains("prd"),
        "design_documented": contains("design") or contains("architecture"),
        "deployment_diagram_present": any(
            "deploy" in name and name.endswith((".mmd", ".puml")) for name in relative
        ),
        "traceability_present": any("trace" in name or "rtm" in name for name in relative),
    }
    required = (
        "source_present",
        "test_present",
        "build_present",
        "dockerfile_present",
        "iac_present",
        "generated_files_clean",
    )
    return {
        "root": str(root.resolve()),
        "fileCount": len(files),
        "javaFileCount": sum(name.endswith(".java") for name in relative),
        "testFileCount": sum(
            "test" in name and name.endswith(SOURCE_SUFFIXES) for name in relative
        ),
        "markdownContaminatedFiles": markdown_contaminated,
        "checks": checks,
        "implementationComplete": all(checks[name] for name in required),
        # Compatibility aliases for older result readers.
        "requiredPassed": all(checks[name] for name in required),
        "cloudNativePassed": checks["dockerfile_present"] and checks["iac_present"],
    }


def normalize_tool_graph(dot: str, source: str) -> dict[str, Any]:
    """Normalize resource nodes and dependency edges from OpenTofu/Terraform DOT."""
    labels: dict[str, str] = {}
    raw_edges: list[tuple[str, str]] = []
    for line in dot.splitlines():
        label_match = DOT_LABEL.match(line)
        if label_match:
            address = ADDRESS.search(label_match.group("label"))
            if address:
                labels[label_match.group("node")] = (
                    f"{address.group('type')}.{address.group('name')}"
                )
        edge_match = DOT_EDGE.match(line)
        if edge_match:
            raw_edges.append((edge_match.group("from"), edge_match.group("to")))
    identities = sorted(set(labels.values()))
    node_types = {
        identity: RESOURCE_TYPES.get(identity.split(".", 1)[0], "unknown")
        for identity in identities
    }
    edges = sorted(
        {
            (labels[left], labels[right])
            for left, right in raw_edges
            if left in labels and right in labels and labels[left] != labels[right]
        }
    )
    return {
        "nodes": [
            {
                "id": identity,
                "type": node_types[identity],
                "providerType": identity.split(".", 1)[0],
                "source": source,
            }
            for identity in identities
        ],
        "edges": [
            {
                "from": left,
                "to": right,
                "fromType": node_types[left],
                "toType": node_types[right],
            }
            for left, right in edges
        ],
        "parseErrors": [],
        "unknownProviderTypes": sorted(
            {
                identity.split(".", 1)[0]
                for identity in identities
                if node_types[identity] == "unknown"
            }
        ),
        "extractionMethod": "opentofu-or-terraform-graph",
    }


def _metric(actual: set[Any], expected: set[Any]) -> dict[str, float | int]:
    true_positive = len(actual & expected)
    precision = true_positive / len(actual) if actual else (1.0 if not expected else 0.0)
    recall = true_positive / len(expected) if expected else (1.0 if not actual else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "truePositive": true_positive,
        "predicted": len(actual),
        "expected": len(expected),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def score_graph(graph: dict[str, Any], oracle: dict[str, Any]) -> dict[str, Any]:
    actual_nodes = {node["type"] for node in graph["nodes"] if node["type"] != "unknown"}
    actual_edges = {(edge["fromType"], edge["toType"]) for edge in graph["edges"]}
    expected_nodes = {str(item) for item in oracle.get("requiredResourceTypes", [])}
    expected_edges = {
        (str(item["from"]), str(item["to"])) for item in oracle.get("requiredDependencyTypes", [])
    }
    forbidden = {str(item) for item in oracle.get("forbiddenResourceTypes", [])}
    return {
        "resourceTypes": _metric(actual_nodes, expected_nodes),
        "dependencyTypes": _metric(actual_edges, expected_edges),
        "forbiddenResourceTypes": sorted(actual_nodes & forbidden),
    }


def resolve_oracle(oracle: dict[str, Any], case_id: str | None) -> dict[str, Any]:
    if oracle.get("schemaVersion") != "easydep-end-to-end-oracle/v1":
        return oracle
    if not case_id:
        raise ValueError("--case-id is required for the end-to-end oracle")
    case = oracle.get("cases", {}).get(case_id)
    if not isinstance(case, dict):
        raise KeyError(f"case is absent from oracle: {case_id}")
    profile_id = str(case["profile"])
    provider_id = str(case["provider"])
    profile = oracle["profiles"][profile_id]
    provider = (oracle.get("providers") or {}).get(provider_id) or {}
    resolved = {
        "schemaVersion": oracle["schemaVersion"],
        "caseId": case_id,
        "profile": profile_id,
        "provider": provider_id,
        "budgetUsd": case["budgetUsd"],
        "requiredCapabilities": profile["requiredCapabilities"],
        "functionalAcceptance": profile.get("functionalAcceptance", []),
        "persistenceAcceptance": profile.get("persistenceAcceptance"),
        "forbiddenConcepts": profile["forbiddenConcepts"],
        "requiredDependencies": (provider.get("requiredDependencies") or {}).get(profile_id, []),
    }
    for key in ("componentDelta", "componentDeltas", "legacyProviderProjection"):
        if key in profile:
            resolved[key] = profile[key]
    delta_ids = resolved.get("componentDeltas") or (
        [resolved["componentDelta"]] if resolved.get("componentDelta") else []
    )
    resolved["componentDependencyExpectations"] = derive_component_dependency_expectations(
        provider_id, delta_ids
    )
    return resolved


def _percentile(values: list[int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 6)


def _coverage(root: Path) -> dict[str, Any]:
    candidates = sorted(root.rglob("jacocoTestReport.xml")) + sorted(root.rglob("jacoco.xml"))
    if not candidates:
        return {"status": "unavailable", "reason": "JaCoCo XML report not found"}
    path = candidates[0]
    report = ET.parse(path).getroot()  # noqa: S314 - local generated evaluation artifact
    counters: dict[str, dict[str, float | int]] = {}
    for counter in report.findall("counter"):
        name = str(counter.attrib.get("type", "")).lower()
        missed = int(counter.attrib.get("missed", 0))
        covered = int(counter.attrib.get("covered", 0))
        total = missed + covered
        counters[name] = {
            "missed": missed,
            "covered": covered,
            "total": total,
            "ratio": round(covered / total, 6) if total else 1.0,
        }
    return {
        "status": "available",
        "source": path.relative_to(root).as_posix(),
        "counters": counters,
    }


def analyze_code_quality(root: Path) -> dict[str, Any]:
    """Measure production-code complexity with Lizard and consume JaCoCo output."""
    try:
        import lizard
    except ImportError:
        complexity: dict[str, Any] = {
            "status": "unavailable",
            "tool": "lizard",
            "reason": "install requirements-dev.txt",
        }
    else:
        functions: list[Any] = []
        total_nloc = 0
        analyzed_files = 0
        for path in _files(root):
            relative_parts = {part.lower() for part in path.relative_to(root).parts}
            if (
                path.suffix.lower() not in SOURCE_SUFFIXES
                or relative_parts & {"test", "tests"}
                or "src/test" in path.relative_to(root).as_posix().lower()
            ):
                continue
            analysis = lizard.analyze_file(str(path))
            analyzed_files += 1
            total_nloc += int(analysis.nloc)
            functions.extend(analysis.function_list)
        complexities = [int(item.cyclomatic_complexity) for item in functions]
        function_nloc = [int(item.nloc) for item in functions]
        decision_points = sum(max(value - 1, 0) for value in complexities)
        high_complexity = sum(value > 10 for value in complexities)
        complexity = {
            "status": "available",
            "tool": "lizard",
            "version": getattr(lizard, "__version__", getattr(lizard, "version", None)),
            "fileCount": analyzed_files,
            "functionCount": len(functions),
            "nloc": total_nloc,
            "cyclomaticComplexity": {
                "mean": round(sum(complexities) / len(complexities), 6) if complexities else 0.0,
                "median": _percentile(complexities, 0.5),
                "p95": _percentile(complexities, 0.95),
                "max": max(complexities, default=0),
                "functionsAbove10": high_complexity,
                "functionsAbove10Ratio": (
                    round(high_complexity / len(complexities), 6) if complexities else 0.0
                ),
            },
            "functionNloc": {
                "mean": round(sum(function_nloc) / len(function_nloc), 6) if function_nloc else 0.0,
                "p95": _percentile(function_nloc, 0.95),
                "max": max(function_nloc, default=0),
            },
            "decisionPointDensityPer100Nloc": (
                round(decision_points * 100 / total_nloc, 6) if total_nloc else 0.0
            ),
        }
    return {
        "complexity": complexity,
        "coverage": _coverage(root),
        "interpretation": (
            "Complexity is reported as a distribution, not a composite quality score. "
            "Coverage is measured only when a JaCoCo XML artifact exists."
        ),
    }


def _command(
    command: list[str],
    cwd: Path,
    timeout: int = 180,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        return {"status": "timeout", "command": command, "seconds": timeout, "error": str(exc)}
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "exitCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _terraform_roots(root: Path) -> list[Path]:
    return sorted(
        {
            path.parent
            for path in root.rglob("*.tf")
            if ".terraform" not in path.parts and ".git" not in path.parts
        }
    )


def _tool_path(name: str, environment_name: str) -> str | None:
    configured = os.getenv(environment_name)
    if configured:
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_file() else None
    return shutil.which(name)


def _iac_engine_result(modules: list[dict[str, Any]]) -> tuple[str, bool]:
    """Separate deployability from non-semantic formatting compliance."""
    format_compliant = bool(modules) and all(
        item["format"]["status"] == "passed" for item in modules
    )
    deployable = bool(modules) and all(
        item["initialize"]["status"] == "passed"
        and item["validate"]["status"] == "passed"
        and bool((item["validate"].get("json") or {}).get("valid"))
        and item["graph"]["status"] == "passed"
        for item in modules
    )
    return ("passed" if deployable else "failed", format_compliant)


def run_iac_tools(root: Path) -> dict[str, Any]:
    """Run independent open-source/standard IaC tools in an isolated copy.

    OpenTofu is preferred because it is open source and Terraform-compatible. Terraform
    is accepted as a fallback. Trivy is optional and provides security/misconfiguration
    findings rather than dependency ground truth.
    """
    tofu = _tool_path("tofu", "EVALUATION_TOFU_PATH")
    terraform = _tool_path("terraform", "EVALUATION_TERRAFORM_PATH")
    trivy = _tool_path("trivy", "EVALUATION_TRIVY_PATH")
    executable = tofu or terraform
    result: dict[str, Any] = {
        "iacEngine": {
            "status": "unavailable" if executable is None else "pending",
            "tool": "opentofu" if tofu else ("terraform" if terraform else None),
            "modules": [],
        },
        "trivy": {"status": "unavailable", "tool": "trivy"},
    }
    if executable:
        result["iacEngine"]["version"] = _command([executable, "version", "-json"], root)
        evaluation_temp = Path(".easydep/evaluation-temp")
        evaluation_temp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="easydep-iac-eval-", dir=evaluation_temp.resolve()
        ) as directory:
            cli_config = Path(directory) / "tofu.rc"
            cli_config.write_text(provider_mirror_configuration(), encoding="utf-8")
            provider_environment = provider_cache_environment()
            # 동일 경로를 mirror와 plugin cache로 함께 지정하면 OpenTofu가 provider를
            # 자기 자신에게 복사하려 한다. 평가에서는 읽기 전용 mirror로만 사용한다.
            provider_environment.pop("TF_PLUGIN_CACHE_DIR", None)
            provider_environment["TF_CLI_CONFIG_FILE"] = str(cli_config.resolve())
            copy = Path(directory) / "repository"
            shutil.copytree(
                root,
                copy,
                ignore=shutil.ignore_patterns(".git", ".terraform", "build", ".gradle"),
            )
            modules: list[dict[str, Any]] = []
            for module in _terraform_roots(copy):
                relative = module.relative_to(copy).as_posix() or "."
                fmt = _command([executable, "fmt", "-check", "-recursive", "-no-color"], module)
                init = _command(
                    [executable, "init", "-backend=false", "-input=false", "-no-color"],
                    module,
                    environment=provider_environment,
                )
                validate = (
                    _command(
                        [executable, "validate", "-json", "-no-color"],
                        module,
                        environment=provider_environment,
                    )
                    if init["status"] == "passed"
                    else {"status": "not-run", "reason": "initialization failed"}
                )
                if validate.get("stdout"):
                    try:
                        validate["json"] = json.loads(validate["stdout"])
                    except json.JSONDecodeError:
                        validate["json"] = None
                graph = (
                    _command(
                        [executable, "graph", "-type=plan"],
                        module,
                        environment=provider_environment,
                    )
                    if init["status"] == "passed"
                    else {"status": "not-run", "reason": "initialization failed"}
                )
                normalized_graph = (
                    normalize_tool_graph(str(graph.get("stdout", "")), relative)
                    if graph["status"] == "passed"
                    else None
                )
                modules.append(
                    {
                        "path": relative,
                        "format": fmt,
                        "initialize": init,
                        "validate": validate,
                        "graph": graph,
                        "normalizedGraph": normalized_graph,
                    }
                )
            result["iacEngine"]["modules"] = modules
            status, format_compliant = _iac_engine_result(modules)
            result["iacEngine"]["status"] = status
            result["iacEngine"]["formatCompliant"] = format_compliant
    if trivy:
        version = _command([trivy, "--version"], root)
        scan = _command(
            [
                trivy,
                "config",
                "--format",
                "json",
                "--scanners",
                "misconfig",
                "--skip-dirs",
                ".terraform",
                str(root),
            ],
            root,
            timeout=300,
        )
        if scan.get("stdout"):
            try:
                payload = json.loads(scan["stdout"])
                failures = sum(
                    len(item.get("Misconfigurations") or [])
                    for item in payload.get("Results") or []
                )
                scan["summary"] = {"misconfigurationCount": failures}
            except json.JSONDecodeError:
                scan["summary"] = None
        result["trivy"] = scan
        result["trivy"]["version"] = version
    return result


def _matches_json(actual: Any, expected: Any, *, tolerance: float = 1e-6) -> bool:
    """Return whether actual contains the method-neutral expected JSON fragment."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _matches_json(actual[key], value, tolerance=tolerance)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _matches_json(left, right, tolerance=tolerance)
                for left, right in zip(actual, expected, strict=True)
            )
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and abs(float(actual) - float(expected)) <= tolerance
        )
    return actual == expected


def _run_http_acceptance(base_url: str, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for scenario in scenarios:
        method = str(scenario.get("method", "GET")).upper()
        body = scenario.get("json")
        request = urllib.request.Request(
            urllib.parse.urljoin(base_url + "/", str(scenario["path"]).lstrip("/")),
            data=(json.dumps(body).encode("utf-8") if body is not None else None),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method=method,
        )
        check: dict[str, Any] = {"name": scenario["name"], "status": "failed"}
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
                actual = json.loads(raw) if raw else None
                check.update({"httpStatus": response.status, "actual": actual})
                expected_status = int(scenario.get("status", 200))
                expected_json = scenario.get("expectJson")
                if response.status == expected_status and (
                    "expectJson" not in scenario
                    or _matches_json(
                        actual,
                        expected_json,
                        tolerance=float(scenario.get("numericTolerance", 1e-6)),
                    )
                ):
                    check["status"] = "passed"
        except urllib.error.HTTPError as error:
            check.update({"httpStatus": error.code, "error": str(error)})
        except (OSError, ValueError, urllib.error.URLError) as error:
            check["error"] = f"{type(error).__name__}: {error}"
        checks.append(check)
    return {
        "status": "passed"
        if checks and all(item["status"] == "passed" for item in checks)
        else "failed",
        "passed": sum(item["status"] == "passed" for item in checks),
        "total": len(checks),
        "checks": checks,
    }


def _wait_for_container_health(
    docker: str, root: Path, container_id: str, *, timeout_seconds: int = 60
) -> dict[str, Any]:
    port = _command([docker, "port", container_id, "8080/tcp"], root)
    match = re.search(r"127\.0\.0\.1:(\d+)", str(port.get("stdout", "")))
    if port["status"] != "passed" or not match:
        return {"status": "failed", "port": port, "reason": "published port not found"}
    base_url = f"http://127.0.0.1:{match.group(1)}"
    url = f"{base_url}/health"
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_error = ""
    while time.monotonic() < deadline:
        attempts += 1
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
                if 200 <= response.status < 400:
                    return {
                        "status": "passed",
                        "url": url,
                        "baseUrl": base_url,
                        "httpStatus": response.status,
                        "attempts": attempts,
                        "port": port,
                    }
        except (OSError, urllib.error.URLError) as error:
            last_error = str(error)
        time.sleep(2)
    return {
        "status": "failed",
        "url": url,
        "attempts": attempts,
        "error": last_error,
        "port": port,
    }


def _run_persistence_acceptance(
    docker: str,
    root: Path,
    tag: str,
    contract: dict[str, Any],
    cleanup_containers: list[str],
    application_port: int,
) -> tuple[dict[str, Any], str]:
    """Write into a named volume, recreate the container, and verify the data."""
    volume = f"easydep-evaluation-{uuid.uuid4().hex}"
    mount_path = str(contract.get("mountPath") or "").strip()
    result: dict[str, Any] = {"status": "failed", "volume": volume, "mountPath": mount_path}
    if not mount_path.startswith("/"):
        result["reason"] = "persistenceAcceptance.mountPath must be an absolute path"
        return result, volume
    created = _command([docker, "volume", "create", volume], root)
    result["createVolume"] = created
    if created["status"] != "passed":
        return result, volume

    def start() -> tuple[str, dict[str, Any]]:
        run = _command(
            [
                docker,
                "run",
                "--detach",
                "--publish",
                f"127.0.0.1::{application_port}",
                "--label",
                "easydep.evaluation=true",
                "--mount",
                f"type=volume,source={volume},target={mount_path}",
                tag,
            ],
            root,
        )
        container = str(run.get("stdout", "")).strip() if run["status"] == "passed" else ""
        if container:
            cleanup_containers.append(container)
        return container, run

    first_id, first_run = start()
    result["beforeRestartRun"] = first_run
    if not first_id:
        return result, volume
    before_health = _wait_for_container_health(docker, root, first_id)
    result["beforeRestartHealth"] = before_health
    if before_health["status"] != "passed":
        return result, volume
    before = _run_http_acceptance(
        str(before_health["baseUrl"]), list(contract.get("beforeRestart") or [])
    )
    result["beforeRestart"] = before
    if before["status"] != "passed":
        return result, volume
    stopped = _command([docker, "stop", "--time", "30", first_id], root, timeout=45)
    result["stopBeforeRestart"] = stopped
    if stopped["status"] != "passed":
        return result, volume
    removed = _command([docker, "rm", first_id], root)
    result["removeBeforeRestart"] = removed
    if removed["status"] != "passed":
        return result, volume
    cleanup_containers.remove(first_id)

    second_id, second_run = start()
    result["afterRestartRun"] = second_run
    if not second_id:
        return result, volume
    after_health = _wait_for_container_health(docker, root, second_id)
    result["afterRestartHealth"] = after_health
    if after_health["status"] != "passed":
        return result, volume
    after = _run_http_acceptance(
        str(after_health["baseUrl"]), list(contract.get("afterRestart") or [])
    )
    result["afterRestart"] = after
    result["status"] = "passed" if after["status"] == "passed" else "failed"
    return result, volume


def run_container_tools(
    root: Path,
    acceptance: list[dict[str, Any]] | None = None,
    persistence: dict[str, Any] | None = None,
    application_port: int | None = None,
) -> dict[str, Any]:
    """Build, start, health-check, and black-box test the generated container."""
    if not isinstance(application_port, int) or isinstance(application_port, bool):
        return {
            "status": "not-configured",
            "reason": "requiredCapabilities.applicationPort is required for container evaluation",
        }
    if not 1 <= application_port <= 65_535:
        return {
            "status": "failed",
            "reason": "requiredCapabilities.applicationPort is outside 1..65535",
        }
    docker = _tool_path("docker", "EVALUATION_DOCKER_PATH")
    if not docker:
        return {"status": "unavailable", "tool": "docker", "reason": "Docker CLI not found"}
    if not (root / "Dockerfile").is_file():
        return {"status": "failed", "tool": "docker", "reason": "Dockerfile not found"}
    daemon = _command([docker, "info", "--format", "{{json .ServerVersion}}"], root)
    if daemon["status"] != "passed":
        return {"status": "unavailable", "tool": "docker", "daemon": daemon}
    tag = f"easydep-evaluation:{uuid.uuid4().hex}"
    build = _command([docker, "build", "--tag", tag, "."], root, timeout=900)
    result: dict[str, Any] = {"status": "failed", "tool": "docker", "build": build}
    container_id = ""
    cleanup_containers: list[str] = []
    cleanup_volume = ""
    try:
        if build["status"] != "passed":
            return result
        run = _command(
            [
                docker,
                "run",
                "--detach",
                "--publish",
                f"127.0.0.1::{application_port}",
                "--label",
                "easydep.evaluation=true",
                tag,
            ],
            root,
        )
        result["run"] = run
        if run["status"] != "passed":
            return result
        container_id = str(run.get("stdout", "")).strip()
        cleanup_containers.append(container_id)
        health = _wait_for_container_health(docker, root, container_id)
        result["port"] = health.get("port")
        result["health"] = health
        if health["status"] != "passed":
            return result
        acceptance_result = (
            _run_http_acceptance(str(health["baseUrl"]), acceptance)
            if acceptance
            else {"status": "not-configured", "checks": []}
        )
        result["acceptance"] = acceptance_result
        if acceptance_result["status"] not in {"passed", "not-configured"}:
            return result
        if persistence:
            result["cleanupPrimaryContainer"] = _command(
                [docker, "rm", "--force", container_id], root
            )
            if result["cleanupPrimaryContainer"]["status"] != "passed":
                return result
            cleanup_containers.remove(container_id)
            container_id = ""
            persistence_result, cleanup_volume = _run_persistence_acceptance(
                docker,
                root,
                tag,
                persistence,
                cleanup_containers,
                application_port,
            )
            result["persistenceAcceptance"] = persistence_result
            result["status"] = persistence_result["status"]
            return result
        result["persistenceAcceptance"] = {"status": "not-configured"}
        result["status"] = "passed"
        return result
    finally:
        for active_container in list(cleanup_containers):
            result["containerInspect"] = _command([docker, "inspect", active_container], root)
            result["containerLogs"] = _command([docker, "logs", active_container], root)
            result["cleanupContainer"] = _command([docker, "rm", "--force", active_container], root)
        if cleanup_volume:
            result["cleanupVolume"] = _command([docker, "volume", "rm", cleanup_volume], root)
        if build["status"] == "passed":
            result["cleanupImage"] = _command([docker, "image", "rm", tag], root)


def evaluate_repository(
    root: Path,
    oracle: dict[str, Any] | None = None,
    run_tools: bool = False,
    case_id: str | None = None,
) -> dict[str, Any]:
    structure = inspect_repository(root)
    expected = resolve_oracle(oracle, case_id) if oracle is not None else None
    persistence = (expected or {}).get("persistenceAcceptance") or {}
    terraform_semantics = analyze_terraform_semantics(
        root, expected_mount_path=persistence.get("mountPath")
    )
    graph: dict[str, Any] = {
        "status": "not-run",
        "nodes": [],
        "edges": [],
        "parseErrors": [],
        "unknownProviderTypes": [],
        "extractionMethod": None,
        "reason": "OpenTofu/Terraform graph was not run",
    }
    external_tools = (
        run_iac_tools(root)
        if run_tools
        else {"status": "not-run", "reason": "enable explicitly with --run-tools"}
    )
    if run_tools:
        required_capabilities = (expected or {}).get("requiredCapabilities") or {}
        external_tools["container"] = run_container_tools(
            root,
            (expected or {}).get("functionalAcceptance"),
            (expected or {}).get("persistenceAcceptance"),
            required_capabilities.get("applicationPort"),
        )
    if run_tools:
        tool_graphs = [
            module.get("normalizedGraph")
            for module in external_tools.get("iacEngine", {}).get("modules", [])
            if (module.get("normalizedGraph") or {}).get("nodes")
        ]
        if tool_graphs:
            graph = {
                "status": "available",
                "nodes": [node for item in tool_graphs for node in item["nodes"]],
                "edges": [edge for item in tool_graphs for edge in item["edges"]],
                "parseErrors": [],
                "unknownProviderTypes": sorted(
                    {
                        resource_type
                        for item in tool_graphs
                        for resource_type in item["unknownProviderTypes"]
                    }
                ),
                "extractionMethod": "opentofu-or-terraform-graph",
            }
    result: dict[str, Any] = {
        "schemaVersion": EVALUATOR_SCHEMA,
        "repository": structure,
        "resourceGraph": graph,
        "terraformSemantics": terraform_semantics,
        "codeQuality": analyze_code_quality(root),
        "staticValidationPassed": (structure["implementationComplete"]),
    }
    if oracle is not None:
        assert expected is not None
        result["expected"] = expected
        if expected.get("schemaVersion") == "easydep-end-to-end-oracle/v1":
            result["score"] = score_semantics(terraform_semantics, expected)
        else:
            result["score"] = (
                score_graph(graph, expected)
                if graph["status"] == "available"
                else {"status": "not-run", "reason": "resource graph is unavailable"}
            )
    result["externalTools"] = external_tools
    result["experimentEligible"] = bool(
        run_tools
        and result["staticValidationPassed"]
        and terraform_semantics["status"] == "available"
        and graph["status"] == "available"
        and external_tools.get("iacEngine", {}).get("status") == "passed"
        and external_tools.get("container", {}).get("status") == "passed"
        and result["codeQuality"]["complexity"]["status"] == "available"
        and (
            oracle is None
            or (expected or {}).get("schemaVersion")
            != "easydep-end-to-end-oracle/v1"
            or (
                (result.get("score") or {}).get("status") == "completed"
                and (result.get("score") or {}).get("failed") == 0
                and (result.get("score") or {}).get("unknown") == 0
            )
        )
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate final Docker-on-VM implementation artifacts"
    )
    parser.add_argument("repository", type=Path)
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-tools", action="store_true")
    args = parser.parse_args()
    oracle = json.loads(args.oracle.read_text(encoding="utf-8")) if args.oracle else None
    result = evaluate_repository(
        args.repository,
        oracle,
        run_tools=args.run_tools,
        case_id=args.case_id,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        write_evaluation(args.output, result)
    print(text, end="")


if __name__ == "__main__":
    main()
