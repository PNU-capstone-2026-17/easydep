"""실패한 체크포인트를 지정한 소유 하위 작업부터 재개하는 운영 CLI."""

from __future__ import annotations

import argparse
import sys

from app.core.orchestration.graph import IMPLEMENTATION_STEP_ORDER, retry_failed_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--repair-owner", choices=IMPLEMENTATION_STEP_ORDER, required=True)
    parser.add_argument("--reason", required=True)
    arguments = parser.parse_args(argv)
    result = retry_failed_run(
        arguments.run_id,
        reason=arguments.reason,
        repair_owner=arguments.repair_owner,
    )
    # Windows의 기본 cp949 콘솔에서도 생성 산출물의 Unicode 문자가
    # 실행 성공을 출력 단계에서 실패로 바꾸지 않도록 UTF-8 바이트로 기록한다.
    sys.stdout.buffer.write((result.model_dump_json(indent=2) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
