"""기능 의존 2라운드(azure) — subnet→firewall(NSG) 도달성 결속.

계획: `document/archive/functional-dependency2-plan-2026-07-31.md`.
변이 = 서브넷에서 NSG 분리/재부착. 1라운드 덤 관측(Standard PIP
secure-by-default)의 정식 승격 — 차단은 NSG 부재 + secure-by-default의
**합성 효과**다(술어에 명시). 라우팅 셀은 azure에 대응 자원이 없어(시스템
라우트) 이 파일에 없다 — 부재 자체가 기록이다.

실행: `python run.py <rg>`
"""

import json
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
VM_SIZE = "Standard_B2s_v2"
IMAGE = "Canonical:ubuntu-24_04-lts:server:latest"


def tcp_ok(ip: str) -> bool:
    try:
        with socket.create_connection((ip, 22), timeout=5):
            return True
    except OSError:
        return False


def probe(ip: str, want: bool, budget: int, confirm: int = 1) -> dict:
    """want 상태가 connect 시도 `confirm`회 연속이 될 때까지 재시도."""
    deadline = time.time() + budget
    tries = streak = 0
    while time.time() < deadline:
        tries += 1
        got = tcp_ok(ip)
        streak = streak + 1 if got == want else 0
        print(f"tcp {ip}:22 -> {got} (want {want}, streak {streak})", flush=True)
        if streak >= confirm:
            return {"ok": True, "errorCodes": [],
                    "excerpt": f"tcp22={got} (시도 {tries}, 연속 {streak})"}
        time.sleep(10)
    return {"ok": False, "errorCodes": ["PROBE_TIMEOUT"],
            "excerpt": f"tcp22가 {budget}초 내 {want}×{confirm}에 도달 못 함"}


def main() -> None:
    rg = sys.argv[1]
    doc = {"_note": ("기능 의존 2라운드(azure) — subnet→NSG 분리/재부착. "
                     "차단은 NSG 부재+Standard PIP secure-by-default의 합성."),
           "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
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
        ["network", "vnet", "create", "-g", rg, "-n", "depkb-f2-vnet",
         "--address-prefix", "10.101.0.0/16", "--subnet-name", "s",
         "--subnet-prefix", "10.101.0.0/24", "-o", "json"]))
    step("R2.create-nsg-allow22", az(
        ["network", "nsg", "create", "-g", rg, "-n", "depkb-f2-nsg",
         "-o", "json"]))
    step("R3.nsg-rule-22", az(
        ["network", "nsg", "rule", "create", "-g", rg,
         "--nsg-name", "depkb-f2-nsg", "-n", "allow22", "--priority", "100",
         "--access", "Allow", "--protocol", "Tcp", "--direction", "Inbound",
         "--destination-port-ranges", "22", "-o", "json"]))
    step("R4.attach-nsg-to-subnet", az(
        ["network", "vnet", "subnet", "update", "-g", rg,
         "--vnet-name", "depkb-f2-vnet", "-n", "s",
         "--network-security-group", "depkb-f2-nsg", "-o", "json"]))
    step("R5.create-pip", az(
        ["network", "public-ip", "create", "-g", rg, "-n", "depkb-f2-pip",
         "--sku", "Standard", "-o", "json"]))
    step("R6.create-nic", az(
        ["network", "nic", "create", "-g", rg, "-n", "depkb-f2-nic",
         "--vnet-name", "depkb-f2-vnet", "--subnet", "s",
         "--public-ip-address", "depkb-f2-pip", "-o", "json"]))
    step("R7.create-vm", az(
        ["vm", "create", "-g", rg, "-n", "depkb-f2-vm",
         "--image", IMAGE, "--size", VM_SIZE, "--nics", "depkb-f2-nic",
         "--admin-username", "depkbadmin", "--generate-ssh-keys",
         "-o", "json"], timeout=600))
    ip = az(["network", "public-ip", "show", "-g", rg, "-n", "depkb-f2-pip",
             "--query", "ipAddress", "-o", "tsv"])["excerpt"].strip()
    doc["ids"]["ip"] = ip
    save()

    step("F1.reachable-baseline", probe(ip, True, 300))
    # M1 — 변이: 사용 중 서브넷에서 NSG 분리. 성공 = 무방비.
    # 1차 실행의 교훈(results-round1.json): --network-security-group ""는
    # CLI가 빈 참조로 해석 못 해 InvalidResourceReference — generic --remove가
    # 분리 경로다.
    step("M1.detach-nsg-from-subnet", az(
        ["network", "vnet", "subnet", "update", "-g", rg,
         "--vnet-name", "depkb-f2-vnet", "-n", "s",
         "--remove", "networkSecurityGroup", "-o", "json"]))
    step("F2.unreachable-after-detach", probe(ip, False, 180, confirm=2))
    step("M2.reattach-nsg", az(
        ["network", "vnet", "subnet", "update", "-g", rg,
         "--vnet-name", "depkb-f2-vnet", "-n", "s",
         "--network-security-group", "depkb-f2-nsg", "-o", "json"]))
    step("F3.reachable-again", probe(ip, True, 300))

    step("T1.delete-vm", az(["vm", "delete", "-g", rg, "-n", "depkb-f2-vm",
                             "--yes"], timeout=600))
    disk = az(["disk", "list", "-g", rg,
               "--query", "[?starts_with(name,'depkb-f2-vm')].name | [0]",
               "-o", "tsv"])["excerpt"].strip()
    if disk:
        step("T2.delete-osdisk", az(["disk", "delete", "-g", rg, "-n", disk,
                                     "--yes"]))
    step("T3.delete-nic", az(["network", "nic", "delete", "-g", rg,
                              "-n", "depkb-f2-nic"]))
    step("T4.delete-pip", az(["network", "public-ip", "delete", "-g", rg,
                              "-n", "depkb-f2-pip"]))
    step("T5.delete-vnet", az(["network", "vnet", "delete", "-g", rg,
                               "-n", "depkb-f2-vnet"]))
    step("T6.delete-nsg", az(["network", "nsg", "delete", "-g", rg,
                              "-n", "depkb-f2-nsg"]))
    step("T7.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
    doc["finishedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
