"""P5b 2라운드 — azure 잔여 unknown을 닫는다.

측정 대상:

- **E1** `network→subnet` 존재: 서브넷 없는 VNet을 실제로 만든다(1라운드는
  preflight 통과까지만이라 증거가 아니었다). 성공하면 optional의 apply 증거.
- **E0** 사슬2: 한 배포가 세 판정을 겸한다 — 내부 LB(frontend에 subnet만)가
  성공하면 `lb→publicIp` optional, 공용 LB(PIP만)가 성공하면 `lb→subnet`
  optional(선언 술어의 분해), PIP 붙은 NIC은 C를 위한 준비.
- **C** `nic→publicIp` 생명주기: 붙어 있는 PIP 삭제 시도 → 거부 코드 측정.
- **D** 역순 정리 + 잔여 확인. Standard PIP·LB가 분 단위로 존재하므로 비용은
  수십 원 규모 상한 — 실측으로 대체한다.

실행: `python run.py <resource-group>`
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFLIGHT = HERE.parent / "azure-preflight-2026-07-30" / "templates"
AZ = shutil.which("az")
_CODE = re.compile(r'"code":\s*"([^"]+)"|\(([A-Za-z]+[A-Za-z0-9]*)\)')


def az(args: list[str], timeout: int = 420) -> dict:
    r = subprocess.run([AZ, *args, "--only-show-errors"],
                       capture_output=True, text=True, timeout=timeout)
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
        print(f"{name:28} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    step("E1.apply-vnet-without-subnet", az(
        ["deployment", "group", "create", "-g", rg, "-n", "depkb-e1",
         "--template-file", str(PREFLIGHT / "omit-vnet-subnet.json"), "-o", "json"]))
    step("E1.cleanup-vnet", az(
        ["network", "vnet", "delete", "-g", rg, "-n", "depkb-vnet-nosub"]))

    step("E0.build-chain2", az(
        ["deployment", "group", "create", "-g", rg, "-n", "depkb-chain2",
         "--template-file", str(HERE / "chain2.json"), "-o", "json"]))

    step("C.delete-pip-attached", az(
        ["network", "public-ip", "delete", "-g", rg, "-n", "depkb2-pip1"]))

    for name, args in [
        ("D.delete-lbi", ["network", "lb", "delete", "-g", rg, "-n", "depkb2-lbi"]),
        ("D.delete-lbp", ["network", "lb", "delete", "-g", rg, "-n", "depkb2-lbp"]),
        ("D.delete-nic", ["network", "nic", "delete", "-g", rg, "-n", "depkb2-nic"]),
        ("D.delete-pip1", ["network", "public-ip", "delete", "-g", rg, "-n", "depkb2-pip1"]),
        ("D.delete-pip2", ["network", "public-ip", "delete", "-g", rg, "-n", "depkb2-pip2"]),
        ("D.delete-vnet", ["network", "vnet", "delete", "-g", rg, "-n", "depkb2-vnet"]),
    ]:
        step(name, az(args))

    step("residual", az(["resource", "list", "-g", rg, "-o", "json"]))

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("azure apply 2라운드 측정 기록. E0의 성공 하나가 lb 선언 "
                  "술어의 분해(개별 optional) 증거를 겸한다. C는 nic→publicIp "
                  "생명주기의 직접 측정."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resourceGroup": rg,
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
