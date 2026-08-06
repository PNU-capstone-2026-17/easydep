"""image 라운드(aws) — vm→image: DryRun 사다리, 실물 생성 0.

계획: `document/archive/image-round-plan-2026-07-31.md`. aws는 부팅을 기존
볼륨으로 대신하는 RunInstances 경로가 없다는 가설 — 생략 거부의 층
(client/server)을 rejectedAt에 그대로 기록한다(nic→subnet의 client-층 한계
명시와 같은 규율). CFN `Required: False`는 위치 플래그(LaunchTemplate 경로)
이고, LT는 AMI를 나르는 다른 자리이지 대안이 아니다.

셀: D1 ImageId·LT 둘 다 생략 → D2 허상 AMI → D3 SSM 공개 파라미터로 실제
AMI 해석 후 DryRun 양성(DryRunOperation).

실행: `python run.py`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
SSM_AMI = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"


def main() -> None:
    doc = {"_note": ("vm→image(aws) — DryRun 사다리. 생략 거부의 층을 "
                     "rejectedAt에 기록, 실물 생성 0."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "ids": {}, "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        save()
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    step("D1.omit-image-and-lt", aws(
        ["ec2", "run-instances", "--instance-type", "t3.micro",
         "--count", "1", "--dry-run", "--output", "json"]))
    step("D2.dangling-ami", aws(
        ["ec2", "run-instances", "--instance-type", "t3.micro",
         "--count", "1", "--image-id", "ami-0aaaaaaaaaaaaaaaa",
         "--dry-run", "--output", "json"]))
    got = step("D3a.resolve-real-ami", aws(
        ["ssm", "get-parameters", "--names", SSM_AMI,
         "--query", "Parameters[0].Value", "--output", "text"]))
    ami = got["excerpt"].strip()
    doc["ids"]["ami"] = ami
    save()
    step("D3b.valid-ami-dryrun", aws(
        ["ec2", "run-instances", "--instance-type", "t3.micro",
         "--count", "1", "--image-id", ami, "--dry-run", "--output", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
