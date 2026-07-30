"""aws 쌍 호환 — 아키텍처 불일치(vm의 타입 ↔ 이미지의 arch).

가설: arm64 AMI + x86 인스턴스 타입 조합은 생성이 거부된다 — 조건이 간선의
한쪽 속성이 아니라 **쌍**에 걸리는 부류의 첫 실측. RunInstances DryRun은 허상을
잡는 깊이가 증명돼 있으므로(1라운드) DryRun만으로 잰다 — 자원 무생성, 비용 0.

대조군: 같은 arm64 AMI + arm 타입(t4g.micro) → DryRunOperation(성공 상당)이면
실패 축이 arch임이 격리된다.

주의: 대상(image)이 어휘 9종 밖이라 이 결과는 claims에 못 싣는다 — 어휘 확장
결정(image·internetGateway)까지 실험 기록으로만 남는다.

실행: `python run.py`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> None:
    steps: dict[str, dict] = {}

    def step(name, result):
        result.pop("_data", None) if False else None
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")
        return result

    ami = step("0.resolve-arm64-ami", aws(
        ["ssm", "get-parameter", "--name",
         "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64",
         "--output", "json"]))
    arm_ami = ami["_data"]["Parameter"]["Value"]

    base = ["ec2", "run-instances", "--dry-run", "--image-id", arm_ami,
            "--count", "1"]
    step("P1.arch-mismatch-arm-ami-x86-type", aws(
        base + ["--instance-type", "t3.micro"]))
    step("P2.arch-match-control-arm-type", aws(
        base + ["--instance-type", "t4g.micro"]))

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("쌍 호환(아키텍처) 측정 — P1은 거부가 가설, P2는 대조군"
                  "(DryRunOperation = 성공 상당). 자원 무생성."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
