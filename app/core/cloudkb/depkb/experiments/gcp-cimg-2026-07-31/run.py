"""customImage 라운드(gcp) — image 자원의 존재·생명주기.

계획: `document/archive/customimage-plan-2026-07-31.md`.
사다리: 원본 디스크 → 허상 원본 거부 → 실제 원본으로 이미지 생성(양성) →
**이미지 존재 중 원본 삭제 시도**(생명주기) → 그 이미지로 VM(사슬 완결)
→ 정리.

실행: `python run.py <project> <zone>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent
DEBIAN = "projects/debian-cloud/global/images/family/debian-12"


def main() -> None:
    project, zone = sys.argv[1], sys.argv[2]
    tok = token()
    g = f"{BASE}/projects/{project}/global"
    z = f"{BASE}/projects/{project}/zones/{zone}"
    src = f"{z}/disks/depkb-cimg-src"
    img = f"{g}/images/depkb-cimg"
    inst = f"{z}/instances/depkb-cimg-vm"
    doc = {"_note": ("customImage(gcp) — 존재(허상 원본 거부·양성)·생명주기"
                     "(이미지 존재 중 원본 삭제)·사슬 완결(그 이미지로 VM)."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = result
        save()
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    # R — 원본 디스크
    step("R1.create-source-disk", mutate(
        "POST", f"{z}/disks?sourceImage={DEBIAN}",
        {"name": "depkb-cimg-src", "sizeGb": "10"}, tok))
    # A1 — 허상 원본으로 이미지 생성(존재 판정의 음성)
    step("A1.dangling-source-disk", mutate(
        "POST", f"{g}/images",
        {"name": "depkb-cimg-bad",
         "sourceDisk": f"{z}/disks/depkb-no-such-disk"}, tok))
    # A2 — 실제 원본으로 생성(양성)
    step("A2.create-image-from-disk", mutate(
        "POST", f"{g}/images", {"name": "depkb-cimg", "sourceDisk": src}, tok))
    state = ""
    deadline = time.time() + 420
    while time.time() < deadline:
        _, got = call("GET", img, None, tok)
        state = got.get("status", "")
        print(f"image status: {state}", flush=True)
        if state in ("READY", "FAILED"):
            break
        time.sleep(15)
    step("A3.image-ready", {"ok": state == "READY", "errorCodes": [],
                            "excerpt": state})
    # L1 — 이미지 존재 중 원본 디스크 삭제 시도(생명주기)
    step("L1.delete-source-while-image-exists", mutate("DELETE", src, None, tok))
    _, got = call("GET", img, None, tok)
    step("L1b.image-after-source-delete", {
        "ok": True, "errorCodes": [],
        "excerpt": json.dumps({"status": got.get("status"),
                               "sourceDisk": got.get("sourceDisk", "")[-30:],
                               "archiveSizeBytes": got.get("archiveSizeBytes")},
                              ensure_ascii=False)})
    # C1 — 그 이미지로 VM(사슬 완결)
    step("C1.create-vm-from-custom-image", mutate(
        "POST", f"{z}/instances",
        {"name": "depkb-cimg-vm", "machineType": f"{z}/machineTypes/e2-small",
         "disks": [{"boot": True, "autoDelete": True,
                    "initializeParams": {"sourceImage": img,
                                         "diskSizeGb": "10"}}],
         "networkInterfaces": [{"network":
                                f"{BASE}/projects/{project}/global/networks/default"}]},
        tok))
    state = ""
    deadline = time.time() + 300
    while time.time() < deadline:
        _, got = call("GET", inst, None, tok)
        state = got.get("status", "")
        if state == "RUNNING":
            break
        time.sleep(10)
    step("C2.vm-running", {"ok": state == "RUNNING", "errorCodes": [],
                           "excerpt": state})
    # T — 정리
    step("T1.delete-vm", mutate("DELETE", inst, None, tok))
    gone = False
    deadline = time.time() + 300
    while time.time() < deadline:
        status, _ = call("GET", inst, None, tok)
        if status == 404:
            gone = True
            break
        time.sleep(15)
    step("T2.vm-gone", {"ok": gone, "errorCodes": [],
                        "excerpt": "404" if gone else "timeout"})
    step("T3.delete-image", mutate("DELETE", img, None, tok))
    step("T4.delete-source-if-left", mutate("DELETE", src, None, tok))
    _, imgs = call("GET", f"{g}/images", None, tok)
    _, disks = call("GET", f"{z}/disks", None, tok)
    residual = ([i["name"] for i in imgs.get("items", [])
                 if i["name"].startswith("depkb")]
                + [d["name"] for d in disks.get("items", [])
                   if d["name"].startswith("depkb")])
    step("T5.residual", {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)})
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
