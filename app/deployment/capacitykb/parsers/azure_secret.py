"""Azure REST 명세의 `x-ms-secret` → 비밀 속성.

**무엇을 답하나.** "이 값을 나중에 확인할 수 있나?" 배포 때 넣는 값 중에는 API로
다시 못 읽는 것이 있다 — 비밀번호·연결 문자열·액세스 키. `x-ms-secret: true`가
그 표시다. 이걸 알면 에이전트가 "이 속성은 배포 시 안전하게 관리해야 하고, 잃어버리면
다시 조회할 수 없다"고 미리 안내할 수 있다.

**왜 우리에게 없었나.** `bicep-types-az`(우리 Azure 제약의 본줄기)에도, 우리가 이미
쓰는 어느 산출물에도 "비밀 여부" 종류가 **0건**이다(실측: azure-capacity의 kind에
secret 없음). `x-ms-mutability`와 **같은 tarball·같은 성격**인데 그 축만 쓰고 있었다.
받는 비용도, 라이선스 판단도, 고정할 것도 새로 생기지 않는다.

## 무엇을 담고 무엇을 안 담나

**PUT 본문에 있는 것만 담는다.** 배포 요청에 사용자가 넣는 값이라 앱 개발에 직접
쓰인다. 응답에만 나오는 secret(조회 시 반환되는 키 등)은 오히려 **읽을 수 있는**
것이라 "다시 못 읽는다"는 이 축의 뜻과 어긋난다.

실측(커밋 76ca9f3e): PUT 본문 안, 우리 Azure 인덱스에 조인되는 것 **288건 / 106종**.
상위 이름이 뜻을 그대로 드러낸다 — `password` 39 · `adminPassword` 16 ·
`administratorLoginPassword` 13 · `storageAccountAccessKey` 10 · `clientSecret` 8 ·
`connectionString` 8. 담지 않은 definitions 전체(1,107건)에는 응답 전용 키가 섞여 있다.

**`x-ms-mutability`와 겹치지 않는다.** 그쪽은 "바꾸면 재생성되나"(create_only)이고
이쪽은 "읽을 수 있나"(secret)다. `administratorLoginPassword`는 비밀이면서 보통
변경 가능이다 — 두 축이 직교하므로 같은 속성에 둘 다 붙을 수 있고, 그래도 중복이
아니다.

## 인프라는 azure_mutability와 공유한다

`arm_type`(PUT 경로 → ARM 타입명)·`_latest_stable`(네임스페이스별 최신 stable 하나)은
그쪽에서 검증된 것을 그대로 쓴다. 순회 함수만 secret용으로 따로 둔다 — `_walk`는
`x-ms-mutability`만 보기 때문이다.
"""

from __future__ import annotations

import json
import sys
import tarfile
from collections import Counter
from pathlib import Path

from app.deployment.capacitykb.model import CapacitySet, Constraint
from app.deployment.capacitykb.parsers.azure_mutability import _latest_stable, arm_type
from app.deployment.kbcommon.fetch import describe_source_set, fetch_cached
from app.deployment.kbcommon.sources import SOURCES
from app.deployment.kbcommon.type_ids import AzureTypeIndex

EVIDENCE = "swagger-secret"
_MAX_DEPTH = 12


class Report:
    def __init__(self) -> None:
        self.scanned = 0
        self.files_with = 0
        self.names: Counter = Counter()
        self.unknown_types: Counter = Counter()
        """우리 Azure 인덱스에 없는 타입. 담지 않고 센다."""


def _walk_secret(
    defs: dict, node, path: str, out: list, seen: frozenset, depth: int = 0
) -> None:
    """정의를 따라 내려가며 `x-ms-secret: true`가 달린 속성을 모은다.

    구조는 `azure_mutability._walk`와 같다($ref·allOf·properties). 다른 것은 보는
    확장 키뿐이라 따로 둔다 — 라벨 하나에 성격 하나라는 규칙과 같은 이유다.
    """
    if depth > _MAX_DEPTH or not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/definitions/"):
        name = ref.split("/")[-1]
        if name not in seen:
            _walk_secret(defs, defs.get(name, {}), path, out, seen | {name}, depth + 1)
        return
    for sub in node.get("allOf") or []:
        _walk_secret(defs, sub, path, out, seen, depth + 1)
    for prop, spec in (node.get("properties") or {}).items():
        if not isinstance(spec, dict):
            continue
        here = f"{path}.{prop}" if path else prop
        if spec.get("x-ms-secret") is True:
            out.append(here)
        _walk_secret(defs, spec, here, out, seen, depth + 1)


def parse_tarball(tar: Path, *, type_index: AzureTypeIndex) -> tuple[CapacitySet, Report]:
    """azure-rest-api-specs tarball에서 PUT 본문의 비밀 속성을 뽑는다."""
    capacity = CapacitySet()
    report = Report()
    wanted = _latest_stable(tar)
    report.scanned = len(wanted)

    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or member.name not in wanted:
                continue
            raw = archive.extractfile(member).read()
            if b"x-ms-secret" not in raw:
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
                _walk_secret(defs, body[0].get("schema") or {}, "", found, frozenset())
                if not found:
                    continue
                if type_name.lower() not in type_index.by_lower:
                    report.unknown_types[type_name] += 1
                    continue
                type_id = type_index.type_id(type_name)
                for prop in found:
                    report.names[prop.split(".")[-1]] += 1
                    capacity.add_constraint(
                        Constraint(
                            type_id=type_id, property=prop, kind="secret",
                            value=True, evidence=EVIDENCE,
                        )
                    )
    return capacity, report


def build(output: Path, *, refresh: bool = False) -> CapacitySet:
    from app.deployment.capacitykb.parsers.azure import _fetch_relative
    from app.deployment.kbcommon.type_ids import read_azure_index

    source = SOURCES["azure-rest-api-specs"]
    tar = fetch_cached(source.url, f"azure-specs-{source.pin[:12]}.tar.gz", refresh=refresh)

    index_path = _fetch_relative(SOURCES["bicep-types-az"].url, "index.json", refresh=refresh)
    type_index = read_azure_index(json.loads(index_path.read_text(encoding="utf-8")))

    capacity, report = parse_tarball(tar, type_index=type_index)
    types = {c.type_id for c in capacity.constraints}
    capacity.provenance = [describe_source_set([tar], source.key)]
    capacity.coverage = [{
        "provider": "azure",
        "types": len(types),
        "type_ids": sorted(types),
        "note": (
            "x-ms-secret from the Azure REST specification, but only what appears in "
            "the **PUT body**. these are values you set at deploy time and cannot "
            "read back through the API. response-only secrets can be read, which "
            "contradicts what this axis means, so they were left out. this axis is "
            "orthogonal to x-ms-mutability, so the two do not overlap."
        ),
    }]

    print(
        f"azure-secret: 비밀 속성 {len(capacity.constraints):,}건 "
        f"({len(types)}종 · stable 최신 {report.scanned:,}개 중 {report.files_with}개 파일에서)"
    )
    print(
        "  이름 상위: "
        + ", ".join(f"{n}={c}" for n, c in report.names.most_common(8)),
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
