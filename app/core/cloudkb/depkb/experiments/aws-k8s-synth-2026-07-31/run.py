"""k8s 층 합성 라운드(aws) — Service→CLB. PVC 셀은 미측정 명시.

계획: `document/archive/k8s-synthesis-plan-2026-07-31.md`. aws 특이점:

- **PVC→디스크 셀은 이번 라운드에서 판정하지 않는다.** EKS는 EBS CSI
  애드온이 기본 설치가 아니고, WaitForFirstConsumer 트리거에 노드가 필요한데
  노드그룹을 안 만든다(EC2 비용 회피 — eks3와 같은 결정). 전제 부재에서
  "합성 없음"을 판정하면 오판이므로 **관측만 기록하고 미측정으로 남긴다.**
- Service→CLB는 컨트롤 플레인의 서비스 컨트롤러가 수행하므로 노드 없이
  생성되는지 자체가 측정 대상이다. LB 서브넷 발견을 위해 IGW·기본 라우트·
  `kubernetes.io/role/elb` 태그를 준비한다(전제이지 판정 대상 아님).

오라클은 컨트롤 플레인 열거(elb describe-load-balancers 등). kubectl 상태는
힌트. 시한 내 미소멸은 잔존이 아니라 미판정.

국면: kickoff → continue → pvc → svc → life → finish.
실행: `python run.py <phase>`
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import REGION, aws  # noqa: E402

HERE = Path(__file__).resolve().parent
KUBECTL = shutil.which("kubectl")
KUBECONFIG = HERE / "kubeconfig"  # gitignore됨
CLUSTER = "depkb-synth"
ROLE = "depkb-synth-role"
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
    return {"_note": ("k8s 층 합성(aws) — Service→CLB만 판정. PVC→디스크는 "
                      "전제 부재(노드 0·CSI 애드온 없음)로 미측정 명시."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ids": {}, "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def elbs() -> dict:
    return aws(["elb", "describe-load-balancers", "--query",
                "LoadBalancerDescriptions[].{name:LoadBalancerName,"
                "dns:DNSName,subnets:Subnets}", "--output", "json"])


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
            ["ec2", "create-vpc", "--cidr-block", "10.98.0.0/16",
             "--output", "json"]))
        ids["vpc"] = vpc["_data"]["Vpc"]["VpcId"]
        igw = step("R.create-igw", aws(
            ["ec2", "create-internet-gateway", "--output", "json"]))
        ids["igw"] = igw["_data"]["InternetGateway"]["InternetGatewayId"]
        step("R.attach-igw", aws(
            ["ec2", "attach-internet-gateway", "--internet-gateway-id",
             ids["igw"], "--vpc-id", ids["vpc"]]))
        rt = aws(["ec2", "describe-route-tables", "--filters",
                  f"Name=vpc-id,Values={ids['vpc']}",
                  "Name=association.main,Values=true",
                  "--query", "RouteTables[0].RouteTableId", "--output", "text"])
        ids["mainRt"] = rt["excerpt"].strip()
        step("R.default-route-igw", aws(
            ["ec2", "create-route", "--route-table-id", ids["mainRt"],
             "--destination-cidr-block", "0.0.0.0/0",
             "--gateway-id", ids["igw"], "--output", "json"]))
        azs = aws(["ec2", "describe-availability-zones",
                   "--query", "AvailabilityZones[:2].ZoneName",
                   "--output", "json"])
        za, zb = azs["_data"]
        for i, (cidr, zone) in enumerate([("10.98.1.0/24", za),
                                          ("10.98.2.0/24", zb)], start=1):
            s = step(f"R.create-subnet{i}", aws(
                ["ec2", "create-subnet", "--vpc-id", ids["vpc"],
                 "--cidr-block", cidr, "--availability-zone", zone,
                 "--output", "json"]))
            ids[f"subnet{i}"] = s["_data"]["Subnet"]["SubnetId"]
            # LB 서브넷 발견 태그 — 측정의 전제이지 판정 대상이 아니다
            step(f"R.tag-subnet{i}", aws(
                ["ec2", "create-tags", "--resources", ids[f"subnet{i}"],
                 "--tags", "Key=kubernetes.io/role/elb,Value=1",
                 f"Key=kubernetes.io/cluster/{CLUSTER},Value=shared"]))
        save(doc)
        time.sleep(10)  # IAM 전파
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
        step("K4.default-storageclass", kc(
            ["get", "sc", "-o", "jsonpath={range .items[*]}{.metadata.name} "
             "default={.metadata.annotations.storageclass\\.kubernetes\\.io/is-default-class} "
             "bind={.volumeBindingMode} reclaim={.reclaimPolicy}\\n{end}"]))
        step("K5.elb-baseline", elbs())
        return

    if phase == "pvc":
        # 관측만 — 판정하지 않는다(모듈 docstring의 미측정 사유)
        step("P1.apply-pvc", kc(["apply", "-f", str(HERE / "pvc.yaml")]))
        time.sleep(60)
        step("P2.pvc-status-alone", kc(
            ["get", "pvc", "depkb-synth-pvc", "-o",
             "jsonpath=phase={.status.phase}"]))
        step("P3.unmeasured-note", {
            "ok": True, "errorCodes": [],
            "excerpt": "PVC→디스크 셀 미측정 — 노드 0(WaitForFirstConsumer 트리거 "
                       "불가)·EBS CSI 애드온 없음. 전제 부재에서 '합성 없음' 판정은 "
                       "오판이므로 관측만 남긴다."})
        return

    if phase == "svc":
        step("S1.apply-svc", kc(["apply", "-f", str(HERE / "svc.yaml")]))
        host = ""
        deadline = time.time() + 420
        while time.time() < deadline:
            r = kc(["get", "svc", "depkb-synth-svc", "-o",
                    "jsonpath={.status.loadBalancer.ingress[0].hostname}"])
            host = r["excerpt"].strip()
            print(f"svc ingress: {host or '(pending)'}", flush=True)
            if host:
                break
            time.sleep(20)
        step("S2.svc-ingress-hint", {"ok": bool(host), "errorCodes": [],
                                     "excerpt": host or "pending-timeout"})
        step("S3.elb-after-svc", elbs())
        step("S4.svc-events-hint", kc(
            ["get", "events", "--field-selector",
             "involvedObject.name=depkb-synth-svc",
             "-o", "jsonpath={range .items[*]}{.reason}: {.message}\\n{end}"]))
        step("S5.sgs-after-svc", aws(
            ["ec2", "describe-security-groups", "--filters",
             f"Name=vpc-id,Values={ids['vpc']}",
             "--query", "SecurityGroups[].GroupName", "--output", "json"]))
        return

    if phase == "life":
        step("L1.delete-svc", kc(["delete", "svc", "depkb-synth-svc"]))
        gone = False
        deadline = time.time() + 360
        while time.time() < deadline:
            r = elbs()
            body = r["excerpt"].strip()
            print(f"elbs: {body[:60]}", flush=True)
            if r["ok"] and body == "[]":
                gone = True
                break
            time.sleep(20)
        step("L2.elb-after-delete", {"ok": gone, "errorCodes": [],
                                     "excerpt": "cleaned" if gone else "timeout-미판정"})
        step("L3.delete-pvc", kc(["delete", "pvc", "depkb-synth-pvc",
                                  "--wait=false"]))
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
        step("F8.residual-elbs", elbs())
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
