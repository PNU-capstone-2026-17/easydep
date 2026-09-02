from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path


REQUIREMENT_ID = re.compile(r"^\s*-\s*\[([^\]]+)]", re.MULTILINE)


def requirement_ids(prompt_file: Path) -> list[str]:
    return REQUIREMENT_ID.findall(prompt_file.read_text(encoding="utf-8"))


def prompt_sha256(prompt_file: Path) -> str:
    return hashlib.sha256(prompt_file.read_bytes()).hexdigest()


def llm_settings() -> tuple[str, str, str]:
    key = (
        os.environ.get("COMPARISON_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("API_KEY")
        or ""
    )
    if not key:
        raise RuntimeError(
            "LLM API 키가 없습니다. COMPARISON_API_KEY, OPENAI_API_KEY 또는 API_KEY를 설정하세요."
        )
    base_url = (
        os.environ.get("COMPARISON_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("BASE_URL")
        or "https://api.openai.com/v1"
    )
    model = (
        os.environ.get("COMPARISON_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or os.environ.get("MODEL")
        or "gpt-4o-mini"
    )
    return key, base_url, model


def safe_project_name(prefix: str, run_dir: Path) -> str:
    digest = hashlib.sha256(str(run_dir.resolve()).encode("utf-8")).hexdigest()[:10]
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", prefix).strip("_") or "comparison"
    return f"{cleaned}_{digest}"

