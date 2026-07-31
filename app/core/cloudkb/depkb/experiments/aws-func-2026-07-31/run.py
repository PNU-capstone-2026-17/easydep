"""기능 의존 첫 실험(aws) — vm→publicIp(EIP): 도달성 결속.

계획: `document/archive/functional-dependency-plan-2026-07-31.md`.
자동 공인 IP 없이 인스턴스를 띄우고 EIP를 부착/분리/재부착한다 — EIP는
분리해도 우리 소유로 남는 실주소라 **같은 주소로 회복**을 관측할 수 있다
(gcp 임시 IP와 다른 점). 22 허용 SG는 전제이지 판정 대상이 아니다.

실행: `python run.py`
"""

import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aws-apply2-2026-07-31"))
from run import aws  # noqa: E402

HERE = Path(__file__).resolve().parent
SSM_AMI = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"


def tcp_ok(ip: str, port: int = 22, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(ip: str, want: bool, budget: int) -> dict:
    deadline = time.time() + budget
    tries = 0
    while time.time() < deadline:
        tries += 1
        got = tcp_ok(ip)
        print(f"tcp {ip}:22 -> {got} (want {want})", flush=True)
        if got == want:
            return {"ok": True, "errorCodes": [],
                    "excerpt": f"tcp22={got} (시도 {tries})"}
        time.sleep(10)
    return {"ok": False, "errorCodes": ["PROBE_TIMEOUT"],
            "excerpt": f"tcp22가 {budget}초 내 {want}에 도달 못 함 (시도 {tries})"}


def main() -> None:
    doc = {"_note": ("기능 의존(aws) — vm→publicIp(EIP). 기능 신호 = 로컬 "
                     "TCP 22. EIP라 같은 주소로 회복 관측."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
    sg = step("R2.create-sg-allow22", aws(
        ["ec2", "create-security-group", "--group-name", "depkb-func-sg",
         "--description", "depkb func round - ssh probe",
         "--output", "json"]))["_data"]["GroupId"]
    ids["sg"] = sg
    save()
    step("R3.sg-ingress-22", aws(
        ["ec2", "authorize-security-group-ingress", "--group-id", sg,
         "--protocol", "tcp", "--port", "22", "--cidr", "0.0.0.0/0",
         "--output", "json"]))
    inst = step("R4.run-instance-no-auto-ip", aws(
        ["ec2", "run-instances", "--instance-type", "t3.micro",
         "--image-id", ami, "--count", "1", "--security-group-ids", sg,
         "--no-associate-public-ip-address", "--output", "json"],
        timeout=300))["_data"]["Instances"][0]["InstanceId"]
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
    step("R5.running", {"ok": state == "running", "errorCodes": [],
                        "excerpt": state})
    alloc = step("R6.allocate-eip", aws(
        ["ec2", "allocate-address", "--output", "json"]))
    ids["allocId"] = alloc["_data"]["AllocationId"]
    ip = alloc["_data"]["PublicIp"]
    ids["ip"] = ip
    save()
    assoc = step("R7.associate-eip", aws(
        ["ec2", "associate-address", "--instance-id", inst,
         "--allocation-id", ids["allocId"], "--output", "json"]))
    ids["assocId"] = assoc["_data"]["AssociationId"]
    save()

    step("F1.reachable-baseline", probe(ip, True, 300))
    # M1 — 변이: 실행 중 인스턴스에서 EIP 분리. 성공 = 무방비.
    step("M1.disassociate-while-running", aws(
        ["ec2", "disassociate-address", "--association-id", ids["assocId"]]))
    step("M1b.instance-public-ip-now", aws(
        ["ec2", "describe-instances", "--instance-ids", inst,
         "--query", "Reservations[0].Instances[0]."
         "{state:State.Name,publicIp:PublicIpAddress}", "--output", "json"]))
    step("F2.unreachable-after-detach", probe(ip, False, 120))
    # M2 — 복원: 같은 EIP 재부착 → 같은 주소로 회복.
    assoc2 = step("M2.reassociate-eip", aws(
        ["ec2", "associate-address", "--instance-id", inst,
         "--allocation-id", ids["allocId"], "--output", "json"]))
    ids["assocId2"] = assoc2["_data"]["AssociationId"]
    save()
    step("F3.reachable-again", probe(ip, True, 300))

    # 정리
    step("T1.disassociate", aws(
        ["ec2", "disassociate-address", "--association-id", ids["assocId2"]]))
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
    step("T5.delete-sg", aws(["ec2", "delete-security-group",
                              "--group-id", sg]))
    step("T6.residual-eips", aws(
        ["ec2", "describe-addresses", "--query", "Addresses[].PublicIp",
         "--output", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
