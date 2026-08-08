from __future__ import annotations


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
Use create to replace each affected allowlisted file completely. Do not call view or str_replace during this repair round; the current sources are included below.
Write only the following repair targets; do not rewrite any other file:
{target_text}
Emit all required create calls in one response, then call finish immediately.

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
