"""B4 보정 — NLB를 internal 스킴으로 다시 잰다.

1차의 `InvalidSubnet`은 의존이 아니라 환경 전제(internet-facing은 IGW 필요)였다
— SkuNotAvailable 교훈의 재현. internal NLB는 IGW가 필요 없으므로 의존 축만
남는다. 결과는 results.json에 F.* 스텝으로 병합한다.

실행: `python run_fix.py`
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import HERE, aws  # noqa: E402


def main() -> None:
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    steps = results["steps"]

    def step(name, result):
        result.pop("_data", None) if name.startswith("_") else None
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        tag = "OK" if result["ok"] else "/".join(result["errorCodes"]) or "FAIL"
        print(f"{name:34} {tag}")
        return result

    vpc = step("F.create-vpc", aws(
        ["ec2", "create-vpc", "--cidr-block", "10.91.0.0/16", "--output", "json"]))
    vpc_id = vpc["_data"]["Vpc"]["VpcId"]
    sub = step("F.create-subnet", aws(
        ["ec2", "create-subnet", "--vpc-id", vpc_id,
         "--cidr-block", "10.91.1.0/24", "--output", "json"]))
    subnet_id = sub["_data"]["Subnet"]["SubnetId"]

    nlb = step("F.internal-nlb-one-subnet-no-sg", aws(
        ["elbv2", "create-load-balancer", "--name", "depkb2f-nlb",
         "--type", "network", "--scheme", "internal",
         "--subnets", subnet_id, "--output", "json"]))
    if nlb["ok"]:
        arn = nlb["_data"]["LoadBalancers"][0]["LoadBalancerArn"]
        step("F.delete-nlb", aws(
            ["elbv2", "delete-load-balancer", "--load-balancer-arn", arn]))

    deadline = time.time() + 360
    while True:
        res = aws(["ec2", "delete-subnet", "--subnet-id", subnet_id])
        if res["ok"] or time.time() > deadline:
            step("F.delete-subnet", res)
            break
        time.sleep(15)
    step("F.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", vpc_id]))

    results["ranAtFix"] = datetime.now(UTC).isoformat(timespec="seconds")
    (HERE / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
