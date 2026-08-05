"""customImage 라운드(aws) — AMI의 존재·생명주기.

계획: `document/archive/customimage-plan-2026-07-31.md`. aws는 이미지가
**인스턴스에서** 나온다(create-image) — 원본이 디스크가 아니라 인스턴스라는
결속 차이를 판정에 적는다. 정리에 **스냅샷 삭제 포함**(등록 해제만 하면
스냅샷이 조용히 남는다).

사다리: 인스턴스 → 허상 인스턴스로 create-image 거부 → 실제 인스턴스로
AMI(양성) → **AMI 존재 중 원본 인스턴스 종료**(생명주기) → 그 AMI로 VM
(사슬 완결) → 정리.

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
SSM_AMI = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"


def main() -> None:
    doc = {"_note": ("customImage(aws) — AMI는 인스턴스에서 나온다(원본이 "
                     "디스크가 아님). 정리에 스냅샷 포함."),
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

    def wait_instance(iid: str, want: str, budget: int) -> str:
        deadline = time.time() + budget
        state = ""
        while time.time() < deadline:
            r = aws(["ec2", "describe-instances", "--instance-ids", iid,
                     "--query", "Reservations[0].Instances[0].State.Name",
                     "--output", "text"])
            state = r["excerpt"].strip()
            if state == want:
                return state
            time.sleep(15)
        return state

    base = step("R1.resolve-ami", aws(
        ["ssm", "get-parameters", "--names", SSM_AMI,
         "--query", "Parameters[0].Value", "--output", "text"]))["excerpt"].strip()
    src = step("R2.run-source-instance", aws(
        ["ec2", "run-instances", "--instance-type", "t3.micro",
         "--image-id", base, "--count", "1", "--output", "json"],
        timeout=300))["_data"]["Instances"][0]["InstanceId"]
    ids["srcInstance"] = src
    save()
    step("R3.source-running", {"ok": wait_instance(src, "running", 300) == "running",
                               "errorCodes": [], "excerpt": "running"})
    # A1 — 허상 인스턴스에서 이미지 생성(존재 판정의 음성)
    step("A1.dangling-source-instance", aws(
        ["ec2", "create-image", "--instance-id", "i-0aaaaaaaaaaaaaaaa",
         "--name", "depkb-cimg-bad", "--output", "json"]))
    # A2 — 실제 인스턴스에서 AMI(양성)
    ami = step("A2.create-image-from-instance", aws(
        ["ec2", "create-image", "--instance-id", src, "--name", "depkb-cimg",
         "--no-reboot", "--output", "json"], timeout=300))["_data"]["ImageId"]
    ids["ami"] = ami
    save()
    state = ""
    deadline = time.time() + 900
    while time.time() < deadline:
        r = aws(["ec2", "describe-images", "--image-ids", ami,
                 "--query", "Images[0].State", "--output", "text"])
        state = r["excerpt"].strip()
        print(f"ami state: {state}", flush=True)
        if state in ("available", "failed"):
            break
        time.sleep(20)
    step("A3.ami-available", {"ok": state == "available", "errorCodes": [],
                              "excerpt": state})
    snaps = step("A4.ami-snapshots", aws(
        ["ec2", "describe-images", "--image-ids", ami, "--query",
         "Images[0].BlockDeviceMappings[].Ebs.SnapshotId", "--output", "json"]))
    ids["snapshots"] = snaps["_data"] or []
    save()
    # L1 — AMI 존재 중 원본 인스턴스 종료(생명주기)
    step("L1.terminate-source-while-ami-exists", aws(
        ["ec2", "terminate-instances", "--instance-ids", src,
         "--output", "json"]))
    step("L1b.source-terminated", {
        "ok": wait_instance(src, "terminated", 420) == "terminated",
        "errorCodes": [], "excerpt": "terminated"})
    step("L1c.ami-after-source-terminate", aws(
        ["ec2", "describe-images", "--image-ids", ami,
         "--query", "Images[0].{state:State,name:Name}", "--output", "json"]))
    # C1 — 그 AMI로 VM(사슬 완결)
    vm = step("C1.run-instance-from-custom-ami", aws(
        ["ec2", "run-instances", "--instance-type", "t3.micro",
         "--image-id", ami, "--count", "1", "--output", "json"],
        timeout=300))["_data"]["Instances"][0]["InstanceId"]
    ids["vm"] = vm
    save()
    step("C2.vm-running", {"ok": wait_instance(vm, "running", 420) == "running",
                           "errorCodes": [], "excerpt": "running"})
    # T — 정리 (스냅샷 포함)
    step("T1.terminate-vm", aws(
        ["ec2", "terminate-instances", "--instance-ids", vm, "--output", "json"]))
    step("T2.vm-terminated", {
        "ok": wait_instance(vm, "terminated", 420) == "terminated",
        "errorCodes": [], "excerpt": "terminated"})
    step("T3.deregister-ami", aws(["ec2", "deregister-image", "--image-id", ami]))
    for i, snap in enumerate(ids["snapshots"], start=1):
        step(f"T4.delete-snapshot{i}", aws(
            ["ec2", "delete-snapshot", "--snapshot-id", snap]))
    step("T5.residual-images", aws(
        ["ec2", "describe-images", "--owners", "self",
         "--query", "Images[].ImageId", "--output", "json"]))
    step("T6.residual-snapshots", aws(
        ["ec2", "describe-snapshots", "--owner-ids", "self",
         "--query", "Snapshots[].SnapshotId", "--output", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
