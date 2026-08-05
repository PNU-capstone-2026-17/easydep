"""기능 의존 첫 실험(azure) — nic→publicIp: 도달성 결속.

계획: `document/archive/functional-dependency-plan-2026-07-31.md`.
인과 사다리: 기능 확인(F1) → 변이 성공=무방비(M1) → 기능 상실(F2) →
복원(M2) → 기능 회복(F3). 기능 신호는 로컬에서의 TCP 22 접속이다 —
VM 안에 들어가지 않는다.

실행: `python run.py <rg>` (단일 국면 — 내부에서 대기)
"""

import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
VM_SIZE = "Standard_B2s_v2"
IMAGE = "Canonical:ubuntu-24_04-lts:server:latest"


def tcp_ok(ip: str, port: int = 22, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(ip: str, want: bool, budget: int) -> dict:
    """want 상태가 될 때까지 재시도 — 시한 내 도달 못 하면 사실대로."""
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
    rg = sys.argv[1]
    doc = {"_note": ("기능 의존(azure) — nic→publicIp. 기능 신호 = 로컬 TCP 22. "
                     "회복(F3)까지 봐야 인과가 선다."),
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

    step("R1.create-vnet", az(
        ["network", "vnet", "create", "-g", rg, "-n", "depkb-func-vnet",
         "--address-prefix", "10.100.0.0/16", "--subnet-name", "s",
         "--subnet-prefix", "10.100.0.0/24", "-o", "json"]))
    step("R2.create-pip", az(
        ["network", "public-ip", "create", "-g", rg, "-n", "depkb-func-pip",
         "--sku", "Standard", "-o", "json"]))
    step("R3.create-nic", az(
        ["network", "nic", "create", "-g", rg, "-n", "depkb-func-nic",
         "--vnet-name", "depkb-func-vnet", "--subnet", "s",
         "--public-ip-address", "depkb-func-pip", "-o", "json"]))
    # NSG 없는 NIC — azure 층 필터 없음(기존 실측 경로). ubuntu sshd가 리스너.
    step("R4.create-vm", az(
        ["vm", "create", "-g", rg, "-n", "depkb-func-vm",
         "--image", IMAGE, "--size", VM_SIZE, "--nics", "depkb-func-nic",
         "--admin-username", "depkbadmin", "--generate-ssh-keys",
         "-o", "json"], timeout=600))
    got = step("R5.pip-address", az(
        ["network", "public-ip", "show", "-g", rg, "-n", "depkb-func-pip",
         "--query", "ipAddress", "-o", "tsv"]))
    ip = got["excerpt"].strip()
    doc["ids"]["ip"] = ip
    save()

    step("F1.reachable-baseline", probe(ip, True, 300))
    # M1 — 변이: 실행 중 VM의 NIC에서 PIP 제거. 성공 자체가 무방비의 관측.
    step("M1.detach-pip-while-running", az(
        ["network", "nic", "ip-config", "update", "-g", rg,
         "--nic-name", "depkb-func-nic", "-n", "ipconfig1",
         "--remove", "publicIPAddress", "-o", "json"]))
    step("M1b.vm-still-running", az(
        ["vm", "get-instance-view", "-g", rg, "-n", "depkb-func-vm",
         "--query", "instanceView.statuses[1].displayStatus", "-o", "tsv"]))
    step("F2.unreachable-after-detach", probe(ip, False, 120))
    # M2 — 복원: 같은 PIP 재부착 → 회복까지 봐야 상실이 변이 탓임이 선다.
    step("M2.reattach-pip", az(
        ["network", "nic", "ip-config", "update", "-g", rg,
         "--nic-name", "depkb-func-nic", "-n", "ipconfig1",
         "--public-ip-address", "depkb-func-pip", "-o", "json"]))
    step("F3.reachable-again", probe(ip, True, 300))

    # 정리
    step("T1.delete-vm", az(["vm", "delete", "-g", rg, "-n", "depkb-func-vm",
                             "--yes"], timeout=600))
    disk = az(["disk", "list", "-g", rg,
               "--query", "[?starts_with(name,'depkb-func-vm')].name | [0]",
               "-o", "tsv"])["excerpt"].strip()
    if disk:
        step("T2.delete-osdisk", az(["disk", "delete", "-g", rg, "-n", disk,
                                     "--yes"]))
    step("T3.delete-nic", az(["network", "nic", "delete", "-g", rg,
                              "-n", "depkb-func-nic"]))
    step("T4.delete-pip", az(["network", "public-ip", "delete", "-g", rg,
                              "-n", "depkb-func-pip"]))
    step("T5.delete-vnet", az(["network", "vnet", "delete", "-g", rg,
                               "-n", "depkb-func-vnet"]))
    step("T6.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
