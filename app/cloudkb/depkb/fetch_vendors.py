"""aws·gcp 벤더 스키마 원문 수집 — 해시 핀과 함께.

azure(`fetch_azure.py`)와 달리 이 둘은 커밋하지 않는다 — CFN 스펙이 16MB라
저장소 관례대로 gitignored 캐시(`.cache/cloudkb/`)에 두고, **핀(버전+SHA-256)만
커밋되는 산출물에 남긴다.**

재현성의 한계를 적어 둔다: 두 URL 모두 롤링("latest"·현재 리비전)이라 나중에
받으면 다른 판이 올 수 있다. 핀은 "그때 그 원문"의 동일성 검증까지만 보장하고,
재획득은 보관본에 의존한다 — 이것이 azure(커밋 SHA로 과거 획득 가능)와 다른
점이며, 산출물을 읽을 때 함께 가야 하는 사실이다.

실행: `python -m app.cloudkb.depkb.fetch_vendors`
"""

from __future__ import annotations

import gzip
import hashlib
import json
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parents[1] / ".cache" / "cloudkb"

#: 캐시 키 → (URL, 파일명, 기대 SHA-256). 해시가 다르면 다른 판이다.
SOURCES: dict[str, dict] = {
    "aws-cfn": {
        "url": ("https://d1uauaxba7bl26.cloudfront.net/258.0.0/gzip/"
                "CloudFormationResourceSpecification.json"),
        "file": "cfn-spec-v258.0.0.json",
        "version": "258.0.0",  # 파일 안 ResourceSpecificationVersion과 일치해야 한다
        "sha256": "cb04ddec8e3e2e87f06a628c1e31b1640f49492e9d86d8cb48d7c2ef527dae63",
    },
    "gcp-compute": {
        "url": "https://www.googleapis.com/discovery/v1/apis/compute/v1/rest",
        "file": "gcp-compute-20260722.json",
        "version": "20260722",  # 파일 안 revision과 일치해야 한다
        "sha256": "b71cb75cb68d790065cecb01363b0d714c6388304ae027c45108255b311a3203",
    },
}


def is_cached(key: str) -> bool:
    """고정 원천이 있고 그 내용이 선언된 해시와 일치하는지 확인한다."""
    src = SOURCES[key]
    path = CACHE / src["file"]
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == src["sha256"]


def load(key: str) -> dict:
    """캐시에서 읽되 핀을 먼저 검증한다 — 같은 이름의 다른 판을 막는다."""
    src = SOURCES[key]
    blob = (CACHE / src["file"]).read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    assert digest == src["sha256"], f"{key}: 캐시가 핀과 다르다 ({digest[:12]}…)"
    return json.loads(blob)


def fetch(key: str) -> None:
    """보관본이 없을 때의 재획득 — 롤링 URL이라 다른 판이 오면 명시적으로 죽는다."""
    src = SOURCES[key]
    raw = urllib.request.urlopen(src["url"], timeout=120).read()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    digest = hashlib.sha256(raw).hexdigest()
    if digest != src["sha256"]:
        raise RuntimeError(
            f"{key}: 원격이 다른 판을 준다({digest[:12]}…) — 롤링 URL의 한계. "
            f"핀을 갱신하려면 판 번호·해시·산출물 재추출을 한 커밋으로 묶어라"
        )
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / src["file"]).write_bytes(raw)


if __name__ == "__main__":
    for key in SOURCES:
        if not (CACHE / SOURCES[key]["file"]).exists():
            fetch(key)
        doc = load(key)
        stated = doc.get("ResourceSpecificationVersion") or doc.get("revision")
        print(f"{key}: ok (version {stated})")
        assert stated == SOURCES[key]["version"], f"{key}: 판 표기 불일치"
