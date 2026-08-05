"""선언 술어의 배타성 5차 — gcp `vm→image`를 **REST로 직접** 건다.

4차에서 `--log-http`가 답을 줬다: `gcloud`가 `--disk boot=yes`를 보면
`initializeParams`를 **아예 안 보낸다.** 그래서 3·4차의 "수락됨"은 API의 답이
아니라 **클라이언트가 걸러 준 결과**였다 — 우리 오라클 서열에 없다(위협 ①의
세 번째 사례다).

여기서는 Compute API에 **본문을 직접** 만들어 보낸다. 부트 디스크 하나에
`initializeParams.sourceImage`(새로 만들라)와 `source`(기존 것을 붙여라)를
**함께** 넣는다.

    거부되면   `OnlyOne` — 정확히 하나다
    수락되면   실물을 보고 **어느 쪽으로 떴는지** 확인한다. 한쪽이 무시되면
               그건 `Or`가 아니라 "한쪽이 조용히 사라진다"이고, 계획층에 주는
               뜻이 다르다(둘 다 적으면 하나는 헛일)

토큰은 `gcloud auth print-access-token`으로 얻는다 — 자격 증명을 새로 만들지
않는다. 거부되면 자원이 안 생기므로 무과금이고, 수락되면 즉시 지운다.

실행: `python run.py`
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZONE, REGION = "asia-northeast3-a", "asia-northeast3"
DISK, VM = "depkb-disj5-disk", "depkb-disj5-vm"
_CODE = re.compile(r'"reason":\s*"([^"]+)"|"message":\s*"([^"]{0,120})')


def gcloud(args, timeout=420):
    path = shutil.which("gcloud")
    r = subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout)
    text = (r.stderr or "") + (r.stdout or "")
    return {"ok": r.returncode == 0, "errorCodes": [],
            "excerpt": text.strip().replace("\r", "")[:400], "_out": r.stdout}


def main() -> None:
    import httpx

    doc = {"_note": ("배타성 5차 — gcp Compute API에 본문을 직접 보낸다. gcloud가 "
                     "initializeParams를 걸러서(4차 --log-http) 3·4차가 API의 "
                     "답이 아니었다."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save():
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def step(name, result):
        steps[name] = {k: v for k, v in result.items() if not k.startswith("_")}
        save()
        print(f"{name:30} "
              f"{'/'.join(result['errorCodes']) or ('OK' if result['ok'] else 'FAIL')}",
              flush=True)
        return result

    project = gcloud(["config", "get-value", "project"])["_out"].strip()
    token = gcloud(["auth", "print-access-token"])["_out"].strip()
    step("S0.context", {"ok": bool(project and token), "errorCodes": [],
                        "excerpt": f"project={project} token={'있음' if token else '없음'}"})

    step("S1.create-disk", gcloud(
        ["compute", "disks", "create", DISK, "--zone", ZONE, "--size", "10GB",
         "--image-family", "ubuntu-2404-lts-amd64", "--image-project",
         "ubuntu-os-cloud", "--quiet"]))

    base = f"https://compute.googleapis.com/compute/v1/projects/{project}"
    body = {
        "name": VM,
        "machineType": f"{base}/zones/{ZONE}/machineTypes/e2-micro",
        "disks": [{
            "boot": True, "autoDelete": False,
            # **둘 다 넣는다** — 이것이 이 라운드의 변수다.
            "source": f"{base}/zones/{ZONE}/disks/{DISK}",
            "initializeParams": {
                "sourceImage": "https://compute.googleapis.com/compute/v1/projects/"
                               "ubuntu-os-cloud/global/images/family/"
                               "ubuntu-2404-lts-amd64"},
        }],
        "networkInterfaces": [{"network": f"{base}/global/networks/default"}],
    }
    (HERE / "request-both.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")

    response = httpx.post(
        f"{base}/zones/{ZONE}/instances",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=body, timeout=120.0)
    text = response.text
    codes = [next(g for g in m.groups() if g) for m in _CODE.finditer(text)]
    step("D1.rest-both-source-and-initializeparams", {
        "ok": response.status_code < 300,
        "errorCodes": list(dict.fromkeys(codes)) or [str(response.status_code)],
        "excerpt": f"HTTP {response.status_code} " + text.strip()[:500]})

    if response.status_code < 300:
        # 수락됐다면 **어느 쪽으로 떴는지** 본다.
        import time
        time.sleep(20)
        step("D2.instance-shape", gcloud(
            ["compute", "instances", "describe", VM, "--zone", ZONE,
             "--format", "json(disks[].source,disks[].boot)", "--quiet"]))
        step("D3.delete-vm", gcloud(
            ["compute", "instances", "delete", VM, "--zone", ZONE, "--quiet"]))
    step("T1.delete-disk", gcloud(
        ["compute", "disks", "delete", DISK, "--zone", ZONE, "--quiet"]))
    step("T2.residual", gcloud(
        ["compute", "instances", "list", "--format", "value(name)", "--quiet"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
