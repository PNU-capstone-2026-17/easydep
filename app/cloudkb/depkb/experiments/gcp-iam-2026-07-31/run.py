"""iamRole 라운드(gcp) — vm→iamRole(서비스 계정): 생략 시 실물 관측.

계획: `document/archive/iamrole-plan-2026-07-31.md`. 단일 셀:
serviceAccounts를 생략하고 인스턴스를 만들면 서버가 기본 compute SA를
붙이는가(server-default) 아니면 SA 없이 서는가(단순 optional) — GET의
`serviceAccounts` 실물이 판정한다. default 네트워크, 즉시 삭제.

실행: `python run.py <project> <zone>`
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent
DEBIAN = "projects/debian-cloud/global/images/family/debian-12"


def main() -> None:
    project, zone = sys.argv[1], sys.argv[2]
    tok = token()
    z = f"{BASE}/projects/{project}/zones/{zone}"
    inst = f"{z}/instances/depkb-iam-vm"
    doc = {"_note": ("vm→iamRole(gcp) — SA 생략 생성 후 serviceAccounts "
                     "실물 관측. 실물이 판정한다."),
           "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
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

    step("A1.create-vm-omit-sa", mutate(
        "POST", f"{z}/instances",
        {"name": "depkb-iam-vm", "machineType": f"{z}/machineTypes/e2-small",
         "disks": [{"boot": True, "autoDelete": True,
                    "initializeParams": {"sourceImage": DEBIAN,
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
    step("A2.running", {"ok": state == "RUNNING", "errorCodes": [],
                        "excerpt": state})
    _, got = call("GET", inst, None, tok)
    sas = got.get("serviceAccounts")
    step("A3.serviceaccounts-shape", {
        "ok": True, "errorCodes": [],
        "excerpt": json.dumps({"serviceAccounts": sas}, ensure_ascii=False)[:400]})
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
    doc["finishedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
