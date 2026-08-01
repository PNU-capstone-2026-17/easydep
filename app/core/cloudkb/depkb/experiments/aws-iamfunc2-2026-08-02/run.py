"""EKS IAM 기능 축(aws) 2차 — 1차(aws-iamfunc-2026-07-31)의 설계 결함 둘을 고친다.

1차의 Z1 기록: **신호가 갈리지 않았다.** 노드그룹 생성이 비동기라 API 수락과
프로비저닝이 갈리는데, 그 사이에 정책을 복원해 최종 상태로는 원인을 못 갈랐다.
다음 조건 (b)를 여기서 실행한다 — **변이 상태를 노드그룹 터미널 상태까지 유지.**

고치는 것 둘:

1. **수락이 아니라 터미널 상태를 읽는다.** F-단계마다 describe-nodegroup을
   ACTIVE/CREATE_FAILED/DEGRADED까지 폴링하고, health 원문을 증거로 남긴다.
2. **양성 대조가 성립할 환경을 먼저 만든다.** 1차 VPC에는 IGW가 없어 정책과
   무관하게 노드 join이 불가능했을 것이다(IGW 교란은 기존 라운드 실측 —
   환경 전제가 의존 검사보다 먼저). IGW·기본 라우트·공인 IP를 넣고,
   F0(정책 부착 상태)이 ACTIVE에 도달해야만 F1의 실패를 정책 탓으로 읽는다.

사다리: F0 양성 대조(ACTIVE) → M1 정책 분리(성공 = 무방비, 분리 상태를
list-attached로 실증) → F1 노드그룹 생성·터미널까지(변이 유지, 끝난 뒤 분리
상태 재확인) → M2 복원 → F2 노드그룹 생성·터미널까지(회복).

**미리 적는 갈림**: 관리형 노드그룹 프로비저닝이 클러스터 역할이 아니라
서비스 연결 역할로 돌 가능성이 있다. 그 경우 F1도 ACTIVE로 가고, 판정은
"이 신호로는 결속 없음"이다 — 그것도 판정이다(qual2의 '정책은 기동 조건이
아니다'와 짝). 실패를 확인하려고 설계하지 않는다.

비용: 클러스터 시간당 $0.10 + t3.small 최대 3대 수십 분. 전부 finish에서 정리.
국면: kickoff → control → probe → finish. 실행: `python run.py <phase>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
CLUSTER = "depkb-iamfunc2"
ROLE, NODE_ROLE = "depkb-iamfunc2-role", "depkb-iamfunc2-node-role"
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
TERMINAL = ("ACTIVE", "CREATE_FAILED", "DEGRADED", "DELETE_FAILED")


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("EKS IAM 기능 축 2차 — 변이를 터미널 상태까지 유지한다. "
                      "1차의 SIGNAL_INVALID(수락≠완료)와 IGW 부재(양성 대조 "
                      "불성립)를 고친 판이다."),
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
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}",
              flush=True)
        return result

    def create_nodegroup(name: str) -> dict:
        return aws(["eks", "create-nodegroup", "--cluster-name", CLUSTER,
                    "--nodegroup-name", name, "--node-role", ids["nodeRoleArn"],
                    "--subnets", ids["subnet1"], ids["subnet2"],
                    "--scaling-config", "minSize=1,maxSize=1,desiredSize=1",
                    "--instance-types", "t3.small", "--output", "json"],
                   timeout=300)

    def wait_nodegroup(name: str, deadline_s: int) -> dict:
        """터미널 상태까지 읽는다 — 1차 결함(수락을 완료로 읽음)을 고치는 자리."""
        state, health = "", None
        deadline = time.time() + deadline_s
        while time.time() < deadline:
            r = aws(["eks", "describe-nodegroup", "--cluster-name", CLUSTER,
                     "--nodegroup-name", name, "--query",
                     "{s: nodegroup.status, h: nodegroup.health}",
                     "--output", "json"])
            if r.get("_data"):
                state, health = r["_data"]["s"], r["_data"]["h"]
                print(f"  {name}: {state}", flush=True)
                if state in TERMINAL:
                    break
            time.sleep(60)
        return {"ok": state == "ACTIVE",
                "errorCodes": [] if state == "ACTIVE" else [state or "TIMEOUT"],
                "excerpt": json.dumps({"status": state, "health": health},
                                      ensure_ascii=False)[:600]}

    def attached_policies() -> dict:
        return aws(["iam", "list-attached-role-policies", "--role-name", ROLE,
                    "--query", "AttachedPolicies[].PolicyArn", "--output", "json"])

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
            ["ec2", "create-vpc", "--cidr-block", "10.109.0.0/16",
             "--output", "json"]))["_data"]["Vpc"]["VpcId"]
        ids["vpc"] = vpc
        # 1차와 다른 자리 — IGW·라우트·공인 IP가 없으면 양성 대조가 성립 안 한다
        igw = step("R6.create-igw", aws(
            ["ec2", "create-internet-gateway", "--output", "json"]
        ))["_data"]["InternetGateway"]["InternetGatewayId"]
        ids["igw"] = igw
        step("R7.attach-igw", aws(
            ["ec2", "attach-internet-gateway", "--internet-gateway-id", igw,
             "--vpc-id", vpc]))
        rt = step("R8.main-route-table", aws(
            ["ec2", "describe-route-tables", "--filters",
             f"Name=vpc-id,Values={vpc}", "Name=association.main,Values=true",
             "--query", "RouteTables[0].[RouteTableId]", "--output", "json"]))
        ids["routeTable"] = rt["_data"][0]
        step("R9.default-route-to-igw", aws(
            ["ec2", "create-route", "--route-table-id", ids["routeTable"],
             "--destination-cidr-block", "0.0.0.0/0", "--gateway-id", igw]))
        azs = aws(["ec2", "describe-availability-zones",
                   "--query", "AvailabilityZones[:2].ZoneName",
                   "--output", "json"])["_data"]
        for i, (cidr, zone) in enumerate([("10.109.1.0/24", azs[0]),
                                          ("10.109.2.0/24", azs[1])], start=1):
            s = step(f"R10.create-subnet{i}", aws(
                ["ec2", "create-subnet", "--vpc-id", vpc,
                 "--cidr-block", cidr, "--availability-zone", zone,
                 "--output", "json"]))["_data"]["Subnet"]["SubnetId"]
            ids[f"subnet{i}"] = s
            step(f"R11.map-public-ip{i}", aws(
                ["ec2", "modify-subnet-attribute", "--subnet-id", s,
                 "--map-public-ip-on-launch"]))
        save(doc)
        time.sleep(10)
        step("K1.create-cluster", aws(
            ["eks", "create-cluster", "--name", CLUSTER,
             "--role-arn", ids["roleArn"], "--resources-vpc-config",
             f"subnetIds={ids['subnet1']},{ids['subnet2']}",
             "--output", "json"], timeout=300))
        return

    if phase == "kickoff2":
        # kickoff이 R8에서 죽은 뒤의 재개 — R1~R7은 이미 생성됐다(steps 기록).
        # 함정: aws() 헬퍼는 stdout이 {/[로 시작할 때만 파싱한다. --query가
        # JSON **문자열**("rtb-…")을 내면 _data가 None이 된다 — 쿼리를
        # 리스트 모양([RouteTableId])으로 바꿔 해결.
        vpc, igw = ids["vpc"], ids["igw"]
        steps["H1.helper-parse-trap"] = {
            "ok": True, "errorCodes": [],
            "excerpt": ("kickoff이 R9에서 TypeError로 죽음 — R8의 --query가 "
                        "JSON 문자열을 냈고 aws()는 {/[ 시작만 파싱해 "
                        "_data=None. 자원은 R1~R7까지 생성된 상태였다")}
        rt = step("R8.main-route-table", aws(
            ["ec2", "describe-route-tables", "--filters",
             f"Name=vpc-id,Values={vpc}", "Name=association.main,Values=true",
             "--query", "RouteTables[0].[RouteTableId]", "--output", "json"]))
        ids["routeTable"] = rt["_data"][0]
        step("R9.default-route-to-igw", aws(
            ["ec2", "create-route", "--route-table-id", ids["routeTable"],
             "--destination-cidr-block", "0.0.0.0/0", "--gateway-id", igw]))
        azs = aws(["ec2", "describe-availability-zones",
                   "--query", "AvailabilityZones[:2].ZoneName",
                   "--output", "json"])["_data"]
        for i, (cidr, zone) in enumerate([("10.109.1.0/24", azs[0]),
                                          ("10.109.2.0/24", azs[1])], start=1):
            s = step(f"R10.create-subnet{i}", aws(
                ["ec2", "create-subnet", "--vpc-id", vpc,
                 "--cidr-block", cidr, "--availability-zone", zone,
                 "--output", "json"]))["_data"]["Subnet"]["SubnetId"]
            ids[f"subnet{i}"] = s
            step(f"R11.map-public-ip{i}", aws(
                ["ec2", "modify-subnet-attribute", "--subnet-id", s,
                 "--map-public-ip-on-launch"]))
        save(doc)
        time.sleep(10)
        step("K1.create-cluster", aws(
            ["eks", "create-cluster", "--name", CLUSTER,
             "--role-arn", ids["roleArn"], "--resources-vpc-config",
             f"subnetIds={ids['subnet1']},{ids['subnet2']}",
             "--output", "json"], timeout=300))
        return

    if phase == "control":
        state = ""
        deadline = time.time() + 1500
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
        if state != "ACTIVE":
            return
        # F0 — 양성 대조: 정책 부착 상태에서 노드그룹이 ACTIVE까지 가는가.
        # 여기가 실패하면 환경 교란이고, F1 판정은 성립하지 않는다.
        step("F0.control-nodegroup-create", create_nodegroup("ng-control"))
        step("F0b.control-nodegroup-terminal", wait_nodegroup("ng-control", 1800))
        return

    if phase == "probe":
        # M1 — 변이: ACTIVE 클러스터의 역할에서 정책 분리(성공 = 무방비)
        step("M1.detach-policy-while-active", aws(
            ["iam", "detach-role-policy", "--role-name", ROLE,
             "--policy-arn", CLUSTER_POLICY]))
        step("M1b.policies-now-empty", attached_policies())
        step("M1c.cluster-still-active", aws(
            ["eks", "describe-cluster", "--name", CLUSTER,
             "--query", "cluster.status", "--output", "text"]))
        time.sleep(60)  # IAM 전파
        # F1 — 기능 신호: 변이를 유지한 채 터미널 상태까지 (1차가 못 한 것)
        step("F1.nodegroup-without-policy", create_nodegroup("ng-nopolicy"))
        step("F1b.nodegroup-terminal-under-mutation",
             wait_nodegroup("ng-nopolicy", 2400))
        step("F1c.policy-still-detached", attached_policies())
        step("O1.control-health-under-mutation", aws(
            ["eks", "describe-nodegroup", "--cluster-name", CLUSTER,
             "--nodegroup-name", "ng-control", "--query",
             "{s: nodegroup.status, h: nodegroup.health}", "--output", "json"]))
        # M2 — 복원 → F2 회복(같은 폴링)
        step("M2.reattach-policy", aws(
            ["iam", "attach-role-policy", "--role-name", ROLE,
             "--policy-arn", CLUSTER_POLICY]))
        time.sleep(60)
        step("F2.nodegroup-after-restore", create_nodegroup("ng-restored"))
        step("F2b.nodegroup-terminal-after-restore",
             wait_nodegroup("ng-restored", 1800))
        return

    if phase == "finish":
        r = aws(["eks", "list-nodegroups", "--cluster-name", CLUSTER,
                 "--query", "nodegroups", "--output", "json"])
        names = r["_data"] if r.get("_data") else []
        for n in names:
            step(f"T1.delete-nodegroup-{n}", aws(
                ["eks", "delete-nodegroup", "--cluster-name", CLUSTER,
                 "--nodegroup-name", n, "--output", "json"]))
        # 1차 교훈(U0): 시한은 노드그룹 수에 비례해야 한다
        deadline = time.time() + 900 + 600 * len(names)
        while names and time.time() < deadline:
            r = aws(["eks", "list-nodegroups", "--cluster-name", CLUSTER,
                     "--query", "nodegroups", "--output", "json"])
            names = r["_data"] if r.get("_data") else []
            if not names:
                break
            print(f"ng remaining: {names}", flush=True)
            time.sleep(60)
        step("T2.nodegroups-gone", {"ok": not names, "errorCodes": [],
                                    "excerpt": json.dumps(names)})
        step("T3.delete-cluster", aws(
            ["eks", "delete-cluster", "--name", CLUSTER, "--output", "json"]))
        gone = False
        deadline = time.time() + 1200
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
        step("T6.detach-igw", aws(
            ["ec2", "detach-internet-gateway", "--internet-gateway-id",
             ids["igw"], "--vpc-id", ids["vpc"]]))
        step("T7.delete-igw", aws(
            ["ec2", "delete-internet-gateway", "--internet-gateway-id",
             ids["igw"]]))
        step("T8.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", ids["vpc"]]))
        step("T9.detach-cluster-policy", aws(
            ["iam", "detach-role-policy", "--role-name", ROLE,
             "--policy-arn", CLUSTER_POLICY]))
        step("T10.delete-cluster-role", aws(
            ["iam", "delete-role", "--role-name", ROLE]))
        for i, p in enumerate(NODE_POLICIES, start=1):
            step(f"T11.detach-node-policy{i}", aws(
                ["iam", "detach-role-policy", "--role-name", NODE_ROLE,
                 "--policy-arn", p]))
        step("T12.delete-node-role", aws(
            ["iam", "delete-role", "--role-name", NODE_ROLE]))
        step("T13.residual-clusters", aws(["eks", "list-clusters",
                                           "--output", "json"]))
        step("T14.residual-roles", aws(
            ["iam", "list-roles", "--query",
             "Roles[?starts_with(RoleName,'depkb')].RoleName",
             "--output", "json"]))
        step("T15.residual-vpcs", aws(
            ["ec2", "describe-vpcs", "--filters",
             "Name=cidr-block-association.cidr-block,Values=10.109.0.0/16",
             "--query", "Vpcs[].VpcId", "--output", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
