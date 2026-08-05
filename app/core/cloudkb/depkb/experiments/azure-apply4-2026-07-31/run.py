"""azure 쌍 호환 — LB SKU ↔ PIP SKU 일치.

가설: Standard LB의 frontend에 Basic PIP를 걸면 거부된다. 단 **Basic PIP
자체가 퇴역 절차 중**이라 생성 시도부터가 측정이다 — 생성이 거부되면 이
호환 축은 신규 배포에서 소멸한 것이고, 그것도 답이다.

실행: `python run.py <resource-group>`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> None:
    rg = sys.argv[1]
    steps: dict[str, dict] = {}

    def step(name, result):
        steps[name] = result
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    basic = az(["network", "public-ip", "create", "-g", rg, "-n", "depkb4-bpip",
                "--sku", "Basic", "-o", "json"])
    step("P1.create-basic-pip", basic)
    if basic["ok"]:
        step("P2.standard-lb-with-basic-pip", az(
            ["network", "lb", "create", "-g", rg, "-n", "depkb4-lb",
             "--sku", "Standard", "--public-ip-address", "depkb4-bpip",
             "-o", "json"]))
        step("D.delete-lb", az(["network", "lb", "delete", "-g", rg,
                                "-n", "depkb4-lb"]))
        step("D.delete-pip", az(["network", "public-ip", "delete", "-g", rg,
                                 "-n", "depkb4-bpip"]))
    step("residual", az(["resource", "list", "-g", rg, "-o", "json"]))

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("쌍 호환(SKU) 측정 — Basic PIP 생성 가능 여부부터가 데이터"
                  "(퇴역 중). P2 거부가 가설."),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resourceGroup": rg, "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
