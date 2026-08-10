"""Provider-neutral observations of deployment bindings in generated IaC."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

import hcl2

from app.core.orchestration.app_cloud_contracts import ConsistencyDiagnostic


@dataclass(frozen=True)
class Observation:
    kind: str
    value: int | str | None
    source: str
    confidence: str


_STRONG_PORT_KEYS = {"backend_port", "target_port", "container_port"}
_PORT_CONTEXT = {
    "backend",
    "backend_http_settings",
    "target_group",
    "health_check",
    "probe",
}

_VM_SIZE_FIELDS = {
    "aws": ("aws_instance", "instance_type"),
    "azure": ("azurerm_linux_virtual_machine", "size"),
    "gcp": ("google_compute_instance", "machine_type"),
}


def _walk(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, (*path, str(key).lower()))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, (*path, str(index)))
    else:
        yield path, value


def _literal_port(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 1 <= value <= 65535:
        return value
    if isinstance(value, str) and re.fullmatch(r'"?\d{1,5}"?', value.strip()):
        parsed = int(value.strip('"'))
        return parsed if 1 <= parsed <= 65535 else None
    return None


def _hcl_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return stripped


def validate_vm_selection_binding(
    files: dict[str, str], *, provider: str, expected_spec_name: str | None
) -> dict[str, Any]:
    """추천 VM 규격이 CSP VM 리소스 또는 변수 기본값에 반영됐는지 관측한다."""
    if not expected_spec_name:
        return {
            "status": "not-applicable",
            "expected": None,
            "observations": [],
            "diagnostics": [],
        }
    target = _VM_SIZE_FIELDS.get(provider)
    if target is None:
        return {
            "status": "failed",
            "expected": expected_spec_name,
            "observations": [],
            "diagnostics": [{
                "code": "BIND-VM-SIZE-001",
                "message": f"No VM size observer is defined for provider {provider!r}.",
            }],
        }
    resource_type, attribute = target
    variables: dict[str, str] = {}
    parsed_files: list[tuple[str, dict[str, Any]]] = []
    for name, content in sorted(files.items()):
        if not name.endswith(".tf"):
            continue
        try:
            parsed = hcl2.load(io.StringIO(content))
        except Exception:
            continue
        parsed_files.append((name, parsed))
        for block in parsed.get("variable") or []:
            for raw_name, body in block.items():
                default = _hcl_string((body or {}).get("default"))
                if default is not None:
                    variables[raw_name.strip('"')] = default

    observations: list[dict[str, Any]] = []
    for name, parsed in parsed_files:
        for block in parsed.get("resource") or []:
            for raw_type, instances in block.items():
                if raw_type.strip('"') != resource_type:
                    continue
                for raw_name, body in (instances or {}).items():
                    raw_value = _hcl_string((body or {}).get(attribute))
                    value = raw_value
                    variable = None
                    match = re.fullmatch(r"\$\{var\.([A-Za-z0-9_-]+)\}", raw_value or "")
                    if match:
                        variable = match.group(1)
                        value = variables.get(variable)
                    observations.append({
                        "source": f"{name}:{resource_type}.{raw_name.strip(chr(34))}.{attribute}",
                        "raw": raw_value,
                        "variable": variable,
                        "value": value,
                    })
    matched = any(item.get("value") == expected_spec_name for item in observations)
    diagnostics = [] if matched else [{
        "code": "BIND-VM-SIZE-001",
        "message": "Generated IaC does not bind the selected VM specification.",
        "details": {
            "expected": expected_spec_name,
            "observed": [item.get("value") for item in observations],
        },
    }]
    return {
        "status": "passed" if matched else "failed",
        "expected": expected_spec_name,
        "observations": observations,
        "diagnostics": diagnostics,
    }


def _port_observations(files: dict[str, str]) -> list[Observation]:
    observations: list[Observation] = []
    for name, content in sorted(files.items()):
        if name.endswith(".tf"):
            try:
                parsed = hcl2.load(io.StringIO(content))
            except Exception:  # syntax is owned by the earlier HCL gate
                continue
            for path, value in _walk(parsed):
                if not path:
                    continue
                key = path[-1]
                context = set(path[:-1])
                strong = key in _STRONG_PORT_KEYS or (
                    key == "port" and bool(context & _PORT_CONTEXT)
                )
                if not strong:
                    continue
                port = _literal_port(value)
                observations.append(
                    Observation(
                        kind="backendPort",
                        value=port,
                        source=f"{name}:{'.'.join(path)}",
                        confidence="observed" if port is not None else "unresolved",
                    )
                )
        for match in re.finditer(
            r"(?i)docker\s+run\b[^\r\n]*?(?:-p|--publish(?:=|\s+))\s*"
            r"(?:[0-9.]+:)?(?:\d+:)?(?P<container>\d{1,5})(?:/tcp)?",
            content,
        ):
            port = _literal_port(match.group("container"))
            observations.append(
                Observation(
                    kind="containerPort",
                    value=port,
                    source=f"{name}:docker-publish",
                    confidence="observed" if port is not None else "unresolved",
                )
            )
    return observations


_GUEST_MOUNT_PATTERN = re.compile(
    r"(?im)\bmount\s+(?:[^\r\n]+\s)?(?P<path>/[^\s;&|]+)"
)
_CONTAINER_MOUNT_PATTERNS = (
    re.compile(r"(?i)(?:target|destination|dst)=(?P<path>/[^,\s'\"]+)"),
    re.compile(r"(?i)(?:-v|--volume)\s+[^\s:]+:(?P<path>/[^\s:]+)"),
)


def _mount_observations(files: dict[str, str]) -> list[Observation]:
    observations: list[Observation] = []
    for name, content in sorted(files.items()):
        for kind, patterns in (
            ("guestMountPath", (_GUEST_MOUNT_PATTERN,)),
            ("containerMountPath", _CONTAINER_MOUNT_PATTERNS),
        ):
            for pattern in patterns:
                for match in pattern.finditer(content):
                    path = match.group("path").rstrip("/)]}'\"")
                    dynamic = any(token in path for token in ("${", "%{", "{{"))
                    observations.append(
                        Observation(
                            kind=kind,
                            value=None if dynamic else path,
                            source=f"{name}:{kind}",
                            confidence="unresolved" if dynamic else "observed",
                        )
                    )
        if re.search(r"(?im)\bmount\b[^\r\n]*(?:\$\{|%\{|\{\{)", content):
            observations.append(
                Observation(
                    kind="guestMountPath",
                    value=None,
                    source=f"{name}:dynamic-mount-command",
                    confidence="unresolved",
                )
            )
    return observations


def _unguarded_filesystem_initializations(files: dict[str, str]) -> list[str]:
    """명백히 무조건 실행되는 mkfs만 찾는다; 복잡한 셸 흐름은 추측하지 않는다."""
    locations: list[str] = []
    for name, content in sorted(files.items()):
        inside_guard = False
        for index, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if re.search(r"(?i)\bif\b.*\b(?:blkid|lsblk|file)\b", line):
                inside_guard = True
            if re.search(r"(?i)\bfi\b", line):
                inside_guard = False
                continue
            if not re.search(r"(?i)\bmkfs(?:\.[a-z0-9_-]+)?\b", line):
                continue
            inline_guard = bool(
                re.search(r"(?i)\b(?:blkid|lsblk|file)\b.*(?:&&|\|\|).*\bmkfs", line)
                or re.search(r"(?i)\bif\b.*\bmkfs", line)
            )
            if not inside_guard and not inline_guard:
                locations.append(f"{name}:{index}")
    return locations


def _ambiguous_storage_device_selections(files: dict[str, str]) -> list[str]:
    """순서가 보장되지 않은 블록 장치 목록의 첫 항목 선택만 보수적으로 거부한다."""
    locations: list[str] = []
    for name, content in sorted(files.items()):
        for index, line in enumerate(content.splitlines(), start=1):
            if re.search(r"(?i)\blsblk\b.*\bhead\s+(?:-n\s*)?1\b", line):
                locations.append(f"{name}:{index}")
    return locations


def validate_iac_bindings(
    files: dict[str, str],
    *,
    application_port: int,
    mount_path: str | None,
) -> dict[str, Any]:
    """Fail only on observable contradictions or an absent required mount operation."""
    port_observations = _port_observations(files)
    mount_observations = _mount_observations(files)
    diagnostics: list[ConsistencyDiagnostic] = []
    unresolved: list[dict[str, Any]] = []

    literal_ports = {
        item.value for item in port_observations if isinstance(item.value, int)
    }
    if literal_ports and application_port not in literal_ports:
        diagnostics.append(
            ConsistencyDiagnostic(
                code="BIND-PORT-001",
                message="Generated IaC has an observable backend/container port that conflicts with the application contract.",
                locations=[item.source for item in port_observations],
                details={
                    "expected": application_port,
                    "observed": sorted(literal_ports),
                },
            )
        )
    elif not literal_ports:
        unresolved.append({
            "code": "BIND-PORT-UNRESOLVED",
            "expected": application_port,
            "reason": "No literal backend/container port was statically observable.",
        })

    if mount_path:
        container_observations = [
            item for item in mount_observations if item.kind == "containerMountPath"
        ]
        relevant_observations = container_observations or [
            item for item in mount_observations if item.kind == "guestMountPath"
        ]
        literal_mounts = {
            str(item.value)
            for item in relevant_observations
            if isinstance(item.value, str)
        }
        if mount_path not in literal_mounts:
            if any(item.confidence == "unresolved" for item in relevant_observations):
                unresolved.append({
                    "code": "BIND-STORAGE-UNRESOLVED",
                    "expected": mount_path,
                    "reason": "A dynamic mount command exists but its target is not statically known.",
                })
            else:
                diagnostics.append(
                    ConsistencyDiagnostic(
                        code="BIND-STORAGE-001",
                        message="Generated IaC does not expose persistent storage at the contracted application path.",
                        locations=[item.source for item in relevant_observations],
                        details={
                            "expected": mount_path,
                            "observed": sorted(literal_mounts),
                            "observedBoundary": (
                                "container" if container_observations else "guest"
                            ),
                        },
                    )
                )
        destructive_initializations = _unguarded_filesystem_initializations(files)
        if destructive_initializations:
            diagnostics.append(
                ConsistencyDiagnostic(
                    code="BIND-STORAGE-DESTRUCTIVE-INIT",
                    message=(
                        "Persistent storage formatting is unconditionally repeated during "
                        "guest initialization."
                    ),
                    locations=destructive_initializations,
                    details={
                        "expected": "format-only-when-no-filesystem-exists",
                        "observed": "unguarded-mkfs",
                    },
                )
            )
        ambiguous_devices = _ambiguous_storage_device_selections(files)
        if ambiguous_devices:
            diagnostics.append(
                ConsistencyDiagnostic(
                    code="BIND-STORAGE-DEVICE-AMBIGUOUS",
                    message=(
                        "Persistent storage bootstrap selects the first enumerated block "
                        "device without a stable provider identity."
                    ),
                    locations=ambiguous_devices,
                    details={
                        "expected": "stable-provider-device-identity",
                        "observed": "first-enumerated-block-device",
                    },
                )
            )

    return {
        "status": "failed" if diagnostics else "passed",
        "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        "unresolved": unresolved,
        "observations": [item.__dict__ for item in [*port_observations, *mount_observations]],
    }
