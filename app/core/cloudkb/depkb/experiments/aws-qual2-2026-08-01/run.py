"""자격 요건 2라운드(aws) — 정책 없는 역할로 EKS가 **ACTIVE까지 가는가**.

1라운드(`aws-qual-2026-08-01`)에서 가설이 깨졌다: 정책 없는 역할로도 생성
요청이 **수락됐다**. 그런데 곧바로 정책을 붙여 최종 상태를 못 쟀다 — EKS IAM
기능 축과 같은 **비동기 변이 복원** 실수다(round1의 Z1).

여기서는 **정책을 붙이지 않고 최종 상태까지 폴링한다.**

  ACTIVE로 가면   → 자격 요건이 **없다**(정책은 클러스터 기동의 조건이 아니다)
  CREATE_FAILED   → 자격 요건이 **있다**(생성은 수락되고 프로비저닝에서 갈린다)

어느 쪽이든 판정이고, 둘의 차이가 곧 "검사가 언제 일어나는가"다.

비용: 실패하면 과금 없음, 성공하면 컨트롤 플레인 몇 분($0.1/h).
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
ROLE = "depkb-qual2-role"
CLUSTER = "depkb-qual2"
TRUST = json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"},
    "Action": "sts:AssumeRole"}]})
POLICY = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"


def main() -> None:
    doc = {"_note": ("자격 요건 2라운드(aws) — 정책 없이 EKS 최종 상태까지 "
                     "폴링한다. 1라운드는 변이를 일찍 복원해 못 쟀다."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "ids": {}, "steps": {}}
    steps, ids = doc["steps"], doc["ids"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        save()
        print(f"{name:38} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    role = step("R1.create-role-no-policy", aws(
        ["iam", "create-role", "--role-name", ROLE,
         "--assume-role-policy-document", TRUST, "--output", "json"]))
    ids["roleArn"] = role["_data"]["Role"]["Arn"]
    vpc = step("R2.create-vpc", aws(
        ["ec2", "create-vpc", "--cidr-block", "10.121.0.0/16",
         "--output", "json"]))["_data"]["Vpc"]["VpcId"]
    ids["vpc"] = vpc
    azs = aws(["ec2", "describe-availability-zones",
               "--query", "AvailabilityZones[:2].ZoneName",
               "--output", "json"])["_data"]
    for i, (cidr, zone) in enumerate([("10.121.1.0/24", azs[0]),
                                      ("10.121.2.0/24", azs[1])], start=1):
        s = step(f"R3.create-subnet{i}", aws(
            ["ec2", "create-subnet", "--vpc-id", vpc, "--cidr-block", cidr,
             "--availability-zone", zone, "--output", "json"]
        ))["_data"]["Subnet"]["SubnetId"]
        ids[f"subnet{i}"] = s
    save()
    time.sleep(10)

    step("R4.role-has-no-policy", aws(
        ["iam", "list-attached-role-policies", "--role-name", ROLE,
         "--query", "AttachedPolicies", "--output", "json"]))
    # A1 — 정책 없이 생성. 1라운드에서 수락된다는 것은 이미 안다.
    step("A1.create-without-policy", aws(
        ["eks", "create-cluster", "--name", CLUSTER,
         "--role-arn", ids["roleArn"], "--resources-vpc-config",
         f"subnetIds={ids['subnet1']},{ids['subnet2']}",
         "--output", "json"], timeout=300))
    # A2 — **최종 상태까지 폴링한다**(이 라운드의 본체). 변이를 복원하지 않는다.
    state = ""
    health = ""
    deadline = time.time() + 1500
    while time.time() < deadline:
        r = aws(["eks", "describe-cluster", "--name", CLUSTER, "--query",
                 "cluster.{s:status,h:health.issues[0].code}", "--output", "json"])
        try:
            got = json.loads(r["excerpt"])
            state, health = got.get("s") or "", got.get("h") or ""
        except Exception:
            state = r["excerpt"].strip()[:30]
        print(f"status={state} health={health}", flush=True)
        if state in ("ACTIVE", "FAILED", "CREATE_FAILED"):
            break
        time.sleep(30)
    step("A2.final-state-without-policy", {
        "ok": state in ("ACTIVE", "FAILED", "CREATE_FAILED"),
        "errorCodes": [] if state == "ACTIVE" else [state or "TIMEOUT"],
        "excerpt": f"status={state} health={health}"})

    # 정리 — 클러스터가 어떤 상태든 지운다
    step("T1.delete-cluster", aws(
        ["eks", "delete-cluster", "--name", CLUSTER, "--output", "json"]))
    gone = False
    deadline = time.time() + 900
    while time.time() < deadline:
        r = aws(["eks", "describe-cluster", "--name", CLUSTER,
                 "--query", "cluster.status", "--output", "text"])
        if not r["ok"]:
            gone = True
            break
        print(f"deleting… {r['excerpt'].strip()[:20]}", flush=True)
        time.sleep(30)
    step("T2.cluster-gone", {"ok": gone, "errorCodes": [],
                             "excerpt": "gone" if gone else "timeout"})
    for i in (1, 2):
        step(f"T3.delete-subnet{i}", aws(
            ["ec2", "delete-subnet", "--subnet-id", ids[f"subnet{i}"]]))
    step("T4.delete-vpc", aws(["ec2", "delete-vpc", "--vpc-id", ids["vpc"]]))
    step("T5.delete-role", aws(["iam", "delete-role", "--role-name", ROLE]))
    step("T6.residual-clusters", aws(["eks", "list-clusters", "--output", "json"]))
    step("T7.residual-roles", aws(
        ["iam", "list-roles", "--query",
         "Roles[?starts_with(RoleName,'depkb')].RoleName", "--output", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
