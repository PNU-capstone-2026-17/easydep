"""image 라운드(gcp) — vm→image: 선언 술어(sourceImage ∨ 기존 디스크) 가설.

계획: `document/archive/image-round-plan-2026-07-31.md`. REST 직접 호출
(gcloud CLI 기본값 주입 배제 — 기존 설계 결정 그대로). default 네트워크 사용
(잔여는 인스턴스·디스크뿐이고 명시 삭제).

셀: G1 생략 거부 → G2 허상 거부 → G3 이미지→디스크→그 디스크로 인스턴스
(sourceImage 없이) 성공 → 정리. 부트 디스크 autoDelete=false 기본(기존 실측)
이라 디스크는 명시 삭제한다.

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
    z = f"{BASE}/projects/{project}/zones/{zone}"
    mt = f"{z}/machineTypes/e2-small"
    doc = {"_note": ("vm→image(gcp) — 생략·허상 거부와 기존 디스크 부팅 성공으로 "
                     "선언 술어를 잰다. REST 직접(CLI 기본값 배제)."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = result
        save()
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    def instance(name, disk_spec):
        return {
            "name": name, "machineType": mt,
            "disks": [dict(boot=True, **disk_spec)],
            "networkInterfaces": [{"network":
                                   f"{BASE}/projects/{project}/global/networks/default"}],
        }

    # G1 — sourceImage도 source도 없는 부트 디스크
    step("G1.omit-image-and-source", mutate(
        "POST", f"{z}/instances",
        instance("depkb-img-omit", {"initializeParams": {"diskSizeGb": "10"}}),
        tok))
    # G2 — 허상 sourceImage
    step("G2.dangling-image", mutate(
        "POST", f"{z}/instances",
        instance("depkb-img-dangling",
                 {"initializeParams": {
                     "sourceImage":
                     f"projects/{project}/global/images/depkb-no-such-image"}}),
        tok))
    # G3a — 이미지에서 독립 디스크 생성(전제 준비)
    step("G3a.disk-from-image", mutate(
        "POST", f"{z}/disks?sourceImage={DEBIAN}",
        {"name": "depkb-img-disk", "sizeGb": "10"}, tok))
    # G3b — 그 디스크로, sourceImage 없이 인스턴스 생성(단독 선택의 반쪽)
    step("G3b.boot-from-existing-disk-no-image", mutate(
        "POST", f"{z}/instances",
        instance("depkb-img-vm", {"source": f"{z}/disks/depkb-img-disk"}),
        tok))
    _, got = call("GET", f"{z}/instances/depkb-img-vm", None, tok)
    step("G3c.instance-shape", {
        "ok": got.get("status") in ("RUNNING", "PROVISIONING", "STAGING"),
        "errorCodes": [], "excerpt": json.dumps(
            {"status": got.get("status"),
             "bootDisk": (got.get("disks") or [{}])[0].get("source", "")[-40:]},
            ensure_ascii=False)})
    # 정리 — 부트 디스크 autoDelete=false 기본(기존 실측)이라 명시 삭제
    step("F1.delete-instance", mutate(
        "DELETE", f"{z}/instances/depkb-img-vm", None, tok))
    deadline = time.time() + 240
    gone = False
    while time.time() < deadline:
        status, _ = call("GET", f"{z}/instances/depkb-img-vm", None, tok)
        if status == 404:
            gone = True
            break
        time.sleep(15)
    step("F2.instance-gone", {"ok": gone, "errorCodes": [],
                              "excerpt": "404" if gone else "timeout"})
    step("F3.delete-disk", mutate(
        "DELETE", f"{z}/disks/depkb-img-disk", None, tok))
    _, ds = call("GET", f"{z}/disks", None, tok)
    residual = [d["name"] for d in ds.get("items", [])]
    step("F4.residual-disks", {"ok": not residual, "errorCodes": [],
                               "excerpt": json.dumps(residual)})
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
