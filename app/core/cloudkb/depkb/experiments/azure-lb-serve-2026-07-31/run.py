"""기능 신호 5(azure) — LB 백엔드 서빙: `loadBalancer→vm`.

계획: `document/archive/functional-signals56-plan-2026-07-31.md`.
**앱을 쓰지 않는다** — 백엔드 응답기는 `python3 -m http.server`(OS 기본).

이 간선은 **존재 판정에는 없다**. LB는 백엔드 없이도 만들어지므로(기존
실측) 존재로는 안 보이고 기능으로만 보인다 — 기능 축이 새 간선을 여는
첫 사례다.

사다리: 백엔드 풀에 VM + http.server → LB 공인 IP로 HTTP 200 → **풀에서
VM 제거**(무방비) → 서빙 상실 → 재등록 → 회복.

국면: build → serve → finish. 실행: `python run.py <phase> <rg>`
"""

import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _guest import run as guest  # noqa: E402
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
VM_SIZE = "Standard_B2s_v2"
IMAGE = "Canonical:ubuntu-24_04-lts:server:latest"
USER = "depkbadmin"
#: az vm create가 만드는 ip-config 이름(실측) — nic create의 ipconfig1과 다르다.
IPCFG = "ipconfigdepkb-lb-vm"
KEY = str(Path.home() / ".ssh" / "id_rsa")


def http_ok(ip: str, timeout: float = 6.0) -> bool:
    """LB 프론트엔드로 HTTP 요청 — 200이면 서빙 중."""
    try:
        with socket.create_connection((ip, 80), timeout=timeout) as s:
            s.sendall(b"GET / HTTP/1.0\r\nHost: depkb\r\n\r\n")
            head = s.recv(64)
        return b"200" in head
    except OSError:
        return False


def serve_probe(ip: str, want: bool, budget: int, confirm: int = 1) -> dict:
    deadline = time.time() + budget
    tries = streak = 0
    while time.time() < deadline:
        tries += 1
        got = http_ok(ip)
        streak = streak + 1 if got == want else 0
        print(f"lb http {ip} -> {got} (want {want}, streak {streak})", flush=True)
        if streak >= confirm:
            return {"ok": True, "errorCodes": [],
                    "excerpt": f"http200={got} (시도 {tries}, 연속 {streak})"}
        time.sleep(10)
    return {"ok": False, "errorCodes": ["PROBE_TIMEOUT"],
            "excerpt": f"{budget}초 내 http200={want}×{confirm} 미도달"}


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("기능 신호 5(azure) — LB 백엔드 서빙. 백엔드 응답기는 "
                      "python3 http.server(OS 기본, 앱 아님)."),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ids": {}, "steps": {}}


def save(doc) -> None:
    (HERE / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    phase, rg = sys.argv[1], sys.argv[2]
    doc = load()
    steps, ids = doc["steps"], doc["ids"]

    def step(name, result):
        steps[name] = result
        save(doc)
        print(f"{name:38} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    if phase == "build":
        step("R1.create-vnet", az(
            ["network", "vnet", "create", "-g", rg, "-n", "depkb-lb-vnet",
             "--address-prefix", "10.112.0.0/16", "--subnet-name", "s",
             "--subnet-prefix", "10.112.0.0/24", "-o", "json"]))
        step("R2.create-nsg", az(
            ["network", "nsg", "create", "-g", rg, "-n", "depkb-lb-nsg",
             "-o", "json"]))
        for prio, port in (("100", "22"), ("110", "80")):
            step(f"R3.nsg-allow{port}", az(
                ["network", "nsg", "rule", "create", "-g", rg,
                 "--nsg-name", "depkb-lb-nsg", "-n", f"allow{port}",
                 "--priority", prio, "--access", "Allow", "--protocol", "Tcp",
                 "--direction", "Inbound", "--destination-port-ranges", port,
                 "-o", "json"]))
        step("R4.attach-nsg", az(
            ["network", "vnet", "subnet", "update", "-g", rg,
             "--vnet-name", "depkb-lb-vnet", "-n", "s",
             "--network-security-group", "depkb-lb-nsg", "-o", "json"]))
        # LB — 프론트엔드 PIP + 백엔드 풀 + 프로브 + 규칙
        step("R5.create-lb-pip", az(
            ["network", "public-ip", "create", "-g", rg, "-n", "depkb-lb-pip",
             "--sku", "Standard", "-o", "json"]))
        step("R6.create-lb", az(
            ["network", "lb", "create", "-g", rg, "-n", "depkb-lb",
             "--sku", "Standard", "--public-ip-address", "depkb-lb-pip",
             "--frontend-ip-name", "fe", "--backend-pool-name", "bepool",
             "-o", "json"], timeout=600))
        step("R7.create-probe", az(
            ["network", "lb", "probe", "create", "-g", rg,
             "--lb-name", "depkb-lb", "-n", "p80", "--protocol", "Tcp",
             "--port", "80", "-o", "json"]))
        step("R8.create-rule", az(
            ["network", "lb", "rule", "create", "-g", rg,
             "--lb-name", "depkb-lb", "-n", "r80", "--protocol", "Tcp",
             "--frontend-port", "80", "--backend-port", "80",
             "--frontend-ip-name", "fe", "--backend-pool-name", "bepool",
             "--probe-name", "p80", "-o", "json"]))
        # VM — 관리 접속용 PIP를 따로 준다(LB 프론트엔드와 분리해야 신호가 갈린다)
        step("R9.create-vm", az(
            ["vm", "create", "-g", rg, "-n", "depkb-lb-vm",
             "--image", IMAGE, "--size", VM_SIZE,
             "--vnet-name", "depkb-lb-vnet", "--subnet", "s",
             "--public-ip-sku", "Standard", "--admin-username", USER,
             "--ssh-key-values", KEY + ".pub", "-o", "json"], timeout=900))
        got = step("R10.vm-public-ip", az(
            ["vm", "show", "-d", "-g", rg, "-n", "depkb-lb-vm",
             "--query", "publicIps", "-o", "tsv"]))
        ids["vmIp"] = got["excerpt"].strip()
        got = step("R11.lb-public-ip", az(
            ["network", "public-ip", "show", "-g", rg, "-n", "depkb-lb-pip",
             "--query", "ipAddress", "-o", "tsv"]))
        ids["lbIp"] = got["excerpt"].strip()
        save(doc)
        # 백엔드 응답기 — OS 기본 python3. 앱이 아니다.
        step("R12.start-http-server", guest(
            ids["vmIp"], USER, KEY,
            "sudo bash -c 'echo depkb-backend > /root/index.html; "
            "cd /root && (setsid nohup python3 -m http.server 80 "
            ">/dev/null 2>&1 &); sleep 2; ss -lnt | grep -c :80'",
            timeout=120))
        # NIC를 백엔드 풀에 넣는다
        step("R13.add-nic-to-pool", az(
            ["network", "nic", "ip-config", "address-pool", "add", "-g", rg,
             "--nic-name", "depkb-lb-vmVMNic", "--ip-config-name", IPCFG,
             "--lb-name", "depkb-lb", "--address-pool", "bepool",
             "-o", "json"], timeout=600))
        return

    if phase == "serve":
        step("F1.lb-serves", serve_probe(ids["lbIp"], True, 420))
        # M1 — 변이: 백엔드 풀에서 NIC 제거(성공 = 무방비)
        step("M1.remove-nic-from-pool", az(
            ["network", "nic", "ip-config", "address-pool", "remove", "-g", rg,
             "--nic-name", "depkb-lb-vmVMNic", "--ip-config-name", IPCFG,
             "--lb-name", "depkb-lb", "--address-pool", "bepool",
             "-o", "json"], timeout=600))
        step("M1b.lb-still-exists", az(
            ["network", "lb", "show", "-g", rg, "-n", "depkb-lb",
             "--query", "{name:name,rules:length(loadBalancingRules)}",
             "-o", "json"]))
        step("F2.serving-lost", serve_probe(ids["lbIp"], False, 420, confirm=2))
        step("M2.re-add-nic", az(
            ["network", "nic", "ip-config", "address-pool", "add", "-g", rg,
             "--nic-name", "depkb-lb-vmVMNic", "--ip-config-name", IPCFG,
             "--lb-name", "depkb-lb", "--address-pool", "bepool",
             "-o", "json"], timeout=600))
        step("F3.serving-again", serve_probe(ids["lbIp"], True, 600))
        return

    if phase == "finish":
        step("T1.delete-vm", az(["vm", "delete", "-g", rg, "-n", "depkb-lb-vm",
                                 "--yes"], timeout=900))
        for name in az(["disk", "list", "-g", rg, "--query", "[].name",
                        "-o", "json"])["excerpt"].strip("[]\n ").split(","):
            n = name.strip().strip('"')
            if n:
                step(f"T2.delete-disk-{n[:20]}", az(
                    ["disk", "delete", "-g", rg, "-n", n, "--yes"]))
        step("T3.delete-lb", az(["network", "lb", "delete", "-g", rg,
                                 "-n", "depkb-lb"]))
        for kind, n in (("nic", "depkb-lb-vmVMNic"),
                        ("public-ip", "depkb-lb-vmPublicIP"),
                        ("public-ip", "depkb-lb-pip")):
            step(f"T4.delete-{kind}-{n[-8:]}", az(
                ["network", kind, "delete", "-g", rg, "-n", n]))
        step("T5.delete-vnet", az(["network", "vnet", "delete", "-g", rg,
                                   "-n", "depkb-lb-vnet"]))
        step("T6.delete-nsg", az(["network", "nsg", "delete", "-g", rg,
                                  "-n", "depkb-lb-nsg"]))
        step("T7.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
