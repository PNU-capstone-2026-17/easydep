"""실제 build 오류를 현재 OpenHands 대화의 다음 수리 메시지로 전달한다."""

def render_verification_feedback(
    evidence: dict[str, object],
    repair_targets: list[str] | None = None,
) -> str:
    """같은 대화에 실제 검사 결과와 수정 가능 범위만 추가한다."""
    output = _verification_output(evidence)
    targets = "\n".join(f"- `{path}`" for path in repair_targets or [])
    return f"""The build or focused test failed. Read the complete diagnostic and current
source with the file editor, find the actual cause, and repair it without asking the user.
Keep earlier correct behavior and generated public contracts. Do not hide the failure by
deleting tests, disabling validation, returning fabricated values, or adding placeholder code.

You may edit only these repair targets:
{targets}

## Verification diagnostic
```text
{output}
```

Use the restricted editor, run `run_task_check` again, and continue in this conversation until
the check passes. Then call `finish`.
"""


def render_frontend_verification_feedback(
    evidence: dict[str, object],
    repair_targets: list[str] | None = None,
) -> str:
    """frontend build 오류도 같은 대화에 짧게 전달한다."""
    targets = "\n".join(f"- `{path}`" for path in repair_targets or [])
    return f"""The frontend production build failed. Diagnose the error from the real output
and inspect current sources with the file editor, then repair it without asking the user. Preserve files under
`src/generated` and use their exported client instead of writing another HTTP client.

You may edit only these repair targets:
{targets}

## Verification diagnostic
```text
{_verification_output(evidence)}
```

Use the restricted editor, run `run_task_check` again, and continue in this conversation until
the production build passes. Then call `finish`.
"""


def _verification_output(evidence: dict[str, object]) -> str:
    """명령, 표준 출력, 오류와 test 결과를 한 번만 이어 붙인다."""
    parts = [
        str(evidence.get(key, "")).strip()
        for key in ("command", "stdout", "stderr", "testResults")
        if evidence.get(key)
    ]
    return "\n\n".join(parts)[-24000:]
