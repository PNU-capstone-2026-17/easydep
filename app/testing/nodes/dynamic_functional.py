import json
import os
from pathlib import Path
from openai import OpenAI

from app.testing.schemas.testing_state import TestingState
from app.core.orchestration.store import RunStore
from app.testing.utils.test_runner import run_dynamic_test
from app.implementation.agents.provider import configured_api_key, configured_model

SYSTEM_PROMPT = """You are an expert QA automation engineer.
Your task is to write a single Python script using `pytest` and `playwright` to test the functional requirements of a web application.
The target application is accessible via the URL provided in the environment variable `TARGET_URL`.

Requirements to test:
{requirements_text}

Write ONLY the Python code. Do not include markdown formatting or explanations.
Use synchronous playwright (`from playwright.sync_api import Page, expect`).
Ensure that tests make assertions based on the requirements.
If a requirement cannot be fully tested via UI (e.g. backend processing without UI), write an API test using the `requests` or `httpx` module if applicable, or add a comment `# Skip: Cannot be tested via UI`.
"""

def dynamic_functional_node(state: TestingState) -> dict:
    """
    Executes dynamic functional (acceptance) testing using LLM generated playwright/pytest tests.
    """
    run_id = state.get("run_id")
    target_url = state.get("target_url", "http://localhost:8080")
    
    # 1. Fetch requirements from RunStore
    store = RunStore()
    try:
        run_state = store.load(run_id)
        requirements_result = run_state.get("requirements_result", {})
        requirements = requirements_result.get("requirements", [])
    except Exception as e:
        return {
            "current_node": "dynamic_functional",
            "errors": [f"Failed to load requirements from store for run {run_id}: {e}"],
            "dynamic_functional_report": {"status": "FAILED", "reason": "Missing requirements"}
        }

    if not requirements:
        return {
            "current_node": "dynamic_functional",
            "dynamic_functional_report": {"status": "SKIPPED", "reason": "No requirements found."}
        }

    # Format requirements for prompt
    req_text = json.dumps(requirements, ensure_ascii=False, indent=2)
    prompt = SYSTEM_PROMPT.format(requirements_text=req_text)

    # 2. Generate Test Code
    api_key = configured_api_key()
    if not api_key:
        return {
            "current_node": "dynamic_functional",
            "errors": ["API key not configured for test generation."],
            "dynamic_functional_report": {"status": "FAILED"}
        }

    from app.core.config import settings
    client = OpenAI(
        api_key=api_key,
        base_url=settings.base_url,
        max_retries=2
    )

    try:
        response = client.chat.completions.create(
            model=configured_model("openai/gpt-4o"),
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        test_code = response.choices[0].message.content or ""
        test_code = test_code.replace("```python", "").replace("```", "").strip()
    except Exception as e:
        return {
            "current_node": "dynamic_functional",
            "errors": [f"LLM generation failed: {e}"],
            "dynamic_functional_report": {"status": "FAILED"}
        }

    # 3. Execute Test Code
    # Repository root is assumed to be the current working directory of the orchestrator
    repository_root = Path(os.getcwd()) 
    report = run_dynamic_test(test_code, target_url, repository_root)

    return {
        "current_node": "dynamic_functional",
        "dynamic_functional_report": report
    }
