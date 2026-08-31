from __future__ import annotations

import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ..workspace import prepare_agent_workspace
from .e2e import e2e_contract_violations, java_code_without_comments_and_strings
from .frontend import run_frontend_command, run_frontend_verification

SQL_RESERVED_IDENTIFIERS = (
    "year",
    "order",
    "group",
    "user",
    "status",
    "key",
    "value",
    "offset",
    "limit",
    "check",
    "date",
)


def _jpa_column_names(source: str) -> set[str]:
    """속성 순서와 무관하게 JPA 열 어노테이션의 ``name`` 값을 모은다."""

    names: set[str] = set()
    for annotation in re.finditer(
        r"@(?:Column|JoinColumn)\s*\((?P<arguments>[^)]*)\)", source
    ):
        name = re.search(r'\bname\s*=\s*"([^"]+)"', annotation.group("arguments"))
        if name:
            names.add(name.group(1).lower())
    return names


def persistence_entity_schema_violations(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """JPA 엔티티와 마이그레이션의 열 매핑이 실제로 맞는지 확인한다.

    Java compile만 통과해도 엔티티가 DB 행을 저장할 수 있다는 뜻은 아니다. 예를 들어
    마이그레이션에는 필수 외래 키가 있는데 엔티티에 해당 열이 없으면, 나중의 E2E insert에서
    원인과 동떨어진 ``NULL not allowed`` 오류가 난다. 엔티티 작업이 아직 파일을 담당할 때
    ``@Column``·``@JoinColumn``과 SQL 열을 비교해 영속성 작업 안에서 바로 수리하게 한다.
    """
    normalized = [path.replace("\\", "/") for path in relative_paths]
    entity_paths = [
        path for path in normalized
        if "/persistence/entity/" in path and path.endswith("Entity.java")
    ]
    migration_candidates = list(
        (sandbox / "application" / "src" / "main" / "resources").rglob(
            "V1__initial_schema.sql"
        )
    )
    if not entity_paths or not migration_candidates:
        return []
    migration = migration_candidates[0].read_text(encoding="utf-8")
    table_columns: dict[str, set[str]] = {}
    for table_match in re.finditer(
        r"(?is)create\s+table(?:\s+if\s+not\s+exists)?\s+\"?(?P<table>[A-Za-z_]\w*)\"?\s*\((?P<body>.*?);",
        migration,
    ):
        table = table_match.group("table").lower()
        columns: set[str] = set()
        for line in table_match.group("body").splitlines():
            match = re.match(
                r"\s*\"?(?P<column>[A-Za-z_]\w*)\"?\s+[A-Za-z][A-Za-z0-9_]*(?:\s*\([^)]*\))?",
                line,
            )
            if match:
                columns.add(match.group("column").lower())
        if columns:
            table_columns[table] = columns

    violations: list[str] = []
    for relative in entity_paths:
        path = sandbox / relative
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        bce_types = set(
            re.findall(
                r"(?m)^import\s+[\w.]+\.bce\.([A-Za-z_]\w*);\s*$",
                source,
            )
        )
        for field in re.finditer(
            r"(?P<annotations>(?:\s*@[A-Za-z_]\w*(?:\([^;]*?\))?\s*)+)"
            r"(?:private|protected|public)\s+(?P<type>[A-Za-z_]\w*)\s+"
            r"(?P<name>[A-Za-z_]\w*)\s*;",
            source,
        ):
            annotations = field.group("annotations")
            field_type = field.group("type")
            has_converter = re.search(
                rf"AttributeConverter\s*<\s*{re.escape(field_type)}\s*,",
                source,
            )
            if (
                "@Column" in annotations
                and field_type in bce_types
                and "@Enumerated" not in annotations
                and "@Convert" not in annotations
                and "@JdbcTypeCode" not in annotations
                and "@Embedded" not in annotations
                and "@Type" not in annotations
                and has_converter is None
            ):
                violations.append(
                    f"{relative}: {field.group('name')} uses BCE value type "
                    f"{field_type} with @Column alone; declare an explicit JPA "
                    "mapping that matches the ERD SQL type"
                )
        table_match = re.search(r'@Table\s*\(\s*name\s*=\s*"([^"]+)"', source)
        if not table_match:
            continue
        table = table_match.group(1).lower()
        expected = table_columns.get(table)
        if not expected:
            continue
        mapped = _jpa_column_names(source)
        missing = sorted(expected - mapped)
        if missing:
            violations.append(
                f"{relative}: @Table({table}) is missing migration column(s): {', '.join(missing)}"
            )
        extra = sorted(mapped - expected)
        if extra:
            violations.append(
                f"{relative}: @Table({table}) maps column(s) absent from the migration: "
                + ", ".join(extra)
            )
    return violations


def repair_invalid_inverse_entity_associations(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Downgrade an invalid inverse JPA collection to transient state.

    A generated ``@OneToMany(mappedBy = "x")`` is valid only if the target
    entity declares an owning relationship property named ``x``.  The ERD can
    describe a scalar foreign key without declaring that Java association.  In
    that case retaining the inverse annotation makes Hibernate reject the
    entire application context.  The collection is useful only as optional
    in-memory convenience state, so mark it ``@Transient`` rather than
    inventing a ``@ManyToOne`` relation not present in the generated contract.
    """
    repaired: list[str] = []
    normalized = [path.replace("\\", "/") for path in relative_paths]
    entity_paths = [
        path for path in normalized
        if "/persistence/entity/" in path and path.endswith("Entity.java")
    ]
    entity_root = sandbox / "application" / "src" / "main" / "java"
    for relative in entity_paths:
        source_path = sandbox / relative
        if not source_path.is_file():
            continue
        source = source_path.read_text(encoding="utf-8")
        original = source
        pattern = re.compile(
            r"@OneToMany\s*\(\s*mappedBy\s*=\s*\"(?P<owner>[A-Za-z_]\w*)\"[^)]*\)"
            r"(?P<between>\s*(?:private|protected)\s+(?:[\w.]+\s*<\s*)?"
            r"(?P<target>[A-Za-z_]\w*Entity)\s*>?\s+[A-Za-z_]\w*"
            r"(?:\s*=\s*[^;]+)?\s*;)",
            re.MULTILINE,
        )
        for match in list(pattern.finditer(source)):
            target_name = match.group("target")
            target_path = next(
                (candidate for candidate in entity_root.rglob(f"{target_name}.java")), None
            )
            target_source = (
                target_path.read_text(encoding="utf-8") if target_path and target_path.is_file() else ""
            )
            owner = match.group("owner")
            owns_relation = bool(re.search(
                rf"@(ManyToOne|OneToOne)\b[\s\S]{{0,300}}?\b{re.escape(owner)}\s*[;=]",
                target_source,
            ))
            if owns_relation:
                continue
            source = source.replace(match.group(0), "@Transient" + match.group("between"), 1)
        if source == original:
            continue
        if "@Transient" in source and not re.search(
            r"(?m)^import\s+jakarta\.persistence\.Transient;", source
        ):
            imports = list(re.finditer(r"(?m)^import\s+[^;]+;", source))
            if imports:
                anchor = imports[-1]
                source = source[:anchor.end()] + "\nimport jakarta.persistence.Transient;" + source[anchor.end():]
        source_path.write_text(source, encoding="utf-8")
        repaired.append(f"{relative}: invalid inverse association marked transient")
    return repaired


def ensure_persistence_schema_test(
    sandbox: Path, relative_paths: list[str], *, overwrite: bool = False
) -> list[str]:
    """Create the deterministic migration smoke test from the declared tables.

    A persistence-schema task has two independent outputs: the migration requires
    design interpretation, while its Flyway/H2 smoke test is a fixed projection
    of the generated SQL.  Retrying a stalled agent solely to write that boilerplate
    wastes a conversation and can fail the whole run even after the migration was
    successfully created.  Generate the test only from the migration's declared
    ``CREATE TABLE`` statements; never invent tables.  ``overwrite`` is used
    after an agent completes: JDBC metadata identifier case varies by database,
    so a generated test must not keep an agent's brittle exact-name lookup.
    """
    normalized = [path.replace("\\", "/") for path in relative_paths]
    migration_relative = next(
        (path for path in normalized if path.endswith("/db/migration/V1__initial_schema.sql")),
        "",
    )
    test_relative = next(
        (path for path in normalized if path.endswith("/persistence/PersistenceSchemaTest.java")),
        "",
    )
    if not migration_relative or not test_relative:
        return []
    migration = sandbox / migration_relative
    test = sandbox / test_relative
    if not migration.is_file() or (test.is_file() and not overwrite):
        return []
    source = migration.read_text(encoding="utf-8")
    tables = sorted({
        match.group("name").strip('"').lower()
        for match in re.finditer(
            r"\bCREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?P<name>\"?[A-Za-z_][A-Za-z0-9_]*\"?)",
            source,
            re.IGNORECASE,
        )
    })
    if not tables:
        return []
    parts = Path(test_relative).parts
    try:
        java_index = parts.index("java")
    except ValueError:
        return []
    package = ".".join(parts[java_index + 1 : -1])
    if not package:
        return []
    expected = ", ".join(f'"{table}"' for table in tables)
    test.parent.mkdir(parents=True, exist_ok=True)
    test.write_text(
        f'''package {package};

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.ResultSet;
import java.util.HashSet;
import java.util.Set;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;

class PersistenceSchemaTest {{
    @Test
    void appliesInitialMigrationAndCreatesEveryDeclaredTable() throws Exception {{
        Flyway flyway = Flyway.configure()
                .dataSource("jdbc:h2:mem:persistence_schema;DB_CLOSE_DELAY=-1", "sa", "")
                .locations("classpath:db/migration")
                .load();
        flyway.migrate();

        Set<String> actualTables = new HashSet<>();
        try (Connection connection = flyway.getConfiguration().getDataSource().getConnection()) {{
            DatabaseMetaData metadata = connection.getMetaData();
            try (ResultSet rows = metadata.getTables(null, null, null, new String[]{{"TABLE"}})) {{
                while (rows.next()) {{
                    actualTables.add(rows.getString("TABLE_NAME").toLowerCase());
                }}
            }}
        }}

        for (String expectedTable : Set.of({expected})) {{
            assertTrue(actualTables.contains(expectedTable), "Missing migrated table: " + expectedTable);
        }}
    }}
}}
''',
        encoding="utf-8",
    )
    return [test_relative]


def gradle_command() -> list[str]:
    """Use EasyDep's pinned wrapper instead of a machine-global Gradle."""
    wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
    wrapper = Path(__file__).resolve().parents[2] / "tools" / "gradle" / wrapper_name
    if not wrapper.is_file():
        raise RuntimeError(f"Bundled Gradle Wrapper is missing: {wrapper}")
    return [str(wrapper)] if os.name == "nt" else ["sh", str(wrapper)]


class WorkspaceVerificationError(RuntimeError):
    def __init__(self, evidence: dict[str, object]):
        self.evidence = evidence
        output = next(
            (
                str(evidence.get(key)).strip()
                for key in ("testResults", "stderr", "stdout")
                if str(evidence.get(key) or "").strip()
            ),
            "No verification output was captured",
        )
        if output == "No verification output was captured" and evidence.get("command"):
            output = f"command={evidence['command']}; {output}"
        if len(output) > 1000:
            # The tail of a Gradle/JUnit trace is usually framework plumbing
            # and can hide the assertion or root exception at the beginning.
            # Preserve both ends so the repair prompt identifies the real
            # failure without another retry just to recover evidence.
            output = (
                output[:600]
                + "\n... [verification output truncated] ...\n"
                + output[-350:]
            )
        super().__init__("Agent workspace verification failed: " + output)


def verification_timeout_seconds() -> int:
    """느린 로컬 환경에서도 검증 병목을 관측할 수 있도록 제한 시간을 구성한다."""
    return max(
        60,
        int(os.getenv("IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS", "900")),
    )


def verify_run_workspace(
    run_root: Path,
    report_name: str = "final-verification.json",
    *,
    verify_frontend: bool = True,
    verify_end_to_end: bool = True,
) -> dict[str, object]:
    """Verify promoted sources from a short ASCII-safe workspace.

    Frontend dependencies are intentionally installed only for the frontend
    phase and the final release gate.  Running ``npm ci`` after every backend
    phase creates an unrelated network-dependent bottleneck and can mask a
    successful backend build.
    """
    _verify_control_boundary_contract(run_root)
    if verify_end_to_end:
        _verify_e2e_semantic_contract(run_root)
    sandbox = prepare_agent_workspace(
        run_root,
        {"task_id": "final-verification", "allowed_write_paths": []},
    )
    if not verify_end_to_end:
        # 상위 phase를 다시 수리할 때 이전 E2E 파일이 이미 남아 있을 수 있다.
        # 아직 재검증 순서가 오지 않은 그 테스트 때문에 wiring 수리가 막히지 않도록,
        # 복사된 임시 검증 공간에서만 manifest에 등록된 E2E 파일을 제외한다.
        manifest_path = run_root / "reports" / "run-manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            test_root = (sandbox / "application" / "src" / "test").resolve()
            for task in manifest.get("implementation_tasks", []):
                if task.get("task_type") != "integration-test":
                    continue
                for relative in task.get("allowed_write_paths", []):
                    target = (sandbox / str(relative)).resolve()
                    if target.is_relative_to(test_root) and target.is_file():
                        target.unlink()
    verification = verify_agent_workspace(sandbox)
    frontend_verification = None
    if (
        verify_frontend
        and (sandbox / "application" / "frontend" / "package.json").is_file()
    ):
        frontend_verification = verify_frontend_workspace(sandbox)
    result = {
        "status": "SUCCEEDED",
        "workspace": str(sandbox),
        "verification": verification,
        "frontendVerification": frontend_verification,
    }
    if Path(report_name).name != report_name or not report_name.endswith(".json"):
        raise ValueError(f"Invalid verification report name: {report_name}")
    report = run_root / "reports" / report_name
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _verify_control_boundary_contract(run_root: Path) -> None:
    """승격된 모든 Control이 호출 Boundary를 다시 참조하지 않는지 확인한다.

    작업별 검사 뒤 다른 수리가 이어져도 최종 소스 조합은 다시 확인해야 한다. 각
    Control 작업이 이미 저장한 typed sequence와 파일 목록을 그대로 사용하므로 별도
    추측 규칙이나 새 계약은 필요하지 않다.
    """

    manifest_path = run_root / "reports" / "run-manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for task in manifest.get("implementation_tasks", []):
        if not isinstance(task, dict) or task.get("task_type") != "control":
            continue
        context_path = run_root / str(task.get("context_file") or "")
        context = (
            json.loads(context_path.read_text(encoding="utf-8"))
            if context_path.is_file()
            else {}
        )
        violations.extend(
            control_boundary_dependency_violations(
                run_root,
                [str(path) for path in task.get("allowed_write_paths", [])],
                context.get("sequence", []),
            )
        )
    if violations:
        raise WorkspaceVerificationError(
            {
                "command": ["control-dependency-contract-gate"],
                "exitCode": 1,
                "stderr": "\n".join(violations),
                "testResults": "",
            }
        )


def _verify_e2e_semantic_contract(run_root: Path) -> None:
    """Gradle보다 먼저 E2E 파일이 현재 계약과 실제 Spring 구성을 따르는지 확인한다.

    이전 checkpoint의 E2E 파일이 남은 채 상위 코드가 수리되면 Gradle 오류가 엉뚱한
    production task를 가리킬 수 있다. 먼저 E2E 자체를 검사하면 코딩 에이전트가 그 파일만
    고치고, 비싼 전체 빌드는 의미 검사를 통과한 뒤 한 번만 실행된다.
    """

    manifest_path = run_root / "reports" / "run-manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    task = next(
        (
            item
            for item in manifest.get("implementation_tasks", [])
            if isinstance(item, dict) and item.get("task_type") == "integration-test"
        ),
        None,
    )
    if task is None:
        return
    output = next(
        (
            run_root / str(path)
            for path in task.get("allowed_write_paths", [])
            if str(path).endswith(".java")
        ),
        None,
    )
    context_path = run_root / str(task.get("context_file") or "")
    context = (
        json.loads(context_path.read_text(encoding="utf-8"))
        if context_path.is_file()
        else {}
    )
    contract = context.get("semanticContract")
    violations = e2e_contract_violations(
        output or run_root / "missing-e2e-test.java",
        contract if isinstance(contract, dict) else None,
    )
    if violations:
        raise WorkspaceVerificationError(
            {
                "command": ["e2e-semantic-contract-gate"],
                "exitCode": 1,
                "stderr": "\n".join(violations),
                "testResults": "",
            }
        )


def verify_agent_workspace(
    sandbox: Path,
    task_type: str = "",
    allowed_write_paths: list[str] | None = None,
    *,
    force_rerun: bool = False,
) -> dict[str, object]:
    if task_type == "frontend-implementation":
        return verify_frontend_workspace(sandbox)
    executable = gradle_command()
    command = task_verification_command(
        executable, task_type, allowed_write_paths, force_rerun=force_rerun
    )
    started = time.monotonic()
    # Windows workers occasionally fail before Gradle reaches the project when
    # the shared cache's virtual-file-system journal is locked or unavailable
    # (FileAccessTimeJournal / WinError 5 / I/O error).  Disable VFS watching
    # for verification: the task gates are short-lived and correctness does not
    # depend on Gradle's filesystem watcher.  Keep any caller-provided options.
    environment = os.environ.copy()
    gradle_opts = environment.get("GRADLE_OPTS", "").strip()
    vfs_option = "-Dorg.gradle.vfs.watch=false"
    if vfs_option not in gradle_opts:
        environment["GRADLE_OPTS"] = f"{gradle_opts} {vfs_option}".strip()
    result = subprocess.run(
        command,
        cwd=sandbox / "application",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=verification_timeout_seconds(),
        check=False,
    )
    evidence = {
        "command": command,
        "exitCode": result.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdout": result.stdout[-16000:],
        "stderr": result.stderr[-16000:],
        "testResults": read_gradle_test_failures(sandbox),
    }
    if result.returncode != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def task_verification_command(
    executable: list[str],
    task_type: str = "",
    allowed_write_paths: list[str] | None = None,
    *,
    force_rerun: bool = False,
) -> list[str]:
    """작업별로 필요한 Gradle 검사만 고르고 재검증이면 캐시를 우회한다."""
    if not task_type or allowed_write_paths is None:
        command = [
            *executable,
            "compileJava",
            "bootJar",
            "test",
            "--build-cache",
        ]
        if force_rerun:
            command.append("--rerun-tasks")
        return command

    test_names = sorted(
        {
            Path(path).stem
            for path in allowed_write_paths
            if "/src/test/" in "/" + path.replace("\\", "/")
            and path.endswith(".java")
        }
    )
    command = [*executable, "compileJava"]
    if test_names:
        command.extend(["testClasses", "test"])
        for test_name in test_names:
            command.extend(["--tests", f"*{test_name}"])
    # These workspaces share Gradle's user home, so allowing the daemon to
    # remain alive avoids starting a one-shot JVM for every agent task.
    command.append("--build-cache")
    # 같은 임시 경로를 지우고 다시 만드는 재시도에서는 Windows의 짧은 파일 시간 간격과
    # Gradle daemon의 이전 snapshot 때문에 바뀐 소스를 UP-TO-DATE로 오판할 수 있다.
    # 첫 시도는 캐시를 쓰되, 재시도와 내부 수리는 실제 compiler를 반드시 다시 실행한다.
    if force_rerun:
        command.append("--rerun-tasks")
    return command


def verify_frontend_workspace(sandbox: Path) -> dict[str, object]:
    evidence = run_frontend_verification(sandbox, run_frontend_command)
    if evidence["exitCode"] != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def api_adapter_contract_violations(
    sandbox: Path, allowed_write_paths: list[str]
) -> list[str]:
    """Reject compilable web adapters that never implement their OpenAPI port.

    Java permits a plain class with no mappings to compile, but Spring then
    exposes no routes and every E2E request returns 404.  The generated
    interface is the authoritative route contract, so require the adapter to
    implement it before the task can be promoted.
    """
    violations: list[str] = []
    for relative in allowed_write_paths:
        normalized = relative.replace("\\", "/")
        if "/src/main/" not in normalized or not normalized.endswith("Controller.java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        adapter = path.stem
        api_name = adapter.removesuffix("Controller")
        source = path.read_text(encoding="utf-8")
        if not re.search(
            rf"\bclass\s+{re.escape(adapter)}\b[^{{]*\bimplements\s+"
            rf"{re.escape(api_name)}\b",
            source,
            flags=re.DOTALL,
        ):
            violations.append(
                f"{normalized}: controller must implement generated {api_name}"
            )
            continue
        # A compilable adapter can still hallucinate a project-local package
        # (for example ``bce.control`` or ``web.dto``) that is not part of the
        # generated contracts.  Resolve imports against the copied source tree
        # before invoking Gradle so the agent receives a precise contract
        # failure instead of spending a build/retry on invented types.
        package_match = re.search(r"\bpackage\s+([\w.]+)\s*;", source)
        package_root = package_match.group(1).split(".adapter.", 1)[0] if package_match else ""
        if package_root:
            source_root = sandbox / "application" / "src" / "main" / "java"
            for imported in re.findall(
                rf"\bimport\s+({re.escape(package_root)}\.[\w.]+)\s*;", source
            ):
                imported_path = source_root / Path(*imported.split(".")).with_suffix(".java")
                if not imported_path.is_file():
                    violations.append(
                        f"{normalized}: imported project type does not exist: {imported}"
                    )
        api_sources = list(
            (sandbox / "application" / "src" / "main" / "java").rglob(
                f"{api_name}.java"
            )
        )
        if not api_sources:
            continue
        api_contract = api_sources[0].read_text(encoding="utf-8")
        statuses = sorted(
            {
                int(value)
                for value in re.findall(
                    r'@ApiResponse\s*\(\s*responseCode\s*=\s*"(\d{3})"',
                    api_contract,
                )
            }
        )
        # Only domain decisions must be observable in the adapter.  Validation,
        # authorization, not-found, and infrastructure failures can be mapped
        # by Spring's global exception handling; requiring a branch for each of
        # them makes a void BCE command look like it can manufacture outcomes it
        # does not actually expose.  409/422 remain strict because they are
        # business outcomes and must be represented by the Control contract.
        required_statuses = {409, 422}
        status_tokens = {
            200: ("ResponseEntity.ok", "HttpStatus.OK", "status(200)"),
            201: ("ResponseEntity.created", "HttpStatus.CREATED", "status(201)"),
            202: (
                "ResponseEntity.accepted",
                "HttpStatus.ACCEPTED",
                "status(202)",
            ),
            204: (
                "ResponseEntity.noContent",
                "HttpStatus.NO_CONTENT",
                "status(204)",
            ),
            400: (
                "ResponseEntity.badRequest",
                "HttpStatus.BAD_REQUEST",
                "status(400)",
            ),
            401: (
                "ResponseEntity.status(HttpStatus.UNAUTHORIZED",
                "HttpStatus.UNAUTHORIZED",
                "status(401)",
            ),
            403: (
                "ResponseEntity.status(HttpStatus.FORBIDDEN",
                "HttpStatus.FORBIDDEN",
                "status(403)",
            ),
            404: (
                "ResponseEntity.notFound",
                "HttpStatus.NOT_FOUND",
                "status(404)",
            ),
            409: (
                "ResponseEntity.status(HttpStatus.CONFLICT",
                "HttpStatus.CONFLICT",
                "status(409)",
            ),
            422: (
                "ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY",
                "HttpStatus.UNPROCESSABLE_ENTITY",
                "status(422)",
            ),
            500: (
                "ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR",
                "HttpStatus.INTERNAL_SERVER_ERROR",
                "status(500)",
            ),
            503: (
                "ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE",
                "HttpStatus.SERVICE_UNAVAILABLE",
                "status(503)",
            ),
        }
        for status in statuses:
            if status not in required_statuses:
                continue
            tokens = status_tokens.get(status)
            if tokens and not any(token in source for token in tokens):
                violations.append(
                    f"{normalized}: missing executable HTTP {status} mapping from {api_name}; "
                    "@ApiResponse/@ApiResponses annotations are documentation only"
                )
    return violations


def boundary_adapter_contract_violations(
    sandbox: Path, allowed_write_paths: list[str], sequence: object = None
) -> list[str]:
    """Reject a state adapter that discards a required Boundary -> Control flow.

    A Boundary task may legitimately keep an optional value unset, but an
    explicit request-to-Control message followed by a value response must not
    be implemented as ``return null``.  That compiles and its focused adapter
    test can still pass, only to make the first real E2E request return 401/404.
    The sequence contract is used only to identify this required delegation;
    no domain-specific method names are assumed.
    """
    has_forward_flow = _has_boundary_control_request(sequence)
    if not has_forward_flow:
        return []
    violations: list[str] = []
    for relative in allowed_write_paths:
        normalized = relative.replace("\\", "/")
        if "/src/main/" not in f"/{normalized}" or not normalized.endswith("Adapter.java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"\breturn\s+null\s*;", source):
            violations.append(
                f"{normalized}: Boundary adapter discards a required sequence flow with `return null`; "
                "delegate/configure the exact contract result instead"
            )
    return violations


def control_boundary_dependency_violations(
    sandbox: Path,
    allowed_write_paths: list[str],
    sequence: object = None,
) -> list[str]:
    """Control이 자신을 호출한 Boundary를 다시 호출하는 순환 구조를 막는다.

    typed sequence의 ``return`` 메시지는 Java 메서드 호출이 아니다. Control에서 Boundary로
    향하는 별도의 ``sync`` 또는 ``async`` 메시지가 있을 때만 그 Boundary 의존성을 허용한다.
    이 검사는 도메인 이름을 가정하지 않고 참여자 종류와 메시지 방향만 사용한다.
    """

    diagrams = sequence if isinstance(sequence, list) else [sequence]
    boundary_names: set[str] = set()
    allowed_outgoing: set[str] = set()
    for diagram in diagrams:
        if not isinstance(diagram, dict):
            continue
        participants = [
            item
            for item in diagram.get("participants", diagram.get("Participants", []))
            if isinstance(item, dict)
        ]
        by_alias = {
            str(item.get("alias") or item.get("name") or ""): item
            for item in participants
        }
        for item in participants:
            if str(item.get("kind", "")).casefold() == "boundary":
                boundary_names.add(
                    str(item.get("source_class") or item.get("name") or "")
                )
        for message in diagram.get("messages", diagram.get("Messages", [])):
            if not isinstance(message, dict):
                continue
            source = by_alias.get(str(message.get("source", "")), {})
            target = by_alias.get(str(message.get("target", "")), {})
            if (
                str(message.get("type", "")).casefold() in {"sync", "async"}
                and str(source.get("kind", "")).casefold() == "control"
                and str(target.get("kind", "")).casefold() == "boundary"
            ):
                allowed_outgoing.add(
                    str(target.get("source_class") or target.get("name") or "")
                )

    violations: list[str] = []
    for relative in allowed_write_paths:
        normalized = relative.replace("\\", "/")
        if "/src/main/" not in f"/{normalized}" or not normalized.endswith("Service.java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        source = java_code_without_comments_and_strings(
            path.read_text(encoding="utf-8")
        )
        used = {
            name
            for name in boundary_names
            if name and re.search(rf"\b{re.escape(name)}\b", source, re.IGNORECASE)
        }
        used.update(
            re.findall(r"(?m)^\s*import\s+[\w.]+\.bce\.([A-Za-z_$]\w*Boundary)\s*;", source)
        )
        unexpected = sorted(used - allowed_outgoing)
        if unexpected:
            violations.append(
                f"{normalized}: Control must not depend on its calling Boundary: "
                + ", ".join(unexpected)
                + "; a return message is not a reverse method call"
            )
    return violations


def _has_boundary_control_request(sequence: object) -> bool:
    """typed sequence에서 Boundary가 Control에 보낸 요청이 있는지 확인한다."""
    if isinstance(sequence, str):
        return bool(re.search(r"\b[A-Za-z_]\w*\s*->\s*[A-Za-z_]\w*\s*:", sequence))
    diagrams = sequence if isinstance(sequence, list) else [sequence]
    for diagram in diagrams:
        if not isinstance(diagram, dict):
            continue
        participant_kinds = {
            str(item.get("alias", "")): str(item.get("kind", "")).casefold()
            for item in diagram.get("participants", diagram.get("Participants", []))
            if isinstance(item, dict)
        }
        if any(
            isinstance(message, dict)
            and str(message.get("type", "")).casefold() in {"sync", "async"}
            and participant_kinds.get(str(message.get("source", ""))) == "boundary"
            and participant_kinds.get(str(message.get("target", ""))) == "control"
            for message in diagram.get("messages", diagram.get("Messages", []))
        ):
            return True
    return False


def production_placeholder_markers(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Reject actionable unresolved markers in contracted production Java outputs.

    A prose comment containing the generic word ``placeholder`` has no runtime
    effect and is not evidence of incomplete code.  Keep this gate focused on
    actionable markers, including a direct ``UnsupportedOperationException``
    that explicitly says an API operation is unimplemented.  The latter can
    compile while making a documented endpoint unusable at runtime.
    """
    evidence: list[str] = []
    pattern = re.compile(
        r"\b(?:TODO|FIXME)\b"
        r"|\bthrow\s+new\s+UnsupportedOperationException\s*\(",
        re.IGNORECASE,
    )
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if "/src/main/java/" not in f"/{normalized}" or not normalized.endswith(".java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # Comments may document a follow-up or an optional refinement; they
            # do not make the executable production path incomplete. Strip
            # line comments before applying the marker gate.
            code_line = re.sub(r"//.*$", "", line).strip()
            if code_line and pattern.search(code_line):
                evidence.append(f"{normalized}:{number}: {line.strip()}")
    return evidence


def production_test_library_markers(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Reject test-only Mockito/JUnit use in contracted production Java files."""
    evidence: list[str] = []
    pattern = re.compile(
        r"^\s*import\s+(?:static\s+)?org\.(?:mockito|junit)\.|\bMockito\s*\.",
        re.MULTILINE,
    )
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if "/src/main/java/" not in f"/{normalized}" or not normalized.endswith(".java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                evidence.append(f"{normalized}:{number}: {line.strip()}")
    return evidence


def persistence_reserved_identifier_markers(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Detect H2-reserved persistence names before expensive downstream tests.

    JPA can compile with a reserved ``@Column`` name and only fail when an
    integration test executes Hibernate SQL.  Detect it while the owning
    persistence task is still running, so its focused repair prompt can fix
    the entity or migration instead of repeatedly rewriting an unrelated E2E
    test.
    """
    evidence: list[str] = []
    names = "|".join(re.escape(name) for name in SQL_RESERVED_IDENTIFIERS)
    java_pattern = re.compile(
        rf'@(?:Column|Table)\s*\([^)]*\bname\s*=\s*"({names})"',
        re.IGNORECASE,
    )
    sql_column_pattern = re.compile(
        rf"(?:^|,)\s*({names})\s+(?:[a-z]+|[a-z]+\s*\([^)]*\))",
        re.IGNORECASE,
    )
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if not normalized.endswith((".java", ".sql")):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = java_pattern.search(line) if normalized.endswith(".java") else sql_column_pattern.search(line)
            if match:
                identifier = match.group(1)
                evidence.append(
                    f"{normalized}:{number}: H2 reserved identifier `{identifier}` must be renamed "
                    "consistently in the JPA mapping and migration."
                )
    return evidence


def repair_persistence_schema_table_quoting(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Align harmless quoted table names with the JPA table contract.

    H2 treats ``"section"`` and the unquoted identifier ``SECTION`` as
    different names.  Agents sometimes quote an ordinary lowercase table name
    in Flyway while ``@Table(name = "section")`` remains unquoted in Hibernate.
    For non-reserved names, remove only table-position quotes so both layers
    resolve the same identifier. Reserved names remain untouched and are
    handled by the reserved-identifier gate.
    """
    entity_tables: set[str] = set()
    table_pattern = re.compile(
        r'@Table\s*\([^)]*\bname\s*=\s*"(?P<name>[a-z][a-z0-9_]*)"',
        re.IGNORECASE,
    )
    entity_root = sandbox / "application" / "src" / "main" / "java"
    for entity in entity_root.rglob("*Entity.java"):
        entity_tables.update(
            match.group("name") for match in table_pattern.finditer(
                entity.read_text(encoding="utf-8")
            )
        )
    if not entity_tables:
        return []

    repaired: list[str] = []
    reserved = set(SQL_RESERVED_IDENTIFIERS)
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if not normalized.endswith(".sql"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        updated = original
        for table in sorted(entity_tables):
            if table in reserved:
                continue
            pattern = re.compile(
                rf"(?P<prefix>\b(?:CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|"
                rf"REFERENCES|ON)\s+)\"{re.escape(table)}\"",
                re.IGNORECASE,
            )
            updated, count = pattern.subn(r"\g<prefix>" + table, updated)
            if count:
                repaired.append(f"{normalized}: unquoted table {table}")
        if updated != original:
            path.write_text(updated, encoding="utf-8")
    return repaired


def read_gradle_test_failures(sandbox: Path) -> str:
    result_dir = sandbox / "application" / "build" / "test-results" / "test"
    reports: list[str] = []
    for report in sorted(result_dir.glob("*.xml")):
        try:
            root = ET.parse(report).getroot()
        except ET.ParseError:
            continue
        for case in root.findall("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                continue
            message = problem.get("message") or "test failed"
            detail = (problem.text or "").strip()
            if detail:
                message += "\n" + summarize_test_failure(detail)
            reports.append(f"{case.get('classname')}.{case.get('name')}: {message}")
    return _truncate_log_snippet("\n\n".join(reports), max_chars=8000)


def _truncate_log_snippet(text: str, max_chars: int = 8000) -> str:
    if len(text) <= max_chars:
        return text
    marker = "\n... [verification output truncated] ...\n"
    remaining = max_chars - len(marker)
    head = remaining // 2
    return text[:head] + marker + text[-(remaining - head):]


def summarize_test_failure(detail: str) -> str:
    """긴 stack trace에서 실제 원인과 애플리케이션 호출 위치를 함께 남긴다."""
    lines = [line.rstrip() for line in detail.splitlines() if line.strip()]
    causal = [
        line
        for line in lines
        if re.search(
            r"(?:Caused by:|Suppressed:|Error creating bean|Requested bean is currently in creation|"
            r"NoSuchBeanDefinitionException|NoUniqueBeanDefinitionException|UnsatisfiedDependencyException|"
            r"BeanCurrentlyInCreationException|AnnotationException|mappedBy|"
            r"JdbcTypeRecommendationException|Could not determine recommended JdbcType|"
            r"does not exist in (?:the )?target entity|HibernateException)",
            line,
        )
    ]
    # 예외 종류만 남기면 실제로 실패한 EasyDep 클래스가 사라져 수리 작업을 잘못
    # 선택하게 된다. 프레임워크 내부 호출은 제외하고 애플리케이션 stack frame을
    # 함께 보존한다.
    application_frames = [
        line
        for line in lines
        if re.search(r"\bat (?:app//)?(?!org\.|java\.|jdk\.|worker\.)[A-Za-z_]", line)
    ]
    causal.extend(application_frames[:12])
    selected = causal or lines[:30]
    selected.extend(lines[-8:])
    return _truncate_log_snippet("\n".join(dict.fromkeys(selected)), max_chars=8000)
