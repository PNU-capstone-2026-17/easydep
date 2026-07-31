"""기능 신호 5 재시도(azure) — LB 백엔드 서빙: `loadBalancer→vm`.

1차(`azure-lb-serve-2026-07-31`)는 **기준선을 못 세워 미판정**이었다. 그때
기록한 다음 시도의 조건을 그대로 따른다:

- **인스턴스 PIP를 주지 않는다.** 관리 접근은 LB의 인바운드 NAT 규칙
  (2222→22)으로 뺀다 — 1차에서 의심한 비대칭 라우팅(응답이 인스턴스 PIP로
  나가는 것)이 원천 배제되고, PIP 없이도 게스트를 볼 수 있다. azure 권장
  구성이기도 하다.
- 프로브가 붙는지 **백엔드 헬스를 게스트 쪽에서 교차 확인**한다(프로브
  요청이 오면 http.server 접근 로그에 남는다).

이 간선은 존재 판정에 없다 — LB는 백엔드 없이도 만들어지므로 기능으로만
보인다. 기능 축이 새 간선을 여는 첫 사례가 될 자리다.

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
# _guest는 포트 지정을 안 해 여기선 직접 ssh를 부른다(아래 _ssh).  # noqa
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
VM_SIZE = "Standard_B2s_v2"
IMAGE = "Canonical:ubuntu-24_04-lts:server:latest"
USER = "depkbadmin"
KEY = str(Path.home() / ".ssh" / "id_rsa")
#: az vm create가 만드는 ip-config 이름(1차 실측 — nic create의 ipconfig1과 다르다)
IPCFG = "ipconfigdepkb-lb2-vm"
SSH_PORT = 2222


def http_ok(ip: str, port: int = 80, timeout: float = 6.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.sendall(b"GET / HTTP/1.0\r\nHost: depkb\r\n\r\n")
            return b"200" in s.recv(64)
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
    return {"_note": ("기능 신호 5 재시도(azure) — 인스턴스 PIP 없이 LB "
                      "인바운드 NAT(2222→22)로 관리. 1차 미판정의 조건 반영."),
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

    def _ssh(cmd, timeout=120):
        """관리 접근 — LB PIP의 NAT 포트로 들어간다(인스턴스 PIP 없음).

        `_guest.run`은 포트를 받지 않으므로 여기서 직접 부른다 — 하네스를
        고치는 대신 이 실험의 특수 사정으로 두었다.
        """
        import shutil
        import subprocess
        ssh = shutil.which("ssh")
        p = subprocess.run(
            [ssh, "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
             "-o", "LogLevel=ERROR", "-o", "BatchMode=yes",
             "-p", str(SSH_PORT), "-i", KEY, f"{USER}@{ids['lbIp']}", cmd],
            capture_output=True, text=True, timeout=timeout)
        text = (p.stdout or "") + (p.stderr or "")
        return {"ok": p.returncode == 0,
                "errorCodes": [] if p.returncode == 0 else
                (["SSH_UNREACHABLE"] if p.returncode == 255 else [f"EXIT_{p.returncode}"]),
                "excerpt": text.strip().replace("\r", "")[:500]}

    if phase == "build":
        step("R1.create-vnet", az(
            ["network", "vnet", "create", "-g", rg, "-n", "depkb-lb2-vnet",
             "--address-prefix", "10.113.0.0/16", "--subnet-name", "s",
             "--subnet-prefix", "10.113.0.0/24", "-o", "json"]))
        step("R2.create-nsg", az(
            ["network", "nsg", "create", "-g", rg, "-n", "depkb-lb2-nsg",
             "-o", "json"]))
        for prio, port in (("100", "22"), ("110", "80")):
            step(f"R3.nsg-allow{port}", az(
                ["network", "nsg", "rule", "create", "-g", rg,
                 "--nsg-name", "depkb-lb2-nsg", "-n", f"allow{port}",
                 "--priority", prio, "--access", "Allow", "--protocol", "Tcp",
                 "--direction", "Inbound", "--destination-port-ranges", port,
                 "-o", "json"]))
        step("R4.attach-nsg", az(
            ["network", "vnet", "subnet", "update", "-g", rg,
             "--vnet-name", "depkb-lb2-vnet", "-n", "s",
             "--network-security-group", "depkb-lb2-nsg", "-o", "json"]))
        step("R5.create-lb-pip", az(
            ["network", "public-ip", "create", "-g", rg, "-n", "depkb-lb2-pip",
             "--sku", "Standard", "-o", "json"]))
        step("R6.create-lb", az(
            ["network", "lb", "create", "-g", rg, "-n", "depkb-lb2",
             "--sku", "Standard", "--public-ip-address", "depkb-lb2-pip",
             "--frontend-ip-name", "fe", "--backend-pool-name", "bepool",
             "-o", "json"], timeout=600))
        step("R7.create-probe", az(
            ["network", "lb", "probe", "create", "-g", rg,
             "--lb-name", "depkb-lb2", "-n", "p80", "--protocol", "Tcp",
             "--port", "80", "-o", "json"]))
        step("R8.create-rule", az(
            ["network", "lb", "rule", "create", "-g", rg,
             "--lb-name", "depkb-lb2", "-n", "r80", "--protocol", "Tcp",
             "--frontend-port", "80", "--backend-port", "80",
             "--frontend-ip-name", "fe", "--backend-pool-name", "bepool",
             "--probe-name", "p80", "-o", "json"]))
        # 관리 접근 — 인스턴스 PIP 대신 LB 인바운드 NAT(2222→22)
        step("R9.create-nat-rule", az(
            ["network", "lb", "inbound-nat-rule", "create", "-g", rg,
             "--lb-name", "depkb-lb2", "-n", "ssh", "--protocol", "Tcp",
             "--frontend-port", str(SSH_PORT), "--backend-port", "22",
             "--frontend-ip-name", "fe", "-o", "json"]))
        # **인스턴스 PIP를 주지 않는다** — 1차 미판정의 핵심 조건
        step("R10.create-vm-no-pip", az(
            ["vm", "create", "-g", rg, "-n", "depkb-lb2-vm",
             "--image", IMAGE, "--size", VM_SIZE,
             "--vnet-name", "depkb-lb2-vnet", "--subnet", "s",
             "--public-ip-address", "", "--admin-username", USER,
             "--ssh-key-values", KEY + ".pub", "-o", "json"], timeout=900))
        got = step("R11.lb-public-ip", az(
            ["network", "public-ip", "show", "-g", rg, "-n", "depkb-lb2-pip",
             "--query", "ipAddress", "-o", "tsv"]))
        ids["lbIp"] = got["excerpt"].strip()
        save(doc)
        step("R12.add-nic-to-pool", az(
            ["network", "nic", "ip-config", "address-pool", "add", "-g", rg,
             "--nic-name", "depkb-lb2-vmVMNic", "--ip-config-name", IPCFG,
             "--lb-name", "depkb-lb2", "--address-pool", "bepool",
             "-o", "json"], timeout=600))
        step("R13.attach-nat-rule", az(
            ["network", "nic", "ip-config", "inbound-nat-rule", "add", "-g", rg,
             "--nic-name", "depkb-lb2-vmVMNic", "--ip-config-name", IPCFG,
             "--lb-name", "depkb-lb2", "--inbound-nat-rule", "ssh",
             "-o", "json"], timeout=600))
        # 관리 접근이 NAT로 되는지 먼저 확인(기준선의 기준선)
        deadline = time.time() + 420
        got = {"ok": False, "errorCodes": ["SSH_UNREACHABLE"], "excerpt": ""}
        while time.time() < deadline:
            got = _ssh("echo depkb-ok", 60)
            print(f"nat ssh: {got['ok']}", flush=True)
            if got["ok"]:
                break
            time.sleep(15)
        step("R14.guest-via-nat", got)
        step("R15.start-http-server", _ssh(
            "sudo bash -c 'echo depkb-backend > /root/index.html; "
            "cd /root && (setsid nohup python3 -m http.server 80 "
            ">/var/log/depkb-http.log 2>&1 &); sleep 2; ss -lnt | grep -c :80'",
            120))
        return

    if phase == "serve":
        step("F1.lb-serves", serve_probe(ids["lbIp"], True, 420))
        # 프로브가 실제로 오는지 교차 확인 — 접근 로그에 남는다
        step("F1b.probe-hits-in-log", _ssh(
            "sudo tail -3 /var/log/depkb-http.log", 60))
        step("M1.remove-nic-from-pool", az(
            ["network", "nic", "ip-config", "address-pool", "remove", "-g", rg,
             "--nic-name", "depkb-lb2-vmVMNic", "--ip-config-name", IPCFG,
             "--lb-name", "depkb-lb2", "--address-pool", "bepool",
             "-o", "json"], timeout=600))
        step("M1b.lb-still-exists", az(
            ["network", "lb", "show", "-g", rg, "-n", "depkb-lb2",
             "--query", "{name:name,rules:length(loadBalancingRules)}",
             "-o", "json"]))
        step("F2.serving-lost", serve_probe(ids["lbIp"], False, 420, confirm=2))
        step("M2.re-add-nic", az(
            ["network", "nic", "ip-config", "address-pool", "add", "-g", rg,
             "--nic-name", "depkb-lb2-vmVMNic", "--ip-config-name", IPCFG,
             "--lb-name", "depkb-lb2", "--address-pool", "bepool",
             "-o", "json"], timeout=600))
        step("F3.serving-again", serve_probe(ids["lbIp"], True, 600))
        return

    if phase == "finish":
        step("T1.delete-vm", az(["vm", "delete", "-g", rg, "-n",
                                 "depkb-lb2-vm", "--yes"], timeout=900))
        for name in az(["disk", "list", "-g", rg, "--query", "[].name",
                        "-o", "json"])["excerpt"].strip("[]\n ").split(","):
            n = name.strip().strip('"')
            if n:
                step(f"T2.delete-disk-{n[:20]}", az(
                    ["disk", "delete", "-g", rg, "-n", n, "--yes"]))
        step("T3.delete-lb", az(["network", "lb", "delete", "-g", rg,
                                 "-n", "depkb-lb2"]))
        step("T4.delete-nic", az(["network", "nic", "delete", "-g", rg,
                                  "-n", "depkb-lb2-vmVMNic"]))
        step("T5.delete-pip", az(["network", "public-ip", "delete", "-g", rg,
                                  "-n", "depkb-lb2-pip"]))
        step("T6.delete-vnet", az(["network", "vnet", "delete", "-g", rg,
                                   "-n", "depkb-lb2-vnet"]))
        step("T7.delete-nsg", az(["network", "nsg", "delete", "-g", rg,
                                  "-n", "depkb-lb2-nsg"]))
        # **az vm create가 NIC에 몰래 붙이는 NSG** — 우리가 만든 것이 아니라
        # 정리 목록에서 빠지기 쉽다(1차·2차 모두 잔여물로 남았다). 이 NSG가
        # 이번 라운드 미판정의 원인이기도 했다(Z1).
        step("T7b.delete-vm-nsg", az(["network", "nsg", "delete", "-g", rg,
                                      "-n", "depkb-lb2-vmNSG"]))
        step("T8.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
