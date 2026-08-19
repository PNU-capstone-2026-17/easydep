from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import JobSpec


HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}
SEMANTIC_STOP_WORDS = {
    "a", "an", "and", "api", "by", "for", "from", "in", "is", "of", "on",
    "or", "record", "request", "response", "the", "to", "with",
}


@dataclass(frozen=True)
class ComponentIR:
    name: str
    stereotype: str
    operations: tuple[str, ...]


@dataclass(frozen=True)
class ApiResponseIR:
    status: int
    description: str


@dataclass(frozen=True)
class ApiOperationIR:
    method: str
    path: str
    operation_id: str | None
    responses: tuple[ApiResponseIR, ...]


@dataclass(frozen=True)
class ApiPortIR:
    name: str
    interface_file: str
    operations: tuple[ApiOperationIR, ...]


@dataclass(frozen=True)
class GatewayIR:
    name: str
    kind: str


@dataclass(frozen=True)
class E2EScenarioIR:
    method: str
    path: str
    status: int
    label: str


@dataclass(frozen=True)
class ImplementationIR:
    schema_version: str
    application_name: str
    application_class: str
    components: tuple[ComponentIR, ...]
    controls: tuple[str, ...]
    boundaries: tuple[str, ...]
    entities: tuple[str, ...]
    persistent_entities: tuple[str, ...]
    gateways: tuple[GatewayIR, ...]
    api_ports: tuple[ApiPortIR, ...]
    e2e_scenarios: tuple[E2EScenarioIR, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_implementation_ir(
    spec: JobSpec, run_root: Path, *, persist: bool = True
) -> ImplementationIR:
    bce = _read(spec.inputs.get("bceClass"))
    sequence = _read(spec.inputs.get("sequence"))
    openapi = _read(spec.inputs.get("openapi"))
    erd = _read(spec.inputs.get("erd"))
    components = tuple(parse_components(bce))
    api_operations = tuple(parse_openapi_operations(openapi))
    api_ports = tuple(discover_api_ports(spec, run_root, api_operations))
    gateways = tuple(
        GatewayIR(item.name, classify_gateway(item))
        for item in components
        if item.stereotype.lower() == "gateway"
    )
    ir = ImplementationIR(
        schema_version="implementation-ir/v1alpha1",
        application_name=spec.name,
        application_class=f"{pascal_case(spec.name)}Application",
        components=components,
        controls=tuple(sorted(c.name for c in components if c.stereotype.lower() == "control")),
        boundaries=tuple(sorted(c.name for c in components if c.stereotype.lower() == "boundary")),
        entities=tuple(sorted(c.name for c in components if c.stereotype.lower() == "entity")),
        persistent_entities=tuple(
            sorted(
                {c.name for c in components if c.stereotype.lower() == "entity"}
                & parse_erd_entities(erd)
            )
        ),
        gateways=gateways,
        api_ports=api_ports,
        e2e_scenarios=tuple(derive_e2e_scenarios(api_operations, sequence)),
    )
    if persist:
        target = run_root / "reports" / "implementation-ir.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(ir.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return ir


def parse_components(source: str) -> list[ComponentIR]:
    pattern = re.compile(
        r"(?ms)^\s*(?:class|interface|entity)\s+(?P<name>[A-Za-z_]\w*)\s+"
        r"<<(?P<stereotype>[^>]+)>>\s*\{(?P<body>.*?)\}"
    )
    result: list[ComponentIR] = []
    for match in pattern.finditer(source):
        operations = tuple(
            line.strip().lstrip("+").strip()
            for line in match.group("body").splitlines()
            if "(" in line and ")" in line
        )
        result.append(
            ComponentIR(match.group("name"), match.group("stereotype").strip(), operations)
        )
    return result


def parse_openapi_operations(source: str) -> list[ApiOperationIR]:
    if source.lstrip().startswith("{"):
        document = json.loads(source)
        result: list[ApiOperationIR] = []
        for path, path_item in document.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if str(method).lower() not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                responses = tuple(
                    ApiResponseIR(int(status), str(value.get("description", "")))
                    for status, value in operation.get("responses", {}).items()
                    if str(status).isdigit() and isinstance(value, dict)
                )
                result.append(
                    ApiOperationIR(
                        str(method).upper(),
                        str(path),
                        operation.get("operationId"),
                        responses,
                    )
                )
        return result
    lines = source.splitlines()
    result: list[ApiOperationIR] = []
    index = 0
    while index < len(lines):
        path_match = re.match(r"^  (/[^:]+):\s*$", lines[index])
        if not path_match:
            index += 1
            continue
        path = path_match.group(1)
        index += 1
        while index < len(lines) and not re.match(r"^  /[^:]+:\s*$", lines[index]):
            method_match = re.match(r"^    ([a-z]+):\s*$", lines[index])
            if not method_match or method_match.group(1) not in HTTP_METHODS:
                if lines[index] and not lines[index].startswith(" "):
                    break
                index += 1
                continue
            method = method_match.group(1).upper()
            start = index
            index += 1
            while index < len(lines):
                if re.match(r"^    [a-z]+:\s*$", lines[index]) or re.match(
                    r"^  /[^:]+:\s*$", lines[index]
                ) or (lines[index] and not lines[index].startswith(" ")):
                    break
                index += 1
            block = lines[start:index]
            operation_id = None
            responses: list[ApiResponseIR] = []
            for offset, line in enumerate(block):
                op_match = re.match(r"^\s+operationId:\s*([^#]+)", line)
                if op_match:
                    operation_id = op_match.group(1).strip().strip('"\'')
                status_match = re.match(r"^\s{8}['\"]?(\d{3})['\"]?:\s*$", line)
                if not status_match:
                    continue
                description = ""
                for following in block[offset + 1 :]:
                    if re.match(r"^\s{8}['\"]?\d{3}['\"]?:\s*$", following):
                        break
                    desc_match = re.match(r"^\s+description:\s*(.*)$", following)
                    if desc_match:
                        description = desc_match.group(1).strip().strip('"\'')
                        break
                responses.append(ApiResponseIR(int(status_match.group(1)), description))
            result.append(ApiOperationIR(method, path, operation_id, tuple(responses)))
    return result


def discover_api_ports(
    spec: JobSpec, run_root: Path, operations: tuple[ApiOperationIR, ...]
) -> list[ApiPortIR]:
    package_path = Path(spec.base_package.replace(".", "/"))
    api_root = run_root / "application" / "src" / "main" / "java" / package_path / "api"
    interfaces = sorted(
        path for path in api_root.glob("*Api.java") if path.name != "ApiUtil.java"
    ) if api_root.is_dir() else []
    result: list[ApiPortIR] = []
    for path in interfaces:
        source = path.read_text(encoding="utf-8")
        matched = tuple(
            operation for operation in operations
            if operation.path in source
            or (operation.operation_id and operation.operation_id in source)
        )
        result.append(
            ApiPortIR(
                path.stem.removesuffix("Api"),
                path.relative_to(run_root).as_posix(),
                matched,
            )
        )
    return result


def classify_gateway(component: ComponentIR) -> str:
    text = " ".join((component.name, *component.operations)).lower()
    persistence_terms = ("persist", "repository", "save", "store", "load", "find", "delete")
    return "persistence" if any(term in text for term in persistence_terms) else "external"


def parse_erd_entities(source: str) -> set[str]:
    quoted = re.findall(
        r'(?m)^\s*entity\s+"[^"]+"\s+as\s+([A-Za-z_]\w*)', source
    )
    plain = re.findall(r"(?m)^\s*entity\s+([A-Za-z_]\w*)\s*\{", source)
    return set(quoted) | set(plain)


def parse_erd_association_entities(source: str, base_entities: set[str]) -> set[str]:
    """Find ERD join entities that connect at least two BCE entities.

    ERDs commonly materialize many-to-many relationships as entities such as
    ``CourseSection``.  These aliases are physical persistence details, not BCE
    Entity components, so requiring exact BCE/ERD equality incorrectly rejects
    otherwise valid designs.
    """
    entities = parse_erd_entities(source)
    neighbors: dict[str, set[str]] = {name: set() for name in entities}
    relation_pattern = re.compile(
        r"(?m)^\s*(?P<left>[A-Za-z_]\w*)\s+[^\n]*(?:--|\.\.)[^\n]*\s+"
        r"(?P<right>[A-Za-z_]\w*)\s*$"
    )
    for match in relation_pattern.finditer(source):
        left, right = match.group("left"), match.group("right")
        if left in entities and right in entities:
            neighbors[left].add(right)
            neighbors[right].add(left)
    return {
        name for name in entities - base_entities
        if len(neighbors.get(name, set()) & base_entities) >= 2
    }


def derive_e2e_scenarios(
    operations: tuple[ApiOperationIR, ...], sequence: str
) -> list[E2EScenarioIR]:
    sequence_tokens = semantic_tokens(sequence)
    scenarios: list[E2EScenarioIR] = []
    for operation in operations:
        successes = [item for item in operation.responses if 200 <= item.status < 300]
        for response in successes:
            scenarios.append(
                E2EScenarioIR(
                    operation.method,
                    operation.path,
                    response.status,
                    f"{operation.method} {operation.path} success",
                )
            )
        for response in operation.responses:
            if response.status < 400:
                continue
            response_tokens = semantic_tokens(response.description)
            if len(response_tokens & sequence_tokens) >= 2:
                scenarios.append(
                    E2EScenarioIR(
                        operation.method,
                        operation.path,
                        response.status,
                        response.description or f"HTTP {response.status}",
                    )
                )
    return list(dict.fromkeys(scenarios))


def semantic_tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z][a-z0-9_-]+", value.lower())
        if token not in SEMANTIC_STOP_WORDS and len(token) > 2
    }


def pascal_case(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    result = "".join(word[:1].upper() + word[1:] for word in words)
    return result or "Generated"


def kebab_case(value: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s1).lower()


camel_to_kebab = kebab_case


def _read(path: Path | None) -> str:
    return path.read_text(encoding="utf-8") if path and path.is_file() else ""


def remove_readonly(function: Any, path: str, _error: Any) -> None:
    """Retry managed bundle cleanup when Windows marks a copied file read-only."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    function(path)

