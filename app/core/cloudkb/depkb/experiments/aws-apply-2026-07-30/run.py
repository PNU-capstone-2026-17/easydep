"""aws P5a/P5b 실행기 — DryRun(preflight 상당)과 실자원 사슬로 후보를 판정한다.

azure와 같은 4국면이되 층의 실물이 다르다 — 그 차이 자체가 산출물이다:

- aws의 preflight는 배포 템플릿 검증이 아니라 **호출 단위 DryRun**이다(EC2 API).
  DryRun은 권한+검증을 수행하고 자원을 만들지 않는다. 성공하면
  `DryRunOperation` "오류"로 돌아온다 — ok가 아니라 이 코드가 성공 신호다.
- **CLI가 필수 인자를 클라이언트에서 먼저 막는다**(서버에 안 닿음) — 거부가
  어느 층(client/server)에서 났는지를 기록한다.
- A국면은 자원을 만들지 않는다(전부 DryRun 또는 거부 예상). B~D는 무료 자원만
  (VPC·subnet·SG·ENI). 인스턴스는 만들지 않는다.

실행: `python run.py` (리전 ap-northeast-2, 자격증명은 aws configure의 것)
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: 설치 직후 세션은 PATH가 낡아 which가 빈다 — 표준 설치 경로로 폴백.
AWS = shutil.which("aws") or r"C:\Program Files\Amazon\AWSCLIv2\aws.exe"
REGION = "ap-northeast-2"

_CODE = re.compile(r"\(([A-Za-z0-9._]+)\) when calling|"
                   r"(MissingParameter|ValidationError)|"
                   r"the following arguments are required: (\S+)")


def aws(args: list[str], timeout: int = 120) -> dict:
    r = subprocess.run([AWS, "--region", REGION, *args, "--no-cli-pager"],
                       capture_output=True, text=True, timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    layer = "client" if "arguments are required" in text else "server"
    parsed = None
    if r.returncode == 0 and r.stdout.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(r.stdout)
        except json.JSONDecodeError:
            parsed = None
    # 응답 전문은 파싱용으로만 들고, 기록에는 발췌만 남긴다 — 1차 실행이
    # 발췌(600자 절단)를 파싱하다 죽었다. 발췌는 기록이지 데이터가 아니다.
    return {"ok": r.returncode == 0,
            "errorCodes": list(dict.fromkeys(codes)),
            "rejectedAt": None if r.returncode == 0 else layer,
            "excerpt": text.strip().replace("\r", "")[:600],
            "_data": parsed}


def jval(res: dict, *path):
    v = res["_data"]
    for p in path:
        v = v[p]
    return v


def main() -> None:
    steps: dict[str, dict] = {}

    def step(name: str, result: dict) -> None:
        steps[name] = result
        tag = "OK" if result["ok"] else "/".join(result["errorCodes"]) or "FAIL"
        where = f" [{result['rejectedAt']}]" if result["rejectedAt"] else ""
        print(f"{name:34} {tag}{where}")

    # AMI는 SSM 공개 파라미터로 푼다(하드코딩 금지 — 리전·시점 종속)
    ami_res = aws(["ssm", "get-parameter", "--name",
                   "/aws/service/ami-amazon-linux-latest/"
                   "al2023-ami-kernel-default-x86_64", "--output", "json"])
    step("0.resolve-ami", ami_res)
    ami = jval(ami_res, "Parameter", "Value") if ami_res["ok"] else None

    # A — 존재·허상 (자원 무생성)
    step("A.omit-nic-subnet", aws(
        ["ec2", "create-network-interface", "--dry-run"]))
    step("A.dangling-nic-subnet", aws(
        ["ec2", "create-network-interface", "--dry-run",
         "--subnet-id", "subnet-0fffffffffffffff0"]))
    if ami:
        base = ["ec2", "run-instances", "--dry-run", "--image-id", ami,
                "--instance-type", "t3.micro", "--count", "1"]
        step("A.dryrun-vm-default-vpc", aws(base))  # 서브넷 생략 → 기본 VPC 대체?
        step("A.dangling-vm-subnet", aws(
            base + ["--subnet-id", "subnet-0fffffffffffffff0"]))
        step("A.dangling-vm-keyname", aws(
            base + ["--key-name", "depkb-absent-key"]))

    # B — 사슬 구축 (무료 자원만)
    vpc = aws(["ec2", "create-vpc", "--cidr-block", "10.40.0.0/16",
               "--output", "json"])
    step("B.create-vpc", vpc)
    vpc_id = jval(vpc, "Vpc", "VpcId") if vpc["ok"] else None
    if vpc_id:
        sub = aws(["ec2", "create-subnet", "--vpc-id", vpc_id,
                   "--cidr-block", "10.40.1.0/24", "--output", "json"])
        step("B.create-subnet", sub)
        subnet_id = jval(sub, "Subnet", "SubnetId")
        sg = aws(["ec2", "create-security-group", "--vpc-id", vpc_id,
                  "--group-name", "depkb-sg", "--description", "depkb",
                  "--output", "json"])
        step("B.create-sg", sg)
        sg_id = jval(sg, "GroupId")
        eni = aws(["ec2", "create-network-interface", "--subnet-id", subnet_id,
                   "--groups", sg_id, "--output", "json"])
        step("B.create-eni", eni)
        eni_id = jval(eni, "NetworkInterface", "NetworkInterfaceId")

        # C — 생명주기 변이: 사용 중 대상 삭제 시도
        step("C.delete-subnet-in-use", aws(
            ["ec2", "delete-subnet", "--subnet-id", subnet_id]))
        step("C.delete-vpc-in-use", aws(
            ["ec2", "delete-vpc", "--vpc-id", vpc_id]))
        step("C.delete-sg-attached", aws(
            ["ec2", "delete-security-group", "--group-id", sg_id]))

        # D — 역순 정리 (양성 대조)
        step("D.delete-eni", aws(
            ["ec2", "delete-network-interface", "--network-interface-id", eni_id]))
        step("D.delete-sg", aws(
            ["ec2", "delete-security-group", "--group-id", sg_id]))
        step("D.delete-subnet", aws(
            ["ec2", "delete-subnet", "--subnet-id", subnet_id]))
        step("D.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", vpc_id]))
        step("residual", aws(
            ["ec2", "describe-network-interfaces", "--filters",
             f"Name=vpc-id,Values={vpc_id}", "--output", "json"]))

    for s in steps.values():
        s.pop("_data", None)
    (HERE / "results.json").write_text(json.dumps({
        "_note": ("aws 측정 기록(P5a=DryRun·P5b=실자원 사슬). DryRunOperation "
                  "코드는 '만들었다면 성공했을 것'이라는 뜻의 성공 신호다. "
                  "rejectedAt은 거부가 난 층(client CLI/server API)."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": REGION,
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
