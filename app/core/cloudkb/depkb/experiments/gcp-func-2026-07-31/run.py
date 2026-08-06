"""기능 의존 첫 실험(gcp) — vm→publicIp: 도달성 결속.

계획: `document/archive/functional-dependency-plan-2026-07-31.md`.
gcp의 공인 IP는 인스턴스의 accessConfig다 — RUNNING인 채로 삭제/재부여가
되는지(무방비)와 TCP 22 도달성이 관측 대상. default 네트워크의
default-allow-ssh 방화벽이 리스너 경로(전제이지 판정 대상 아님).
재부여 시 임시 IP는 **새 주소**가 온다 — 회복 관측은 새 주소로.

실행: `python run.py <project> <zone>`
"""

import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent
DEBIAN = "projects/debian-cloud/global/images/family/debian-12"


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
    project, zone = sys.argv[1], sys.argv[2]
    tok = token()
    z = f"{BASE}/projects/{project}/zones/{zone}"
    inst = f"{z}/instances/depkb-func-vm"
    doc = {"_note": ("기능 의존(gcp) — vm→publicIp(accessConfig). 기능 신호 = "
                     "로컬 TCP 22. 재부여 임시 IP는 새 주소."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "ids": {}, "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = result
        save()
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    def nat_ip() -> str:
        _, got = call("GET", inst, None, tok)
        acs = (got.get("networkInterfaces") or [{}])[0].get("accessConfigs") or []
        return (acs[0].get("natIP") or "") if acs else ""

    step("R1.create-vm", mutate(
        "POST", f"{z}/instances",
        {"name": "depkb-func-vm", "machineType": f"{z}/machineTypes/e2-small",
         "disks": [{"boot": True, "autoDelete": True,  # 정리 편의 — 판정 아님
                    "initializeParams": {"sourceImage": DEBIAN,
                                         "diskSizeGb": "10"}}],
         "networkInterfaces": [{
             "network": f"{BASE}/projects/{project}/global/networks/default",
             "accessConfigs": [{"type": "ONE_TO_ONE_NAT",
                                "name": "External NAT"}]}]},
        tok))
    state = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        _, got = call("GET", inst, None, tok)
        state = got.get("status", "")
        if state == "RUNNING":
            break
        time.sleep(10)
    step("R2.running", {"ok": state == "RUNNING", "errorCodes": [],
                        "excerpt": state})
    ip = nat_ip()
    doc["ids"]["ip"] = ip
    save()
    step("R3.nat-ip", {"ok": bool(ip), "errorCodes": [], "excerpt": ip})

    step("F1.reachable-baseline", probe(ip, True, 300))
    # M1 — 변이: RUNNING인 채로 accessConfig 삭제. 성공 = 무방비.
    step("M1.delete-accessconfig-while-running", mutate(
        "POST", f"{inst}/deleteAccessConfig"
        "?accessConfig=External%20NAT&networkInterface=nic0", None, tok))
    _, got = call("GET", inst, None, tok)
    step("M1b.vm-still-running", {
        "ok": got.get("status") == "RUNNING", "errorCodes": [],
        "excerpt": f"status={got.get('status')} natIP={nat_ip() or '(없음)'}"})
    step("F2.unreachable-after-delete", probe(ip, False, 120))
    # M2 — 복원: accessConfig 재부여(새 임시 IP) → 회복 관측은 새 주소로.
    step("M2.add-accessconfig", mutate(
        "POST", f"{inst}/addAccessConfig?networkInterface=nic0",
        {"type": "ONE_TO_ONE_NAT", "name": "External NAT"}, tok))
    ip2 = nat_ip()
    doc["ids"]["ip2"] = ip2
    save()
    step("M2b.new-nat-ip", {"ok": bool(ip2), "errorCodes": [],
                            "excerpt": f"{ip2} (이전 {ip})"})
    step("F3.reachable-again", probe(ip2, True, 300))

    step("T1.delete-vm", mutate("DELETE", inst, None, tok))
    gone = False
    deadline = time.time() + 300
    while time.time() < deadline:
        status, _ = call("GET", inst, None, tok)
        if status == 404:
            gone = True
            break
        time.sleep(15)
    step("T2.vm-gone", {"ok": gone, "errorCodes": [],
                        "excerpt": "404" if gone else "timeout"})
    _, ds = call("GET", f"{z}/disks", None, tok)
    residual = [d["name"] for d in ds.get("items", [])]
    step("T3.residual-disks", {"ok": not residual, "errorCodes": [],
                               "excerpt": json.dumps(residual)})
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
