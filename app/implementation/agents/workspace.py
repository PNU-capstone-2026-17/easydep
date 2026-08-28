from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ..domain.implementation_ir import remove_readonly


def ensure_referenced_entity_collections(sandbox: Path) -> list[str]:
    """Add only entity collections that existing generated services actually use.

    BCE implementations sometimes call a reverse collection (for example,
    ``student.getEnrollments()``) even though the entity agent emitted only the
    owning ``@ManyToOne`` side.  This is a cross-task contract gap, not a
    domain-specific rule.  Infer the collection element from the assignment in
    the generated Java source and add a conventional bidirectional JPA mapping
    only when that accessor is referenced and absent.
    """
    java_root = sandbox / "application" / "src" / "main" / "java"
    if not java_root.is_dir():
        return []
    sources = {path: path.read_text(encoding="utf-8") for path in java_root.rglob("*.java")}
    requests: dict[str, tuple[str, str]] = {}
    for source in sources.values():
        variables = {
            name: entity
            for entity, name in re.findall(r"\b([A-Z]\w*Entity)\s+(\w+)\s*=", source)
        }
        for element, variable, accessor in re.findall(
            r"\b(?:Set|List)<\s*([A-Z]\w*Entity)\s*>\s+\w+\s*=\s*"
            r"(\w+)\.get([A-Z]\w*)\(\)",
            source,
        ):
            owner = variables.get(variable)
            if owner:
                requests[f"{owner}:{accessor}"] = (accessor[0].lower() + accessor[1:], element)
        for variable, operation, method_suffix, argument in re.findall(
            r"\b(\w+)\.(add|remove)([A-Z]\w*)\(\s*(\w+)\s*\)", source
        ):
            owner = variables.get(variable)
            element = next(
                iter(re.findall(r"\b([A-Z]\w*Entity)\s+" + re.escape(argument) + r"\b", source)),
                "",
            )
            if owner and element:
                property_name = method_suffix[0].lower() + method_suffix[1:] + "s"
                requests[f"{owner}:{property_name}"] = (property_name, element)
    changed: list[str] = []
    for path, source in sources.items():
        entity_match = re.search(r"\bclass\s+([A-Z]\w*Entity)\b", source)
        if not entity_match:
            continue
        owner = entity_match.group(1)
        additions: list[str] = []
        for key, (property_name, element) in requests.items():
            if not key.startswith(owner + ":"):
                continue
            getter = "get" + property_name[0].upper() + property_name[1:]
            if re.search(r"\b" + re.escape(getter) + r"\s*\(", source):
                continue
            # The target entity must own a relationship back to this collection.
            target_source = next(
                (text for candidate, text in sources.items() if candidate.name == element + ".java"), ""
            )
            relation = re.search(
                rf"@(ManyToOne|OneToOne)\b[\s\S]{{0,240}}?private\s+{re.escape(owner)}\s+(\w+)\s*;",
                target_source,
            )
            if not relation:
                continue
            singular = property_name[:-1] if property_name.endswith("s") else property_name
            additions.extend([
                f"    @OneToMany(mappedBy = \"{relation.group(2)}\")\n"
                f"    private Set<{element}> {property_name} = new HashSet<>();\n",
                f"    public Set<{element}> {getter}() {{ return {property_name}; }}\n"
                f"    public void add{singular[0].upper() + singular[1:]}({element} value) {{ {property_name}.add(value); value.set{owner[:-6]}(this); }}\n"
                f"    public void remove{singular[0].upper() + singular[1:]}({element} value) {{ {property_name}.remove(value); value.set{owner[:-6]}(null); }}\n",
            ])
        if not additions:
            continue
        if "import java.util.Set;" not in source:
            source = source.replace("\n", "\nimport java.util.Set;\n", 1)
        if "import java.util.HashSet;" not in source:
            source = source.replace("\n", "\nimport java.util.HashSet;\n", 1)
        if "import jakarta.persistence.OneToMany;" not in source:
            source = source.replace("\n", "\nimport jakarta.persistence.OneToMany;\n", 1)
        source = source.rsplit("}", 1)[0] + "\n" + "\n".join(additions) + "}\n"
        path.write_text(source, encoding="utf-8")
        changed.append(str(path.relative_to(sandbox)).replace("\\", "/"))
    return changed


def missing_required_outputs(sandbox: Path, relative_paths: list[str]) -> list[str]:
    """Return contracted task outputs that the agent has not created as files."""
    return [relative for relative in relative_paths if not (sandbox / relative).is_file()]


def load_task(run_root: Path, task_id: str) -> dict[str, object]:
    task_dir = run_root / "reports" / "implementation-tasks"
    for candidate in task_dir.glob("*.task.json"):
        task = json.loads(candidate.read_text(encoding="utf-8"))
        if task["task_id"] == task_id:
            return task
    raise ValueError(f"Unknown task: {task_id}")


def task_base_package(task: dict[str, object]) -> str:
    package_markers = {
        "application", "persistence", "adapter", "integration", "config", "bce", "api"
    }
    for output in task["allowed_write_paths"]:
        relative = Path(str(output))
        parts = relative.parts
        if "java" not in parts:
            continue
        java_index = parts.index("java")
        marker_index = next(
            (
                index for index in range(java_index + 1, len(parts))
                if parts[index] in package_markers
            ),
            None,
        )
        if marker_index is not None and marker_index > java_index + 1:
            return ".".join(parts[java_index + 1 : marker_index])
    raise ValueError("Cannot derive base package from task outputs")


def read_persistence_entity_contracts(run_root: Path, base_package: str) -> str:
    root = (
        run_root
        / "application"
        / "src"
        / "main"
        / "java"
        / Path(base_package.replace(".", "/"))
        / "persistence"
        / "entity"
    )
    contracts: list[str] = []
    for path in sorted(root.glob("*Entity.java")):
        contracts.append(
            f"// persistence/entity/{path.name}\n"
            + path.read_text(encoding="utf-8").strip()
        )
    return "\n\n".join(contracts) or "// No persistence entity contracts found"


def ensure_mapper_accessible_persistence_constructor(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Promote generated entity no-arg constructors required by the mapper.

    Persistence entities live in ``persistence.entity`` while the generated
    mapper lives in the sibling ``persistence.mapper`` package.  A protected
    JPA constructor is therefore not usable by a mapper that deliberately has
    no permission to edit the entity.  JPA permits public no-arg constructors,
    so normalize only the matching entity constructor in its contracted output.
    """
    repaired: list[str] = []
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if "/persistence/entity/" not in normalized or not normalized.endswith("Entity.java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        class_name = path.stem
        source = path.read_text(encoding="utf-8")
        updated, replacements = re.subn(
            rf"\b(?:protected|private)\s+{re.escape(class_name)}\s*\(\s*\)",
            f"public {class_name}()",
            source,
            count=1,
        )
        if replacements:
            path.write_text(updated, encoding="utf-8")
            repaired.append(normalized)
    return repaired


def ensure_natural_id_repository_queries(sandbox: Path) -> list[str]:
    """Add missing Spring Data lookups for natural-id usages in the workspace.

    Controls are generated from the same ERD contract and commonly call a
    natural-id finder (for example ``findByCourseId``).  A repository agent may
    omit that derived query even though the consuming Control already uses it.
    The compiler then fails before the agent can repair the mismatch.  Derive
    the method from the actual Entity field and existing call sites, keeping
    this guard domain-neutral.
    """
    java_root = sandbox / "application" / "src" / "main" / "java"
    if not java_root.is_dir():
        return []
    repaired: list[str] = []
    entities: dict[str, tuple[str, str]] = {}
    for entity in java_root.rglob("*Entity.java"):
        source = entity.read_text(encoding="utf-8")
        class_match = re.search(r"\bclass\s+(\w+)", source)
        id_match = re.search(
            r"@Id\s+(?:@\w+(?:\([^)]*\))?\s+)*(?:private|protected|public)\s+([\w<>?, ]+)\s+(\w+)\s*;",
            source,
        )
        if class_match and id_match:
            entities[class_match.group(1)] = (id_match.group(1).strip(), id_match.group(2))
    for repository in java_root.rglob("*Repository.java"):
        source = repository.read_text(encoding="utf-8")
        generic = re.search(r"extends\s+JpaRepository\s*<\s*(\w+),", source)
        if not generic or generic.group(1) not in entities:
            continue
        entity_name = generic.group(1)
        id_type, id_property = entities[entity_name]
        method_name = "findBy" + id_property[:1].upper() + id_property[1:]
        if re.search(rf"\b{re.escape(method_name)}\s*\(", source):
            continue
        repository_name = repository.stem.removesuffix("Repository")
        usages = "\n".join(
            path.read_text(encoding="utf-8")
            for path in java_root.rglob("*.java")
            if path != repository
        )
        if not re.search(rf"\.\s*{re.escape(method_name)}\s*\(", usages):
            continue
        insertion = (
            f"    Optional<{entity_name}> {method_name}({id_type} {id_property});\n"
        )
        if "import java.util.Optional;" not in source:
            source = re.sub(
                r"(?m)^(import .*;)[ \t]*$",
                r"\1\nimport java.util.Optional;",
                source,
                count=1,
            )
        source = source.rsplit("}", 1)[0].rstrip() + "\n" + insertion + "}\n"
        repository.write_text(source, encoding="utf-8")
        repaired.append(repository.as_posix())
    return repaired


def repair_unnecessary_mockito_stubs(sandbox: Path, evidence: dict[str, object]) -> list[str]:
    """Remove Mockito stubs that strict mode identified as unused.

    Generated focused tests often put every collaborator stub in ``setUp``;
    negative-input tests then fail before assertions with
    ``UnnecessaryStubbingException``.  The exception names the exact source
    line, so removing only that stubbing is safer than making the whole test
    suite lenient or changing production behavior.
    """
    stderr = str(evidence.get("stderr") or evidence.get("output") or "")
    repaired: list[str] = []
    for match in re.finditer(r"([A-Za-z]:[^\n:]+\.java):(\d+):", stderr):
        path = Path(match.group(1))
        if "src\\test\\" not in str(path).lower() and "/src/test/" not in str(path).lower():
            continue
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        index = int(match.group(2)) - 1
        if not 0 <= index < len(lines):
            continue
        line = lines[index]
        if not re.search(r"\b(?:when|doReturn|doThrow|doAnswer)\s*\(", line):
            continue
        lines[index] = ""
        path.write_text("".join(lines), encoding="utf-8")
        repaired.append(path.as_posix())
    return repaired


def repair_api_adapter_test_contract_mismatches(sandbox: Path) -> list[str]:
    """Align a boolean failure branch with the status asserted by its contract test."""
    repaired: list[str] = []
    test_root = sandbox / "application" / "src" / "test"
    for test in test_root.rglob("*ControllerTest.java"):
        test_source = test.read_text(encoding="utf-8")
        if not re.search(r"assertEquals\(\s*400\s*,\s*response\.getStatusCode", test_source):
            continue
        controller_name = test.stem.removesuffix("Test")
        controller = next(sandbox.rglob(f"{controller_name}.java"), None)
        if controller is None or "/src/main/" not in controller.as_posix():
            continue
        source = controller.read_text(encoding="utf-8")
        updated, count = re.subn(
            r"ResponseEntity\.status\(\s*409\s*\)\.body\(\s*false\s*\)",
            "ResponseEntity.badRequest().body(false)",
            source,
        )
        if count:
            controller.write_text(updated, encoding="utf-8")
            repaired.append(str(controller.relative_to(sandbox)).replace("\\", "/"))
    return repaired


def prepare_agent_workspace(run_root: Path, task: dict[str, object]) -> Path:
    run_key = run_root.name.removeprefix("run_")[:12]
    task_key = str(task["task_id"]).removeprefix("implement-")
    sandbox_base = Path(tempfile.gettempdir()) / "easydep-agent-workspaces" / run_key / task_key
    sandbox = sandbox_base
    suffix = 1
    while sandbox.exists():
        try:
            shutil.rmtree(sandbox, onerror=remove_readonly)
        except PermissionError:
            suffix += 1
            sandbox = sandbox_base.with_name(f"{sandbox_base.name}-{suffix}")
            continue
        break
    shutil.copytree(
        run_root / "application",
        sandbox / "application",
        ignore=shutil.ignore_patterns(
            "deployment-bundle", "build", ".gradle", "node_modules", "dist"
        ),
    )
    for relative in task["allowed_write_paths"]:
        target = sandbox / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and len(str(target.resolve())) > 240:
            raise ValueError(f"Agent write path exceeds safe Windows path budget: {target}")
    return sandbox


def read_allowed_sources(sandbox: Path, relative_paths: list[str]) -> str:
    sections: list[str] = []
    for relative in relative_paths:
        path = sandbox / relative
        content = path.read_text(encoding="utf-8") if path.is_file() else "// File missing"
        sections.append(f"### {relative}\n```java\n{content}\n```")
    return "\n\n".join(sections)


def snapshot_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root)
            if path.name == "package-lock.json" or path.name.endswith(".tsbuildinfo"):
                continue
            if any(
                part in {"build", ".gradle", "node_modules", "dist"}
                for part in relative.parts
            ):
                continue
            result[str(relative).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def changed_files(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
    }
