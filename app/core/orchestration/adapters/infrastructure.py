"""Temporary LLM-only infrastructure recommendation boundary."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

SYSTEM_PROMPT = """You are a provisional Docker-on-VM infrastructure planner.
Use only the supplied requirements, resource constraints, and dependency plan.
Return one JSON object with: vmFamily, vmCount, vCpuPerVm, memoryGiBPerVm,
storageGiB, estimatedMonthlyCostUsd, assumptions, rationale, confidence.
Do not use Kubernetes or managed application platforms. Values are planning
placeholders, not measured recommendations. Keep rationale and assumptions short."""


class InfrastructureRecommendationAdapter:
    def __init__(self, invoke: Callable[[str], str] | None = None) -> None:
        self._invoke = invoke or self._invoke_llm

    @staticmethod
    def _invoke_llm(prompt: str) -> str:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ["API_KEY"],
            base_url=os.getenv("BASE_URL"),
            timeout=300,
            max_retries=2,
        )
        response = client.chat.completions.create(
            model=os.getenv("MODEL", "openai/gpt-oss-120b"),
            temperature=float(os.getenv("TEMPERATURE", "0")),
            seed=int(os.getenv("SEED", "42")),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or "{}"

    def recommend(
        self,
        *,
        requirements_result: dict[str, Any],
        cloud_design_result: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = json.dumps(
            {
                "resourceSpec": requirements_result.get("resource_spec") or {},
                "deploymentNeeds": requirements_result.get("deployment_needs") or {},
                "dependencyPlan": cloud_design_result.get("dependency_plan") or {},
                "deferredByDesign": cloud_design_result.get("deferred") or [],
            },
            ensure_ascii=False,
        )
        recommendation = json.loads(self._invoke(prompt))
        if not isinstance(recommendation, dict):
            raise TypeError("Infrastructure recommendation must be a JSON object")
        return {
            "status": "provisional",
            "method": "llm_prompt_only",
            "measured": False,
            "warning": "Not backed by performance benchmarks or live pricing.",
            "recommendation": recommendation,
        }
