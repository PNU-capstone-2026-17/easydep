from __future__ import annotations

import re


def render_verification_feedback(
    evidence: dict[str, object],
    current_sources: str = "",
    repair_targets: list[str] | None = None,
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
) -> str:
    output = (
        str(evidence.get("stdout", ""))
        + "\n"
        + str(evidence.get("stderr", ""))
    )[-20000:]
    targets = "\n".join(f"- `{path}`" for path in (repair_targets or []))
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
    if 'expected "identifier"' in output or "Syntax error in SQL statement" in output or "JdbcSQLSyntaxErrorException" in output:
        hints.append(
            "- SQL Syntax / Reserved Keyword: H2/SQL query or table definition contains a reserved keyword "
            "(such as `year`, `order`, `group`, `user`, `status`, `key`, `value`, `offset`, `limit`, `check`, `date`). "
            "Quote the identifier with backticks/quotes (e.g. `\"year\"` or ``` `year` ```) or rename the column/table in the schema and entity mapping."
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
