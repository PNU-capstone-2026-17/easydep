"""aws 2라운드 — 잔여 8쌍을 닫는다. DryRun 깊이가 증명 안 된 API는 실물로.

측정 대상:

- **B1** subnet→network: 허상 VPC로 실제 생성 시도(자원 무생성 실패 예상) —
  참조 해석의 서버 증거. 생략은 클라이언트 층이 막아 서버로 못 보낸다(한계 명시).
- **B2** firewall→network: vpc-id 생략하고 **실제** SG 생성 → 기본 VPC 대체
  예상. 성공하면 서버가 채운 VpcId 실물을 읽고 지운다.
- **B3** nic→firewall: --groups 없이 **실제** ENI 생성 → 기본 SG 부착 예상.
  서버가 채운 그룹 실물을 읽는다.
- **B4** lb→subnet: ①서브넷 없이 → 거부 예상 ②ALB에 서브넷 1개 → 2-AZ 요구
  거부 예상(카디널리티 술어) ③NLB에 서브넷 1개·SG 없이 → 성공 예상
  (lb→firewall optional 겸).
- vm→nic·vm→firewall·vm→disk는 1라운드 RunInstances DryRun 성공으로 닫는다
  (그 DryRun은 허상을 잡았으므로 깊이가 증명돼 있다) — 새 실험 불요.

비용: NLB 수 분(시간당 ~$0.02) 외 전부 무료. 실행: `python run.py`
"""

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
AWS = shutil.which("aws") or r"C:\Program Files\Amazon\AWSCLIv2\aws.exe"
REGION = "ap-northeast-2"
_CODE = re.compile(r"\(([A-Za-z0-9._]+)\) when calling|"
                   r"the following arguments are required: (\S+)")


def aws(args: list[str], timeout: int = 180) -> dict:
    r = subprocess.run([AWS, "--region", REGION, *args, "--no-cli-pager"],
                       capture_output=True, text=True, timeout=timeout, check=False)
    text = (r.stderr or "") + (r.stdout or "")
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    parsed = None
    if r.returncode == 0 and r.stdout.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(r.stdout)
        except json.JSONDecodeError:
            parsed = None
    return {"ok": r.returncode == 0,
            "errorCodes": list(dict.fromkeys(codes)),
            "rejectedAt": None if r.returncode == 0 else (
                "client" if "arguments are required" in text else "server"),
            "excerpt": text.strip().replace("\r", "")[:600],
            "_data": parsed}


def main() -> None:
    steps: dict[str, dict] = {}

    def step(name: str, result: dict) -> None:
        steps[name] = result
        tag = "OK" if result["ok"] else "/".join(result["errorCodes"]) or "FAIL"
        print(f"{name:34} {tag}")

    # B1 — 허상 VPC 밑 서브넷 (실패 예상, 자원 무생성)
    step("B1.dangling-subnet-vpc", aws(
        ["ec2", "create-subnet", "--vpc-id", "vpc-0fffffffffffffff0",
         "--cidr-block", "10.90.1.0/24"]))

    # B2 — vpc-id 없이 실제 SG 생성 → 기본 VPC 대체 관측
    sg = aws(["ec2", "create-security-group", "--group-name", "depkb2-defsg",
              "--description", "depkb default-vpc probe", "--output", "json"])
    step("B2.sg-omit-vpc", sg)
    if sg["ok"]:
        gid = sg["_data"]["GroupId"]
        desc = aws(["ec2", "describe-security-groups", "--group-ids", gid,
                    "--query", "SecurityGroups[0].VpcId", "--output", "json"])
        steps["B2.server-filled-vpc"] = {**desc,
                                         "excerpt": desc["excerpt"][:120]}
        print(f"{'B2.server-filled-vpc':34} {desc['excerpt'][:40]}")
        step("B2.cleanup-sg", aws(
            ["ec2", "delete-security-group", "--group-id", gid]))

    # 사슬 (무료): VPC + 서브넷 1
    vpc = aws(["ec2", "create-vpc", "--cidr-block", "10.90.0.0/16",
               "--output", "json"])
    step("B0.create-vpc", vpc)
    vpc_id = vpc["_data"]["Vpc"]["VpcId"]
    sub = aws(["ec2", "create-subnet", "--vpc-id", vpc_id,
               "--cidr-block", "10.90.1.0/24", "--output", "json"])
    step("B0.create-subnet", sub)
    subnet_id = sub["_data"]["Subnet"]["SubnetId"]

    # B3 — --groups 없이 실제 ENI → 기본 SG 부착 관측
    eni = aws(["ec2", "create-network-interface", "--subnet-id", subnet_id,
               "--output", "json"])
    step("B3.eni-omit-groups", eni)
    eni_id = None
    if eni["ok"]:
        eni_id = eni["_data"]["NetworkInterface"]["NetworkInterfaceId"]
        groups = eni["_data"]["NetworkInterface"].get("Groups", [])
        steps["B3.server-filled-groups"] = {
            "ok": bool(groups), "errorCodes": [], "rejectedAt": None,
            "excerpt": json.dumps(groups, ensure_ascii=False)[:200]}
        print(f"{'B3.server-filled-groups':34} {[g.get('GroupName') for g in groups]}")

    # B4 — LB와 서브넷
    step("B4.lb-omit-subnets", aws(
        ["elbv2", "create-load-balancer", "--name", "depkb2-lb0",
         "--type", "network"]))
    step("B4.alb-one-subnet", aws(
        ["elbv2", "create-load-balancer", "--name", "depkb2-alb",
         "--type", "application", "--subnets", subnet_id]))
    nlb = aws(["elbv2", "create-load-balancer", "--name", "depkb2-nlb",
               "--type", "network", "--subnets", subnet_id,
               "--output", "json"])
    step("B4.nlb-one-subnet-no-sg", nlb)
    nlb_arn = (nlb["_data"]["LoadBalancers"][0]["LoadBalancerArn"]
               if nlb["ok"] else None)

    # D — 정리 (NLB 삭제는 비동기 — 서브넷이 풀릴 때까지 재시도)
    if nlb_arn:
        step("D.delete-nlb", aws(
            ["elbv2", "delete-load-balancer", "--load-balancer-arn", nlb_arn]))
    if eni_id:
        step("D.delete-eni", aws(
            ["ec2", "delete-network-interface",
             "--network-interface-id", eni_id]))
    deadline = time.time() + 300
    while True:
        res = aws(["ec2", "delete-subnet", "--subnet-id", subnet_id])
        if res["ok"] or time.time() > deadline:
            step("D.delete-subnet", res)
            break
        time.sleep(15)
    step("D.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", vpc_id]))
    step("residual", aws(
        ["ec2", "describe-network-interfaces", "--filters",
         f"Name=vpc-id,Values={vpc_id}", "--output", "json"]))

    for s in steps.values():
        s.pop("_data", None)
    (HERE / "results.json").write_text(json.dumps({
        "_note": ("aws 2라운드 측정 기록 — DryRun 깊이가 증명 안 된 API는 실물 "
                  "생성으로 쟀다. server-filled-* 스텝이 서버 대체의 실물이다."),
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "region": REGION,
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
