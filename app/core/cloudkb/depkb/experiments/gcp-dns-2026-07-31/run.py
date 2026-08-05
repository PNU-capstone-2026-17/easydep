"""globalDns 라운드(gcp) — managed zone과 record의 존재·생명주기.

계획: `document/archive/dns-filesystem-plan-2026-07-31.md`. 사설 영역만
쓴다(소유하지 않은 공인 이름 등록 금지) — 사설 한정이라는 한계는 판정
note에 적는다. Cloud DNS API는 compute와 다른 엔드포인트라 여기서 직접
호출한다(헬퍼의 call/token만 재사용).

셀: A1 zone 없이 record → A2 zone 생성 → A3 record 생성(양성) →
L1 record 존재 중 zone 삭제 → L2 record 삭제 후 zone 삭제(양성 대조).

실행: `python run.py <project>`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, token  # noqa: E402

HERE = Path(__file__).resolve().parent
DNS = "https://dns.googleapis.com/dns/v1"
ZONE, DOMAIN = "depkb-zone", "depkb.internal."


def main() -> None:
    project = sys.argv[1]
    tok = token()
    zones = f"{DNS}/projects/{project}/managedZones"
    doc = {"_note": ("globalDns(gcp) — 사설 managed zone·record. 사설 한정 "
                     "측정(공인 영역 미측정)."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def api(method, url, body=None):
        status, resp = call(method, url, body, tok)
        err = resp.get("error", {}) if isinstance(resp, dict) else {}
        codes = [e.get("reason", "") for e in err.get("errors", [])] or (
            [] if status < 400 else [str(status)])
        return {"ok": status < 400, "httpStatus": status,
                "errorCodes": [c for c in codes if c],
                "excerpt": json.dumps(resp, ensure_ascii=False)[:400]}

    def step(name, result):
        steps[name] = result
        save()
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    change = {"additions": [{"name": f"api.{DOMAIN}", "type": "A", "ttl": 300,
                             "rrdatas": ["10.0.0.10"]}]}
    # A1 — 없는 영역에 레코드(존재 판정의 음성)
    step("A1.record-without-zone", api(
        "POST", f"{zones}/{ZONE}/changes", change))
    # A2 — 사설 영역 생성
    step("A2.create-private-zone", api("POST", zones, {
        "name": ZONE, "dnsName": DOMAIN, "description": "depkb dep round",
        "visibility": "private",
        "privateVisibilityConfig": {
            "networks": [{"networkUrl":
                          f"{BASE}/projects/{project}/global/networks/default"}]}}))
    # A3 — 레코드 생성(양성)
    step("A3.create-record", api("POST", f"{zones}/{ZONE}/changes", change))
    # L1 — 레코드 있는 영역 삭제(생명주기)
    step("L1.delete-zone-with-record", api("DELETE", f"{zones}/{ZONE}"))
    # L2 — 레코드 삭제 후 영역 삭제(양성 대조)
    step("L2.delete-record", api("POST", f"{zones}/{ZONE}/changes", {
        "deletions": change["additions"]}))
    step("L3.delete-zone-after", api("DELETE", f"{zones}/{ZONE}"))
    step("T1.residual", api("GET", zones))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
