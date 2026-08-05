"""EKS 카디널리티 격리 — 실역할·실서브넷으로, 클러스터는 만들지 않는다.

전 라운드에서 허상 검사가 카디널리티 검사를 가렸다. 여기서는 실물 IAM 역할과
실물 서브넷을 만들어 그 앞 검사들을 통과시키고, **서브넷 수·AZ 분산 조건만**
남긴다. 두 시도 모두 거부가 가설이라 클러스터는 생기지 않는다(무비용 —
IAM 역할·VPC·서브넷은 무료·즉시 삭제).

- C1: 역할 + 서브넷 1개 → "둘 이상" 조건 측정
- C2: 역할 + 같은 AZ 서브넷 2개 → "서로 다른 AZ" 조건 측정

실행: `python run.py`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
TRUST = json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"},
    "Action": "sts:AssumeRole"}]})


def main() -> None:
    steps: dict[str, dict] = {}

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    role = step("R.create-role", aws(
        ["iam", "create-role", "--role-name", "depkb-eks-role",
         "--assume-role-policy-document", TRUST, "--output", "json"]))
    role_arn = role["_data"]["Role"]["Arn"]

    vpc = step("R.create-vpc", aws(
        ["ec2", "create-vpc", "--cidr-block", "10.92.0.0/16",
         "--output", "json"]))
    vpc_id = vpc["_data"]["Vpc"]["VpcId"]
    s1 = step("R.create-subnet1", aws(
        ["ec2", "create-subnet", "--vpc-id", vpc_id,
         "--cidr-block", "10.92.1.0/24", "--output", "json"]))
    s1_id = s1["_data"]["Subnet"]["SubnetId"]
    az1 = s1["_data"]["Subnet"]["AvailabilityZone"]
    s2 = step("R.create-subnet2-same-az", aws(
        ["ec2", "create-subnet", "--vpc-id", vpc_id,
         "--cidr-block", "10.92.2.0/24", "--availability-zone", az1,
         "--output", "json"]))
    s2_id = s2["_data"]["Subnet"]["SubnetId"]

    step("C1.one-real-subnet", aws(
        ["eks", "create-cluster", "--name", "depkb-eks",
         "--role-arn", role_arn,
         "--resources-vpc-config", f"subnetIds={s1_id}"]))
    step("C2.two-subnets-same-az", aws(
        ["eks", "create-cluster", "--name", "depkb-eks",
         "--role-arn", role_arn,
         "--resources-vpc-config", f"subnetIds={s1_id},{s2_id}"]))
    step("C3.confirm-no-cluster", aws(["eks", "list-clusters", "--output", "json"]))

    step("D.delete-subnet1", aws(["ec2", "delete-subnet", "--subnet-id", s1_id]))
    step("D.delete-subnet2", aws(["ec2", "delete-subnet", "--subnet-id", s2_id]))
    step("D.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", vpc_id]))
    step("D.delete-role", aws(["iam", "delete-role", "--role-name", "depkb-eks-role"]))

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("EKS 카디널리티 격리 — 실역할·실서브넷으로 앞 검사를 통과시켜 "
                  "서브넷 수·AZ 조건만 남겼다. 클러스터 무생성."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
