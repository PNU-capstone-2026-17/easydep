"""P5b 실행기 — apply 층의 반사실 실험.

세 국면이고, 비용 통제가 설계에 박혀 있다:

- **A(존재·허상)**: preflight가 못 본 다섯을 실제 배포로 묻는다. 전부 "실패해야
  성공"이라 자원이 생기지 않는다. VM 템플릿도 생성 전에 거부되는 것이 가설이므로
  VM 비용이 없다(만약 생성되면 teardown이 지운다).
- **B(사슬 구축) + C(생명주기 변이)**: 무료 자원만으로(vnet·subnet·NSG·NIC 둘)
  사슬을 만들고, **사용 중인 대상의 삭제를 시도**한다 — lifecycle 질문의 직접
  측정이다. nic1은 NSG 없이 만들어 "nic→firewall 선택"의 apply 증거를 겸한다.
- **D(정리 = 양성 대조)**: 역순 삭제가 전부 성공해야 한다. 실패해도 계속 진행해
  잔여물을 세고, 마지막에 RG 잔여 목록을 기록한다 — 빈 RG로 끝나는 것까지가 실험.

실행: `python run.py <resource-group>`
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFLIGHT = HERE.parent / "azure-preflight-2026-07-30" / "templates"
AZ = shutil.which("az")
_CODE = re.compile(r'"code":\s*"([^"]+)"|ErrorCode:\s*(\S+)|\(([A-Za-z]+Error|[A-Za-z]*NotFound|[A-Za-z]*InUse[A-Za-z]*)\)')


def az(args: list[str], timeout: int = 420) -> dict:
    r = subprocess.run([AZ, *args, "--only-show-errors"],
                       capture_output=True, text=True, timeout=timeout, check=False)
    text = (r.stderr or "") + (r.stdout or "")
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    return {"ok": r.returncode == 0,
            "errorCodes": list(dict.fromkeys(codes)),
            "excerpt": text.strip().replace("\r", "")[:600]}


def deploy(rg: str, template: Path, name: str) -> dict:
    return az(["deployment", "group", "create", "-g", rg, "-n", name,
               "--template-file", str(template), "-o", "json"])


def main() -> None:
    rg = sys.argv[1]
    steps: dict[str, dict] = {}

    def step(name: str, result: dict) -> None:
        steps[name] = result
        print(f"{name:32} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    # A — 존재·허상 (실패해야 성공)
    for t in ["omit-vm-nic", "dangling-nic-subnet", "dangling-subnet-parent",
              "dangling-vm-nic", "dangling-lb-pip"]:
        step(f"A.apply.{t}", deploy(rg, PREFLIGHT / f"{t}.json", f"depkb-{t}"))

    # B — 사슬 구축 (무료 자원만)
    step("B.build-chain", deploy(rg, HERE / "chain.json", "depkb-chain"))

    # C — 생명주기 변이: 사용 중인 대상의 삭제 시도
    step("C.delete-subnet-in-use", az(
        ["network", "vnet", "subnet", "delete", "-g", rg,
         "--vnet-name", "depkb-l-vnet", "-n", "s1"]))
    step("C.delete-vnet-in-use", az(
        ["network", "vnet", "delete", "-g", rg, "-n", "depkb-l-vnet"]))
    step("C.delete-nsg-attached", az(
        ["network", "nsg", "delete", "-g", rg, "-n", "depkb-l-nsg"]))

    # D — 역순 정리 (양성 대조)
    for name, args in [
        ("D.delete-nic1", ["network", "nic", "delete", "-g", rg, "-n", "depkb-l-nic1"]),
        ("D.delete-nic2", ["network", "nic", "delete", "-g", rg, "-n", "depkb-l-nic2"]),
        ("D.delete-nsg", ["network", "nsg", "delete", "-g", rg, "-n", "depkb-l-nsg"]),
        ("D.delete-subnet", ["network", "vnet", "subnet", "delete", "-g", rg,
                             "--vnet-name", "depkb-l-vnet", "-n", "s1"]),
        ("D.delete-vnet", ["network", "vnet", "delete", "-g", rg, "-n", "depkb-l-vnet"]),
    ]:
        step(name, az(args))

    step("residual", az(["resource", "list", "-g", rg, "-o", "json"]))

    (HERE / "results.json").write_text(json.dumps({
        "_note": (
            "azure apply 측정 기록(P5b). A국면은 실패가 성공이다(자원 무생성). "
            "C국면은 생명주기 질문의 직접 측정. residual이 빈 목록이어야 실험이 "
            "깨끗이 끝난 것이다."
        ),
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "resourceGroup": rg,
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
