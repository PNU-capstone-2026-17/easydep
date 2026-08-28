import json
import os
from pathlib import Path

from openai import OpenAI

from app.testing.runtime.provider import (
    configured_api_key,
    configured_model,
)
from app.testing.runtime.provider import (
    settings as provider_settings,
)
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.requirements_source import (
    RequirementsUnavailable,
    functional_requirements,
)
from app.testing.utils.test_runner import run_dynamic_test
from app.validation import RepairLedger, stable_digest

SYSTEM_PROMPT = """You are an expert QA automation engineer.
Your task is to write a single Python script using `pytest` and `playwright` to test the functional requirements of a web application.
The target application is accessible via the URL provided in the environment variable `TARGET_URL`.

Requirements to test:
{requirements_text}

Write ONLY the Python code. Do not include markdown formatting or explanations.
Use synchronous playwright (`from playwright.sync_api import Page, expect`).
Ensure that tests make assertions based on the requirements.
Name each test after the requirement id it covers (e.g. `def test_fr1_...`).
If a requirement cannot be fully tested via UI (e.g. backend processing without UI), write an API test using the `requests` or `httpx` module if applicable, or add a comment `# Skip: Cannot be tested via UI`.
"""


def dynamic_functional_node(state: TestingState) -> dict:
    """
    Executes dynamic functional (acceptance) testing using LLM generated playwright/pytest tests.

    The requirements come from the requirements agent's stored ``REFINE_REQ``
    artifact, so the suite is written against what the user asked for rather
    than against the generated code.
    """
    run_id = state.get("run_id")
    target_url = state.get("target_url") or ""

    # 1. Fetch the functional requirements the requirements agent stored.
    app_id = state.get("app_id")
    if not app_id:
        return {
            "current_node": "dynamic_functional",
            "errors": [f"Missing app_id in state for run {run_id}"],
            "dynamic_functional_report": {"status": "FAILED", "reason": "Missing app_id"},
        }
    if not target_url:
        # The caller owns the application's lifetime. Asserting against a URL
        # nobody is serving would report failures that say nothing about the
        # generated code.
        return {
            "current_node": "dynamic_functional",
            "dynamic_functional_report": {
                "status": "SKIPPED",
                "reason": "No running application was available to test against.",
            },
        }

    try:
        requirements = functional_requirements(app_id)
    except RequirementsUnavailable as error:
        return {
            "current_node": "dynamic_functional",
            "dynamic_functional_report": {
                "status": "SKIPPED",
                "reason": str(error),
            },
        }
    except Exception as error:  # Storage is reachable but did not answer.
        return {
            "current_node": "dynamic_functional",
            "errors": [f"Failed to load requirements from DB for app {app_id}: {error}"],
            "dynamic_functional_report": {"status": "FAILED", "reason": "DB error"},
        }

    if not requirements:
        return {
            "current_node": "dynamic_functional",
            "dynamic_functional_report": {
                "status": "SKIPPED",
                "reason": "The stored requirements analysis contains no functional requirements.",
            },
        }

    requirement_ids = [str(item.get("id")) for item in requirements if item.get("id")]

    # Format requirements for prompt
    req_text = json.dumps(requirements, ensure_ascii=False, indent=2)
    prompt = SYSTEM_PROMPT.format(requirements_text=req_text)
    raw_history = state.get("repair_history") or {}
    if raw_history and raw_history.get("attempts"):
        history = RepairLedger.model_validate(raw_history)
        prompt += (
            "\n\nPrevious generated-test repair attempts are listed below. "
            "Do not repeat a rejected test candidate or failed approach.\n"
            f"{history.prompt_context()}"
        )

    # 2. Generate Test Code
    api_key = configured_api_key()
    if not api_key:
        return {
            "current_node": "dynamic_functional",
            "errors": ["API key not configured for test generation."],
            "dynamic_functional_report": {"status": "FAILED"},
        }

    # Transport retries are owned by the outer repair episode so every physical
    # request and failure remains visible in its audit history.
    client = OpenAI(api_key=api_key, base_url=provider_settings.base_url, max_retries=0)

    try:
        response = client.chat.completions.create(
            model=configured_model("openai/gpt-4o"),
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        test_code = response.choices[0].message.content or ""
        test_code = test_code.replace("```python", "").replace("```", "").strip()
    except Exception as e:
        return {
            "current_node": "dynamic_functional",
            "errors": [f"LLM generation failed: {e}"],
            "dynamic_functional_report": {"status": "FAILED"},
        }

    # 3. Execute Test Code
    # Repository root is assumed to be the current working directory of the orchestrator
    repository_root = Path(os.getcwd())
    report = run_dynamic_test(test_code, target_url, repository_root)
    report["candidateDigest"] = stable_digest(test_code)
    report["targetUrl"] = target_url
    # Which requirements this run claims to cover, so a passing report can be
    # traced back to the analysis it was derived from.
    report["requirements"] = {
        "source": "db",
        "artifact_type": "REFINE_REQ",
        "count": len(requirements),
        "ids": requirement_ids,
    }

    return {"current_node": "dynamic_functional", "dynamic_functional_report": report}
