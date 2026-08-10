"""gcp 쌍 호환 — 존 불일치(vm의 존 ↔ 디스크의 존).

가설: 다른 존의 디스크를 부착 참조한 인스턴스 생성은 거부된다. 대조군은
같은 존 조합의 성공(→ 실패 축이 존임을 격리). 인스턴스는 대조군에서 잠깐
생겼다 지워진다(부트 autoDelete) — 비용 분 단위.

측정 결과는 기존 gcp vm→disk 간선의 술어(쌍 호환)로 승격된다 — 대상이
어휘 안이라 claims에 실을 수 있다(aws arch와 다른 점).

실행: `python run.py <project> <region> <zoneA> <zoneB>`
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gcp-apply3-2026-07-31"))
from run import BASE, call, mutate, token  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> None:
    project, region, zone_a, zone_b = sys.argv[1:5]
    tok = token()
    za = f"{BASE}/projects/{project}/zones/{zone_a}"
    zb = f"{BASE}/projects/{project}/zones/{zone_b}"
    disk_url = f"{za}/disks/depkbg5-disk"
    steps: dict[str, dict] = {}

    def step(name, result):
        steps[name] = result
        print(f"{name:34} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")

    def vm(zone_url, name):
        return {"name": name,
                "machineType": f"{zone_url}/machineTypes/e2-micro",
                "disks": [
                    {"boot": True, "autoDelete": True, "initializeParams": {
                        "sourceImage": "projects/debian-cloud/global/images/"
                                       "family/debian-12"}},
                    {"boot": False, "source": disk_url},
                ],
                "networkInterfaces": [{"network":
                    f"{BASE}/projects/{project}/global/networks/default"}]}

    step("G.create-disk-zoneA", mutate("POST", f"{za}/disks", {
        "name": "depkbg5-disk", "sizeGb": "10"}, tok))
    # default 네트워크가 없는 프로젝트면 여기서 죽는다 — 앞 라운드에서 확인된
    # auto 모드 네트워크를 쓰는 것이 실험 격리에 낫지만, 이 프로젝트에는
    # default가 실재함을 1라운드 firewall-omit 실험이 이미 보였다.
    step("P1.zone-mismatch-vm-zoneB", mutate(
        "POST", f"{zb}/instances", vm(zb, "depkbg5-x1"), tok))
    p2 = mutate("POST", f"{za}/instances", vm(za, "depkbg5-vm"), tok)
    step("P2.same-zone-control", p2)
    if p2["ok"]:
        step("D.delete-vm", mutate(
            "DELETE", f"{za}/instances/depkbg5-vm", None, tok))
    step("D.delete-disk", mutate("DELETE", disk_url, None, tok))
    _, disks = call("GET", f"{za}/disks", None, tok)
    residual = [d["name"] for d in disks.get("items", [])
                if d["name"].startswith("depkbg5")]
    steps["residual"] = {"ok": not residual, "errorCodes": [],
                         "excerpt": json.dumps(residual)}
    print(f"{'residual':34} {residual}")

    (HERE / "results.json").write_text(json.dumps({
        "_note": ("쌍 호환(존) 측정 — P1 거부가 가설, P2 대조군. 인스턴스는 "
                  "대조군에서만 잠깐 존재."),
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": project, "zones": [zone_a, zone_b],
        "steps": steps,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
