"""k8s 합성 2라운드(aws) — Ingress(기본 구성)·RWX PVC 관측.

계획: `document/archive/k8s-synthesis2-plan-2026-07-31.md`. 1라운드 하네스
재사용(IAM 역할·VPC·IGW·태그 서브넷·클러스터, 노드그룹 없음).

- **Ingress**: 기본 EKS에 컨트롤러가 없다는 가설 — IngressClass 부재와
  Ingress 방치(주소 없음·ELB/ALB 목록 불변)를 관측한다.
- **RWX PVC**: 전제 부재(EFS CSI·노드 0) — 관측만, 미측정 명시.

국면: kickoff → continue → cells → finish. 실행: `python run.py <phase>`
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
KUBECONFIG = HERE / "kubeconfig"
CLUSTER = "depkb-synth2"
ROLE = "depkb-synth2-role"
TRUST = json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"},
    "Action": "sts:AssumeRole"}]})
POLICY = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"


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
    return {"_note": ("합성 2라운드(aws) — Ingress 기본 구성(컨트롤러 부재 가설)"
                      "·RWX PVC 전제 부재 관측."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ids": {}, "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def elbs_all() -> dict:
    clb = aws(["elb", "describe-load-balancers", "--query",
               "LoadBalancerDescriptions[].LoadBalancerName", "--output", "json"])
    alb = aws(["elbv2", "describe-load-balancers", "--query",
               "LoadBalancers[].LoadBalancerName", "--output", "json"])
    return {"ok": clb["ok"] and alb["ok"], "errorCodes": [],
            "excerpt": json.dumps({"clb": clb["excerpt"].strip(),
                                   "albnlb": alb["excerpt"].strip()})[:400]}


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
            ["iam", "create-role", "--role-name", ROLE,
             "--assume-role-policy-document", TRUST, "--output", "json"]))
        ids["roleArn"] = role["_data"]["Role"]["Arn"]
        step("R.attach-policy", aws(
            ["iam", "attach-role-policy", "--role-name", ROLE,
             "--policy-arn", POLICY]))
        vpc = step("R.create-vpc", aws(
            ["ec2", "create-vpc", "--cidr-block", "10.97.0.0/16",
             "--output", "json"]))
        ids["vpc"] = vpc["_data"]["Vpc"]["VpcId"]
        igw = step("R.create-igw", aws(
            ["ec2", "create-internet-gateway", "--output", "json"]))
        ids["igw"] = igw["_data"]["InternetGateway"]["InternetGatewayId"]
        step("R.attach-igw", aws(
            ["ec2", "attach-internet-gateway", "--internet-gateway-id",
             ids["igw"], "--vpc-id", ids["vpc"]]))
        azs = aws(["ec2", "describe-availability-zones",
                   "--query", "AvailabilityZones[:2].ZoneName",
                   "--output", "json"])
        za, zb = azs["_data"]
        for i, (cidr, zone) in enumerate([("10.97.1.0/24", za),
                                          ("10.97.2.0/24", zb)], start=1):
            s = step(f"R.create-subnet{i}", aws(
                ["ec2", "create-subnet", "--vpc-id", ids["vpc"],
                 "--cidr-block", cidr, "--availability-zone", zone,
                 "--output", "json"]))
            ids[f"subnet{i}"] = s["_data"]["Subnet"]["SubnetId"]
            step(f"R.tag-subnet{i}", aws(
                ["ec2", "create-tags", "--resources", ids[f"subnet{i}"],
                 "--tags", "Key=kubernetes.io/role/elb,Value=1",
                 f"Key=kubernetes.io/cluster/{CLUSTER},Value=shared"]))
        save(doc)
        time.sleep(10)
        step("K1.create-cluster", aws(
            ["eks", "create-cluster", "--name", CLUSTER,
             "--role-arn", ids["roleArn"], "--resources-vpc-config",
             f"subnetIds={ids['subnet1']},{ids['subnet2']}",
             "--output", "json"], timeout=300))
        return

    if phase == "continue":
        state = ""
        deadline = time.time() + 900
        while time.time() < deadline:
            r = aws(["eks", "describe-cluster", "--name", CLUSTER,
                     "--query", "cluster.status", "--output", "text"])
            state = r["excerpt"].strip()
            print(f"status: {state}", flush=True)
            if state in ("ACTIVE", "FAILED"):
                break
            time.sleep(30)
        step("K2.cluster-active", {"ok": state == "ACTIVE",
                                   "errorCodes": [] if state == "ACTIVE" else [state],
                                   "excerpt": state})
        step("K3.update-kubeconfig", aws(
            ["eks", "update-kubeconfig", "--name", CLUSTER,
             "--kubeconfig", str(KUBECONFIG)]))
        step("K4.ingressclasses", kc(["get", "ingressclass", "-o", "name"]))
        step("K5.elb-baseline", elbs_all())
        return

    if phase == "cells":
        step("I1.apply-np-svc", kc(["apply", "-f", str(HERE / "svc-np.yaml")]))
        step("I2.apply-ingress", kc(["apply", "-f", str(HERE / "ingress.yaml")]))
        step("P1.apply-rwx-pvc", kc(["apply", "-f", str(HERE / "rwx-pvc.yaml")]))
        time.sleep(150)
        step("I3.ingress-address-hint", kc(
            ["get", "ingress", "depkb-synth2-ing", "-o",
             "jsonpath=addr={.status.loadBalancer.ingress[0].hostname}"]))
        step("I4.elb-after-ingress", elbs_all())
        step("P2.rwx-status", kc(
            ["get", "pvc", "depkb-synth2-rwx", "-o",
             "jsonpath=phase={.status.phase}"]))
        step("P3.unmeasured-note", {
            "ok": True, "errorCodes": [],
            "excerpt": "RWX 셀 미측정 — EFS CSI 애드온·노드 0(전제 부재). "
                       "1라운드와 같은 규율: 전제 부재에서 '합성 없음' 판정은 "
                       "오판이므로 관측만 남긴다. Ingress 셀과 다른 점: Ingress는 "
                       "컨트롤러 부재 자체가 기본 구성의 실물이라 판정 대상이다."})
        step("C1.cleanup-objects", kc(
            ["delete", "ingress/depkb-synth2-ing", "svc/depkb-synth2-np",
             "pvc/depkb-synth2-rwx", "--wait=false"], timeout=180))
        return

    if phase == "finish":
        step("F0.delete-cluster", aws(
            ["eks", "delete-cluster", "--name", CLUSTER, "--output", "json"]))
        gone = False
        deadline = time.time() + 720
        while time.time() < deadline:
            r = aws(["eks", "describe-cluster", "--name", CLUSTER,
                     "--query", "cluster.status", "--output", "text"])
            if not r["ok"]:
                gone = True
                break
            print(f"deleting… {r['excerpt'].strip()[:30]}", flush=True)
            time.sleep(30)
        step("F1.cluster-gone", {"ok": gone, "errorCodes": [],
                                 "excerpt": "gone" if gone else "timeout"})
        for i in (1, 2):
            step(f"F2.delete-subnet{i}", aws(
                ["ec2", "delete-subnet", "--subnet-id", ids[f"subnet{i}"]]))
        step("F3.detach-igw", aws(
            ["ec2", "detach-internet-gateway", "--internet-gateway-id",
             ids["igw"], "--vpc-id", ids["vpc"]]))
        step("F4.delete-igw", aws(
            ["ec2", "delete-internet-gateway", "--internet-gateway-id",
             ids["igw"]]))
        step("F5.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", ids["vpc"]]))
        step("F6.detach-policy", aws(
            ["iam", "detach-role-policy", "--role-name", ROLE,
             "--policy-arn", POLICY]))
        step("F7.delete-role", aws(["iam", "delete-role", "--role-name", ROLE]))
        step("F8.residual-elbs", elbs_all())
        step("F9.residual-vpcs", aws(
            ["ec2", "describe-vpcs", "--query",
             "Vpcs[?IsDefault==`false`].VpcId", "--output", "json"]))
        step("F10.residual-clusters", aws(["eks", "list-clusters",
                                           "--output", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
