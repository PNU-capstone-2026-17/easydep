"""azure 벤더 스키마 원문 수집 — 핀 박힌 커밋에서, 해시와 함께.

핀 없는 소스는 재현이 안 된다(graphkb `test_sources_are_pinned`와 같은 규율).
여기서는 저장소 HEAD가 아니라 **커밋 SHA**로 받는다 — 태그가 없는 저장소라서다.

수집 대상은 어휘(vocabulary.py)가 요구하는 최소 집합이다. 판(api-version)은
2026-07-30에 stable 디렉터리를 열거해 가장 최신을 골랐다 — Network는 2025-07-01,
Compute는 자원군별로 판이 갈린다(VM은 2026-03-01, Disk는 2026-03-02).

실행: `python -m app.cloudkb.depkb.fetch_azure`
산출: `cache/azure/*.json` + `cache/azure/manifest.json`(URL·SHA-256·크기·수집일)
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

REPO = "Azure/azure-rest-api-specs"
#: 2026-07-30 HEAD. 이 커밋이 곧 핀이다 — 바꾸면 재수집이고, manifest가 그걸 기록한다.
COMMIT = "478f542f0e4a8872a8c6e5cde5dd4e44a01bc120"

_NET = "specification/network/resource-manager/Microsoft.Network/Network/stable/2025-07-01"
_CMP = "specification/compute/resource-manager/Microsoft.Compute/Compute/stable"

#: 캐시 키 → 저장소 내 경로. 키가 인용(cite)의 첫 칸이 된다.
FILES: dict[str, str] = {
    "network-virtualNetwork": f"{_NET}/virtualNetwork.json",
    "network-common": f"{_NET}/common.json",
    "network-loadBalancer": f"{_NET}/loadBalancer.json",
    "compute-ComputeRP": f"{_CMP}/2026-03-01/ComputeRP.json",
    "compute-DiskRP": f"{_CMP}/2026-03-02/DiskRP.json",
}

CACHE = Path(__file__).resolve().parent / "cache" / "azure"


def fetch(fetched_on: str) -> dict:
    """전부 받고 manifest를 쓴다. 이미 있으면 해시만 다시 확인한다."""
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {
        "_pin": {"repo": REPO, "commit": COMMIT, "fetchedOn": fetched_on},
        "files": {},
    }
    for key, path in FILES.items():
        url = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{path}"
        target = CACHE / f"{key}.json"
        if not target.exists():
            with urllib.request.urlopen(url, timeout=60) as r:
                target.write_bytes(r.read())
        blob = target.read_bytes()
        manifest["files"][key] = {
            "path": path,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob),
        }
    (CACHE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    import sys

    m = fetch(fetched_on=sys.argv[1] if len(sys.argv) > 1 else "unspecified")
    for k, v in m["files"].items():
        print(f"{k}: {v['bytes']:,} bytes sha256={v['sha256'][:12]}…")
