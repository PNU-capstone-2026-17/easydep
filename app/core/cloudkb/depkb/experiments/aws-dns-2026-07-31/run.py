"""globalDns 라운드(aws) — Route53 private hosted zone과 record.

계획: `document/archive/dns-filesystem-plan-2026-07-31.md`. 사설 영역만
쓴다. private hosted zone은 VPC를 요구할 수 있고, 그렇다면 그 자체가
관측(globalDns→network)이다 — 전제 VPC를 만들고 진행한다.

셀: A0 VPC 없이 private zone → A1 없는 zone에 record → A2 VPC 주고 zone →
A3 record(양성) → L1 record 있는 zone 삭제 → L2 record 삭제 후 zone 삭제.

실행: `python run.py`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import REGION, aws  # noqa: E402

HERE = Path(__file__).resolve().parent
DOMAIN = "depkb.internal"
RRSET = json.dumps({"Changes": [{"Action": "CREATE", "ResourceRecordSet": {
    "Name": f"api.{DOMAIN}", "Type": "A", "TTL": 300,
    "ResourceRecords": [{"Value": "10.0.0.10"}]}}]})
RRSET_DEL = RRSET.replace('"CREATE"', '"DELETE"')


def main() -> None:
    doc = {"_note": ("globalDns(aws) — Route53 사설 영역. 사설 한정 측정."),
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

    # A0 — VPC 없이 사설 영역(존재 의존의 음성: globalDns→network)
    step("A0.private-zone-without-vpc", aws(
        ["route53", "create-hosted-zone", "--name", DOMAIN,
         "--caller-reference", "depkb-a0", "--hosted-zone-config",
         "PrivateZone=true", "--output", "json"]))
    # A1 — 없는 영역에 레코드
    step("A1.record-without-zone", aws(
        ["route53", "change-resource-record-sets", "--hosted-zone-id",
         "Z00000000000000000000", "--change-batch", RRSET, "--output", "json"]))
    # 전제 — VPC
    vpc = step("R1.create-vpc", aws(
        ["ec2", "create-vpc", "--cidr-block", "10.104.0.0/16",
         "--output", "json"]))["_data"]["Vpc"]["VpcId"]
    ids["vpc"] = vpc
    save()
    # A2 — VPC 주고 사설 영역(양성)
    zone = step("A2.create-private-zone", aws(
        ["route53", "create-hosted-zone", "--name", DOMAIN,
         "--caller-reference", "depkb-a2", "--hosted-zone-config",
         "PrivateZone=true", "--vpc",
         f"VPCRegion={REGION},VPCId={vpc}", "--output", "json"]))
    zid = zone["_data"]["HostedZone"]["Id"].rsplit("/", 1)[-1]
    ids["zoneId"] = zid
    save()
    # A3 — 레코드(양성)
    step("A3.create-record", aws(
        ["route53", "change-resource-record-sets", "--hosted-zone-id", zid,
         "--change-batch", RRSET, "--output", "json"]))
    # L1 — 레코드 있는 영역 삭제(생명주기)
    step("L1.delete-zone-with-record", aws(
        ["route53", "delete-hosted-zone", "--id", zid, "--output", "json"]))
    # L2 — 레코드 삭제 후 영역 삭제(양성 대조)
    step("L2.delete-record", aws(
        ["route53", "change-resource-record-sets", "--hosted-zone-id", zid,
         "--change-batch", RRSET_DEL, "--output", "json"]))
    step("L3.delete-zone-after", aws(
        ["route53", "delete-hosted-zone", "--id", zid, "--output", "json"]))
    step("T1.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", vpc]))
    step("T2.residual-zones", aws(
        ["route53", "list-hosted-zones", "--query", "HostedZones[].Name",
         "--output", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
