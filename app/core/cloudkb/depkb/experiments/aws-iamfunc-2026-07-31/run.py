"""EKS IAM 기능 축(aws) — 미판정으로 남았던 마지막 기능 의존.

계획: `document/archive/remaining-rounds-plan-2026-07-31.md` §3.
기능 의존 1라운드에서 "클러스터 ACTIVE인데 역할 정책 분리 성공"을 관측만
하고 미뤘다 — **무엇이 깨지는지 신호를 못 정해서**다. 여기서 정한다:

  **신호 = 새 노드그룹 생성이 성공하는가.** EKS 컨트롤 플레인은 클러스터
  역할로 EC2·ENI를 조작하므로, 정책을 떼면 그 조작이 막혀야 한다.

고른 이유: 외부에서 API 하나로 관측되고 · 실패하면 EC2가 안 떠서 비용이
없고 · 성공/실패가 이분법이다.

사다리: 클러스터 ACTIVE → 정책 분리(성공 = 무방비) → 노드그룹 생성 시도
(기능 신호) → 정책 재부착 → 재시도(회복) → 정리.

국면: kickoff → probe → finish. 실행: `python run.py <phase>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
CLUSTER = "depkb-iamfunc"
ROLE, NODE_ROLE = "depkb-iamfunc-role", "depkb-iamfunc-node-role"
TRUST_EKS = json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"},
    "Action": "sts:AssumeRole"}]})
TRUST_EC2 = json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"}]})
CLUSTER_POLICY = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
NODE_POLICIES = [
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
]


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("EKS IAM 기능 축 — 신호는 '새 노드그룹 생성 성공 "
                      "여부'. 신호 정의가 이 라운드의 본체다."),
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
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    def try_nodegroup(name: str) -> dict:
        """기능 신호 — 컨트롤 플레인이 EC2를 조작할 수 있는가."""
        return aws(["eks", "create-nodegroup", "--cluster-name", CLUSTER,
                    "--nodegroup-name", name, "--node-role", ids["nodeRoleArn"],
                    "--subnets", ids["subnet1"], ids["subnet2"],
                    "--scaling-config", "minSize=1,maxSize=1,desiredSize=1",
                    "--instance-types", "t3.small", "--output", "json"],
                   timeout=300)

    if phase == "kickoff":
        role = step("R1.create-cluster-role", aws(
            ["iam", "create-role", "--role-name", ROLE,
             "--assume-role-policy-document", TRUST_EKS, "--output", "json"]))
        ids["roleArn"] = role["_data"]["Role"]["Arn"]
        step("R2.attach-cluster-policy", aws(
            ["iam", "attach-role-policy", "--role-name", ROLE,
             "--policy-arn", CLUSTER_POLICY]))
        nrole = step("R3.create-node-role", aws(
            ["iam", "create-role", "--role-name", NODE_ROLE,
             "--assume-role-policy-document", TRUST_EC2, "--output", "json"]))
        ids["nodeRoleArn"] = nrole["_data"]["Role"]["Arn"]
        for i, p in enumerate(NODE_POLICIES, start=1):
            step(f"R4.attach-node-policy{i}", aws(
                ["iam", "attach-role-policy", "--role-name", NODE_ROLE,
                 "--policy-arn", p]))
        vpc = step("R5.create-vpc", aws(
            ["ec2", "create-vpc", "--cidr-block", "10.108.0.0/16",
             "--output", "json"]))["_data"]["Vpc"]["VpcId"]
        ids["vpc"] = vpc
        azs = aws(["ec2", "describe-availability-zones",
                   "--query", "AvailabilityZones[:2].ZoneName",
                   "--output", "json"])["_data"]
        for i, (cidr, zone) in enumerate([("10.108.1.0/24", azs[0]),
                                          ("10.108.2.0/24", azs[1])], start=1):
            s = step(f"R6.create-subnet{i}", aws(
                ["ec2", "create-subnet", "--vpc-id", vpc,
                 "--cidr-block", cidr, "--availability-zone", zone,
                 "--output", "json"]))["_data"]["Subnet"]["SubnetId"]
            ids[f"subnet{i}"] = s
        save(doc)
        time.sleep(10)
        step("K1.create-cluster", aws(
            ["eks", "create-cluster", "--name", CLUSTER,
             "--role-arn", ids["roleArn"], "--resources-vpc-config",
             f"subnetIds={ids['subnet1']},{ids['subnet2']}",
             "--output", "json"], timeout=300))
        return

    if phase == "probe":
        state = ""
        deadline = time.time() + 900
        while time.time() < deadline:
            r = aws(["eks", "describe-cluster", "--name", CLUSTER,
                     "--query", "cluster.status", "--output", "text"])
            state = r["excerpt"].strip()
            print(f"cluster: {state}", flush=True)
            if state in ("ACTIVE", "FAILED"):
                break
            time.sleep(30)
        step("K2.cluster-active", {"ok": state == "ACTIVE",
                                   "errorCodes": [] if state == "ACTIVE" else [state],
                                   "excerpt": state})
        # M1 — 변이: 클러스터가 ACTIVE인 채로 역할 정책 분리(성공 = 무방비)
        step("M1.detach-policy-while-active", aws(
            ["iam", "detach-role-policy", "--role-name", ROLE,
             "--policy-arn", CLUSTER_POLICY]))
        step("M1b.cluster-still-active", aws(
            ["eks", "describe-cluster", "--name", CLUSTER,
             "--query", "cluster.status", "--output", "text"]))
        # F1 — 기능 신호: IAM 전파를 감안해 두 번 시도한다
        time.sleep(30)
        first = step("F1.nodegroup-without-policy", try_nodegroup("ng-nopolicy"))
        if first["ok"]:
            time.sleep(60)
            step("F1b.nodegroup-retry-after-wait",
                 try_nodegroup("ng-nopolicy2"))
        # M2 — 복원
        step("M2.reattach-policy", aws(
            ["iam", "attach-role-policy", "--role-name", ROLE,
             "--policy-arn", CLUSTER_POLICY]))
        time.sleep(30)
        step("F2.nodegroup-after-restore", try_nodegroup("ng-restored"))
        return

    if phase == "finish":
        # 만들어졌을 수 있는 노드그룹 전부 정리
        r = aws(["eks", "list-nodegroups", "--cluster-name", CLUSTER,
                 "--query", "nodegroups", "--output", "json"])
        names = r["_data"] if r.get("_data") else []
        for n in names:
            step(f"T1.delete-nodegroup-{n}", aws(
                ["eks", "delete-nodegroup", "--cluster-name", CLUSTER,
                 "--nodegroup-name", n, "--output", "json"]))
        deadline = time.time() + 900
        while names and time.time() < deadline:
            r = aws(["eks", "list-nodegroups", "--cluster-name", CLUSTER,
                     "--query", "nodegroups", "--output", "json"])
            names = r["_data"] if r.get("_data") else []
            if not names:
                break
            print(f"ng remaining: {names}", flush=True)
            time.sleep(30)
        step("T2.nodegroups-gone", {"ok": not names, "errorCodes": [],
                                    "excerpt": json.dumps(names)})
        step("T3.delete-cluster", aws(
            ["eks", "delete-cluster", "--name", CLUSTER, "--output", "json"]))
        gone = False
        deadline = time.time() + 900
        while time.time() < deadline:
            r = aws(["eks", "describe-cluster", "--name", CLUSTER,
                     "--query", "cluster.status", "--output", "text"])
            if not r["ok"]:
                gone = True
                break
            print(f"cluster deleting… {r['excerpt'].strip()[:20]}", flush=True)
            time.sleep(30)
        step("T4.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "gone" if gone else "timeout"})
        for i in (1, 2):
            step(f"T5.delete-subnet{i}", aws(
                ["ec2", "delete-subnet", "--subnet-id", ids[f"subnet{i}"]]))
        step("T6.delete-vpc", aws(["ec2", "delete-vpc",
                                   "--vpc-id", ids["vpc"]]))
        step("T7.detach-cluster-policy", aws(
            ["iam", "detach-role-policy", "--role-name", ROLE,
             "--policy-arn", CLUSTER_POLICY]))
        step("T8.delete-cluster-role", aws(
            ["iam", "delete-role", "--role-name", ROLE]))
        for i, p in enumerate(NODE_POLICIES, start=1):
            step(f"T9.detach-node-policy{i}", aws(
                ["iam", "detach-role-policy", "--role-name", NODE_ROLE,
                 "--policy-arn", p]))
        step("T10.delete-node-role", aws(
            ["iam", "delete-role", "--role-name", NODE_ROLE]))
        step("T11.residual-clusters", aws(["eks", "list-clusters",
                                           "--output", "json"]))
        step("T12.residual-roles", aws(
            ["iam", "list-roles", "--query",
             "Roles[?starts_with(RoleName,'depkb')].RoleName", "--output", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
