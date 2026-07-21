"""Azure REST 명세의 `x-ms-mutability` → 불변 속성.

**왜 따로 받나.** `bicep-types-az`(우리 Azure 제약의 본줄기)에는 이 정보가 **0건**이다.
원본에 없어서가 아니라 **생성기가 떨어뜨린다** — Bicep 타입에는 `Immutable` 플래그가
없어서 `["read","create"]`가 "쓸 수 있고 읽을 수 있음"으로 접히고 `flags`에 아무것도
안 남는다. 그래서 Azure 3,371종 전부가 "변경 불가로 알려진 속성이 없습니다"라고
답하고 있었다 — 데이터 부재가 사실 부재로 읽히는 최대 규모 사례였다.

**단일 소스다.** 이 프로젝트는 "한 사실에 두 소스를 댄다"를 원칙으로 두지만 여기엔
댈 짝이 없다. 대신 원본이 직접 단 주석이라 `basis=stated`이고, 타입 이름이 우리
인덱스에 실재하는지는 대조한다.

실측(커밋 76ca9f3e, 2026-07-21):
  stable 스펙 6,842개 → 최신만 1,422개, 그중 `x-ms-mutability`가 있는 파일 325개
  값 조합 — [create,read] 1,133 · [create,read,update] 208 · [create,update] 198 ·
            [create] 152 · [read] 134 · [read,update] 22 · [update] 5
  **생성 후 불변**(create 있고 update 없음) 1,285건 / 428종

붙는 자리가 핵심 속성이다: `DBforMySQL/flexibleServers.properties.administratorLogin`,
`NetworkCloud/virtualMachines.properties.adminUsername`,
`Sql/managedInstances/databases.properties.collation`.

`read`만 있는 것은 담지 않는다 — 읽기 전용은 `bicep-flags`가 이미 4,704건 담고 있어
중복이고, 라벨 하나에 성격 하나라는 규칙상 섞으면 안 된다.
"""

from __future__ import annotations

import json
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path

from capacitykb.model import CapacitySet, Constraint
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES
from kbcommon.type_ids import AzureTypeIndex

EVIDENCE = "swagger-mutability"

#: `.../resource-manager/<네임스페이스>/…/stable/<버전>/<파일>.json`
_STABLE = re.compile(
    r"/resource-manager/(Microsoft\.[^/]+)/(?:.*/)?stable/"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}[^/]*)/([^/]+\.json)$"
)
_PROVIDERS = "/providers/"
_MAX_DEPTH = 12


class Report:
    def __init__(self) -> None:
        self.scanned = 0
        self.files_with = 0
        self.combos: Counter = Counter()
        self.unknown_types: Counter = Counter()
        """우리 Azure 인덱스에 없는 타입. 담지 않고 센다."""


def arm_type(url: str) -> str | None:
    """PUT 경로 → ARM 타입명.

    ARM 경로는 `네임스페이스/타입/이름/타입/이름…` **교대**다. 그러니 위치로 읽는다 —
    `{파라미터}`를 걸러내는 방식으로 하면 이름이 리터럴일 때 그걸 타입으로 읽는다.
    `/virtualMachineInstances/default`의 `default`가 그렇다(실측 21종 27건).

    **`/providers/`가 두 번 나오면 마지막 것을 쓴다** —
    `/providers/Microsoft.Sql/servers/{n}/providers/Microsoft.Insights/…` 에서
    앞에서부터 읽으면 `Microsoft.Sql/servers/providers`라는 없는 타입이 나온다
    (실측 10종). 실제 타입은 뒤쪽 공급자다.
    """
    index = url.rfind(_PROVIDERS)
    if index < 0:
        return None
    parts = [p for p in url[index + len(_PROVIDERS) :].split("/") if p]
    if len(parts) < 2 or not parts[0].lower().startswith("microsoft."):
        return None
    # parts[0]=네임스페이스, 이후 홀수 자리가 타입, 짝수 자리가 이름이다.
    segments = [parts[0]] + [p for i, p in enumerate(parts[1:]) if i % 2 == 0]
    if any(p.startswith("{") for p in segments):
        return None  # 타입 자리에 파라미터가 오면 우리가 못 읽는 모양이다
    return "/".join(segments)


def _walk(defs: dict, node, path: str, out: list, seen: frozenset, depth: int = 0) -> None:
    """정의를 따라 내려가며 `x-ms-mutability`가 달린 속성을 모은다."""
    if depth > _MAX_DEPTH or not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/definitions/"):
        name = ref.split("/")[-1]
        if name not in seen:
            _walk(defs, defs.get(name, {}), path, out, seen | {name}, depth + 1)
        return
    for sub in node.get("allOf") or []:
        _walk(defs, sub, path, out, seen, depth + 1)
    for prop, spec in (node.get("properties") or {}).items():
        if not isinstance(spec, dict):
            continue
        here = f"{path}.{prop}" if path else prop
        mutability = spec.get("x-ms-mutability")
        if isinstance(mutability, list):
            out.append((here, tuple(sorted(mutability))))
        _walk(defs, spec, here, out, seen, depth + 1)


def _latest_stable(tar: Path) -> dict[str, str]:
    """(네임스페이스, 파일명)마다 **최신 stable 하나**만 고른다.

    한 파일 안에서 API 버전을 섞지 않는다는 규약과 같은 이유다 — 옛 버전의 표시와
    새 버전의 표시가 섞이면 어느 쪽이 지금 사실인지 아무도 모른다.
    """
    latest: dict[tuple[str, str], tuple[str, str]] = {}
    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            found = _STABLE.search(member.name)
            if not found:
                continue
            namespace, version, filename = found.groups()
            key = (namespace, filename)
            if key not in latest or version > latest[key][0]:
                latest[key] = (version, member.name)
    return {name: version for version, name in latest.values()}


def parse_tarball(tar: Path, *, type_index: AzureTypeIndex) -> tuple[CapacitySet, Report]:
    """azure-rest-api-specs tarball에서 불변 속성을 뽑는다."""
    capacity = CapacitySet()
    report = Report()
    wanted = _latest_stable(tar)
    report.scanned = len(wanted)

    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or member.name not in wanted:
                continue
            raw = archive.extractfile(member).read()
            if b"x-ms-mutability" not in raw:
                continue
            report.files_with += 1
            try:
                doc = json.loads(raw)
            except Exception:
                continue
            defs = doc.get("definitions") or {}
            for url, operations in (doc.get("paths") or {}).items():
                if not isinstance(operations, dict) or "put" not in operations:
                    continue
                type_name = arm_type(url)
                if not type_name:
                    continue
                body = [
                    p for p in (operations["put"].get("parameters") or [])
                    if p.get("in") == "body"
                ]
                if not body:
                    continue
                found: list = []
                _walk(defs, body[0].get("schema") or {}, "", found, frozenset())
                if not found:
                    continue
                if type_name.lower() not in type_index.by_lower:
                    report.unknown_types[type_name] += 1
                    continue
                type_id = type_index.type_id(type_name)
                for prop, mutability in found:
                    report.combos[mutability] += 1
                    # `read`만 있는 것은 담지 않는다 — bicep-flags가 이미 담고 있다.
                    if "create" not in mutability or "update" in mutability:
                        continue
                    capacity.add_constraint(
                        Constraint(
                            type_id=type_id, property=prop, kind="mutability",
                            value="create_only", evidence=EVIDENCE,
                        )
                    )
    return capacity, report


def build(output: Path, *, refresh: bool = False) -> CapacitySet:
    from capacitykb.parsers.azure import _fetch_relative
    from kbcommon.type_ids import read_azure_index

    source = SOURCES["azure-rest-api-specs"]
    tar = fetch_cached(source.url, f"azure-specs-{source.pin[:12]}.tar.gz", refresh=refresh)

    index_path = _fetch_relative(SOURCES["bicep-types-az"].url, "index.json", refresh=refresh)
    type_index = read_azure_index(json.loads(index_path.read_text(encoding="utf-8")))

    capacity, report = parse_tarball(tar, type_index=type_index)
    capacity.provenance = [describe_source_set([tar], source.key)]
    capacity.coverage = [{
        "provider": "azure",
        "types": len({c.type_id for c in capacity.constraints}),
        "type_ids": sorted({c.type_id for c in capacity.constraints}),
        "note": (
            "Azure REST 명세의 x-ms-mutability. bicep-types에는 이 정보가 0건인데 "
            "원본에 없어서가 아니라 생성기가 떨어뜨리기 때문이다. 교차 검증할 짝이 "
            "없는 단일 소스다."
        ),
    }]

    print(
        f"azure-mutability: 불변 속성 {len(capacity.constraints):,}건 "
        f"({len({c.type_id for c in capacity.constraints})}종 · "
        f"stable 최신 {report.scanned:,}개 중 {report.files_with}개 파일에서)"
    )
    print(
        "  값 조합: "
        + ", ".join(f"{list(k)}={v}" for k, v in report.combos.most_common()),
        file=sys.stderr,
    )
    if report.unknown_types:
        total = sum(report.unknown_types.values())
        print(
            f"  우리 Azure 인덱스에 없는 타입 {len(report.unknown_types)}종({total}건)은 "
            f"담지 않음: {list(report.unknown_types)[:5]}",
            file=sys.stderr,
        )
    capacity.save(output)
    return capacity
