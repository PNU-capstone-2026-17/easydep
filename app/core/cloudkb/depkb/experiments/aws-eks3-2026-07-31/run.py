"""EKS 실생성 라운드 — 양성 대조 + 생명주기. 국면형(kickoff/continue/life/finish).

지금까지 EKS는 거부만 봤다. 여기서 실제 클러스터를 세워 둘을 잰다:
- **양성 대조**: 다른 AZ 서브넷 2개 + IAM 역할이면 정말 만들어지는가(거부
  판정들의 대우 확인 — 거부만 보면 "무엇을 해도 안 된다"와 구별되지 않는다).
- **생명주기**: 클러스터가 쓰는 서브넷·VPC·역할을 지울 수 있는가.
  k8s 층에서 삭제 제약이 어떤 코드로 나오는지가 IaaS 층과 같은지 다른지.

비용: EKS 컨트롤 플레인 ~$0.10/h × 약 30분. 노드그룹은 만들지 않는다(EC2 비용
회피 — k8sNodeGroup CRUD는 azure·gcp에서 이미 쟀다).

실행: `python run.py {kickoff|continue|life|finish}`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
TRUST = json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"},
    "Action": "sts:AssumeRole"}]})
POLICY = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("EKS 실생성 라운드 — 양성 대조와 생명주기. 노드그룹은 "
                      "만들지 않는다(비용 회피)."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ids": {}, "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    phase = sys.argv[1]
    doc = load()
    steps, ids = doc["steps"], doc["ids"]

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        save(doc)
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    if phase == "kickoff":
        role = step("R.create-role", aws(
            ["iam", "create-role", "--role-name", "depkb-eks3-role",
             "--assume-role-policy-document", TRUST, "--output", "json"]))
        ids["roleArn"] = role["_data"]["Role"]["Arn"]
        step("R.attach-policy", aws(
            ["iam", "attach-role-policy", "--role-name", "depkb-eks3-role",
             "--policy-arn", POLICY]))
        vpc = step("R.create-vpc", aws(
            ["ec2", "create-vpc", "--cidr-block", "10.93.0.0/16",
             "--output", "json"]))
        ids["vpc"] = vpc["_data"]["Vpc"]["VpcId"]
        azs = aws(["ec2", "describe-availability-zones",
                   "--query", "AvailabilityZones[:2].ZoneName",
                   "--output", "json"])
        za, zb = azs["_data"]
        for i, (cidr, zone) in enumerate([("10.93.1.0/24", za),
                                          ("10.93.2.0/24", zb)], start=1):
            s = step(f"R.create-subnet{i}", aws(
                ["ec2", "create-subnet", "--vpc-id", ids["vpc"],
                 "--cidr-block", cidr, "--availability-zone", zone,
                 "--output", "json"]))
            ids[f"subnet{i}"] = s["_data"]["Subnet"]["SubnetId"]
        save(doc)
        time.sleep(10)  # IAM 전파
        step("K1.create-cluster", aws(
            ["eks", "create-cluster", "--name", "depkb-eks3",
             "--role-arn", ids["roleArn"], "--resources-vpc-config",
             f"subnetIds={ids['subnet1']},{ids['subnet2']}",
             "--output", "json"], timeout=300))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 480
        while time.time() < deadline:
            r = aws(["eks", "describe-cluster", "--name", "depkb-eks3",
                     "--query", "cluster.status", "--output", "text"])
            state = r["excerpt"].strip()
            print(f"status: {state}", flush=True)
            if state in ("ACTIVE", "FAILED"):
                break
            time.sleep(30)
        step("K2.cluster-active", {"ok": state == "ACTIVE",
                                   "errorCodes": [] if state == "ACTIVE" else [state],
                                   "rejectedAt": None, "excerpt": state})
        if state == "ACTIVE":
            step("K3.cluster-shape", aws(
                ["eks", "describe-cluster", "--name", "depkb-eks3", "--query",
                 "cluster.{vpc:resourcesVpcConfig.vpcId,"
                 "sg:resourcesVpcConfig.clusterSecurityGroupId,"
                 "subnets:length(resourcesVpcConfig.subnetIds)}",
                 "--output", "json"]))
        return

    if phase == "life":
        step("L1.delete-subnet-in-use", aws(
            ["ec2", "delete-subnet", "--subnet-id", ids["subnet1"]]))
        step("L2.delete-vpc-in-use", aws(
            ["ec2", "delete-vpc", "--vpc-id", ids["vpc"]]))
        step("L3.delete-role-in-use", aws(
            ["iam", "detach-role-policy", "--role-name", "depkb-eks3-role",
             "--policy-arn", POLICY]))
        step("L4.delete-cluster", aws(
            ["eks", "delete-cluster", "--name", "depkb-eks3", "--output", "json"]))
        return

    if phase == "finish":
        gone = False
        deadline = time.time() + 480
        while time.time() < deadline:
            r = aws(["eks", "describe-cluster", "--name", "depkb-eks3",
                     "--query", "cluster.status", "--output", "text"])
            if not r["ok"]:
                gone = True
                break
            print(f"deleting… {r['excerpt'].strip()[:30]}", flush=True)
            time.sleep(30)
        step("F1.cluster-gone", {"ok": gone, "errorCodes": [], "rejectedAt": None,
                                 "excerpt": "gone" if gone else "timeout"})
        for i in (1, 2):
            step(f"F2.delete-subnet{i}", aws(
                ["ec2", "delete-subnet", "--subnet-id", ids[f"subnet{i}"]]))
        step("F3.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", ids["vpc"]]))
        step("F4.delete-role", aws(
            ["iam", "delete-role", "--role-name", "depkb-eks3-role"]))
        step("F5.residual-vpcs", aws(
            ["ec2", "describe-vpcs", "--query",
             "Vpcs[?IsDefault==`false`].VpcId", "--output", "json"]))
        step("F6.residual-clusters", aws(
            ["eks", "list-clusters", "--output", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
