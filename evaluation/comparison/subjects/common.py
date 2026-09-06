from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from dotenv import dotenv_values


REQUIREMENT_ID = re.compile(r"^\s*-\s*\[([^\]]+)]", re.MULTILINE)


def requirement_ids(prompt_file: Path) -> list[str]:
    return REQUIREMENT_ID.findall(prompt_file.read_text(encoding="utf-8"))


def prompt_sha256(prompt_file: Path) -> str:
    return hashlib.sha256(prompt_file.read_bytes()).hexdigest()


def llm_settings(dotenv_path: Path | None = None) -> tuple[str, str, str]:
    source = dotenv_path or (Path(__file__).resolve().parents[3] / ".env")
    file_values = dotenv_values(source) if source.is_file() else {}

    def setting(*names: str, default: str = "") -> str:
        for name in names:
            value = os.environ.get(name)
            if value and value.strip():
                return value.strip()
        for name in names:
            value = file_values.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return default

    cloudflare_token = setting("CLOUDFLARE_API_TOKEN")
    cloudflare_account = setting("CLOUDFLARE_ACCOUNT_ID")
    cloudflare_gateway = setting("CLOUDFLARE_AI_GATEWAY_ID")
    explicit_comparison_key = setting("COMPARISON_API_KEY")
    if (
        not explicit_comparison_key
        and cloudflare_token
        and cloudflare_account
        and cloudflare_gateway
    ):
        cloudflare_model = setting(
            "CLOUDFLARE_COMPARISON_MODEL",
            default="workers-ai/@cf/openai/gpt-oss-120b",
        )
        return (
            cloudflare_token,
            "https://gateway.ai.cloudflare.com/v1/"
            f"{cloudflare_account}/{cloudflare_gateway}/compat",
            cloudflare_model,
        )

    key = setting("COMPARISON_API_KEY", "OPENAI_API_KEY", "API_KEY")
    if not key:
        raise RuntimeError(
            "LLM API 키가 없습니다. COMPARISON_API_KEY, OPENAI_API_KEY 또는 API_KEY를 설정하세요."
        )
    base_url = setting(
        "COMPARISON_BASE_URL",
        "OPENAI_BASE_URL",
        "BASE_URL",
        default="https://api.openai.com/v1",
    )
    model = setting(
        "COMPARISON_MODEL",
        "OPENAI_MODEL",
        "MODEL",
        default="gpt-4o-mini",
    )
    return key, base_url, model


def safe_project_name(prefix: str, run_dir: Path) -> str:
    digest = hashlib.sha256(str(run_dir.resolve()).encode("utf-8")).hexdigest()[:10]
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", prefix).strip("_") or "comparison"
    return f"{cleaned}_{digest}"
