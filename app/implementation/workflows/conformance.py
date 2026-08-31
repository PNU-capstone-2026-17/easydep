"""생성된 Java 공개 계약과 ERD 기반 영속화 구조가 보존됐는지 확인한다.

Boundary, Control, API와 DataType 파일은 그대로 보존하고, Entity는 공개 Java
signature가 같을 때 메서드 본문 변경만 허용한다. 실제 호출 흐름은 정규식으로 Java
본문을 추측하지 않고 Gradle 단위 테스트와 실제 HTTP E2E에서 확인한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA_VERSION = "source-design-conformance/v1alpha1"
SNAPSHOT_FILE = "reports/generated-source-contracts.json"
REPORT_FILE = "reports/source-design-conformance.json"


def capture_generated_contracts(run_root: Path, base_package: str) -> dict[str, object]:
    """Persist the immutable Java contract baseline before an agent can edit it."""
    package_root = (
        run_root / "application" / "src" / "main" / "java" / Path(base_package.replace(".", "/"))
    )
    files: list[dict[str, object]] = []
    for area in ("bce", "api"):
        root = package_root / area
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.java")):
            content = path.read_text(encoding="utf-8")
            files.append(
                {
                    "path": path.relative_to(run_root).as_posix(),
                    "sha256": _sha256(content),
                    "content": content,
                    "structure": _java_structure(content),
                }
            )
    payload: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "basePackage": base_package,
        "files": files,
    }
    target = run_root / SNAPSHOT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def verify_source_design_conformance(run_root: Path, spec) -> dict[str, object]:
    """공개 Java 계약과 ERD 구현을 최종 빌드가 통과한 뒤 검사한다.

    시퀀스 흐름은 여러 HTTP 요청과 여러 구현 class에 걸칠 수 있어 Java 문자열 검색으로
    판정하지 않는다. 구조화된 시퀀스는 구현 prompt의 입력으로 사용하고, 결과는 실제
    단위 테스트와 E2E가 검증한다. 실패해도 보고서를 먼저 저장한다.
    """
    snapshot_path = run_root / SNAPSHOT_FILE
    violations: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checks: dict[str, object] = {"generatedContracts": []}
    if not snapshot_path.is_file():
        warnings.append(
            {
                "code": "MISSING_CONTRACT_BASELINE",
                "message": "This run predates generated source contract snapshots; immutable contract verification was skipped.",
            }
        )
    else:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        for item in snapshot.get("files", []):
            relative = str(item["path"])
            path = run_root / relative
            check: dict[str, object] = {
                "path": relative,
                "status": "PASSED",
                "integrity": "PASSED",
                "contract": "PASSED",
            }
            if not path.is_file():
                check["status"] = "FAILED"
                check["integrity"] = "FAILED"
                check["contract"] = "FAILED"
                violations.append(
                    {
                        "code": "GENERATED_CONTRACT_REMOVED",
                        "path": relative,
                        "message": "Generated BCE/OpenAPI contract file is missing.",
                    }
                )
            else:
                current = path.read_text(encoding="utf-8")
                if _sha256(current) != item.get("sha256"):
                    changes = _structural_changes(
                        item.get("structure", {}), _java_structure(current)
                    )
                    check["changes"] = changes
                    if _is_entity_contract(item) and _same_public_java_signature(
                        str(item.get("content", "")), current
                    ):
                        check["integrity"] = "MODIFIED"
                        check["body"] = "ALLOWED"
                    else:
                        check["status"] = "FAILED"
                        check["integrity"] = "FAILED"
                        violations.append(
                            {
                                "code": "GENERATED_CONTRACT_MODIFIED",
                                "path": relative,
                                "message": "Generated BCE/OpenAPI contract differs from its pre-agent snapshot.",
                            }
                        )
                    if check["status"] == "FAILED" and _has_structural_changes(changes):
                        check["contract"] = "FAILED"
                        violations.append(
                            {
                                "code": "GENERATED_CONTRACT_STRUCTURE_CHANGED",
                                "path": relative,
                                "message": (
                                    "Generated skeleton class, field, or method signature "
                                    "was added, modified, or deleted."
                                ),
                            }
                        )
            checks["generatedContracts"].append(check)

    _verify_erd_conformance(run_root, spec, checks, violations, warnings)

    report: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "FAILED" if violations else "PASSED",
        "verificationOrder": [
            "Gradle compileJava",
            "Gradle unit/E2E tests",
            "source design conformance",
        ],
        "checks": checks,
        "violations": violations,
        "warnings": warnings,
    }
    target = run_root / REPORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if violations:
        raise SourceDesignConformanceError(report)
    return report


def restore_generated_contracts(run_root: Path) -> list[str]:
    """변경된 계약만 복구하되, 같은 공개 signature의 Entity 본문은 보존한다."""
    snapshot_path = run_root / SNAPSHOT_FILE
    if not snapshot_path.is_file():
        return []
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for item in snapshot.get("files", []):
        relative = str(item.get("path", ""))
        content = item.get("content")
        if not relative or not isinstance(content, str):
            continue
        path = run_root / relative
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if (
            current is not None
            and _is_entity_contract(item)
            and _same_public_java_signature(content, current)
        ):
            continue
        if current != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            restored.append(relative)
    return restored


def entity_public_signature_violations(
    run_root: Path, candidate_root: Path, relative_paths: list[str]
) -> list[str]:
    """Entity 작업이 공개 Java 호출 계약을 바꿨으면 파일별 오류를 반환한다."""
    snapshot_path = run_root / SNAPSHOT_FILE
    if not snapshot_path.is_file():
        return ["Generated Java contract baseline is missing."]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    baseline = {
        str(item.get("path", "")): item
        for item in snapshot.get("files", [])
        if _is_entity_contract(item)
    }
    violations: list[str] = []
    for relative in relative_paths:
        item = baseline.get(str(relative).replace("\\", "/"))
        candidate = candidate_root / relative
        if item is None or not candidate.is_file():
            violations.append(f"{relative}: Entity contract or candidate source is missing")
            continue
        if not _same_public_java_signature(
            str(item.get("content", "")), candidate.read_text(encoding="utf-8")
        ):
            violations.append(
                f"{relative}: preserve the generated public class and method signatures exactly"
            )
    return violations


class SourceDesignConformanceError(RuntimeError):
    def __init__(self, report: dict[str, object]):
        self.report = report
        super().__init__("Source/design conformance verification failed; see " + REPORT_FILE)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _is_entity_contract(item: object) -> bool:
    """BCE class만 Entity로 보고 interface·record·enum은 기존처럼 고정한다."""
    if not isinstance(item, dict) or "/bce/" not in f"/{item.get('path', '')}":
        return False
    structure = item.get("structure", {})
    types = structure.get("types", {}) if isinstance(structure, dict) else {}
    return isinstance(types, dict) and "class" in types.values()


def _same_public_java_signature(before: str, after: str) -> bool:
    """본문과 공백을 제외하고 외부 호출자가 보는 공개 선언만 비교한다."""

    def signatures(source: str) -> tuple[str, ...]:
        clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
        normalized = re.sub(r"\s*\n\s*", " ", clean)
        matches = re.findall(
            r"\bpublic\s+(?:final\s+)?(?:class|interface|enum|record)\s+[A-Za-z_$]\w*|"
            r"\bpublic\s+(?:(?:abstract|final|static)\s+)*[A-Za-z_$][\w$<>,.?\[\] ]*\s+"
            r"[A-Za-z_$]\w*\s*\([^)]*\)(?:\s+throws\s+[^{;]+)?(?=\s*[{;])|"
            r"\bpublic\s+[A-Za-z_$]\w*\s*\([^)]*\)(?=\s*\{)|"
            r"\bpublic\s+(?:(?:static|final)\s+)*[A-Za-z_$][\w$<>,.?\[\] ]*\s+"
            r"[A-Za-z_$]\w*\s*(?=[=;])",
            normalized,
        )
        # 선언 순서는 무시하지만 같은 method가 두 번 생긴 경우는 놓치지 않는다.
        return tuple(sorted(re.sub(r"\s+", "", match) for match in matches))

    return signatures(before) == signatures(after)


def _verify_erd_conformance(
    run_root: Path,
    spec,
    checks: dict[str, object],
    violations: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> None:
    erd_path = spec.inputs.get("erd")
    if erd_path is None or not erd_path.is_file():
        return
    entities, relations = _erd_contract(erd_path.read_text(encoding="utf-8"))
    if not entities:
        warnings.append(
            {
                "code": "UNPARSEABLE_ERD",
                "message": "ERD input exists but no entity contracts could be parsed.",
            }
        )
        return
    package_root = (
        run_root / "application/src/main/java" / Path(spec.base_package.replace(".", "/"))
    )
    migration_path = run_root / "application/src/main/resources/db/migration/V1__initial_schema.sql"
    migration = migration_path.read_text(encoding="utf-8") if migration_path.is_file() else ""
    erd_checks: list[dict[str, object]] = []
    entity_sources: dict[str, str] = {}
    for entity, fields in entities.items():
        entity_path = package_root / "persistence/entity" / f"{entity}Entity.java"
        repository_path = package_root / "persistence/repository" / f"{entity}Repository.java"
        source = entity_path.read_text(encoding="utf-8") if entity_path.is_file() else ""
        entity_sources[entity] = source
        entity_migration = _migration_entity_body(migration, entity)
        migration_tokens = _normalized_identifiers(entity_migration)
        missing_fields: list[str] = []
        missing_columns: list[str] = []
        type_mismatches: list[str] = []
        source_tokens = _normalized_identifiers(source)
        expected_names = {_normalize_identifier(name) for name in fields}
        mapped_fields = _persistence_mapped_fields(source)
        unexpected_fields = sorted(
            f"{field_name} ({column_name})"
            for column_name, field_name in mapped_fields
            if _normalize_identifier(field_name) not in expected_names
            and _normalize_identifier(column_name) not in expected_names
        )
        mapped_erd_columns = {
            _normalize_identifier(column_name)
            for column_name, field_name in mapped_fields
            if _normalize_identifier(field_name) in expected_names
            or _normalize_identifier(column_name) in expected_names
        }
        unexpected_columns = sorted(
            column
            for column in _migration_columns(entity_migration)
            if _normalize_identifier(column) not in expected_names
            and _normalize_identifier(column) not in mapped_erd_columns
        )
        for field_name, field_type in fields.items():
            normalized = _normalize_identifier(field_name)
            if normalized not in source_tokens:
                missing_fields.append(field_name)
            is_collection = field_type.lower().startswith(("list", "set", "collection"))
            if not is_collection and normalized not in migration_tokens:
                missing_columns.append(field_name)
            expected_java, expected_sql = _erd_type_families(field_type)
            if expected_java and not any(token in source for token in expected_java):
                type_mismatches.append(f"{field_name}: expected Java {expected_java[0]}")
            if (
                expected_sql
                and not is_collection
                and not any(token in entity_migration.upper() for token in expected_sql)
            ):
                type_mismatches.append(f"{field_name}: expected SQL {expected_sql[0]}")
        status = "PASSED"
        if not entity_path.is_file() or not repository_path.is_file() or not migration:
            status = "FAILED"
        if (
            missing_fields
            or missing_columns
            or type_mismatches
            or unexpected_fields
            or unexpected_columns
        ):
            status = "FAILED"
        check = {
            "entity": entity,
            "status": status,
            "entityFile": entity_path.relative_to(run_root).as_posix(),
            "repositoryFile": repository_path.relative_to(run_root).as_posix(),
            "missingFields": missing_fields,
            "missingColumns": missing_columns,
            "typeMismatches": type_mismatches,
            "unexpectedFields": unexpected_fields,
            "unexpectedColumns": unexpected_columns,
        }
        erd_checks.append(check)
        if status == "FAILED":
            violations.append(
                {
                    "code": "ERD_ENTITY_NOT_IMPLEMENTED",
                    "path": entity_path.relative_to(run_root).as_posix(),
                    "message": (
                        f"ERD entity {entity} is not structurally represented; "
                        f"missing fields={missing_fields}, missing columns={missing_columns}, "
                        f"type mismatches={type_mismatches}, unexpected fields={unexpected_fields}, "
                        f"unexpected columns={unexpected_columns}."
                    ),
                }
            )
    for left, right in relations:
        related = (
            right in entity_sources.get(left, "")
            and re.search(
                r"@(OneToOne|OneToMany|ManyToOne|ManyToMany)",
                entity_sources[left],
            )
        ) or (
            left in entity_sources.get(right, "")
            and re.search(r"@(OneToOne|OneToMany|ManyToOne|ManyToMany)", entity_sources[right])
        )
        if not related:
            violations.append(
                {
                    "code": "ERD_RELATION_NOT_IMPLEMENTED",
                    "path": "application/src/main/java",
                    "message": f"ERD relation {left} <-> {right} has no JPA association.",
                }
            )
    checks["erdEntities"] = erd_checks


def _erd_contract(source: str) -> tuple[dict[str, dict[str, str]], list[tuple[str, str]]]:
    entities: dict[str, dict[str, str]] = {}
    pattern = re.compile(r'(?ms)^\s*entity\s+(?:"[^"]+"\s+as\s+)?([A-Za-z_]\w*)\s*\{(.*?)^\s*\}')
    for match in pattern.finditer(source):
        fields: dict[str, str] = {}
        for raw_line in match.group(2).splitlines():
            line = raw_line.strip().lstrip("*+#-").strip()
            if not line or line == "--":
                continue
            if ":" in line:
                name, field_type = line.split(":", 1)
            else:
                parts = line.split()
                name, field_type = parts[0], "id"
            fields[name.strip()] = field_type.strip()
        entities[match.group(1)] = fields
    names = set(entities)
    relations: list[tuple[str, str]] = []
    for line in source.splitlines():
        mentioned = [name for name in names if re.search(rf"\b{re.escape(name)}\b", line)]
        if len(mentioned) == 2 and re.search(r"[|}{o*]+(?:--|\.\.)[|}{o*]+", line):
            relations.append((mentioned[0], mentioned[1]))
    return entities, relations


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _normalized_identifiers(source: str) -> set[str]:
    return {_normalize_identifier(token) for token in re.findall(r"[A-Za-z_]\w*", source)}


def _persistence_mapped_fields(source: str) -> list[tuple[str, str]]:
    """JPA 열 이름과 그 열을 담당하는 Java 필드 이름을 함께 읽는다.

    실제 DB 열 이름은 예약어 회피 때문에 ERD 이름과 다를 수 있다. 따라서 열 이름만
    비교하지 않고 Java 필드 이름도 함께 비교해야 정상적인 이름 변경을 오탐하지 않는다.
    """
    return [
        (match.group("column"), match.group("field"))
        for match in re.finditer(
            r'@(?:Column|JoinColumn)\s*\([^)]*?name\s*=\s*"(?P<column>[^"]+)"[^)]*\)'
            r'(?:(?!;).)*?\b(?:private|protected|public)\s+'
            r'[A-Za-z_$][\w$<>,.?\[\] ]*\s+(?P<field>[A-Za-z_$]\w*)\s*;',
            source,
            flags=re.DOTALL,
        )
    ]


def _migration_columns(table_body: str) -> set[str]:
    """CREATE TABLE 본문에서 실제 열 선언의 이름만 추린다."""
    columns: set[str] = set()
    for line in table_body.splitlines():
        match = re.match(r'\s*["`]?([A-Za-z_]\w*)["`]?\s+', line)
        if not match:
            continue
        name = match.group(1)
        if name.lower() not in {"primary", "foreign", "unique", "check"}:
            columns.add(name)
    return columns


def _migration_entity_body(source: str, entity: str) -> str:
    candidates = {_normalize_identifier(entity), _normalize_identifier(entity + "s")}
    if entity.lower().endswith("y"):
        candidates.add(_normalize_identifier(entity[:-1] + "ies"))
    for match in re.finditer(
        r'(?is)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?([A-Za-z_]\w*)["`]?\s*\((.*?)\)\s*;',
        source,
    ):
        if _normalize_identifier(match.group(1)) in candidates:
            return match.group(2)
    return ""


def _erd_type_families(field_type: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lowered = field_type.lower()
    if lowered.startswith(("list", "set", "collection")):
        return (("List<", "Set<", "Collection<"), ())
    if "string" in lowered or lowered == "fk" or lowered == "id":
        return (("String", "Long", "UUID"), ("VARCHAR", "BIGINT", "UUID"))
    if lowered in {"int", "integer", "long"}:
        return (("Integer", "int", "Long", "long"), ("INTEGER", "BIGINT"))
    if lowered in {"float", "double", "decimal"}:
        return (("Double", "double", "Float", "BigDecimal"), ("DOUBLE", "REAL", "DECIMAL"))
    if lowered in {"bool", "boolean"}:
        return (("Boolean", "boolean"), ("BOOLEAN",))
    if "date" in lowered or "time" in lowered:
        return (
            ("Instant", "LocalDateTime", "OffsetDateTime", "ZonedDateTime"),
            ("TIMESTAMP", "DATE"),
        )
    return ((), ())


def _java_structure(source: str) -> dict[str, object]:
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    types = {
        match.group("name"): match.group("kind")
        for match in re.finditer(
            r"(?m)^\s*(?:public\s+)?(?:abstract\s+|final\s+)?"
            r"(?P<kind>class|interface|enum|record)\s+(?P<name>\w+)",
            clean,
        )
    }
    fields = {
        match.group("name"): _normalize_java_type(match.group("type"))
        for match in re.finditer(
            r"(?m)^\s*(?!(?:package|import)\b)(?:public|protected|private)?\s*(?:static\s+)?"
            r"(?:final\s+)?(?P<type>[A-Za-z_$][\w$<>,.?\[\] ]*)\s+"
            r"(?P<name>[A-Za-z_$]\w*)\s*(?:=[^;]*)?;\s*$",
            clean,
        )
    }
    # Normalize multiline signatures into continuous space for robust regex matching
    normalized_signatures = re.sub(r"\s*[\r\n]+\s*", " ", clean)
    methods: dict[str, str] = {}
    for match in re.finditer(
        r"(?:(?:public|protected|private|static|abstract|default|final|"
        r"synchronized|native)\s+)*(?P<return>[A-Za-z_$][\w$<>,.?\[\] ]*)\s+"
        r"(?P<name>[A-Za-z_$]\w*)\s*\((?P<params>[^)]*)\)\s*"
        r"(?:throws\s+(?P<throws>[^\{;]+))?[\{;]",
        normalized_signatures,
    ):
        parameters = _normalize_parameters(match.group("params"))
        throws = _normalize_java_type(match.group("throws") or "")
        key = f"{match.group('name')}({parameters})"
        methods[key] = _normalize_java_type(match.group("return")) + (
            f" throws {throws}" if throws else ""
        )
    return {"types": types, "fields": fields, "methods": methods}


def _normalize_java_type(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _normalize_parameters(value: str) -> str:
    value = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", value)
    return _normalize_java_type(value)


def _structural_changes(
    before: object, after: dict[str, object]
) -> dict[str, dict[str, list[str]]]:
    original = before if isinstance(before, dict) else {}
    changes: dict[str, dict[str, list[str]]] = {}
    for key in ("types", "fields", "methods"):
        baseline = original.get(key, {})
        current = after.get(key, {})
        # Compatibility with snapshots created by the first implementation of
        # this gate, which represented each item as a list of names.
        if not isinstance(baseline, dict):
            baseline = {str(item): "" for item in baseline}
        if not isinstance(current, dict):
            current = {str(item): "" for item in current}
        removed = sorted(set(baseline) - set(current))
        added = sorted(set(current) - set(baseline))
        modified = sorted(
            f"{name}: {baseline[name]} -> {current[name]}"
            for name in set(baseline) & set(current)
            if baseline[name] != current[name]
        )
        changes[key] = {"removed": removed, "added": added, "modified": modified}
    return changes


def _has_structural_changes(changes: dict[str, dict[str, list[str]]]) -> bool:
    return any(items for group in changes.values() for items in group.values())
