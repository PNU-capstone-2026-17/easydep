"""VPN 라운드(aws) — VPN Gateway의 존재·생명주기.

계획: `document/archive/remaining-rounds-plan-2026-07-31.md` §1.
azure만 완주였던 vpn 어휘를 3사로 완결한다. **터널·고객 게이트웨이는
만들지 않는다** — 상대편 장비가 필요하고 우리 어휘 밖이다.

사다리: A1 허상 VPC attach 거부 → A2 VPN GW 생성 → A3 실제 VPC attach
(양성) → L1 attach 상태에서 VPC 삭제 시도(생명주기) → detach 후 정리.

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


def main() -> None:
    doc = {"_note": ("vpn(aws) — VPN Gateway 존재·생명주기. 터널은 범위 밖."),
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

    vgw = step("A1.create-vpn-gateway", aws(
        ["ec2", "create-vpn-gateway", "--type", "ipsec.1", "--output", "json"]
    ))["_data"]["VpnGateway"]["VpnGatewayId"]
    ids["vgw"] = vgw
    save()
    # A2 — 허상 VPC에 attach(존재 판정의 음성)
    step("A2.attach-to-dangling-vpc", aws(
        ["ec2", "attach-vpn-gateway", "--vpn-gateway-id", vgw,
         "--vpc-id", "vpc-0aaaaaaaaaaaaaaaa", "--output", "json"]))
    vpc = step("R1.create-vpc", aws(
        ["ec2", "create-vpc", "--cidr-block", "10.107.0.0/16",
         "--output", "json"]))["_data"]["Vpc"]["VpcId"]
    ids["vpc"] = vpc
    save()
    # A3 — 실제 VPC attach(양성)
    step("A3.attach-to-vpc", aws(
        ["ec2", "attach-vpn-gateway", "--vpn-gateway-id", vgw,
         "--vpc-id", vpc, "--output", "json"]))
    state = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        r = aws(["ec2", "describe-vpn-gateways", "--vpn-gateway-ids", vgw,
                 "--query", "VpnGateways[0].VpcAttachments[0].State",
                 "--output", "text"])
        state = r["excerpt"].strip()
        print(f"attach: {state}", flush=True)
        if state in ("attached", "detached"):
            break
        time.sleep(15)
    step("A4.attached", {"ok": state == "attached", "errorCodes": [],
                         "excerpt": state})
    # L1 — attach 상태에서 VPC 삭제(생명주기)
    step("L1.delete-vpc-with-vgw", aws(
        ["ec2", "delete-vpc", "--vpc-id", vpc]))
    # 정리
    step("T1.detach", aws(
        ["ec2", "detach-vpn-gateway", "--vpn-gateway-id", vgw,
         "--vpc-id", vpc]))
    state = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        r = aws(["ec2", "describe-vpn-gateways", "--vpn-gateway-ids", vgw,
                 "--query", "VpnGateways[0].VpcAttachments[0].State",
                 "--output", "text"])
        state = r["excerpt"].strip()
        print(f"detach: {state}", flush=True)
        if state in ("detached", "None", ""):
            break
        time.sleep(15)
    step("T2.detached", {"ok": True, "errorCodes": [], "excerpt": state})
    step("L2.delete-vpc-after-detach", aws(
        ["ec2", "delete-vpc", "--vpc-id", vpc]))
    step("T3.delete-vgw", aws(
        ["ec2", "delete-vpn-gateway", "--vpn-gateway-id", vgw]))
    step("T4.residual-vgws", aws(
        ["ec2", "describe-vpn-gateways", "--query",
         "VpnGateways[?State!='deleted'].VpnGatewayId", "--output", "json"]))
    step("T5.residual-vpcs", aws(
        ["ec2", "describe-vpcs", "--query", "Vpcs[?IsDefault==`false`].VpcId",
         "--output", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
