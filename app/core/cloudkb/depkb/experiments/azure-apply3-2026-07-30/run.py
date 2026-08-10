"""P5b 3라운드 — azure 마지막 unknown(vm→disk)과 디스크 생명주기.

측정 대상:

- **F0** `vm→disk` 존재: 데이터 디스크 없이 VM을 만든다(선언한 디스크는 별도
  자원 depkb3-data뿐, VM의 dataDisks는 빈 채). 성공하면 optional의 apply 증거.
  **덤: OS 디스크의 서버측 합성** — 우리가 선언하지 않은 디스크가 RG에 생기는
  것을 F0 직후 디스크 목록으로 관측한다(클라우드측 암묵의 실물).
- **F1/C1/F2** `vm→disk` 생명주기: 붙인 디스크의 삭제 시도 → 거부 코드 측정.
- **D** VM 삭제 후 **OS 디스크가 남는지** 관측한다 — CB azure 드라이버가
  cleanVMRelatedResource에서 디스크를 직접 지우던 이유의 검증이다. 이어
  역순 정리·잔여 확인.

비용: B2ats_v2 VM 수 분 + 소형 디스크 — 수십 원 규모 상한. 실행:
`python run.py <resource-group>`
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
AZ = shutil.which("az")
_CODE = re.compile(r'"code":\s*"([^"]+)"|\(([A-Za-z]+[A-Za-z0-9]*)\)')


def az(args: list[str], timeout: int = 600) -> dict:
    r = subprocess.run([AZ, *args, "--only-show-errors"],
                       capture_output=True, text=True, timeout=timeout, check=False)
    text = (r.stderr or "") + (r.stdout or "")
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    return {"ok": r.returncode == 0,
            "errorCodes": list(dict.fromkeys(codes)),
            "excerpt": text.strip().replace("\r", "")[:600]}


def main() -> None:
    rg = sys.argv[1]
    steps: dict[str, dict] = {}

    def step(name: str, result: dict) -> None:
        steps[name] = result
        print(f"{name:26} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    step("F0.build-vm-no-datadisk", az(
        ["deployment", "group", "create", "-g", rg, "-n", "depkb-chain3",
         "--template-file", str(HERE / "chain3.json"), "-o", "json"], timeout=900))
    step("F0.disks-after-create", az(
        ["disk", "list", "-g", rg, "--query", "[].name", "-o", "json"]))

    step("F1.attach-data-disk", az(
        ["vm", "disk", "attach", "-g", rg, "--vm-name", "depkb3-vm",
         "--name", "depkb3-data"]))
    step("C1.delete-disk-attached", az(
        ["disk", "delete", "-g", rg, "-n", "depkb3-data", "--yes"]))
    step("F2.detach-data-disk", az(
        ["vm", "disk", "detach", "-g", rg, "--vm-name", "depkb3-vm",
         "--name", "depkb3-data"]))

    step("D.delete-vm", az(["vm", "delete", "-g", rg, "-n", "depkb3-vm", "--yes"]))
    step("D.disks-after-vm-delete", az(
        ["disk", "list", "-g", rg, "--query", "[].name", "-o", "json"]))
    step("D.delete-data-disk", az(
        ["disk", "delete", "-g", rg, "-n", "depkb3-data", "--yes"]))
    # OS 디스크 이름은 서버가 지었으므로 남은 디스크를 전부 지운다
    left = az(["disk", "list", "-g", rg, "--query", "[].name", "-o", "json"])
    for name in (json.loads(left["excerpt"]) if left["ok"] else []):
        step(f"D.delete-os-disk.{name[:28]}", az(
            ["disk", "delete", "-g", rg, "-n", name, "--yes"]))
    step("D.delete-nic", az(["network", "nic", "delete", "-g", rg, "-n", "depkb3-nic"]))
    step("D.delete-vnet", az(["network", "vnet", "delete", "-g", rg, "-n", "depkb3-vnet"]))
    step("residual", az(["resource", "list", "-g", rg, "-o", "json"]))

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("azure apply 3라운드 측정 기록 — vm→disk 존재·생명주기와 "
                  "OS 디스크의 서버측 합성·잔존 관측."),
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "resourceGroup": rg,
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
