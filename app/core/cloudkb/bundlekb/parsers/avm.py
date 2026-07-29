"""Azure Verified Modules → 리소스 군 세 층.

**같은 tarball을 graphkb도 읽는다.** 저쪽은 `dependsOn`(배포 순서)을 보고 이쪽은
**모듈이 무엇을 배포하는가**를 본다. 소스는 같고 축이 다르다.

## 판별자 — 두 번 틀리고 세 번째에 확정했다

컴파일된 ARM 템플릿에서 리소스가 실제로 만들어지는지는 세 가지로 갈린다.

    condition 있음                      → 선택 (파라미터에 따라)
    copy.count에 coalesce/createArray   → 선택 (빈 배열 폴백이 있다)
    copy.count에 폴백 없음              → **필수** (값을 반드시 줘야 한다)
    둘 다 없음                          → 무조건

**1차 실패** — `condition`만 봤더니 패턴 하나가 104종을 "항상 배포"한다고 나왔고,
한 Cosmos 계정에 Cassandra·Gremlin·Mongo·SQL이 **동시에** 들어 있었다. `copy`(파라미터
배열 루프)를 안 센 탓이다.

**2차 실패** — `defaultValue` 부재를 '필수'로 읽었더니 `Insights/diagnosticSettings`가
**173개 중 89개 모듈의 필수 동반자**로 나왔다. 명백한 오탐이다. AVM은 Bicep의 nullable
파라미터를 `defaultValue` 없이 컴파일하고 사용처에서 `coalesce(x, createArray())`로
처리한다 — 기본값이 파라미터 선언이 아니라 **본문**에 있다.

## 중첩 배포는 재귀로 풀어야 한다

VM의 NIC은 `deployments → deployments → networkInterfaces`로 **두 단** 중첩이다.
한 단만 보면 VM의 **유일한 진짜 필수 동반자를 통째로 잃고**, "VM은 아무것도 필요
없다"는 사실과 정반대인 KB가 된다. `test_avm_bundles.py`가 이 회귀를 고정한다.

## 실측 (커밋 b7c2b1a2)

    res 173개 · ptn 49개
    res: 무조건 1종인 모듈 152개 · 필수 동반이 있는 모듈 15개 · 선택 평균 5.4종
    검증되는 사례: virtual-machine → networkInterfaces
                   virtual-network-gateway → publicIPAddresses

## 이것은 모듈 저자의 설계이지 API의 강제가 아니다

`avm-dependson`·`tpg-schema`에 적어 둔 것과 같은 경계다. 다만 위 두 사례는 클라우드
사실과도 일치한다 — Azure VM은 NIC이 있어야 하고 VPN 게이트웨이는 공인 IP가 있어야
한다. **일치를 확인한 것과 일치한다고 가정하는 것은 다르다.**
"""

from __future__ import annotations

import json
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path

from app.core.cloudkb.bundlekb.model import ALWAYS, OPTIONAL, REQUIRED, Bundle, BundleSet, Member
from app.core.cloudkb.kbcommon.fetch import describe_source_set, fetch_cached
from app.core.cloudkb.kbcommon.sources import SOURCES

EVIDENCE = "avm-module"
PROVIDER = "azure"

_RES = re.compile(r"/avm/res/([^/]+)/([^/]+)/main\.json$")
_PTN = re.compile(r"/avm/ptn/(.+)/main\.json$")
_DEPLOY = "Microsoft.Resources/deployments"
#: 텔레메트리 배포. 그래프 파서가 겪은 것과 같은 함정 — 안 거르면 가짜 구성원이 된다.
_TELEMETRY = "enableTelemetry"
_MAX_DEPTH = 8


def _resources(doc: dict | None) -> list:
    """`resources`는 배열일 수도 dict일 수도 있다(languageVersion 2.0)."""
    if not isinstance(doc, dict):
        return []
    found = doc.get("resources")
    if isinstance(found, dict):
        return list(found.values())
    return found or []


def _is_soft(resource: dict) -> bool:
    """이 리소스가 **선택**인가 — 조건이 붙었거나 빈 배열 폴백이 있는 루프인가."""
    if "condition" in resource:
        return True
    count = str((resource.get("copy") or {}).get("count") or "")
    return "coalesce(" in count or "createArray()" in count


def _looped(resource: dict) -> bool:
    return bool((resource.get("copy") or {}).get("count"))


def resolve_types(resource: dict, depth: int = 0) -> list[tuple[str, bool]]:
    """중첩 배포를 풀어 `(타입, 안쪽에서 선택이 되었나)` 목록을 낸다.

    **재귀가 핵심이다.** 한 단만 보면 두 단 중첩된 리소스가 통째로 사라진다.
    """
    if depth > _MAX_DEPTH or not isinstance(resource, dict):
        return []
    type_name = resource.get("type")
    if type_name and type_name != _DEPLOY:
        return [(type_name, False)]
    inner = (resource.get("properties") or {}).get("template")
    if not isinstance(inner, dict):
        return []
    out: list[tuple[str, bool]] = []
    for child in _resources(inner):
        if not isinstance(child, dict):
            continue
        soft = _is_soft(child)
        for found, inner_soft in resolve_types(child, depth + 1):
            out.append((found, soft or inner_soft))
    return out


def classify(doc: dict) -> dict[str, str]:
    """타입 → 등급. 같은 타입이 여러 자리에 나오면 **가장 강한 등급**을 남긴다."""
    tiers: dict[str, str] = {}

    def keep(type_name: str, tier: str) -> None:
        rank = {ALWAYS: 0, REQUIRED: 1, OPTIONAL: 2}
        if type_name not in tiers or rank[tier] < rank[tiers[type_name]]:
            tiers[type_name] = tier

    for resource in _resources(doc):
        if not isinstance(resource, dict):
            continue
        # 텔레메트리는 모듈 기능이지 리소스 군이 아니다.
        if _TELEMETRY in str(resource.get("condition") or ""):
            continue
        soft = _is_soft(resource)
        looped = _looped(resource)
        for type_name, inner_soft in resolve_types(resource):
            if type_name == _DEPLOY:
                continue
            if soft or inner_soft:
                keep(type_name, OPTIONAL)
            elif looped:
                keep(type_name, REQUIRED)
            else:
                keep(type_name, ALWAYS)
    return tiers


class Report:
    def __init__(self) -> None:
        self.res = 0
        self.ptn = 0
        self.skipped_empty = 0
        self.dropped_ptn_optional = 0
        self.tiers: Counter = Counter()


def parse_tarball(tar: Path) -> tuple[BundleSet, Report]:
    bundles = BundleSet()
    report = Report()
    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            name = "/" + member.name.replace("\\", "/")
            kind = "res" if _RES.search(name) else "ptn" if _PTN.search(name) else None
            if kind is None:
                continue
            try:
                doc = json.loads(archive.extractfile(member).read())
            except Exception:
                continue
            tiers = classify(doc)
            if not tiers:
                report.skipped_empty += 1
                continue
            key = name.split(f"/avm/{kind}/", 1)[1].rsplit("/main.json", 1)[0]
            if kind == "ptn":
                # **패턴의 선택 층은 담지 않는다.** 안에 든 `res` 모듈의 선택 리소스가
                # 전부 전이돼, `iaas-vm-cosmosdb-tier4` 하나가 Cosmos의 Cassandra·
                # Gremlin·Mongo·SQL API를 **동시에** 선택지로 갖게 된다. 그건
                # graphkb가 "가능한 것"과 "필요한 것"을 못 가르던 실패 그대로다.
                # 담지 않는 이유는 coverage에 적는다 — 조용히 버리지 않는다.
                tiers = {t: v for t, v in tiers.items() if v != OPTIONAL}
                report.dropped_ptn_optional += 1
                if not tiers:
                    report.skipped_empty += 1
                    continue
            members = tuple(
                Member(f"{PROVIDER}::{t}", tier) for t, tier in sorted(tiers.items())
            )
            report.tiers.update(m.tier for m in members)
            setattr(report, kind, getattr(report, kind) + 1)
            always = [m for m in members if m.tier == ALWAYS]
            bundles.add(
                Bundle(
                    id=f"avm::{kind}/{key}",
                    name=f"avm/{kind}/{key}",
                    provider=PROVIDER,
                    evidence=EVIDENCE,
                    members=members,
                    # `res` 모듈은 감싸는 리소스가 딱 하나일 때만 앵커가 분명하다.
                    anchor=always[0].type_id if len(always) == 1 else None,
                    description=(doc.get("metadata") or {}).get("description"),
                )
            )
    return bundles, report


def build(output: Path, *, refresh: bool = False) -> BundleSet:
    source = SOURCES["avm-bicep"]
    tar = fetch_cached(source.url, f"avm-{source.pin[:12]}.tar.gz", refresh=refresh)
    bundles, report = parse_tarball(tar)
    bundles.provenance = [describe_source_set([tar], source.key)]
    bundles.coverage = [
        {
            "provider": PROVIDER,
            "bundles": len(bundles.bundles),
            "note": (
                "Resource groups deployed by "
                f"{report.res + report.ptn} Azure Verified Modules "
                f"(res {report.res} · ptn {report.ptn}). **This is the module "
                "author's design, not what the API enforces** — the same boundary "
                "as graphkb's avm-dependson. Tiers are split by condition/copy in "
                "the compiled ARM: a loop with no fallback is 'you must supply a "
                "value', a coalesce/createArray fallback is 'optional "
                f"attachment'. **The optional tier of {report.dropped_ptn_optional} "
                "`ptn` (pattern) modules is not included** — the optional "
                "resources of the `res` modules inside all carry over, so a single "
                "pattern ends up offering Cosmos' Cassandra·Gremlin·Mongo·SQL APIs "
                "as options at the same time. For patterns, read that as **'we did "
                "not include them'**, not as 'there are no optional resources'."
            ),
        }
    ]
    from app.core.cloudkb.kbcommon import artifact

    artifact.write_dataset(output, bundles.to_dict(), _schema())
    print(
        f"avm 번들: {len(bundles.bundles):,}개 "
        f"(res {report.res} · ptn {report.ptn}) → {output}"
    )
    print(f"  등급별 구성원: {dict(report.tiers)}", file=sys.stderr)
    if report.skipped_empty:
        print(f"  리소스를 못 읽은 모듈 {report.skipped_empty}개", file=sys.stderr)
    return bundles


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
