"""선언 술어의 배타성(azure) — `Or`인가 `OnlyOne`인가.

## 왜 이 라운드가 생겼나

술어 분류를 IDL(RESTest, ICSOC'20)에 정박하다가 나온 질문이다. IDL은
`Or`(**적어도 하나**)와 `OnlyOne`(**정확히 하나**)을 가르는데, 우리
`disjunctive:` 3건은 그 구별을 잰 적이 없다 — 셋 중 하나면 된다는 것만 알고
**둘을 함께 주면 어떻게 되는지**는 안 걸어 봤다. 형식주의를 채택하자 미측정
칸이 드러난 것이고, 그때까지는 더 약한 `Or`로 적어 두었다.

여기서 재는 것은 `azure loadBalancer→subnet|publicIp|publicIPPrefix` 하나다.

## 무엇이 판정인가

한 프런트엔드 IP 구성에 **subnet과 publicIp를 동시에** 준다.

    거부되면   `OnlyOne` — 정확히 하나다
    수락되면   `Or`     — 적어도 하나이고 겹쳐도 된다

**둘 다 판정이다.** 그리고 대조군을 함께 둔다: 같은 회차에서 하나씩만 준 것이
성공하는지 본다(D2·D3) — 실패가 "둘이라서"가 아니라 다른 이유일 가능성을
배제하려는 것이다.

## 비용

Standard LB는 시간당 과금이 있으나 라운드가 수 분이다. 생성이 거부되면 과금
자체가 없다. 정리는 리소스 그룹 삭제로 끝낸다.

실행: `python run.py`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
RG = "depkb-disj"
LOC = "koreacentral"
VNET, SUBNET = "depkb-disj-vnet", "depkb-disj-subnet"
PIP = "depkb-disj-pip"


def main() -> None:
    doc = {"_note": ("선언 술어의 배타성(azure loadBalancer 프런트엔드) — "
                     "IDL의 Or와 OnlyOne을 가르는 실측. 정박이 낸 질문이다."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if k != "_data"}
        save()
        codes = "/".join(result["errorCodes"]) or ("OK" if result["ok"] else "FAIL")
        print(f"{name:34} {codes}", flush=True)
        return result

    # ── 준비 ──────────────────────────────────────────────────────────────
    step("S1.create-rg", az(["group", "create", "-n", RG, "-l", LOC]))
    step("S2.create-vnet", az(
        ["network", "vnet", "create", "-g", RG, "-n", VNET,
         "--address-prefix", "10.90.0.0/16",
         "--subnet-name", SUBNET, "--subnet-prefix", "10.90.1.0/24"]))
    step("S3.create-pip", az(
        ["network", "public-ip", "create", "-g", RG, "-n", PIP,
         "--sku", "Standard", "--allocation-method", "Static"]))

    # ── D1: 본 판정 — 둘을 함께 준다 ────────────────────────────────────────
    step("D1.frontend-both-subnet-and-pip", az(
        ["network", "lb", "create", "-g", RG, "-n", "depkb-lb-both",
         "--sku", "Standard", "--frontend-ip-name", "fe",
         "--vnet-name", VNET, "--subnet", SUBNET,
         "--public-ip-address", PIP]))

    # ── 대조군: 하나씩만 주면 서는가 ────────────────────────────────────────
    step("D2.frontend-subnet-only", az(
        ["network", "lb", "create", "-g", RG, "-n", "depkb-lb-subnet",
         "--sku", "Standard", "--frontend-ip-name", "fe",
         "--vnet-name", VNET, "--subnet", SUBNET]))
    step("D3.frontend-pip-only", az(
        ["network", "lb", "create", "-g", RG, "-n", "depkb-lb-pip",
         "--sku", "Standard", "--frontend-ip-name", "fe",
         "--public-ip-address", PIP]))

    # D1이 수락됐다면 **실물이 무엇을 들고 있는지** 본다 — 하나를 조용히
    # 버렸을 수 있고, 그러면 "둘 다 받았다"가 아니다.
    if steps.get("D1.frontend-both-subnet-and-pip", {}).get("ok"):
        step("D4.both-lb-shape", az(
            ["network", "lb", "frontend-ip", "show", "-g", RG,
             "--lb-name", "depkb-lb-both", "-n", "fe",
             "--query", "{subnet:subnet.id, pip:publicIPAddress.id}",
             "-o", "json"]))

    # ── 정리 ──────────────────────────────────────────────────────────────
    step("T1.delete-rg", az(["group", "delete", "-n", RG, "--yes", "--no-wait"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
