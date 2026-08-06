"""기능 신호 라운드(azure) — DNS 해석 · 볼륨 I/O.

계획: `document/archive/functional-signals4-plan-2026-07-31.md`.
게스트 하네스는 `_guest.py`. **앱을 쓰지 않는다** — OS 기본 도구만.

- 셀 1 **DNS 해석**: 사설 DNS 영역을 vnet에 link → 게스트에서 이름 해석
  성공 → **link 삭제**(무방비) → 해석 상실 → link 재생성 → 회복.
  겨누는 간선: `globalDns→network`(연결이 기능을 나른다).
- 셀 2 **볼륨 I/O**: 데이터 디스크 attach → 마운트·쓰기 성공 → **detach**
  (무방비) → 쓰기 상실 → 재attach·재마운트 → 회복. 겨누는 간선: `vm→disk`.
  재마운트는 운영 절차이지 판정 대상이 아니다(회복의 전제).

아웃바운드 셀은 azure에서 뺐다 — 신규 서브넷의 defaultOutboundAccess가
기본 꺼짐이라(func2 관측) 그 자체가 교란이다.

국면: build → dns → disk → finish. 실행: `python run.py <phase> <rg>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _guest import probe, run  # noqa: E402
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
VM_SIZE = "Standard_B2s_v2"
IMAGE = "Canonical:ubuntu-24_04-lts:server:latest"
USER = "depkbadmin"
KEY = str(Path.home() / ".ssh" / "id_rsa")
ZONE = "depkb.internal"
NAME = f"api.{ZONE}"


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("기능 신호(azure) — DNS 해석·볼륨 I/O. 게스트 안에서 "
                      "OS 기본 도구로만 관측한다(앱 없음)."),
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

    def g(cmd, timeout=60):
        return run(ids["ip"], USER, KEY, cmd, timeout=timeout)

    if phase == "build":
        step("R1.create-vnet", az(
            ["network", "vnet", "create", "-g", rg, "-n", "depkb-s4-vnet",
             "--address-prefix", "10.110.0.0/16", "--subnet-name", "s",
             "--subnet-prefix", "10.110.0.0/24", "-o", "json"]))
        step("R2.create-nsg", az(
            ["network", "nsg", "create", "-g", rg, "-n", "depkb-s4-nsg",
             "-o", "json"]))
        step("R3.nsg-allow22", az(
            ["network", "nsg", "rule", "create", "-g", rg,
             "--nsg-name", "depkb-s4-nsg", "-n", "allow22", "--priority", "100",
             "--access", "Allow", "--protocol", "Tcp", "--direction", "Inbound",
             "--destination-port-ranges", "22", "-o", "json"]))
        step("R4.attach-nsg", az(
            ["network", "vnet", "subnet", "update", "-g", rg,
             "--vnet-name", "depkb-s4-vnet", "-n", "s",
             "--network-security-group", "depkb-s4-nsg", "-o", "json"]))
        step("R5.create-vm", az(
            ["vm", "create", "-g", rg, "-n", "depkb-s4-vm",
             "--image", IMAGE, "--size", VM_SIZE,
             "--vnet-name", "depkb-s4-vnet", "--subnet", "s",
             "--public-ip-sku", "Standard", "--admin-username", USER,
             "--ssh-key-values", KEY + ".pub", "-o", "json"], timeout=900))
        got = step("R6.public-ip", az(
            ["vm", "show", "-d", "-g", rg, "-n", "depkb-s4-vm",
             "--query", "publicIps", "-o", "tsv"]))
        ids["ip"] = got["excerpt"].strip()
        save(doc)
        # 기준선 — 게스트 명령이 도는지 먼저 본다(하네스 규율)
        step("R7.guest-baseline", probe(
            ids["ip"], USER, KEY, "echo depkb-ok", True, 420))
        return

    if phase == "dns":
        step("D1.create-private-zone", az(
            ["network", "private-dns", "zone", "create", "-g", rg, "-n", ZONE,
             "-o", "json"], timeout=600))
        step("D2.link-zone-to-vnet", az(
            ["network", "private-dns", "link", "vnet", "create", "-g", rg,
             "-n", "depkb-s4-link", "-z", ZONE, "-v", "depkb-s4-vnet",
             "-e", "false", "-o", "json"], timeout=600))
        step("D3.create-record", az(
            ["network", "private-dns", "record-set", "a", "add-record", "-g", rg,
             "-z", ZONE, "-n", "api", "-a", "10.110.0.99", "-o", "json"]))
        # F1 — 기능 확인: 게스트가 사설 이름을 푼다
        step("F1.resolve-works", probe(
            ids["ip"], USER, KEY, f"getent hosts {NAME}", True, 300))
        # M1 — 변이: link 삭제(성공 = 무방비)
        step("M1.delete-link", az(
            ["network", "private-dns", "link", "vnet", "delete", "-g", rg,
             "-n", "depkb-s4-link", "-z", ZONE, "--yes", "-o", "json"],
            timeout=600))
        step("F2.resolve-lost", probe(
            ids["ip"], USER, KEY, f"getent hosts {NAME}", False, 300, confirm=2))
        # M2 — 복원
        step("M2.relink", az(
            ["network", "private-dns", "link", "vnet", "create", "-g", rg,
             "-n", "depkb-s4-link", "-z", ZONE, "-v", "depkb-s4-vnet",
             "-e", "false", "-o", "json"], timeout=600))
        step("F3.resolve-again", probe(
            ids["ip"], USER, KEY, f"getent hosts {NAME}", True, 420))
        return

    if phase == "disk":
        step("K1.create-disk", az(
            ["disk", "create", "-g", rg, "-n", "depkb-s4-disk",
             "--size-gb", "4", "--sku", "Standard_LRS", "-o", "json"],
            timeout=600))
        step("K2.attach-disk", az(
            ["vm", "disk", "attach", "-g", rg, "--vm-name", "depkb-s4-vm",
             "-n", "depkb-s4-disk", "-o", "json"], timeout=600))
        time.sleep(20)
        # 게스트: 첫 빈 디스크를 찾아 포맷·마운트 (OS 기본 도구만)
        setup = ("sudo bash -c 'set -e; d=$(lsblk -rno NAME,TYPE,MOUNTPOINT | "
                 "awk \"\\$2==\\\"disk\\\" && \\$3==\\\"\\\" {print \\$1}\" | "
                 "tail -1); mkfs.ext4 -F /dev/$d >/dev/null 2>&1; "
                 "mkdir -p /mnt/depkb; mount /dev/$d /mnt/depkb; "
                 "echo mounted-$d'")
        step("K3.guest-format-mount", g(setup, timeout=180))
        write = "sudo dd if=/dev/zero of=/mnt/depkb/probe bs=1M count=1 2>&1 | tail -1"
        step("F1.write-works", probe(
            ids["ip"], USER, KEY, write, True, 180))
        # M1 — 변이: 실행 중 VM에서 디스크 detach(성공 = 무방비)
        step("M1.detach-while-running", az(
            ["vm", "disk", "detach", "-g", rg, "--vm-name", "depkb-s4-vm",
             "-n", "depkb-s4-disk", "-o", "json"], timeout=600))
        step("M1b.vm-still-running", az(
            ["vm", "get-instance-view", "-g", rg, "-n", "depkb-s4-vm",
             "--query", "instanceView.statuses[1].displayStatus", "-o", "tsv"]))
        step("F2.write-lost", probe(
            ids["ip"], USER, KEY, write, False, 300, confirm=2))
        # M2 — 복원: 재attach + 재마운트(운영 절차, 판정 대상 아님)
        step("M2.reattach", az(
            ["vm", "disk", "attach", "-g", rg, "--vm-name", "depkb-s4-vm",
             "-n", "depkb-s4-disk", "-o", "json"], timeout=600))
        time.sleep(20)
        remount = ("sudo bash -c 'umount -l /mnt/depkb 2>/dev/null; "
                   "d=$(lsblk -rno NAME,TYPE,MOUNTPOINT | awk \"\\$2==\\\"disk\\\" "
                   "&& \\$3==\\\"\\\" {print \\$1}\" | tail -1); "
                   "mount /dev/$d /mnt/depkb; echo remounted-$d'")
        step("M2b.guest-remount", g(remount, timeout=180))
        step("F3.write-again", probe(
            ids["ip"], USER, KEY, write, True, 300))
        return

    if phase == "finish":
        step("T1.delete-vm", az(["vm", "delete", "-g", rg, "-n", "depkb-s4-vm",
                                 "--yes"], timeout=900))
        for name in az(["disk", "list", "-g", rg, "--query", "[].name",
                        "-o", "json"])["excerpt"].strip("[]\n ").split(","):
            n = name.strip().strip('"')
            if n:
                step(f"T2.delete-disk-{n[:22]}", az(
                    ["disk", "delete", "-g", rg, "-n", n, "--yes"]))
        step("T3.delete-zone", az(
            ["network", "private-dns", "zone", "delete", "-g", rg, "-n", ZONE,
             "--yes", "-o", "json"], timeout=600))
        for kind, n in (("nic", "depkb-s4-vmVMNic"), ("public-ip", "depkb-s4-vmPublicIP")):
            step(f"T4.delete-{kind}", az(
                ["network", kind, "delete", "-g", rg, "-n", n]))
        step("T5.delete-vnet", az(["network", "vnet", "delete", "-g", rg,
                                   "-n", "depkb-s4-vnet"]))
        step("T6.delete-nsg", az(["network", "nsg", "delete", "-g", rg,
                                  "-n", "depkb-s4-nsg"]))
        step("T7.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
