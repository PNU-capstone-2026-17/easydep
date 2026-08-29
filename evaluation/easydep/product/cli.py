"""얇은 제품 경로 실행기의 명령행 진입점이다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.easydep.product_scenario import (
    HttpProductScenarioTransport,
    ProductScenarioRunner,
    ProductScenarioStopped,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="프론트엔드와 같은 공개 API로 EasyDep 앱을 한 번 실행합니다."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    message = parser.add_mutually_exclusive_group(required=True)
    message.add_argument("--message", help="앱 생성 화면에 입력할 요구사항")
    message.add_argument(
        "--message-file", type=Path, help="UTF-8로 저장한 요구사항 파일"
    )
    parser.add_argument(
        "--stop-after",
        choices=("requirements", "design", "implementation", "testing"),
        default="testing",
        help="완료를 기다릴 마지막 공개 Workspace 단계",
    )
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    parser.add_argument(
        "--output",
        type=Path,
        help="원시 Workspace·산출물 응답 또는 실패 위치를 저장할 JSON 파일",
    )
    return parser


def _write(value: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    if output is None:
        print(rendered)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    message = (
        args.message
        if args.message is not None
        else args.message_file.read_text(encoding="utf-8")
    )
    runner = ProductScenarioRunner(
        HttpProductScenarioTransport(args.base_url),
        timeout_seconds=args.timeout_seconds,
    )
    try:
        result = runner.run(message, stop_after_stage=args.stop_after)
    except ProductScenarioStopped as error:
        _write({"ok": False, "location": error.location.as_dict()}, args.output)
        return 2
    _write({"ok": True, **result.as_dict()}, args.output)
    return 0
