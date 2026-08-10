"""고정된 공식 API 모델에서 Native v2 관측·표본·검토 양식을 생성한다."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.cloudkb.depkb.control_plane_collect import (
    collect_aws,
    collect_azure,
    collect_gcp,
)
from app.core.cloudkb.depkb.native_v2 import boundary_sample, make_review, review_scope
from evaluation.research_protocol.core.paths import DEFINITION_ROOT, PROTOCOL_ROOT

HERE = PROTOCOL_ROOT
ANCHORS = DEFINITION_ROOT / "decision-anchors.json"
AZURE_CACHE = Path("app/core/cloudkb/depkb/cache/azure")


def _read_json(path: Path) -> dict[str, Any]:
    stream = (
        gzip.open(path, "rt", encoding="utf-8")
        if path.suffix == ".gz"
        else path.open("r", encoding="utf-8")
    )
    with stream:
        return json.load(stream)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_lf_normalized(path: Path) -> str:
    """Git의 Windows checkout 변환을 제외한 상류 원본 바이트 해시다."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _anchors(provider: str) -> list[dict[str, Any]]:
    document = _read_json(ANCHORS)
    if document.get("status") not in {"development", "frozen"}:
        raise ValueError("development 또는 frozen 앵커만 수집에 사용할 수 있습니다.")
    return document["anchors"][provider]


def _aws_models(root: Path) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for service in {item["serviceFamily"] for item in _anchors("aws")}:
        candidates = sorted((root / service).glob("*/service-2.json*"))
        if not candidates:
            raise FileNotFoundError(f"AWS service model not found: {service}")
        models[service] = _read_json(candidates[-1])
    return models


def build(provider: str, source: Path, version: str) -> dict[str, Any]:
    if provider == "aws":
        return collect_aws(_aws_models(source), _anchors(provider), version=version)
    if provider == "gcp":
        return collect_gcp(_read_json(source), _anchors(provider), version=version)
    manifest = _read_json(source / "manifest.json")
    if manifest.get("_pin", {}).get("commit") != version:
        raise ValueError("Azure manifest commit differs from requested version")
    for family, record in manifest["files"].items():
        path = source / f"{family}.json"
        if _sha256_lf_normalized(path) != record["sha256"]:
            raise ValueError(f"Azure source digest mismatch: {family}")
    documents = {
        family: _read_json(source / f"{family}.json")
        for family in manifest["files"]
        if family != "network-common"
    }
    return collect_azure(documents, _anchors(provider), version=version)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("aws", "azure", "gcp"))
    parser.add_argument("source", type=Path)
    parser.add_argument("version")
    parser.add_argument("output", type=Path)
    parser.add_argument("--reviewers", nargs="*", default=[])
    args = parser.parse_args()
    inventory = build(args.provider, args.source, args.version)
    sample = boundary_sample(inventory["observations"])
    scope = review_scope(inventory, sample)
    _write_json(args.output / f"{args.provider}-observations.json", inventory)
    _write_json(args.output / f"{args.provider}-boundary-sample.json", sample)
    _write_json(args.output / f"{args.provider}-review-scope.json", scope)
    for reviewer in args.reviewers:
        _write_json(
            args.output / f"{args.provider}-review-{reviewer}.json",
            make_review(inventory, reviewer, native_ids=scope["selectedNativeIds"]),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
