"""customImage 라운드(azure) — 관리 이미지의 존재·생명주기.

계획: `document/archive/customimage-plan-2026-07-31.md`. azure는 이미지를
**VM(일반화 없이 --hyper-v-generation 지정)** 또는 OS 디스크에서 만든다.
여기서는 디스크 원본 경로를 쓴다 — customImage→disk 간선을 겨누기 때문.
graphkb의 node→customImage 생명주기 관측이 이 라운드의 대조 대상이다.

사다리: VM → OS 디스크 확보(VM 삭제, 디스크 잔존은 기실측) → 허상 원본
거부 → 실제 디스크로 이미지(양성) → **이미지 존재 중 원본 디스크 삭제**
(생명주기) → 그 이미지로 VM(사슬 완결) → 정리.

실행: `python run.py <phase> <rg>` — phase: build | probe | finish
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
IMAGE = "Canonical:ubuntu-24_04-lts:server:latest"


def load() -> dict:
    p = HERE / "results.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"_note": ("customImage(azure) — 디스크 원본 경로. 이미지 존재 중 "
                      "원본 삭제가 막히는지가 생명주기 셀."),
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
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    if phase == "build":
        step("R1.create-vnet", az(
            ["network", "vnet", "create", "-g", rg, "-n", "depkb-ci-vnet",
             "--address-prefix", "10.103.0.0/16", "--subnet-name", "s",
             "--subnet-prefix", "10.103.0.0/24", "-o", "json"]))
        step("R2.create-vm", az(
            ["vm", "create", "-g", rg, "-n", "depkb-ci-vm",
             "--image", IMAGE, "--size", VM_SIZE,
             "--vnet-name", "depkb-ci-vnet", "--subnet", "s",
             "--public-ip-address", "", "--admin-username", "depkbadmin",
             "--generate-ssh-keys", "-o", "json"], timeout=600))
        got = step("R3.os-disk-name", az(
            ["vm", "show", "-g", rg, "-n", "depkb-ci-vm",
             "--query", "storageProfile.osDisk.name", "-o", "tsv"]))
        ids["osDisk"] = got["excerpt"].strip()
        save(doc)
        # 이미지 원본은 분리된 디스크여야 한다(VM 삭제, 디스크 잔존은 기실측)
        step("R4.delete-vm-keep-disk", az(
            ["vm", "delete", "-g", rg, "-n", "depkb-ci-vm", "--yes"],
            timeout=600))
        got = step("R5.disk-id", az(
            ["disk", "show", "-g", rg, "-n", ids["osDisk"],
             "--query", "id", "-o", "tsv"]))
        ids["diskId"] = got["excerpt"].strip()
        save(doc)
        return

    if phase == "probe":
        sub = ids["diskId"].split("/resourceGroups/")[0]
        bad = f"{sub}/resourceGroups/{rg}/providers/Microsoft.Compute/disks/depkb-no-such-disk"
        # A1 — 허상 원본(존재 판정의 음성)
        step("A1.dangling-source-disk", az(
            ["image", "create", "-g", rg, "-n", "depkb-ci-bad",
             "--source", bad, "--os-type", "Linux", "-o", "json"]))
        # A2 — 실제 원본(양성). 1차 실행의 교훈(results-round1.json):
        # --hyper-v-generation 기본값 V1이 소스 디스크(V2)와 불일치해
        # InvalidParameter — 세대는 원본을 따라야 한다(전제이지 판정 아님).
        step("A2.create-image-from-disk", az(
            ["image", "create", "-g", rg, "-n", "depkb-ci-img",
             "--source", ids["diskId"], "--os-type", "Linux",
             "--hyper-v-generation", "V2", "-o", "json"], timeout=600))
        # L1 — 이미지 존재 중 원본 디스크 삭제(생명주기)
        step("L1.delete-source-while-image-exists", az(
            ["disk", "delete", "-g", rg, "-n", ids["osDisk"], "--yes"]))
        step("L1b.disk-exists-after", az(
            ["disk", "show", "-g", rg, "-n", ids["osDisk"],
             "--query", "{name:name,state:diskState}", "-o", "json"]))
        # C1 — 그 이미지로 VM(사슬 완결)
        step("C1.create-vm-from-custom-image", az(
            ["vm", "create", "-g", rg, "-n", "depkb-ci-vm2",
             "--image", "depkb-ci-img", "--size", VM_SIZE,
             "--vnet-name", "depkb-ci-vnet", "--subnet", "s",
             "--public-ip-address", "", "--admin-username", "depkbadmin",
             "--generate-ssh-keys", "-o", "json"], timeout=900))
        return

    if phase == "finish":
        step("T1.delete-vm2", az(["vm", "delete", "-g", rg, "-n",
                                  "depkb-ci-vm2", "--yes"], timeout=600))
        step("T2.delete-image", az(["image", "delete", "-g", rg,
                                    "-n", "depkb-ci-img"]))
        for d in az(["disk", "list", "-g", rg, "--query",
                     "[].name", "-o", "json"])["excerpt"].strip("[]\n ").split(","):
            name = d.strip().strip('"')
            if name:
                step(f"T3.delete-disk-{name[:24]}", az(
                    ["disk", "delete", "-g", rg, "-n", name, "--yes"]))
        step("T4.delete-nic-vnet", az(
            ["network", "vnet", "delete", "-g", rg, "-n", "depkb-ci-vnet"]))
        step("T5.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
        doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(doc)
        return


if __name__ == "__main__":
    main()
