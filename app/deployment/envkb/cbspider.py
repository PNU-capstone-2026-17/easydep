"""cb-spider 드라이버 → **멀티클라우드 도구가 어느 CSP의 무엇을 다루는가.**

**무엇에 대한 지식인가.** 이건 **도구의 커버리지**이지 클라우드의 사실이 아니다.
cb-spider는 여러 CSP를 한 인터페이스로 다루는 계층이고, 여기 없는 리소스는
"그 CSP에 없다"가 아니라 **"이 도구로는 다루지 못한다"**는 뜻이다.

우리는 배포기가 아니라 **가이드라인 지식베이스**를 만들므로 이 구분이 중요하다.
멀티클라우드 도구를 고려하는 사람에게 "어느 CSP까지 한 방식으로 다룰 수 있나"는
쓸모 있는 정보지만, 그걸 "KT Cloud에 쿠버네티스가 없다"로 옮겨 말하면 거짓이다.

지금까지 우리는 CSP를 다섯만 알았다(aws·azure·gcp·alibaba·tencent). cb-spider는
**열둘**을 다룬다. 나머지 일곱(ibm·kt·ktclassic·ncp·nhn·openstack·oracle)은
costkb·perfkb에 가격과 스펙이 있는데도 "무엇을 만들 수 있나"는 답할 수 없었다.

## 매트릭스가 곧 지식이다 (v0.12.37 실측)

    VPC·Security·KeyPair·VM·Disk·MyImage   12/12  전부 지원
    NLB                                    11/12  oracle 없음
    Cluster(k8s)                            8/12  kt·ktclassic·openstack·oracle 없음

**"파일이 있다"와 "구현됐다"는 다르다.** cb-spider는 미지원 기능을 "not supported"
에러를 던지는 스텁으로 두기도 한다. 파일 존재만으로 답하면 확신에 찬 오답이 되므로,
**핸들러의 주 생성 메서드가 실제로 있는지**까지 본다.

메서드 이름이 핸들러마다 다르다는 것이 함정이다 — 인터페이스를 직접 읽고 확인했다:

    VMHandler      → StartVM      (CreateVM이 아니다)
    MyImageHandler → SnapshotVM   (CreateImage가 아니다)

`Create`로만 찾으면 이 둘이 **전부 미구현으로 잡힌다**(처음에 그 상태를 만들었다).
"""

from __future__ import annotations

import re
import tarfile
from functools import lru_cache
from pathlib import Path

from kbcommon.artifact import load_json, resolve, write_dataset
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

#: 핸들러 → (core 리소스들, 주 생성 메서드).
#: 메서드 이름은 `cloud-driver/interfaces/resources/*.go`에서 직접 확인했다.
HANDLERS: dict[str, tuple[tuple[str, ...], str]] = {
    "VPCHandler": (("vNet", "subnet"), "CreateVPC"),
    "SecurityHandler": (("securityGroup",), "CreateSecurity"),
    "KeyPairHandler": (("sshKey",), "CreateKey"),
    "VMHandler": (("vm",), "StartVM"),
    "NLBHandler": (("nlb",), "CreateNLB"),
    "ClusterHandler": (("k8sCluster", "k8sNodeGroup"), "CreateCluster"),
    "DiskHandler": (("dataDisk",), "CreateDisk"),
    "MyImageHandler": (("customImage",), "SnapshotVM"),
}

#: `drivers/<csp>/resources/<Handler>.go`. `-plugin` 디렉터리와 mock은 뺀다.
_PATH = re.compile(r"/cloud-driver/drivers/([a-z0-9]+)/resources/(\w+Handler)\.go$")

#: 미구현 표시. 주 메서드가 이것만 돌려주면 스텁이다.
_UNSUPPORTED = re.compile(
    r"(?:not\s+supported|does\s+not\s+support|unsupported|미지원|지원하지\s*않)", re.I
)

_SKIP_CSP = {"mock"}

SCHEMA = {
    "type": "object",
    "required": ["csps", "_source"],
    "properties": {
        "_note": {"type": "string"},
        "_source": {"type": "array"},
        "csps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["csp", "resources"],
                "additionalProperties": False,
                "properties": {
                    "csp": {"type": "string", "minLength": 1},
                    "resources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["core", "supported"],
                            "additionalProperties": False,
                            "properties": {
                                "core": {"type": "string", "minLength": 1},
                                "supported": {"type": "boolean"},
                                "handler": {"type": ["string", "null"]},
                                "method": {"type": ["string", "null"]},
                                # 왜 미지원인지 — 파일이 없나, 스텁인가
                                "reason": {"type": ["string", "null"]},
                            },
                        },
                    },
                },
            },
        },
    },
}


def _method_body(text: str, method: str) -> str | None:
    """`func (h *X) Method(...) {` 의 **여는 중괄호 다음부터** 본문. 없으면 None.

    시그니처부터 자르면 안 된다 — 첫 줄이 `req irs.ClusterInfo) (…, error) {`
    같은 시그니처 잔여물이 되어 "첫 실행문" 판정이 통째로 빗나간다(테스트가 잡았다).
    """
    found = re.search(rf"func \([^)]*\) {re.escape(method)}\s*\(", text)
    if found is None:
        return None
    opening = text.find("{", found.end())
    if opening < 0:
        return None
    # 다음 최상위 `func `까지를 본문으로 본다 — 중괄호를 세는 것보다 튼튼하다.
    nxt = text.find("\nfunc ", opening)
    return text[opening + 1 : nxt if nxt > 0 else len(text)]


def classify(text: str, method: str) -> tuple[bool, str | None]:
    """(구현됐나, 아니라면 이유).

    **판정은 "미지원 반환이 첫 실행문인가"다.** 처음엔 본문 길이로 갈랐는데
    임계값이 취약했다 — 짧지만 실제로 구현된 본문을 스텁으로 잡았다(테스트가
    잡아냈다). 길이는 우연이지만 **조건 없이 바로 미지원을 돌려주는 것**은
    스텁의 정의 그 자체다.

    반대로 `if`나 다른 문장 뒤에 나오는 미지원 반환은 **일부 기능 제한**이다
    (NLB가 UDP 헬스체크만 거부하는 식). 그걸 스텁으로 보면 되는 것을 안 된다고
    답하게 되고, 그 오답은 침묵보다 나쁘다.
    """
    body = _method_body(text, method)
    if body is None:
        return False, f"the {method} method is missing"
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
            continue
        if stripped.startswith("return") and _UNSUPPORTED.search(stripped):
            return False, f"{method} only returns an unsupported error (a stub)"
        break  # 첫 실행문이 미지원 반환이 아니면 구현으로 본다
    return True, None


def parse_tarball(tar: Path) -> tuple[list[dict], dict]:
    """드라이버 tarball → CSP별 지원 매트릭스."""
    files: dict[tuple[str, str], str] = {}
    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            found = _PATH.search("/" + member.name)
            if not found:
                continue
            csp, handler = found.groups()
            if csp in _SKIP_CSP or handler not in HANDLERS:
                continue
            try:
                files[(csp, handler)] = archive.extractfile(member).read().decode(
                    "utf-8", "replace"
                )
            except Exception:
                continue

    csps = sorted({csp for csp, _ in files})
    stats = {"csps": len(csps), "supported": 0, "missing": 0, "stub": 0}
    records = []
    for csp in csps:
        resources = []
        for handler, (cores, method) in HANDLERS.items():
            text = files.get((csp, handler))
            if text is None:
                ok, reason = False, "the handler file is missing"
            else:
                ok, reason = classify(text, method)
            if ok:
                stats["supported"] += len(cores)
            elif text is None:
                stats["missing"] += len(cores)
            else:
                stats["stub"] += len(cores)
            for core in cores:
                resources.append({
                    "core": core,
                    "supported": ok,
                    "handler": handler,
                    "method": method,
                    "reason": reason,
                })
        records.append({"csp": csp, "resources": resources})
    return records, stats


def build(output: Path, *, refresh: bool = False) -> dict:
    source = SOURCES["cb-spider"]
    tar = fetch_cached(source.url, f"cb-spider-{source.pin}.tar.gz", refresh=refresh)
    records, stats = parse_tarball(tar)

    dataset = {
        "_note": (
            "Which resources the cb-spider drivers handle per CSP. **This is the "
            "tool's coverage, not a fact about the cloud** — unsupported here "
            "does not mean 'the CSP lacks that feature', it means 'this tooling "
            "cannot handle it'. Use it when weighing a multi-cloud tool."
        ),
        "_source": [describe_source_set([tar], source.key)],
        "csps": records,
    }
    write_dataset(output, dataset, SCHEMA)
    print(
        f"cbspider-support: {stats['csps']} CSPs · supported {stats['supported']} · "
        f"no handler {stats['missing']} · stub {stats['stub']}"
    )
    return dataset


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------

ARTIFACT = "cbspider-support.json"

_MISSING = (
    "No cb-spider support artifact. Build it with "
    "`python -m kbcommon build-cbspider`."
)


@lru_cache(maxsize=4)
def _load(output_dir: str) -> dict[str, dict[str, dict]]:
    path = resolve(Path(output_dir), ARTIFACT)
    if path is None:
        return {}
    try:
        data = load_json(path)
    except Exception:
        return {}
    return {
        row["csp"]: {r["core"]: r for r in row["resources"]}
        for row in data.get("csps") or []
    }


def providers(*, output_dir: str = "output") -> tuple[str, ...]:
    return tuple(sorted(_load(output_dir)))


def supports(csp: str, core: str, *, output_dir: str = "output") -> dict | None:
    return _load(output_dir).get(csp.strip().lower(), {}).get(core)


def describe(csp: str | None = None, core: str | None = None, *, output_dir: str = "output") -> str:
    """어느 CSP가 무엇을 만들 수 있는지. 사람이 읽는 답."""
    data = _load(output_dir)
    if not data:
        return _MISSING

    if csp is not None:
        key = csp.strip().lower()
        if key not in data:
            return (
                f"cb-spider does not handle the CSP '{csp}' — that does not mean "
                f"no such CSP exists, it is **outside this tool's coverage**.\n"
                f"  CSPs handled: {', '.join(sorted(data))}"
            )
        rows = data[key]
        if core is not None:
            record = rows.get(core)
            if record is None:
                return (
                    f"{key}: '{core}' is not a resource we track.\n"
                    f"  Tracked: {', '.join(sorted(rows))}"
                )
            if record["supported"]:
                return (
                    f"{core} on {key}: **cb-spider handles it** "
                    f"({record['handler']}.{record['method']})"
                )
            return (
                f"{core} on {key}: **the cb-spider driver does not handle it** "
                f"({record['reason']}).\n"
                "  **This does not mean that CSP lacks the feature** — it means "
                "handling it one uniform way through the multi-cloud tool needs a "
                "separate route. Whether that CSP offers this service is not what "
                "this data answers."
            )
        ok = sorted(c for c, r in rows.items() if r["supported"])
        no = sorted(c for c, r in rows.items() if not r["supported"])
        lines = [
            f"Resources cb-spider handles on {key} ({len(ok)}): {', '.join(ok)}"
        ]
        if no:
            lines.append(
                f"  Not handled by this tool: {', '.join(no)} — this means there "
                "is no driver, not that the CSP lacks it."
            )
        return "\n".join(lines)

    if core is not None:
        yes = sorted(c for c, rows in data.items() if (rows.get(core) or {}).get("supported"))
        no = sorted(c for c, rows in data.items() if core in rows and not rows[core]["supported"])
        if not yes and not no:
            return f"'{core}' is not a resource we track."
        lines = [
            f"CSPs where cb-spider handles {core} ({len(yes)}): {', '.join(yes)}"
        ]
        if no:
            lines.append(
                f"  No driver: {', '.join(no)} — this does not mean those CSPs "
                "lack the feature."
            )
        return "\n".join(lines)

    return f"CSPs cb-spider handles ({len(data)}): {', '.join(sorted(data))}"
