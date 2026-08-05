"""aws EKS 거부 라운드 — 자원 무생성.

EKS는 새 의존 둘을 드러낼 후보다: **IAM 역할**(roleArn 필수 — 어휘 밖 자원)과
**서브넷 카디널리티**(둘 이상, 서로 다른 AZ — ALB와 같은 꼴 예상). 전부 거부
예상이라 클러스터는 생기지 않는다. 거부 순서(어느 검사가 먼저인가)도 기록한다
— 교란 격리를 위해 한 번에 한 축만 흔든다.

실행: `python run.py`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
ACCOUNT = "838062934141"
ABSENT_ROLE = f"arn:aws:iam::{ACCOUNT}:role/depkb-absent-role"
S1, S2 = "subnet-0fffffffffffffff0", "subnet-0fffffffffffffff1"


def main() -> None:
    steps: dict[str, dict] = {}

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}"
              f"{' [' + result['rejectedAt'] + ']' if result['rejectedAt'] else ''}",
              flush=True)

    step("E1.omit-vpc-config", aws(
        ["eks", "create-cluster", "--name", "depkb-eks",
         "--role-arn", ABSENT_ROLE]))
    step("E2.omit-role", aws(
        ["eks", "create-cluster", "--name", "depkb-eks",
         "--resources-vpc-config", f"subnetIds={S1},{S2}"]))
    step("E3.one-subnet", aws(
        ["eks", "create-cluster", "--name", "depkb-eks",
         "--role-arn", ABSENT_ROLE,
         "--resources-vpc-config", f"subnetIds={S1}"]))
    step("E4.two-dangling-subnets", aws(
        ["eks", "create-cluster", "--name", "depkb-eks",
         "--role-arn", ABSENT_ROLE,
         "--resources-vpc-config", f"subnetIds={S1},{S2}"]))
    step("E5.confirm-no-cluster", aws(
        ["eks", "list-clusters", "--output", "json"]))

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("EKS 거부 라운드 — 자원 무생성. 거부 코드가 어느 축(역할·"
                  "서브넷 수·허상)에서 났는지와 검사 순서를 기록. 양성 대조"
                  "(실제 클러스터)는 후속 라운드(비용·시간 게이트)."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
