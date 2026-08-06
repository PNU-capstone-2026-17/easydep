"""image 라운드(azure) — vm→image: 선언 술어(image ∨ 기존 OS 디스크) 가설.

계획: `document/archive/image-round-plan-2026-07-31.md`. 생략 셀은 CLI 기본값
주입을 피해 ARM 템플릿으로 넣는다(azure-apply2의 방식). 양성 대조(B0)와
attach 경로(B1)는 az vm 명령을 쓰되, 판정 대상은 성공/거부이지 기본값이
아니다. `--os-type`은 attach의 전제 인자이지 판정 대상이 아니다.

국면: neg(무자원·거부 셀) → pos(실 VM → 디스크 잔존 → attach VM) → finish.
실행: `python run.py <phase> <rg>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
VM_SIZE = "Standard_B2s_v2"
IMAGE = "Canonical:ubuntu-24_04-lts:server:latest"  # 양성 대조용 마켓플레이스 이미지


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("vm→image(azure) — 생략·허상 거부와 attach 경로 성공으로 "
                      "선언 술어(image ∨ 기존 OS 디스크)를 잰다."),
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
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    if phase == "neg":
        step("R.create-vnet", az(
            ["network", "vnet", "create", "-g", rg, "-n", "depkb-img-vnet",
             "--address-prefix", "10.99.0.0/16", "--subnet-name", "s",
             "--subnet-prefix", "10.99.0.0/24", "-o", "json"]))
        step("R.create-nic", az(
            ["network", "nic", "create", "-g", rg, "-n", "depkb-img-nic",
             "--vnet-name", "depkb-img-vnet", "--subnet", "s", "-o", "json"]))
        got = step("R.nic-id", az(
            ["network", "nic", "show", "-g", rg, "-n", "depkb-img-nic",
             "--query", "id", "-o", "tsv"]))
        ids["nicId"] = got["excerpt"].strip()
        save(doc)
        # A1 — 이미지도 기존 디스크도 없이: 합집합 필수의 반쪽
        step("A1.omit-image-and-disk", az(
            ["deployment", "group", "create", "-g", rg, "-n", "depkb-img-a1",
             "--template-file", str(HERE / "t-omit-image.json"),
             "--parameters", f"nicId={ids['nicId']}", "-o", "json"]))
        # A2 — 허상 이미지 id
        step("A2.dangling-image", az(
            ["deployment", "group", "create", "-g", rg, "-n", "depkb-img-a2",
             "--template-file", str(HERE / "t-dangling-image.json"),
             "--parameters", f"nicId={ids['nicId']}", "-o", "json"]))
        return

    if phase == "pos":
        # B0 — 마켓플레이스 이미지로 실 VM(양성 대조). 기존 NIC 재사용.
        step("B0.create-vm-from-image", az(
            ["vm", "create", "-g", rg, "-n", "depkb-img-vm",
             "--image", IMAGE, "--size", VM_SIZE,
             "--nics", "depkb-img-nic", "--admin-username", "depkbadmin",
             "--generate-ssh-keys", "-o", "json"], timeout=600))
        got = step("B0.os-disk-name", az(
            ["vm", "show", "-g", rg, "-n", "depkb-img-vm",
             "--query", "storageProfile.osDisk.name", "-o", "tsv"]))
        ids["osDisk"] = got["excerpt"].strip()
        save(doc)
        # VM 삭제 — OS 디스크 잔존은 기존 실측(azure-apply3)이고 여기선 재사용
        step("B0.delete-vm-keep-disk", az(
            ["vm", "delete", "-g", rg, "-n", "depkb-img-vm", "--yes"],
            timeout=600))
        step("B0.disk-survives", az(
            ["disk", "show", "-g", rg, "-n", ids["osDisk"],
             "--query", "{name:name,state:diskState}", "-o", "json"]))
        # B1 — 그 디스크를 attach: 이미지 없이 VM이 서는가(단독 선택의 반쪽)
        step("B1.create-vm-attach-disk-no-image", az(
            ["vm", "create", "-g", rg, "-n", "depkb-img-vm2",
             "--attach-os-disk", ids["osDisk"], "--os-type", "Linux",
             "--size", VM_SIZE, "--nics", "depkb-img-nic", "-o", "json"],
            timeout=600))
        step("B1.vm2-image-slot-empty", az(
            ["vm", "show", "-g", rg, "-n", "depkb-img-vm2",
             "--query", "storageProfile.imageReference", "-o", "json"]))
        return

    if phase == "finish":
        step("F1.delete-vm2", az(
            ["vm", "delete", "-g", rg, "-n", "depkb-img-vm2", "--yes"],
            timeout=600))
        step("F2.delete-disk", az(
            ["disk", "delete", "-g", rg, "-n", ids["osDisk"], "--yes"]))
        step("F3.delete-nic", az(
            ["network", "nic", "delete", "-g", rg, "-n", "depkb-img-nic"]))
        step("F4.delete-vnet", az(
            ["network", "vnet", "delete", "-g", rg, "-n", "depkb-img-vnet"]))
        step("F5.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
