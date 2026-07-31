"""볼륨 I/O 셀 재측정 — 페이지 캐시를 우회한다.

1차 측정에서 디스크를 detach했는데도 `dd` 쓰기가 계속 성공했다(F2 상실
미관측). 원인은 **페이지 캐시** — 1MB 쓰기가 캐시에만 들어가고 디스크에
안 닿았다. DNS 셀의 TTL·resolved 캐시와 같은 부류이고, 이 라운드에서 두
번째다. `oflag=direct`로 캐시를 건너뛴다.

실행: `python redo_disk.py <rg>`
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from _guest import probe, run as guest  # noqa: E402
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
USER = "depkbadmin"
KEY = str(Path.home() / ".ssh" / "id_rsa")
#: 캐시를 건너뛰는 쓰기. 4k·1블록이면 충분하다 — 재려는 것은 처리량이 아니라
#: "디스크에 닿는가"다.
DIRECT = ("sudo dd if=/dev/zero of=/mnt/depkb/probe bs=4k count=1 "
          "oflag=direct 2>&1 | tail -1")
#: 재부착 후 파일시스템을 다시 붙인다(운영 절차 — 판정 대상이 아니다).
REMOUNT = (
    "sudo bash -c 'umount -l /mnt/depkb 2>/dev/null; "
    "d=$(lsblk -rno NAME,TYPE,MOUNTPOINT | "
    'awk "\\$2==\\"disk\\" && \\$3==\\"\\" {print \\$1}" | tail -1); '
    "mount /dev/$d /mnt/depkb && echo remounted-$d'"
)


def main() -> None:
    rg = sys.argv[1]
    path = HERE / "results.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    steps, ip = doc["steps"], doc["ids"]["ip"]

    def step(name, result):
        steps[name] = result
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"{name:38} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    step("K5.remount-check", guest(ip, USER, KEY, REMOUNT, timeout=180))
    step("F1c.write-direct-works", probe(ip, USER, KEY, DIRECT, True, 240))
    step("M1c.detach-again", az(
        ["vm", "disk", "detach", "-g", rg, "--vm-name", "depkb-s4-vm",
         "-n", "depkb-s4-disk", "-o", "json"], timeout=600))
    step("F2c.write-direct-lost", probe(
        ip, USER, KEY, DIRECT, False, 300, confirm=2))
    step("M2c.reattach-again", az(
        ["vm", "disk", "attach", "-g", rg, "--vm-name", "depkb-s4-vm",
         "-n", "depkb-s4-disk", "-o", "json"], timeout=600))
    time.sleep(20)
    step("M2d.remount", guest(ip, USER, KEY, REMOUNT, timeout=180))
    step("F3c.write-direct-again", probe(ip, USER, KEY, DIRECT, True, 300))


if __name__ == "__main__":
    main()
