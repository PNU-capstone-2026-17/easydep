"""fileSystem 라운드(aws) — EFS와 mount target의 존재·생명주기.

계획: `document/archive/dns-filesystem-plan-2026-07-31.md`. k8s를 거치지
않고 자원 층에서 직접 잰다(합성 2라운드의 RWX 완주 불가를 자원 층으로
우회). EFS는 탄력 모드라 분 단위 비용이 거의 없다.

셀: A1 파일시스템 생성(네트워크 인자 없이 되는가) → A2 mount target에서
subnet 생략(클라이언트/서버 층 관측) → A3 허상 subnet → A4 실제 subnet
으로 생성(양성) → L1 mount target 존재 중 subnet 삭제(생명주기) →
정리(mount target → 파일시스템 → 네트워크).

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
    doc = {"_note": ("fileSystem(aws) — EFS·mount target. RWX의 자원 층을 "
                     "k8s 없이 직접 잰다."),
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

    # A1 — 파일시스템 자체는 네트워크 인자 없이 서는가
    fs = step("A1.create-filesystem-no-network", aws(
        ["efs", "create-file-system", "--performance-mode", "generalPurpose",
         "--tags", "Key=Name,Value=depkb-fs", "--output", "json"]))
    fsid = fs["_data"]["FileSystemId"]
    ids["fs"] = fsid
    save()
    state = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        r = aws(["efs", "describe-file-systems", "--file-system-id", fsid,
                 "--query", "FileSystems[0].LifeCycleState", "--output", "text"])
        state = r["excerpt"].strip()
        print(f"fs state: {state}", flush=True)
        if state == "available":
            break
        time.sleep(10)
    step("A2.filesystem-available", {"ok": state == "available",
                                     "errorCodes": [], "excerpt": state})
    # 전제 — VPC·서브넷
    vpc = step("R1.create-vpc", aws(
        ["ec2", "create-vpc", "--cidr-block", "10.105.0.0/16",
         "--output", "json"]))["_data"]["Vpc"]["VpcId"]
    ids["vpc"] = vpc
    subnet = step("R2.create-subnet", aws(
        ["ec2", "create-subnet", "--vpc-id", vpc,
         "--cidr-block", "10.105.1.0/24", "--output", "json"]
    ))["_data"]["Subnet"]["SubnetId"]
    ids["subnet"] = subnet
    save()
    # A3 — mount target에서 subnet 생략
    step("A3.mount-target-omit-subnet", aws(
        ["efs", "create-mount-target", "--file-system-id", fsid,
         "--output", "json"]))
    # A4 — 허상 subnet
    step("A4.mount-target-dangling-subnet", aws(
        ["efs", "create-mount-target", "--file-system-id", fsid,
         "--subnet-id", "subnet-0aaaaaaaaaaaaaaaa", "--output", "json"]))
    # A5 — 실제 subnet(양성)
    mt = step("A5.create-mount-target", aws(
        ["efs", "create-mount-target", "--file-system-id", fsid,
         "--subnet-id", subnet, "--output", "json"]))
    mtid = mt["_data"]["MountTargetId"]
    ids["mountTarget"] = mtid
    save()
    state = ""
    deadline = time.time() + 420
    while time.time() < deadline:
        r = aws(["efs", "describe-mount-targets", "--mount-target-id", mtid,
                 "--query", "MountTargets[0].LifeCycleState", "--output", "text"])
        state = r["excerpt"].strip()
        print(f"mt state: {state}", flush=True)
        if state == "available":
            break
        time.sleep(15)
    step("A6.mount-target-available", {"ok": state == "available",
                                       "errorCodes": [], "excerpt": state})
    # L1 — mount target 존재 중 subnet 삭제(생명주기)
    step("L1.delete-subnet-in-use", aws(
        ["ec2", "delete-subnet", "--subnet-id", subnet]))
    # L2 — 파일시스템 삭제 시도(mount target 있는 채로)
    step("L2.delete-filesystem-with-mount-target", aws(
        ["efs", "delete-file-system", "--file-system-id", fsid]))
    # 정리
    step("T1.delete-mount-target", aws(
        ["efs", "delete-mount-target", "--mount-target-id", mtid]))
    gone = False
    deadline = time.time() + 420
    while time.time() < deadline:
        r = aws(["efs", "describe-mount-targets", "--file-system-id", fsid,
                 "--query", "length(MountTargets)", "--output", "text"])
        if r["excerpt"].strip() in ("0", ""):
            gone = True
            break
        time.sleep(15)
    step("T2.mount-target-gone", {"ok": gone, "errorCodes": [],
                                  "excerpt": "gone" if gone else "timeout"})
    step("T3.delete-filesystem", aws(
        ["efs", "delete-file-system", "--file-system-id", fsid]))
    step("T4.delete-subnet", aws(["ec2", "delete-subnet",
                                  "--subnet-id", subnet]))
    step("T5.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", vpc]))
    step("T6.residual-filesystems", aws(
        ["efs", "describe-file-systems", "--query", "FileSystems[].FileSystemId",
         "--output", "json"]))
    step("T7.residual-vpcs", aws(
        ["ec2", "describe-vpcs", "--query", "Vpcs[?IsDefault==`false`].VpcId",
         "--output", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
