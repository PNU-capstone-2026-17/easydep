"""기능 의존 2라운드(aws) — vm→firewall(SG)·subnet→internetGateway(라우트).

계획: `document/archive/functional-dependency2-plan-2026-07-31.md`.
전용 VPC 사슬 위에서 두 셀을 연속으로: (1) 인스턴스의 SG를 빈 인그레스
SG로 교체/원복(관계 변이), (2) 0.0.0.0/0→IGW 라우트 삭제/재생성 —
어휘 밖 대기열 internetGateway를 기능 축으로 닫는다. 셀 사이에 회복(F3)을
확인해 원인 섞임을 막는다.

실행: `python run.py`
"""

import json
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
SSM_AMI = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"


def tcp_ok(ip: str) -> bool:
    try:
        with socket.create_connection((ip, 22), timeout=5):
            return True
    except OSError:
        return False


def probe(ip: str, want: bool, budget: int, confirm: int = 1) -> dict:
    deadline = time.time() + budget
    tries = streak = 0
    while time.time() < deadline:
        tries += 1
        got = tcp_ok(ip)
        streak = streak + 1 if got == want else 0
        print(f"tcp {ip}:22 -> {got} (want {want}, streak {streak})", flush=True)
        if streak >= confirm:
            return {"ok": True, "errorCodes": [],
                    "excerpt": f"tcp22={got} (시도 {tries}, 연속 {streak})"}
        time.sleep(10)
    return {"ok": False, "errorCodes": ["PROBE_TIMEOUT"],
            "excerpt": f"tcp22가 {budget}초 내 {want}×{confirm}에 도달 못 함"}


def main() -> None:
    doc = {"_note": ("기능 의존 2라운드(aws) — SG 교체·IGW 라우트 삭제/복원. "
                     "기능 신호 = 로컬 TCP 22."),
           "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
           "ids": {}, "steps": {}}
    steps, ids = doc["steps"], doc["ids"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        save()
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    ami = step("R1.resolve-ami", aws(
        ["ssm", "get-parameters", "--names", SSM_AMI,
         "--query", "Parameters[0].Value", "--output", "text"]))["excerpt"].strip()
    vpc = step("R2.create-vpc", aws(
        ["ec2", "create-vpc", "--cidr-block", "10.102.0.0/16",
         "--output", "json"]))["_data"]["Vpc"]["VpcId"]
    ids["vpc"] = vpc
    igw = step("R3.create-igw", aws(
        ["ec2", "create-internet-gateway", "--output", "json"]
    ))["_data"]["InternetGateway"]["InternetGatewayId"]
    ids["igw"] = igw
    step("R4.attach-igw", aws(
        ["ec2", "attach-internet-gateway", "--internet-gateway-id", igw,
         "--vpc-id", vpc]))
    rt = aws(["ec2", "describe-route-tables", "--filters",
              f"Name=vpc-id,Values={vpc}", "Name=association.main,Values=true",
              "--query", "RouteTables[0].RouteTableId",
              "--output", "text"])["excerpt"].strip()
    ids["rt"] = rt
    step("R5.route-to-igw", aws(
        ["ec2", "create-route", "--route-table-id", rt,
         "--destination-cidr-block", "0.0.0.0/0", "--gateway-id", igw,
         "--output", "json"]))
    subnet = step("R6.create-subnet", aws(
        ["ec2", "create-subnet", "--vpc-id", vpc,
         "--cidr-block", "10.102.1.0/24", "--output", "json"]
    ))["_data"]["Subnet"]["SubnetId"]
    ids["subnet"] = subnet
    sg22 = step("R7.create-sg22", aws(
        ["ec2", "create-security-group", "--group-name", "depkb-f2-sg22",
         "--description", "depkb func2 allow22", "--vpc-id", vpc,
         "--output", "json"]))["_data"]["GroupId"]
    ids["sg22"] = sg22
    step("R8.sg22-ingress", aws(
        ["ec2", "authorize-security-group-ingress", "--group-id", sg22,
         "--protocol", "tcp", "--port", "22", "--cidr", "0.0.0.0/0",
         "--output", "json"]))
    sg_empty = step("R9.create-sg-empty", aws(
        ["ec2", "create-security-group", "--group-name", "depkb-f2-sgempty",
         "--description", "depkb func2 no ingress", "--vpc-id", vpc,
         "--output", "json"]))["_data"]["GroupId"]
    ids["sgEmpty"] = sg_empty
    inst = step("R10.run-instance", aws(
        ["ec2", "run-instances", "--instance-type", "t3.micro",
         "--image-id", ami, "--count", "1", "--subnet-id", subnet,
         "--security-group-ids", sg22, "--no-associate-public-ip-address",
         "--output", "json"], timeout=300))["_data"]["Instances"][0]["InstanceId"]
    ids["instance"] = inst
    save()
    state = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        r = aws(["ec2", "describe-instances", "--instance-ids", inst,
                 "--query", "Reservations[0].Instances[0].State.Name",
                 "--output", "text"])
        state = r["excerpt"].strip()
        if state == "running":
            break
        time.sleep(10)
    step("R11.running", {"ok": state == "running", "errorCodes": [],
                         "excerpt": state})
    alloc = step("R12.allocate-eip", aws(
        ["ec2", "allocate-address", "--output", "json"]))
    ids["allocId"] = alloc["_data"]["AllocationId"]
    ip = alloc["_data"]["PublicIp"]
    ids["ip"] = ip
    save()
    step("R13.associate-eip", aws(
        ["ec2", "associate-address", "--instance-id", inst,
         "--allocation-id", ids["allocId"], "--output", "json"]))

    step("F1.reachable-baseline", probe(ip, True, 300))
    # ── 셀 1: vm→firewall — 실행 중 SG를 빈 인그레스 SG로 교체(관계 변이)
    step("M1.swap-to-empty-sg", aws(
        ["ec2", "modify-instance-attribute", "--instance-id", inst,
         "--groups", sg_empty]))
    step("F2.unreachable-empty-sg", probe(ip, False, 180, confirm=2))
    step("M2.restore-sg22", aws(
        ["ec2", "modify-instance-attribute", "--instance-id", inst,
         "--groups", sg22]))
    step("F3.reachable-again", probe(ip, True, 300))
    # ── 셀 2: subnet→IGW(라우트) — 0.0.0.0/0 라우트 삭제(변이 성공 = 무방비)
    step("M3.delete-default-route", aws(
        ["ec2", "delete-route", "--route-table-id", rt,
         "--destination-cidr-block", "0.0.0.0/0"]))
    step("F4.unreachable-no-route", probe(ip, False, 180, confirm=2))
    step("M4.recreate-route", aws(
        ["ec2", "create-route", "--route-table-id", rt,
         "--destination-cidr-block", "0.0.0.0/0", "--gateway-id", igw,
         "--output", "json"]))
    step("F5.reachable-final", probe(ip, True, 300))

    # 정리
    assoc = aws(["ec2", "describe-addresses", "--allocation-ids",
                 ids["allocId"], "--query", "Addresses[0].AssociationId",
                 "--output", "text"])["excerpt"].strip()
    step("T1.disassociate", aws(
        ["ec2", "disassociate-address", "--association-id", assoc]))
    step("T2.release-eip", aws(
        ["ec2", "release-address", "--allocation-id", ids["allocId"]]))
    step("T3.terminate", aws(
        ["ec2", "terminate-instances", "--instance-ids", inst,
         "--output", "json"]))
    gone = False
    deadline = time.time() + 420
    while time.time() < deadline:
        r = aws(["ec2", "describe-instances", "--instance-ids", inst,
                 "--query", "Reservations[0].Instances[0].State.Name",
                 "--output", "text"])
        if r["excerpt"].strip() == "terminated":
            gone = True
            break
        time.sleep(15)
    step("T4.terminated", {"ok": gone, "errorCodes": [],
                           "excerpt": "terminated" if gone else "timeout"})
    step("T5.delete-sg22", aws(["ec2", "delete-security-group",
                                "--group-id", sg22]))
    step("T6.delete-sg-empty", aws(["ec2", "delete-security-group",
                                    "--group-id", sg_empty]))
    step("T7.delete-subnet", aws(["ec2", "delete-subnet",
                                  "--subnet-id", subnet]))
    step("T8.detach-igw", aws(
        ["ec2", "detach-internet-gateway", "--internet-gateway-id", igw,
         "--vpc-id", vpc]))
    step("T9.delete-igw", aws(["ec2", "delete-internet-gateway",
                               "--internet-gateway-id", igw]))
    step("T10.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", vpc]))
    step("T11.residual-vpcs", aws(
        ["ec2", "describe-vpcs", "--query", "Vpcs[?IsDefault==`false`].VpcId",
         "--output", "json"]))
    doc["finishedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
