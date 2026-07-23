from __future__ import annotations

import re
from pathlib import Path


def e2e_contract_violations(
    path: Path, contract: dict[str, object] | None = None
) -> list[str]:
    """Return semantic defects that make the generated E2E test non-evidentiary."""
    if not path.is_file():
        return ["E2E test file is missing"]

    source = path.read_text(encoding="utf-8")
    violations: list[str] = []
    required_groups: dict[str, tuple[str, ...]] = {
        "real HTTP client": ("TestRestTemplate", "MockMvc"),
    }
    if contract:
        for repository in contract.get("repositories", []):
            required_groups[f"repository evidence {repository}"] = (str(repository),)
        for gateway in contract.get("gatewayAdapters", []):
            required_groups[f"concrete gateway {gateway}"] = (str(gateway),)
    else:
        # Compatibility for runs planned before semantic contracts were added.
        required_groups.update({
            "concrete deterministic trading gateway": ("InMemoryTradingSiteGatewayAdapter",),
            "gateway success seam": ("enqueueOutcome",),
            "gateway rejection seam": ("rejectSite",),
            "purchase persistence evidence": ("PurchaseRecordRepository",),
            "holding persistence evidence": ("HoldingRepository",),
            "success branch": ("completed",),
            "delayed branch": ("delayed",),
            "missing-information branch": ("missing_information",),
            "clarification request": ("clarification",),
            "portfolio response": ("/portfolio",),
        })
    for label, alternatives in required_groups.items():
        if not any(token in source for token in alternatives):
            violations.append(f"Missing {label}: {' or '.join(alternatives)}")

    if contract:
        for expected_path in contract.get("paths", []):
            pattern = re.escape(str(expected_path))
            pattern = re.sub(r"\\\{[^}]+\\\}", r'[^"\\s]+', pattern)
            if not re.search(pattern, source):
                violations.append(f"Missing HTTP path evidence: {expected_path}")
        for expected_status in contract.get("statuses", []):
            status = str(expected_status)
            assertion = re.compile(
                rf"(?im)^.*(?:assert|expect|status).*\b{re.escape(status)}\b.*$"
            )
            if not assertion.search(source):
                violations.append(f"Missing asserted HTTP status: {status}")

    test_count = len(re.findall(r"(?m)^\s*@Test\b", source))
    minimum_tests = int(contract.get("minimumTests", 1)) if contract else 4
    if test_count < minimum_tests:
        violations.append(
            f"Expected at least {minimum_tests} independent E2E scenarios, found {test_count}"
        )

    forbidden = {
        "test bean configuration": ("@TestConfiguration", "@MockBean", "@MockitoBean"),
        "reflection-based gateway access": ("java.lang.reflect", ".getMethod("),
        "weak dual-outcome assertion": (
            "accept both",
            "may be null",
            "no strict assertion",
            "simplified integration test",
        ),
        "disabled test": ("@Disabled",),
    }
    lowered = source.lower()
    for label, tokens in forbidden.items():
        found = [token for token in tokens if token.lower() in lowered]
        if found:
            violations.append(f"Forbidden {label}: {', '.join(found)}")
    return violations


def deployment_contract_violations(run_root: Path, paths: list[str]) -> list[str]:
    """Return structural defects in the bounded Kubernetes deployment output set."""
    required = {
        "application/Dockerfile": ("FROM ", "EXPOSE 8000", "USER "),
        "application/k8s/namespace.yaml": ("apiVersion:", "kind: Namespace", "metadata:"),
        "application/k8s/deployment.yaml": ("apiVersion: apps/v1", "kind: Deployment", "metadata:", "readinessProbe:", "livenessProbe:"),
        "application/k8s/service.yaml": ("apiVersion: v1", "kind: Service", "metadata:", "type: LoadBalancer"),
        "application/k8s/hpa.yaml": ("apiVersion: autoscaling/v2", "kind: HorizontalPodAutoscaler", "metadata:"),
        "application/k8s/secret.example.yaml": ("apiVersion: v1", "kind: Secret", "metadata:", "DB_PASSWORD"),
    }
    violations: list[str] = []
    for relative in paths:
        path = run_root / relative
        if not path.is_file():
            violations.append(f"Deployment output is missing: {relative}")
            continue
        source = path.read_text(encoding="utf-8")
        for token in required.get(relative, ("apiVersion:", "kind:", "metadata:")):
            if token not in source:
                violations.append(f"{relative} is missing required content: {token}")
        if relative.endswith("secret.example.yaml") and re.search(
            r"(?im)^\s*(?:DB_PASSWORD|API_KEY)\s*:\s*(?!['\"]?(?:<|\$|\}|$))[^#\s]+",
            source,
        ):
            violations.append(f"{relative} appears to contain a non-placeholder secret value")
    return violations
