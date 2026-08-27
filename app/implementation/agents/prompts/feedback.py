from __future__ import annotations

import json
import re


def render_verification_feedback(
    evidence: dict[str, object],
    current_sources: str = "",
    repair_targets: list[str] | None = None,
    semantic_contract: dict[str, object] | None = None,
    api_controls: list[str] | None = None,
    api_contracts: str = "",
    generated_contracts: str = "",
) -> str:
    output = (
        str(evidence.get("stdout", ""))
        + "\n"
        + str(evidence.get("stderr", ""))
        + "\n"
        + str(evidence.get("testResults", ""))
    )[-20000:]
    hints = verification_failure_hints(output)
    target_text = "\n".join(f"- `{path}`" for path in (repair_targets or []))
    contract_text = ""
    if semantic_contract:
        contract_text = f"""
E2E semantic contract (immutable):
```json
{json.dumps(semantic_contract, ensure_ascii=False, indent=2)}
```
For an E2E repair, preserve every existing passing test and append or correct the
missing scenario tests. Do not replace the file with a smaller sample and do not
remove scenarios that are not named in the current diagnostic.
        """
    api_control_text = ""
    if api_controls:
        api_control_text = f"""
API adapter collaborator contract (immutable):
Use only these exact BCE Control interfaces: {", ".join(api_controls)}.
Import them from the package shown in the embedded contracts. Do not derive a
resource-named substitute such as `StudentsControl`, and do not leave TODO or
placeholder code in the controller.
"""
    api_contract_text = ""
    if api_contracts:
        api_contract_text = f"""
Exact generated API/BCE contracts for this repair (immutable):
```java
{api_contracts}
```
Use only these declarations. If they cannot express a documented response,
do not invent an exception, DTO, package, or Control method.
"""
    generated_contract_text = ""
    if generated_contracts:
        generated_contract_text = f"""
Exact generated contracts for this repair (immutable):
```text
{generated_contracts}
```
Use only declarations that appear above. Do not invent a package, type, method,
or accessor when the contract does not provide one.
"""
    return f"""The orchestrator compiled and tested your files, and verification failed.
Fix every reported error in the existing allowed files, including test compilation errors.
Generated contracts are authoritative: never assume a return value or method that is absent from their exact signatures.
Do not invent Java aliases, access private fields, or use reflection to bypass a generated contract.
For a cannot-find-symbol diagnostic, remove the absent call or field access. Do not invent a replacement accessor; leave a focused TODO when the contract exposes no equivalent.
For an already-defined-method diagnostic, keep one implementation of that signature and remove the duplicate.
For Mockito negative verification, use verify(mock, never()).method(...); verifyNever does not exist.
For a 'void type not allowed here' diagnostic, delete the unnecessary when(mock.voidMethod(...)) stub. If custom behavior is required, use Mockito doAnswer(...).when(mock).voidMethod(...).
Use `restricted_file_editor` with `command: 'create'` to replace each affected allowlisted file completely. Do not call view or str_replace during this repair round; the current sources are included below.
Write only the following repair targets; do not rewrite any other file:
{target_text}
Emit all required `restricted_file_editor` create calls in one response, then call finish immediately.

Failure-specific guidance:
{hints}

{contract_text}

{api_control_text}

{api_contract_text}

{generated_contract_text}

Gradle output:
```text
{output}
```

Current allowlisted sources:
{current_sources}
"""


def render_frontend_verification_feedback(
    evidence: dict[str, object],
    current_sources: str = "",
    repair_targets: list[str] | None = None,
    generated_contracts: str = "",
) -> str:
    output = (
        str(evidence.get("stdout", ""))
        + "\n"
        + str(evidence.get("stderr", ""))
    )[-20000:]
    targets = "\n".join(f"- `{path}`" for path in (repair_targets or []))
    contracts = (
        "\nGenerated TypeScript client contracts (immutable):\n```text\n"
        + generated_contracts
        + "\n```\n"
        if generated_contracts
        else ""
    )
    return f"""The TypeScript frontend contract gate or npm production build failed.
Fix every reported error using only the repair targets below. Preserve project configuration
and all files under src/generated. Use exact generated API/model exports; do not replace them
with fetch, axios, XMLHttpRequest, or hard-coded endpoint strings.

Repair targets:
{targets}

Marker-specific rule:
- Remove every TODO, FIXME, or PLACEHOLDER token from the repair targets, including
  prose comments. Replace placeholder values or branches with the actual contracted UI
behavior; do not merely rename the marker or leave a hard-coded demo fallback.
{contracts}

Verification output:
```text
{output}
```

Current allowlisted sources:
```text
{current_sources}
```
"""


def verification_failure_hints(output: str) -> str:
    hints: list[str] = []
    missing_paths = re.findall(r"Missing HTTP path evidence:\s*(\S+)", output)
    if missing_paths:
        expected = ", ".join(dict.fromkeys(missing_paths))
        hints.append(
            "- E2E path contract: the request URI must resolve exactly to the listed "
            f"template(s) ({expected}). Use the template literally or concatenate only "
            "the declared path variables; remove any extra suffix, prefix, or resource "
            "segment. Do not substitute a similar endpoint."
        )
    missing_methods = re.findall(
        r"Missing HTTP method evidence for scenario:\s*(\S+\s+\S+)", output
    )
    if missing_methods:
        expected = ", ".join(dict.fromkeys(missing_methods))
        hints.append(
            "- E2E method contract: invoke the exact HTTP verb with TestRestTemplate "
            f"or MockMvc for each listed scenario ({expected}); do not replace it with "
            "a repository or controller call."
        )
    minimum_match = re.search(
        r"Expected at least (\d+) independent E2E scenarios, found (\d+)",
        output,
    )
    if minimum_match:
        hints.append(
            "- E2E scenario coverage: preserve every existing @Test method and append one "
            f"independent test for each missing scenario until there are at least {minimum_match.group(1)}. "
            "Do not replace the current flow with a smaller sample."
        )
    if 'expected "identifier"' in output or "Syntax error in SQL statement" in output or "JdbcSQLSyntaxErrorException" in output:
        hints.append(
            "- SQL Syntax / Reserved Keyword: H2/SQL query or table definition contains a reserved keyword "
            "(such as `year`, `order`, `group`, `user`, `status`, `key`, `value`, `offset`, `limit`, `check`, `date`). "
            "Quote the identifier with backticks/quotes (e.g. `\"year\"` or ``` `year` ```) or rename the column/table in the schema and entity mapping."
        )
    if re.search(
        r"Referential integrity constraint violation|DataIntegrityViolationException|FOREIGN KEY",
        output,
        re.IGNORECASE,
    ):
        hints.append(
            "- Referential integrity failure: the E2E fixture uses an identifier that is a foreign-key "
            "target but has not been persisted. Seed the referenced parent entity through the exact "
            "Spring Data repository before issuing the HTTP request, using the same identifier that "
            "the request sends. Do not disable constraints, alter production mappings, or substitute "
            "a different fixture key."
        )
    if "Persistence entity schema mismatch" in output or "missing migration column" in output:
        hints.append(
            "- Persistence entity contract: the entity omits one or more columns declared by "
            "the Flyway migration/ERD. Add every listed @Column with the exact snake_case name, "
            "Java type, constructor argument, getter, and setter before allowing downstream "
            "mapping or E2E work. Do not weaken NOT NULL constraints or patch only the test fixture."
        )
    if "no suitable constructor found for" in output:
        hints.append(
            "- Persistence mapper constructor contract: the compiler error identifies a mapper call "
            "to an entity constructor. Preserve every existing public constructor used by "
            "BcePersistenceMapper (including relationship arguments such as StudentEntity and "
            "CourseEntity); add fields or an overloaded constructor instead of replacing that "
            "signature. Re-run the build after the entity repair."
        )
    if "StackOverflowError" in output:
        hints.append(
            "- Boundary/Control recursion: the stack trace shows a Boundary adapter calling its Control "
            "while that Control calls the same Boundary. Follow the sequence direction exactly: a "
            "Boundary delegates inbound input to a Control, while the Control must read/write its "
            "ERD-backed Repository or return a contract-supported value; it must never call back into "
            "the Boundary. Remove the recursive edge in the affected production service/adapter."
        )
    missing_column = re.findall(
        r'Column ["`]?([A-Za-z0-9_]+)["`]? not found', output, re.IGNORECASE
    )
    if missing_column:
        columns = ", ".join(dict.fromkeys(missing_column))
        hints.append(
            "- Persistence schema mismatch: H2 reports missing column(s) "
            f"{columns}. Align the JPA @Column names and migration columns using lower snake_case "
            "for both sides. Do not quote camelCase migration identifiers while Hibernate expects "
            "snake_case, and do not rename an ERD field arbitrarily."
        )
    if "InvalidPathException" in output:
        hints.append(
            "- Invalid JSONPath assertion: use a valid JsonPath expression against the actual "
            "response body (for example `$.token` or `$.studentId`). Inspect the generated DTO "
            "JSON shape first; do not pass a Java property expression, empty path, or fabricated "
            "field to JsonPath."
        )
    if "incompatible types" in output:
        hints.append(
            "- Incompatible types: Check package imports and exact contract types (e.g. java.time types vs domain models). "
            "Ensure constructor and method arguments match the exact declared parameter types in the contracts."
        )
        object_conversion = re.findall(
            r"(?P<source>[\w.$]+) cannot be converted to (?P<target>[\w.$]+)",
            output,
        )
        if object_conversion:
            pairs = ", ".join(
                f"{source} -> {target}" for source, target in object_conversion
            )
            hints.append(
                "- API/BCE request conversion: the generated HTTP parameter type is not the BCE "
                f"Control parameter ({pairs}). Do not pass the body directly or cast it. Build the "
                "exact BCE input using its public constructor/accessors; for an empty BCE DTO use "
                "its public no-argument constructor, and map every shared field for non-empty DTOs."
            )
    if re.search(r"expected:\s*<\d+>\s+but\s+was:\s*<\d+>", output, re.IGNORECASE):
        hints.append(
            "- HTTP status assertion mismatch: inspect the exact OpenAPI response contract and "
            "the controller's Control-result mapping before changing production code. In the "
            "success test, stub the exact Control method with exact converted arguments and its "
            "success value (for boolean results, `true`); reserve `false` for the documented "
            "conflict/failure scenario. Assert the contract's status, not a generic 200."
        )
    if "missing executable HTTP" in output or "missing explicit HTTP" in output:
        hints.append(
            "- API response contract: @ApiResponse/@ApiResponses annotations are documentation only. "
            "Transport-level statuses (400/401/403/404/500/503) may be handled by global exception "
            "mapping and do not need fabricated controller branches. For a reported 409/422, add a "
            "reachable ResponseEntity branch only when the exact Control contract exposes a domain "
            "result/outcome; do not guess an error condition from a void, entity, or collection return."
        )
    if re.search(r"package\s+[\w.]+\s+does not exist|cannot find symbol", output):
        hints.append(
            "- Project contract import: remove invented project packages or types. Inspect the "
            "generated BCE and API sources and import only the exact interfaces/models that exist "
            "there; do not create aliases such as bce.control or web.dto."
        )
    if "TooManyActualInvocations" in output:
        hints.append(
            "- TooManyActualInvocations: do not verify a broad matcher once when the "
            "method is legitimately called with multiple arguments. Remove incidental log "
            "verification or verify an exact argument."
        )
    if "TooFewActualInvocations" in output:
        hints.append(
            "- TooFewActualInvocations: the test expects more calls than the implementation "
            "actually makes. Remove duplicate verification or reduce times(n) to the exact "
            "observed count; do not add production calls solely to satisfy a mock count."
        )
    if "UnnecessaryStubbingException" in output or "Unnecessary stubbings detected" in output:
        hints.append(
            "- UnnecessaryStubbingException: delete every stubbing identified as unused. "
            "Do not make it lenient and do not add production behavior just to consume it."
        )
    if "NotAMockException" in output or "Argument passed to verify() is of type" in output:
        hints.append(
            "- Mockito verify requires a mock collaborator. Never verify the real service "
            "under test; call it normally and assert state or verify its mocked dependencies."
        )
    if "InvalidUseOfMatchersException" in output or "matchers expected" in output:
        hints.append(
            "- InvalidUseOfMatchersException: if one argument uses a matcher, wrap every "
            "argument in that invocation with a matcher. For example, use "
            "verify(timer).startTimer(eq(30), anyString()), not startTimer(30, anyString())."
        )
    if "Forbidden test bean configuration" in output or any(
        marker in output for marker in ("@mockbean", "@mockitobean", "@testconfiguration")
    ):
        hints.append(
            "- Forbidden test bean configuration: this is a real E2E test, so remove "
            "@MockBean, @MockitoBean, @TestConfiguration, @Bean, and @Primary from the "
            "test. Do not replace application beans or enable bean overriding. Use the real "
            "Spring application graph and autowire only concrete production adapters or "
            "repositories already present in the generated source."
        )
    if "ConnectionFails_HandlesFailure" in output and "Wanted but not invoked" in output:
        hints.append(
            "- Connection failure scenario: arrange the void openConnection call with "
            "doThrow(new RuntimeException(...)).when(webConnectionManager).openConnection(...). "
            "The service should catch that runtime failure, invoke handleFailure, and stop before "
            "captureResponse. Do not expect failure after arranging a successful void call."
        )
    if "Wanted but not invoked" in output:
        hints.append(
            "- Wanted but not invoked: trace the implementation branch. Remove conflicting "
            "stubs of the same method and do not verify a branch the arranged return values skip."
        )
    if "void type not allowed here" in output:
        hints.append(
            "- Void Mockito method: remove when(mock.voidMethod(...)); void mocks need no stub "
            "unless doAnswer(...).when(mock).voidMethod(...) is genuinely required."
        )
    return "\n".join(hints) or "- Fix the reported compiler or test failure without weakening meaningful assertions."
