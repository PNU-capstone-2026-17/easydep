"""자격 요건(aws) — 역할은 **만들기만 해서는 쓸모가 없다**.

계획: `document/archive/view-gaps-plan-2026-08-01.md` 공백 A.
worked example에서 드러난 공백: `createOrder`가 iamRole을 넣지만 빈 역할로는
클러스터가 서지 않는다. 우리는 늘 정책을 **붙이고** 성공했을 뿐, 안 붙이고
시도한 적이 없다 — 그래서 "붙여야 한다"가 실측이 아니었다.

셀: 정책 없는 역할 생성 → 그 역할로 EKS 생성 시도 → 거부 코드 관측 →
정책 부착 → 재시도(양성 대조) → 정리.

**클러스터를 실제로 만들지 않는다** — 양성 대조는 거부가 사라지는 것까지만
본다(요청이 수락되면 즉시 삭제). EKS 컨트롤 플레인 과금을 피한다.

실행: `python run.py`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
ROLE = "depkb-qual-role"
CLUSTER = "depkb-qual"
TRUST = json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"},
    "Action": "sts:AssumeRole"}]})
POLICY = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"


def main() -> None:
    doc = {"_note": ("자격 요건(aws) — 정책 없는 역할로 EKS 생성 시도. "
                     "클러스터는 실제로 만들지 않는다(과금 회피)."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "ids": {}, "steps": {}}
    steps, ids = doc["steps"], doc["ids"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        save()
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    # 전제 — VPC·서브넷 2개(다른 AZ). 자격 요건 축을 격리하려면 다른 필수는
    # 다 갖춰야 한다(한 번에 한 축만 흔든다).
    role = step("R1.create-role-no-policy", aws(
        ["iam", "create-role", "--role-name", ROLE,
         "--assume-role-policy-document", TRUST, "--output", "json"]))
    ids["roleArn"] = role["_data"]["Role"]["Arn"]
    vpc = step("R2.create-vpc", aws(
        ["ec2", "create-vpc", "--cidr-block", "10.120.0.0/16",
         "--output", "json"]))["_data"]["Vpc"]["VpcId"]
    ids["vpc"] = vpc
    azs = aws(["ec2", "describe-availability-zones",
               "--query", "AvailabilityZones[:2].ZoneName",
               "--output", "json"])["_data"]
    for i, (cidr, zone) in enumerate([("10.120.1.0/24", azs[0]),
                                      ("10.120.2.0/24", azs[1])], start=1):
        s = step(f"R3.create-subnet{i}", aws(
            ["ec2", "create-subnet", "--vpc-id", vpc, "--cidr-block", cidr,
             "--availability-zone", zone, "--output", "json"]
        ))["_data"]["Subnet"]["SubnetId"]
        ids[f"subnet{i}"] = s
    save()
    time.sleep(10)  # IAM 전파

    # A1 — 정책 없는 역할로 생성 시도(자격 요건의 음성)
    step("A1.create-cluster-role-without-policy", aws(
        ["eks", "create-cluster", "--name", CLUSTER,
         "--role-arn", ids["roleArn"], "--resources-vpc-config",
         f"subnetIds={ids['subnet1']},{ids['subnet2']}",
         "--output", "json"], timeout=300))
    # A2 — 정책 부착 후 재시도(양성 대조). 수락되면 **즉시 삭제**한다.
    step("A2.attach-policy", aws(
        ["iam", "attach-role-policy", "--role-name", ROLE,
         "--policy-arn", POLICY]))
    time.sleep(15)  # IAM 전파
    accepted = step("A3.create-cluster-with-policy", aws(
        ["eks", "create-cluster", "--name", CLUSTER,
         "--role-arn", ids["roleArn"], "--resources-vpc-config",
         f"subnetIds={ids['subnet1']},{ids['subnet2']}",
         "--output", "json"], timeout=300))
    if accepted["ok"]:
        step("A4.delete-cluster-immediately", aws(
            ["eks", "delete-cluster", "--name", CLUSTER, "--output", "json"]))
        gone = False
        deadline = time.time() + 900
        while time.time() < deadline:
            r = aws(["eks", "describe-cluster", "--name", CLUSTER,
                     "--query", "cluster.status", "--output", "text"])
            if not r["ok"]:
                gone = True
                break
            print(f"deleting… {r['excerpt'].strip()[:20]}", flush=True)
            time.sleep(30)
        step("A5.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "gone" if gone else "timeout"})

    # 정리
    for i in (1, 2):
        step(f"T1.delete-subnet{i}", aws(
            ["ec2", "delete-subnet", "--subnet-id", ids[f"subnet{i}"]]))
    step("T2.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", ids["vpc"]]))
    step("T3.detach-policy", aws(
        ["iam", "detach-role-policy", "--role-name", ROLE,
         "--policy-arn", POLICY]))
    step("T4.delete-role", aws(["iam", "delete-role", "--role-name", ROLE]))
    step("T5.residual-clusters", aws(["eks", "list-clusters", "--output", "json"]))
    step("T6.residual-roles", aws(
        ["iam", "list-roles", "--query",
         "Roles[?starts_with(RoleName,'depkb')].RoleName", "--output", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
