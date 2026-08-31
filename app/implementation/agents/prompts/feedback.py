"""실제 build 오류와 현재 source를 다음 OpenHands 수리 턴에 전달한다."""

from __future__ import annotations

import json


def render_verification_feedback(
    evidence: dict[str, object],
    current_sources: str = "",
    repair_targets: list[str] | None = None,
    semantic_contract: dict[str, object] | None = None,
    generated_contracts: str = "",
) -> str:
    """오류 종류를 추측하지 않고 검사기가 남긴 원문과 관련 파일만 보여 준다."""
    output = _verification_output(evidence)
    targets = "\n".join(f"- `{path}`" for path in repair_targets or [])
    contract = (
        "\n\n## Generated contracts (read-only)\n```text\n"
        + generated_contracts
        + "\n```"
        if generated_contracts
        else ""
    )
    scenario = (
        "\n\n## HTTP flow contract (read-only)\n```json\n"
        + json.dumps(semantic_contract, ensure_ascii=False, indent=2)
        + "\n```"
        if semantic_contract
        else ""
    )
    return f"""The build or focused test failed. Read the complete diagnostic and current
sources, find the actual cause, and repair it without asking the user. Keep earlier correct
behavior and generated public contracts. Do not hide the failure by deleting tests,
disabling validation, returning fabricated values, or adding placeholder code.

You may edit only these repair targets:
{targets}

## Verification diagnostic
```text
{output}
```

## Current repair sources
```text
{current_sources}
```{contract}{scenario}

Use the restricted editor for the necessary changes and call `finish`. The runtime will run
the same build or test again and return another repair turn if it still fails.
"""


def render_frontend_verification_feedback(
    evidence: dict[str, object],
    current_sources: str = "",
    repair_targets: list[str] | None = None,
    generated_contracts: str = "",
) -> str:
    """frontend build 오류도 backend와 같은 짧은 수리 계약으로 전달한다."""
    targets = "\n".join(f"- `{path}`" for path in repair_targets or [])
    contracts = (
        "\n\n## Generated TypeScript contracts (read-only)\n```text\n"
        + generated_contracts
        + "\n```"
        if generated_contracts
        else ""
    )
    return f"""The frontend production build failed. Diagnose the error from the real output
and current sources, then repair it without asking the user. Preserve files under
`src/generated` and use their exported client instead of writing another HTTP client.

You may edit only these repair targets:
{targets}

## Verification diagnostic
```text
{_verification_output(evidence)}
```

## Current repair sources
```text
{current_sources}
```{contracts}

Use the restricted editor for the necessary changes and call `finish`. The runtime will run
the production build again and continue the repair loop if it still fails.
"""


def _verification_output(evidence: dict[str, object]) -> str:
    """명령, 표준 출력, 오류와 test 결과를 한 번만 이어 붙인다."""
    parts = [
        str(evidence.get(key, "")).strip()
        for key in ("command", "stdout", "stderr", "testResults")
        if evidence.get(key)
    ]
    return "\n\n".join(parts)[-24000:]
