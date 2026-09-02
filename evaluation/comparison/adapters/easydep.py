"""EasyDep 공개 제품 실행 결과와 별도 사용량을 공통 결과로 묶는다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import load_evidence, write_subject_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EasyDep 제품 실행 결과를 비교 공통 결과로 변환")
    parser.add_argument("--product-result", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--usage", type=Path, help="LangSmith 등에서 내보낸 공통 usage JSON")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--framework-version", default="current")
    args = parser.parse_args(argv)
    product = json.loads(args.product_result.read_text(encoding="utf-8"))
    if not isinstance(product, dict):
        raise ValueError("제품 실행 결과는 JSON 객체여야 합니다.")
    location = product.get("location", {})
    completed = isinstance(location, dict) and location.get("status") == "COMPLETED"
    usage = {}
    if args.usage:
        usage = json.loads(args.usage.read_text(encoding="utf-8"))
        if not isinstance(usage, dict):
            raise ValueError("usage JSON은 객체여야 합니다.")
    metadata = {"appId": location.get("app_id")} if isinstance(location, dict) else {}
    write_subject_result(
        args.output,
        framework="EasyDep",
        framework_version=args.framework_version,
        status="completed" if completed else "failed",
        workspace=args.workspace,
        input_tokens=usage.get("inputTokens"),
        output_tokens=usage.get("outputTokens"),
        total_tokens=usage.get("totalTokens"),
        llm_calls=usage.get("llmCalls"),
        missing_usage_calls=int(usage.get("missingUsageCalls", 0)),
        source=str(usage.get("source", "not-reported")),
        evidence=load_evidence(args.evidence),
        metadata=metadata,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
