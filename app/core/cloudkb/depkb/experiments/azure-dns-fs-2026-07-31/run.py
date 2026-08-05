"""globalDns·fileSystem 라운드(azure) — private DNS zone·Storage/File Share.

계획: `document/archive/dns-filesystem-plan-2026-07-31.md`. 두 자원이
겹치지 않아 한 실행에서 잰다.

- DNS: 사설 영역만(공인 이름 미소유). 없는 영역에 레코드 → 영역 생성 →
  레코드 → 레코드 있는 영역 삭제(생명주기) → 정리.
- fileSystem: **합성 2라운드에서 CSI가 계정 합성을 시도하다 구독 정책
  (TLS)으로 실패했다.** 같은 정책이 사용자 직접 생성에도 걸리는지가 이번
  관측의 핵심이다 — 걸리면 그건 우리 구독의 성질이고, 안 걸리면 CSI 경로의
  성질이다(가름이 목적).

실행: `python run.py <rg>`
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure-apply2-2026-07-30"))
from run import az  # noqa: E402

HERE = Path(__file__).resolve().parent
ZONE = "depkb.internal"
# 스토리지 계정 이름은 전역 유일·소문자·숫자만. 고정값이라 재실행 시 충돌하면
# 그 자체가 기록된다(이름 규칙도 결속의 일부).
SA = "depkbfs20260731a"


def main() -> None:
    rg = sys.argv[1]
    doc = {"_note": ("globalDns·fileSystem(azure) — 사설 DNS 영역과 "
                     "스토리지 계정/파일 공유. CSI 실패가 구독 정책 탓인지 "
                     "사용자 직접 생성으로 가른다."),
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

    # ── globalDns ──
    step("D1.record-without-zone", az(
        ["network", "private-dns", "record-set", "a", "add-record", "-g", rg,
         "-z", ZONE, "-n", "api", "-a", "10.0.0.10", "-o", "json"]))
    step("D2.create-private-zone", az(
        ["network", "private-dns", "zone", "create", "-g", rg, "-n", ZONE,
         "-o", "json"], timeout=600))
    step("D3.create-record", az(
        ["network", "private-dns", "record-set", "a", "add-record", "-g", rg,
         "-z", ZONE, "-n", "api", "-a", "10.0.0.10", "-o", "json"]))
    step("D4.delete-zone-with-record", az(
        ["network", "private-dns", "zone", "delete", "-g", rg, "-n", ZONE,
         "--yes", "-o", "json"], timeout=600))
    step("D4b.zone-exists-after", az(
        ["network", "private-dns", "zone", "show", "-g", rg, "-n", ZONE,
         "--query", "name", "-o", "tsv"]))

    # ── fileSystem ──
    # 1차 실행의 교훈(results-round1.json): Microsoft.Storage RP가 미등록이면
    # **SubscriptionNotFound**가 난다 — 구독이 없다는 뜻이 아니라 RP 미등록
    # 이라는 오해를 부르는 문구다(환경 전제이지 판정 대상 아님).
    step("F0.storage-rp-state", az(
        ["provider", "show", "-n", "Microsoft.Storage",
         "--query", "registrationState", "-o", "tsv"]))
    step("F1.create-storage-account", az(
        ["storage", "account", "create", "-g", rg, "-n", SA,
         "--sku", "Standard_LRS", "--kind", "StorageV2", "-o", "json"],
        timeout=900))
    step("F2.create-file-share", az(
        ["storage", "share-rm", "create", "-g", rg,
         "--storage-account", SA, "-n", "depkbshare", "--quota", "100",
         "-o", "json"], timeout=600))
    step("F3.delete-account-with-share", az(
        ["storage", "account", "delete", "-g", rg, "-n", SA, "--yes",
         "-o", "json"], timeout=600))
    step("F3b.account-exists-after", az(
        ["storage", "account", "show", "-g", rg, "-n", SA,
         "--query", "name", "-o", "tsv"]))

    # ── 정리 ──
    step("T1.delete-zone-if-left", az(
        ["network", "private-dns", "zone", "delete", "-g", rg, "-n", ZONE,
         "--yes", "-o", "json"], timeout=600))
    step("T2.delete-account-if-left", az(
        ["storage", "account", "delete", "-g", rg, "-n", SA, "--yes",
         "-o", "json"], timeout=600))
    time.sleep(10)
    step("T3.residual", az(["resource", "list", "-g", rg, "-o", "json"]))
    doc["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save()


if __name__ == "__main__":
    main()
