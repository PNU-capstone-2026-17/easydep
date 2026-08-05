"""EKS PVC 셀(aws) — 1라운드 미측정의 해소.

배경: k8s 합성 1라운드에서 aws만 PVC→디스크를 못 쟀다(노드 0·EBS CSI
애드온 부재 — 전제 부재에서 '합성 없음' 판정은 오판이라 관측만 남겼다).
여기서 **전제를 갖춰** 잰다: 노드그룹 + EBS CSI 애드온 + CSI 드라이버가
쓸 IAM 정책.

덤: EKS 클러스터/노드그룹의 IAM 실물(iamRole 대기열)을 함께 기록한다.

국면: kickoff → nodes → probe → finish. 실행: `python run.py <phase>`
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
KUBECTL = shutil.which("kubectl")
KUBECONFIG = HERE / "kubeconfig"  # gitignore됨
CLUSTER = "depkb-ekspvc"
ROLE, NODE_ROLE = "depkb-ekspvc-role", "depkb-ekspvc-node-role"
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
    # CSI 드라이버가 볼륨을 만들 권한 — 전제이지 판정 대상이 아니다.
    "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy",
]


def kc(args: list[str], timeout: int = 120) -> dict:
    r = subprocess.run([KUBECTL, "--kubeconfig", str(KUBECONFIG), *args],
                       capture_output=True, text=True, timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    return {"ok": r.returncode == 0, "errorCodes": [],
            "excerpt": text.strip().replace("\r", "")[:600]}


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("EKS PVC — 1라운드 미측정(전제 부재)을 노드그룹+EBS "
                      "CSI 애드온으로 해소한다. IAM 실물도 함께 관측."),
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

    def volumes():
        return aws(["ec2", "describe-volumes", "--filters",
                    "Name=tag:kubernetes.io/created-for/pvc/name,Values=depkb-ekspvc",
                    "--query", "Volumes[].{id:VolumeId,size:Size,state:State}",
                    "--output", "json"])

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
            ["ec2", "create-vpc", "--cidr-block", "10.106.0.0/16",
             "--output", "json"]))["_data"]["Vpc"]["VpcId"]
        ids["vpc"] = vpc
        igw = step("R6.create-igw", aws(
            ["ec2", "create-internet-gateway", "--output", "json"]
        ))["_data"]["InternetGateway"]["InternetGatewayId"]
        ids["igw"] = igw
        step("R7.attach-igw", aws(
            ["ec2", "attach-internet-gateway", "--internet-gateway-id", igw,
             "--vpc-id", vpc]))
        rt = aws(["ec2", "describe-route-tables", "--filters",
                  f"Name=vpc-id,Values={vpc}",
                  "Name=association.main,Values=true",
                  "--query", "RouteTables[0].RouteTableId",
                  "--output", "text"])["excerpt"].strip()
        step("R8.default-route", aws(
            ["ec2", "create-route", "--route-table-id", rt,
             "--destination-cidr-block", "0.0.0.0/0", "--gateway-id", igw,
             "--output", "json"]))
        azs = aws(["ec2", "describe-availability-zones",
                   "--query", "AvailabilityZones[:2].ZoneName",
                   "--output", "json"])["_data"]
        for i, (cidr, zone) in enumerate([("10.106.1.0/24", azs[0]),
                                          ("10.106.2.0/24", azs[1])], start=1):
            s = step(f"R9.create-subnet{i}", aws(
                ["ec2", "create-subnet", "--vpc-id", vpc,
                 "--cidr-block", cidr, "--availability-zone", zone,
                 "--output", "json"]))["_data"]["Subnet"]["SubnetId"]
            ids[f"subnet{i}"] = s
            step(f"R10.public-ip-on-launch{i}", aws(
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

    if phase == "nodes":
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
        # iamRole 대기열: EKS가 쓰는 역할 실물
        step("K3.cluster-iam-shape", aws(
            ["eks", "describe-cluster", "--name", CLUSTER, "--query",
             "cluster.{role:roleArn,sg:resourcesVpcConfig.clusterSecurityGroupId}",
             "--output", "json"]))
        step("N1.create-nodegroup", aws(
            ["eks", "create-nodegroup", "--cluster-name", CLUSTER,
             "--nodegroup-name", "ng", "--node-role", ids["nodeRoleArn"],
             "--subnets", ids["subnet1"], ids["subnet2"],
             "--scaling-config", "minSize=1,maxSize=1,desiredSize=1",
             "--instance-types", "t3.small", "--output", "json"],
            timeout=300))
        ng = ""
        deadline = time.time() + 900
        while time.time() < deadline:
            r = aws(["eks", "describe-nodegroup", "--cluster-name", CLUSTER,
                     "--nodegroup-name", "ng", "--query", "nodegroup.status",
                     "--output", "text"])
            ng = r["excerpt"].strip()
            print(f"nodegroup: {ng}", flush=True)
            if ng in ("ACTIVE", "CREATE_FAILED"):
                break
            time.sleep(30)
        step("N2.nodegroup-active", {"ok": ng == "ACTIVE",
                                     "errorCodes": [] if ng == "ACTIVE" else [ng],
                                     "excerpt": ng})
        step("N3.install-ebs-csi-addon", aws(
            ["eks", "create-addon", "--cluster-name", CLUSTER,
             "--addon-name", "aws-ebs-csi-driver", "--output", "json"],
            timeout=300))
        addon = ""
        deadline = time.time() + 600
        while time.time() < deadline:
            r = aws(["eks", "describe-addon", "--cluster-name", CLUSTER,
                     "--addon-name", "aws-ebs-csi-driver",
                     "--query", "addon.status", "--output", "text"])
            addon = r["excerpt"].strip()
            print(f"addon: {addon}", flush=True)
            if addon in ("ACTIVE", "CREATE_FAILED", "DEGRADED"):
                break
            time.sleep(20)
        step("N4.addon-status", {"ok": addon == "ACTIVE",
                                 "errorCodes": [] if addon == "ACTIVE" else [addon],
                                 "excerpt": addon})
        step("N5.update-kubeconfig", aws(
            ["eks", "update-kubeconfig", "--name", CLUSTER,
             "--kubeconfig", str(KUBECONFIG)]))
        step("N6.storageclasses", kc(
            ["get", "sc", "-o", "jsonpath={range .items[*]}{.metadata.name} "
             "prov={.provisioner} bind={.volumeBindingMode}\\n{end}"]))
        step("N7.nodes-ready", kc(["get", "nodes", "-o",
                                   "jsonpath={range .items[*]}{.metadata.name} "
                                   "{.status.conditions[-1].type}\\n{end}"]))
        return

    if phase == "probe":
        step("P0.volumes-baseline", volumes())
        step("P1.apply-pvc", kc(["apply", "-f", str(HERE / "pvc.yaml")]))
        time.sleep(45)
        step("P2.pvc-status-alone", kc(
            ["get", "pvc", "depkb-ekspvc", "-o", "jsonpath=phase={.status.phase}"]))
        step("P3.volumes-after-pvc-alone", volumes())
        step("P4.apply-pod-trigger", kc(["apply", "-f", str(HERE / "pod.yaml")]))
        bound = ""
        deadline = time.time() + 420
        while time.time() < deadline:
            r = kc(["get", "pvc", "depkb-ekspvc", "-o",
                    "jsonpath={.status.phase}"])
            bound = r["excerpt"].strip()
            print(f"pvc phase: {bound}", flush=True)
            if bound == "Bound":
                break
            time.sleep(20)
        step("P5.pvc-bound", {"ok": bound == "Bound",
                              "errorCodes": [] if bound == "Bound" else [bound],
                              "excerpt": bound})
        step("P6.volumes-after-pod", volumes())
        step("P7.pv-volumehandle-hint", kc(
            ["get", "pv", "-o", "jsonpath={range .items[*]}{.spec.csi.volumeHandle}\\n{end}"]))
        step("P8.delete-pod", kc(["delete", "pod", "depkb-ekspvc-pod",
                                  "--wait=false"]))
        time.sleep(30)
        step("P9.delete-pvc", kc(["delete", "pvc", "depkb-ekspvc"],
                                 timeout=180))
        gone = False
        deadline = time.time() + 420
        while time.time() < deadline:
            r = volumes()
            body = r["excerpt"].strip()
            print(f"volumes: {body[:60]}", flush=True)
            if r["ok"] and body == "[]":
                gone = True
                break
            time.sleep(20)
        step("P10.volumes-after-pvc-delete", {"ok": gone, "errorCodes": [],
                                              "excerpt": "cleaned" if gone else "timeout-미판정"})
        return

    if phase == "finish":
        step("F1.delete-nodegroup", aws(
            ["eks", "delete-nodegroup", "--cluster-name", CLUSTER,
             "--nodegroup-name", "ng", "--output", "json"]))
        gone = False
        deadline = time.time() + 900
        while time.time() < deadline:
            r = aws(["eks", "describe-nodegroup", "--cluster-name", CLUSTER,
                     "--nodegroup-name", "ng", "--query", "nodegroup.status",
                     "--output", "text"])
            if not r["ok"]:
                gone = True
                break
            print(f"ng deleting… {r['excerpt'].strip()[:20]}", flush=True)
            time.sleep(30)
        step("F2.nodegroup-gone", {"ok": gone, "errorCodes": [],
                                   "excerpt": "gone" if gone else "timeout"})
        step("F3.delete-cluster", aws(
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
        step("F4.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "gone" if gone else "timeout"})
        for i in (1, 2):
            step(f"F5.delete-subnet{i}", aws(
                ["ec2", "delete-subnet", "--subnet-id", ids[f"subnet{i}"]]))
        step("F6.detach-igw", aws(
            ["ec2", "detach-internet-gateway", "--internet-gateway-id",
             ids["igw"], "--vpc-id", ids["vpc"]]))
        step("F7.delete-igw", aws(
            ["ec2", "delete-internet-gateway", "--internet-gateway-id",
             ids["igw"]]))
        step("F8.delete-vpc", aws(["ec2", "delete-vpc",
                                   "--vpc-id", ids["vpc"]]))
        step("F9.detach-cluster-policy", aws(
            ["iam", "detach-role-policy", "--role-name", ROLE,
             "--policy-arn", CLUSTER_POLICY]))
        step("F10.delete-cluster-role", aws(
            ["iam", "delete-role", "--role-name", ROLE]))
        for i, p in enumerate(NODE_POLICIES, start=1):
            step(f"F11.detach-node-policy{i}", aws(
                ["iam", "detach-role-policy", "--role-name", NODE_ROLE,
                 "--policy-arn", p]))
        step("F12.delete-node-role", aws(
            ["iam", "delete-role", "--role-name", NODE_ROLE]))
        # IRSA 잔여물 — 실험 도중에 만든 것이라 kickoff 목록에 없다(도중
        # 진단으로 필요해진 전제. 정리에서 빠뜨리면 조용히 남는다).
        if ids.get("irsaArn"):
            step("F12b.detach-irsa-policy", aws(
                ["iam", "detach-role-policy", "--role-name", "depkb-ekspvc-irsa",
                 "--policy-arn",
                 "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"]))
            step("F12c.delete-irsa-role", aws(
                ["iam", "delete-role", "--role-name", "depkb-ekspvc-irsa"]))
        if ids.get("oidcIssuer"):
            acct = aws(["sts", "get-caller-identity", "--query", "Account",
                        "--output", "text"])["excerpt"].strip()
            host = ids["oidcIssuer"].replace("https://", "")
            step("F12d.delete-oidc-provider", aws(
                ["iam", "delete-open-id-connect-provider",
                 "--open-id-connect-provider-arn",
                 f"arn:aws:iam::{acct}:oidc-provider/{host}"]))
        step("F13.residual-volumes", aws(
            ["ec2", "describe-volumes", "--query", "Volumes[].VolumeId",
             "--output", "json"]))
        step("F14.residual-vpcs", aws(
            ["ec2", "describe-vpcs", "--query",
             "Vpcs[?IsDefault==`false`].VpcId", "--output", "json"]))
        step("F15.residual-clusters", aws(["eks", "list-clusters",
                                           "--output", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
