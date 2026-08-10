"""기능 신호 라운드(aws) — 메타데이터(자격증명) · 아웃바운드.

계획: `document/archive/functional-signals4-plan-2026-07-31.md`.
게스트 하네스는 `_guest.py`. OS 기본 도구(`curl`)만 쓴다.

- 셀 1 **메타데이터**: 인스턴스 프로필을 붙여 게스트가 IMDS에서 역할
  자격증명을 얻는다 → **프로필 분리**(무방비) → 획득 상실 → 재부착 → 회복.
  겨누는 간선: `vm→iamRole`. EKS CSI가 'no EC2 IMDS role found'로 죽은 것과
  같은 기제를 VM 층에서 격리해 잰다.
- 셀 2 **아웃바운드**: 기본 라우트로 외부 HTTP 200 → **라우트 삭제**
  (무방비) → 아웃바운드 상실 → 재생성 → 회복. 겨누는 간선:
  `subnet→internetGateway`. 기능 2라운드는 **인바운드**(우리→VM)를 쟀는데
  여기서는 **아웃바운드**(VM→외부)다 — 방향이 다른 신호다.

IMDS 전파 지연을 감안해 상실 관측 시한을 넉넉히 둔다(EKS IAM 라운드에서
비동기 때문에 판정을 놓쳤다). 시한 내 미상실이면 미판정으로 적는다.

국면: build → meta → egress → finish. 실행: `python run.py <phase>`
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _guest import probe  # noqa: E402
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
PEM = HERE / "depkb-sig4.pem"
USER = "ec2-user"
SSM_AMI = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
ROLE, PROFILE = "depkb-sig4-role", "depkb-sig4-profile"
TRUST_EC2 = json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"}]})
READONLY = "arn:aws:iam::aws:policy/ReadOnlyAccess"
#: IMDSv2 — 토큰을 먼저 받고 역할 이름을 조회한다. 실패하면 rc != 0.
IMDS = ("t=$(curl -s -X PUT 'http://169.254.169.254/latest/api/token' "
        "-H 'X-aws-ec2-metadata-token-ttl-seconds: 60' --max-time 5); "
        "curl -sf -H \"X-aws-ec2-metadata-token: $t\" --max-time 5 "
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/")
EGRESS = ("curl -s -o /dev/null -w '%{http_code}' --max-time 8 "
          "https://checkip.amazonaws.com | grep -q 200")


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("기능 신호(aws) — 메타데이터 자격증명·아웃바운드. "
                      "게스트 안에서 curl로만 관측(앱 없음)."),
            "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
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
        print(f"{name:38} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    if phase == "build":
        # **헬퍼도 `--output text`도 쓰지 않는다.** aws()의 excerpt는 600자에서
        # 잘리고(1차 시도), `--output text`도 개인키를 611바이트로 절단한다
        # (2차 — 둘 다 게스트에서 'error in libcrypto'로 나타났다). JSON으로
        # 받아 파싱하면 1678바이트·27줄이 온다. **길이를 검증해** 접속 실패가
        # 판정으로 둔갑하지 않게 막는다.
        import shutil as _sh
        import subprocess as _sp
        proc = _sp.run([_sh.which("aws"), "--region", "ap-northeast-2",
                        "ec2", "create-key-pair", "--key-name", "depkb-sig4",
                        "--output", "json", "--no-cli-pager"],
                       capture_output=True, text=True, timeout=120, check=False)
        material = (json.loads(proc.stdout)["KeyMaterial"]
                    if proc.returncode == 0 else "")
        PEM.write_text(material, encoding="utf-8")
        PEM.chmod(0o600)
        ok = len(material) > 1000 and material.count("\n") > 10
        step("R1.create-key-pair", {
            "ok": ok, "errorCodes": [] if ok else ["KEY_TRUNCATED"],
            "excerpt": f"개인키 {len(material)}바이트 · "
                       f"{material.count(chr(10)) + 1}줄 (JSON 경로 — "
                       f"text·헬퍼는 절단됐다)"})
        if not ok:
            raise SystemExit("개인키가 온전하지 않다 — 중단")
        step("R2.create-role", aws(
            ["iam", "create-role", "--role-name", ROLE,
             "--assume-role-policy-document", TRUST_EC2, "--output", "json"]))
        step("R3.attach-readonly", aws(
            ["iam", "attach-role-policy", "--role-name", ROLE,
             "--policy-arn", READONLY]))
        step("R4.create-instance-profile", aws(
            ["iam", "create-instance-profile",
             "--instance-profile-name", PROFILE, "--output", "json"]))
        step("R5.add-role-to-profile", aws(
            ["iam", "add-role-to-instance-profile",
             "--instance-profile-name", PROFILE, "--role-name", ROLE]))
        vpc = step("R6.create-vpc", aws(
            ["ec2", "create-vpc", "--cidr-block", "10.111.0.0/16",
             "--output", "json"]))["_data"]["Vpc"]["VpcId"]
        ids["vpc"] = vpc
        igw = step("R7.create-igw", aws(
            ["ec2", "create-internet-gateway", "--output", "json"]
        ))["_data"]["InternetGateway"]["InternetGatewayId"]
        ids["igw"] = igw
        step("R8.attach-igw", aws(
            ["ec2", "attach-internet-gateway", "--internet-gateway-id", igw,
             "--vpc-id", vpc]))
        rt = aws(["ec2", "describe-route-tables", "--filters",
                  f"Name=vpc-id,Values={vpc}",
                  "Name=association.main,Values=true",
                  "--query", "RouteTables[0].RouteTableId",
                  "--output", "text"])["excerpt"].strip()
        ids["rt"] = rt
        step("R9.default-route", aws(
            ["ec2", "create-route", "--route-table-id", rt,
             "--destination-cidr-block", "0.0.0.0/0", "--gateway-id", igw,
             "--output", "json"]))
        az0 = aws(["ec2", "describe-availability-zones",
                   "--query", "AvailabilityZones[0].ZoneName",
                   "--output", "text"])["excerpt"].strip()
        subnet = step("R10.create-subnet", aws(
            ["ec2", "create-subnet", "--vpc-id", vpc,
             "--cidr-block", "10.111.1.0/24", "--availability-zone", az0,
             "--output", "json"]))["_data"]["Subnet"]["SubnetId"]
        ids["subnet"] = subnet
        step("R11.public-ip-on-launch", aws(
            ["ec2", "modify-subnet-attribute", "--subnet-id", subnet,
             "--map-public-ip-on-launch"]))
        sg = step("R12.create-sg", aws(
            ["ec2", "create-security-group", "--group-name", "depkb-sig4-sg",
             "--description", "depkb sig4", "--vpc-id", vpc,
             "--output", "json"]))["_data"]["GroupId"]
        ids["sg"] = sg
        step("R13.sg-allow22", aws(
            ["ec2", "authorize-security-group-ingress", "--group-id", sg,
             "--protocol", "tcp", "--port", "22", "--cidr", "0.0.0.0/0",
             "--output", "json"]))
        ami = aws(["ssm", "get-parameters", "--names", SSM_AMI,
                   "--query", "Parameters[0].Value",
                   "--output", "text"])["excerpt"].strip()
        save(doc)
        time.sleep(10)
        inst = step("R14.run-instance", aws(
            ["ec2", "run-instances", "--instance-type", "t3.micro",
             "--image-id", ami, "--count", "1", "--subnet-id", subnet,
             "--security-group-ids", sg, "--key-name", "depkb-sig4",
             "--iam-instance-profile", f"Name={PROFILE}",
             "--output", "json"], timeout=300))["_data"]["Instances"][0]["InstanceId"]
        ids["instance"] = inst
        save(doc)
        state = ""
        deadline = time.time() + 420
        while time.time() < deadline:
            r = aws(["ec2", "describe-instances", "--instance-ids", inst,
                     "--query", "Reservations[0].Instances[0].State.Name",
                     "--output", "text"])
            state = r["excerpt"].strip()
            if state == "running":
                break
            time.sleep(15)
        step("R15.running", {"ok": state == "running", "errorCodes": [],
                             "excerpt": state})
        ip = aws(["ec2", "describe-instances", "--instance-ids", inst,
                  "--query", "Reservations[0].Instances[0].PublicIpAddress",
                  "--output", "text"])["excerpt"].strip()
        ids["ip"] = ip
        save(doc)
        step("R16.guest-baseline", probe(
            ip, USER, str(PEM), "echo depkb-ok", True, 420))
        return

    if phase == "meta":
        step("F1.imds-credentials-work", probe(
            ids["ip"], USER, str(PEM), IMDS, True, 300))
        # M1 — 변이: 실행 중 인스턴스에서 프로필 분리(성공 = 무방비)
        assoc = aws(["ec2", "describe-iam-instance-profile-associations",
                     "--filters", f"Name=instance-id,Values={ids['instance']}",
                     "--query", "IamInstanceProfileAssociations[0].AssociationId",
                     "--output", "text"])["excerpt"].strip()
        ids["assoc"] = assoc
        save(doc)
        step("M1.disassociate-profile", aws(
            ["ec2", "disassociate-iam-instance-profile",
             "--association-id", assoc, "--output", "json"]))
        step("M1b.instance-still-running", aws(
            ["ec2", "describe-instances", "--instance-ids", ids["instance"],
             "--query", "Reservations[0].Instances[0].State.Name",
             "--output", "text"]))
        # IMDS 전파 지연을 넉넉히 — 시한 내 미상실이면 미판정
        step("F2.imds-credentials-lost", probe(
            ids["ip"], USER, str(PEM), IMDS, False, 420, confirm=2))
        step("M2.reassociate-profile", aws(
            ["ec2", "associate-iam-instance-profile",
             "--instance-id", ids["instance"],
             "--iam-instance-profile", f"Name={PROFILE}", "--output", "json"]))
        step("F3.imds-credentials-again", probe(
            ids["ip"], USER, str(PEM), IMDS, True, 420))
        return

    if phase == "egress":
        step("G1.egress-works", probe(
            ids["ip"], USER, str(PEM), EGRESS, True, 300))
        step("M3.delete-default-route", aws(
            ["ec2", "delete-route", "--route-table-id", ids["rt"],
             "--destination-cidr-block", "0.0.0.0/0"]))
        step("G2.egress-lost", probe(
            ids["ip"], USER, str(PEM), EGRESS, False, 300, confirm=2))
        step("M4.recreate-route", aws(
            ["ec2", "create-route", "--route-table-id", ids["rt"],
             "--destination-cidr-block", "0.0.0.0/0",
             "--gateway-id", ids["igw"], "--output", "json"]))
        step("G3.egress-again", probe(
            ids["ip"], USER, str(PEM), EGRESS, True, 300))
        return

    if phase == "finish":
        step("T1.terminate", aws(
            ["ec2", "terminate-instances", "--instance-ids", ids["instance"],
             "--output", "json"]))
        gone = False
        deadline = time.time() + 600
        while time.time() < deadline:
            r = aws(["ec2", "describe-instances", "--instance-ids",
                     ids["instance"],
                     "--query", "Reservations[0].Instances[0].State.Name",
                     "--output", "text"])
            if r["excerpt"].strip() == "terminated":
                gone = True
                break
            time.sleep(20)
        step("T2.terminated", {"ok": gone, "errorCodes": [],
                               "excerpt": "terminated" if gone else "timeout"})
        step("T3.delete-key-pair", aws(
            ["ec2", "delete-key-pair", "--key-name", "depkb-sig4"]))
        step("T4.delete-sg", aws(["ec2", "delete-security-group",
                                  "--group-id", ids["sg"]]))
        step("T5.delete-subnet", aws(["ec2", "delete-subnet",
                                      "--subnet-id", ids["subnet"]]))
        step("T6.detach-igw", aws(
            ["ec2", "detach-internet-gateway", "--internet-gateway-id",
             ids["igw"], "--vpc-id", ids["vpc"]]))
        step("T7.delete-igw", aws(
            ["ec2", "delete-internet-gateway", "--internet-gateway-id",
             ids["igw"]]))
        step("T8.delete-vpc", aws(["ec2", "delete-vpc",
                                   "--vpc-id", ids["vpc"]]))
        step("T9.remove-role-from-profile", aws(
            ["iam", "remove-role-from-instance-profile",
             "--instance-profile-name", PROFILE, "--role-name", ROLE]))
        step("T10.delete-profile", aws(
            ["iam", "delete-instance-profile",
             "--instance-profile-name", PROFILE]))
        step("T11.detach-readonly", aws(
            ["iam", "detach-role-policy", "--role-name", ROLE,
             "--policy-arn", READONLY]))
        step("T12.delete-role", aws(["iam", "delete-role",
                                     "--role-name", ROLE]))
        if PEM.exists():
            PEM.unlink()
        step("T13.residual-vpcs", aws(
            ["ec2", "describe-vpcs", "--query",
             "Vpcs[?IsDefault==`false`].VpcId", "--output", "json"]))
        step("T14.residual-roles", aws(
            ["iam", "list-roles", "--query",
             "Roles[?starts_with(RoleName,'depkb')].RoleName",
             "--output", "json"]))
        doc["finishedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
