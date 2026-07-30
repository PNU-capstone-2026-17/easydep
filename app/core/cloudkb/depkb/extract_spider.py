"""cb-spider 드라이버에서 **네이티브 생성/삭제 호출 색인**을 뽑는다 — 기계 층.

중립화 적용 지도는 두 층이다:

- **기계 층(이 파일)**: 드라이버 핸들러 안의 SDK 생성/삭제 호출 전부 —
  (CSP, 핸들러, 둘러싼 함수, 줄, 수신자, 호출, 연산). 재계산 가능하고, 판단이
  들어가지 않는다. 소스는 태그 핀 타르볼(`v0.12.37`)이고 SHA-256을 산출물에 적는다.
- **판정 층(`neutralization_map.json`의 `judgments`)**: 기제 분류(합성·절단·치환·
  값 인라인·서버측 암묵)는 **우리 구성**이다. 모든 판정은 기계 층의 호출 실물을
  인용해야 하고, 그 정합을 `test_depkb_spider.py`가 강제한다.

한계를 적어 둔다: 이 색인은 **호출의 존재**를 말할 뿐 호출 경로(어떤 중립 연산이
그 호출에 닿는가)는 둘러싼 함수 이름까지만 안다. 경로 주장은 판정 층에서 근거와
함께만 한다.

실행: `python -m app.core.cloudkb.depkb.extract_spider`  (색인만 재생성 —
판정 층은 보존한다)
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path

SPIDER_VERSION = "v0.12.37"
TARBALL = (
    Path(__file__).resolve().parents[1]
    / ".cache" / "cloudkb" / f"cb-spider-{SPIDER_VERSION}.tar.gz"
)
#: 타르볼 실물의 해시 — 다르면 같은 이름의 다른 소스다.
TARBALL_SHA256 = "a3c638c4f183055b1cc738e25f33a56e10b22f85182a497fe50aeeedea81b1c8"

_DRIVERS = "cb-spider-0.12.37/cloud-control-manager/cloud-driver/drivers"
CSPS = ("aws", "azure", "gcp")
#: 어휘 9종에 닿는 핸들러만 — 전 핸들러 확장은 다음 절단면.
HANDLERS = ("VMHandler", "VPCHandler", "SecurityHandler",
            "KeyPairHandler", "DiskHandler", "NLBHandler")

_ARTIFACT = Path(__file__).resolve().parent / "neutralization_map.json"

_CALL = re.compile(
    r"([A-Za-z_][\w.]*)\.((?:Begin)?Create\w*|(?:Begin)?Delete\w*"
    r"|RunInstances|TerminateInstances|Insert)\s*\("
)
_FUNC = re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(")


def _op_of(call: str) -> str:
    bare = call.removeprefix("Begin")
    if bare.startswith(("Create", "RunInstances", "Insert")):
        return "create"
    return "delete"


def scan_calls() -> list[dict]:
    calls: list[dict] = []
    with tarfile.open(TARBALL) as tar:
        for csp in CSPS:
            for handler in HANDLERS:
                member = f"{_DRIVERS}/{csp}/resources/{handler}.go"
                try:
                    fh = tar.extractfile(member)
                except KeyError:
                    continue
                current_func = ""
                for lineno, line in enumerate(
                        fh.read().decode("utf-8", "replace").splitlines(), 1):
                    if (m := _FUNC.match(line)):
                        current_func = m.group(1)
                    if line.strip().startswith("//"):
                        continue
                    for m in _CALL.finditer(line):
                        calls.append({
                            "csp": csp,
                            "handler": handler,
                            "func": current_func,
                            "line": lineno,
                            "receiver": m.group(1),
                            "call": m.group(2),
                            "op": _op_of(m.group(2)),
                        })
    calls.sort(key=lambda c: (c["csp"], c["handler"], c["line"]))
    return calls


def rebuild_index() -> dict:
    """색인만 다시 뽑아 산출물에 싣는다. 판정 층(`judgments`)은 보존한다."""
    existing = {}
    if _ARTIFACT.exists():
        existing = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    blob = TARBALL.read_bytes()
    assert hashlib.sha256(blob).hexdigest() == TARBALL_SHA256, (
        "타르볼이 핀과 다르다 — 같은 이름의 다른 소스"
    )
    return {
        "_note": (
            "cb-spider 드라이버의 네이티브 생성/삭제 호출 색인(기계 층, 재계산 "
            "가능) + 기제 판정(judgments, 우리 구성 — 모든 판정은 색인의 호출을 "
            "인용해야 한다). 인용 형식: <csp>/<handler>.go:<line>."
        ),
        "_pin": {
            "source": f"cb-spider {SPIDER_VERSION}",
            "tarballSha256": TARBALL_SHA256,
        },
        "calls": scan_calls(),
        "judgments": existing.get("judgments", {}),
    }


if __name__ == "__main__":
    result = rebuild_index()
    _ARTIFACT.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    from collections import Counter
    by = Counter((c["csp"], c["handler"]) for c in result["calls"])
    print(f"calls: {len(result['calls'])}")
    for (csp, handler), n in sorted(by.items()):
        print(f"  {csp}/{handler}: {n}")
