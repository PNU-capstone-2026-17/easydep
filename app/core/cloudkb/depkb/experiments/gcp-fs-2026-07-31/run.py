"""fileSystem 라운드(gcp) — Filestore의 존재 의존, **거부 층까지만**.

계획: `document/archive/dns-filesystem-plan-2026-07-31.md`. Filestore는
최소 티어가 1TiB급이라 **실생성하지 않는다** — 비용 규율이고, 그래서 이
CSP만 판정 깊이가 얕다는 사실을 판정 note에 그대로 적는다("gcp는 안
된다"가 아니라 "우리가 거기까지만 쟀다").

셀: A1 network 생략 → A2 허상 network. 둘 다 거부 코드가 오라클이다.

실행: `python run.py <project> <zone>`
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import call, token  # noqa: E402

HERE = Path(__file__).resolve().parent
FILE_API = "https://file.googleapis.com/v1"


def main() -> None:
    project, zone = sys.argv[1], sys.argv[2]
    tok = token()
    parent = f"{FILE_API}/projects/{project}/locations/{zone}/instances"
    doc = {"_note": ("fileSystem(gcp) — Filestore 거부 층만(최소 티어 1TiB "
                     "비용 규율). 판정 깊이가 얕다는 사실을 함께 적는다."),
           "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "steps": {}}
    steps = doc["steps"]

    def save() -> None:
        (HERE / "results.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    def api(method, url, body=None):
        status, resp = call(method, url, body, tok)
        err = resp.get("error", {}) if isinstance(resp, dict) else {}
        codes = [e.get("reason", "") for e in err.get("errors", [])]
        if err.get("status"):
            codes.append(err["status"])
        if not codes and status >= 400:
            codes = [str(status)]
        return {"ok": status < 400, "httpStatus": status,
                "errorCodes": [c for c in codes if c],
                "excerpt": json.dumps(resp, ensure_ascii=False)[:400]}

    def step(name, result):
        steps[name] = result
        save()
        print(f"{name:36} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}", flush=True)
        return result

    body_base = {"tier": "BASIC_HDD",
                 "fileShares": [{"name": "depkbshare", "capacityGb": "1024"}]}
    # A1 — networks 생략
    step("A1.omit-network", api(
        "POST", f"{parent}?instanceId=depkb-fs", dict(body_base)))
    # A2 — 허상 network
    step("A2.dangling-network", api(
        "POST", f"{parent}?instanceId=depkb-fs",
        dict(body_base, networks=[{"network": "depkb-no-such-network",
                                   "modes": ["MODE_IPV4"]}])))
    step("T1.residual", api("GET", parent))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
