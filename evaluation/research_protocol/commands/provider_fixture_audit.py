"""공급자별 최소 Terraform 관계 fixture를 격리 검증한다."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.research_protocol.core.paths import REPOSITORY_ROOT
from evaluation.research_protocol.core.provider_tools import (
    PINNED_PROVIDERS as PROVIDERS,
)
from evaluation.research_protocol.core.provider_tools import (
    PLUGIN_CACHE,
    audit_provider_cache,
    directory_size,
    provider_cache_environment,
    run_provider_command,
)

ROOT = REPOSITORY_ROOT
FIXTURE_ROOT = ROOT / "evaluation/research_protocol/provider-fixtures"


def validate_fixture(provider: str, fixture_root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    source = fixture_root / provider
    if not source.is_dir():
        raise FileNotFoundError(source)
    tofu = shutil.which("tofu")
    if not tofu:
        raise RuntimeError("OpenTofu CLI를 찾을 수 없다")
    started = time.perf_counter()
    cache_before = directory_size(PLUGIN_CACHE)
    environment = provider_cache_environment()
    with tempfile.TemporaryDirectory(prefix=f"easydep-fixture-{provider}-") as directory:
        isolated = Path(directory) / "fixture"
        shutil.copytree(source, isolated)
        fmt = run_provider_command(
            [tofu, "fmt", "-check", "-recursive", "-no-color"],
            isolated,
            environment=environment,
        )
        initialize = run_provider_command(
            [tofu, "init", "-backend=false", "-input=false", "-no-color"],
            isolated,
            environment=environment,
        )
        validate = (
            run_provider_command(
                [tofu, "validate", "-json", "-no-color"],
                isolated,
                environment=environment,
            )
            if initialize["status"] == "passed"
            else {"status": "not-run", "reason": "initialization failed"}
        )
        if validate.get("stdout"):
            try:
                validate["json"] = json.loads(validate["stdout"])
            except json.JSONDecodeError:
                validate["json"] = None
        passed = bool(
            fmt["status"] == "passed"
            and initialize["status"] == "passed"
            and validate["status"] == "passed"
            and (validate.get("json") or {}).get("valid") is True
        )
        return {
            "provider": provider,
            "status": "passed" if passed else "failed",
            "format": fmt,
            "initialize": initialize,
            "validate": validate,
            "elapsedSeconds": round(time.perf_counter() - started, 6),
            "providerCache": {
                "path": str(PLUGIN_CACHE.relative_to(ROOT)),
                "policy": "dedicated-pinned-versions-only-serial",
                "allowed": PROVIDERS,
                "bytesBefore": cache_before,
                "bytesAfter": directory_size(PLUGIN_CACHE),
                "contentsAfter": audit_provider_cache(),
            },
        }


def run(providers: list[str], fixture_root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    started = time.perf_counter()
    result = {
        "schemaVersion": "easydep-provider-fixture-audit/v1",
        "startedAt": datetime.now(UTC).isoformat(),
        "providers": [validate_fixture(provider, fixture_root) for provider in providers],
    }
    result["completedAt"] = datetime.now(UTC).isoformat()
    result["elapsedSeconds"] = round(time.perf_counter() - started, 6)
    result["status"] = (
        "passed"
        if result["providers"] and all(item["status"] == "passed" for item in result["providers"])
        else "failed"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="공급자 관계 fixture를 격리 검증합니다.")
    parser.add_argument("--provider", action="append", required=True)
    parser.add_argument("--fixture-root", type=Path, default=FIXTURE_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.provider, args.fixture_root)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
