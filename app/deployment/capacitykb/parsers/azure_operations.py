"""Azure REST 명세의 `x-ms-long-running-operation` → 작업이 오래 걸리는가.

**무엇을 답하나.** "이 리소스는 만드는 데 오래 걸리나?" "재시작이 바로 되나?"
배포 스크립트의 타임아웃과 단계 나누기가 여기에 달려 있다.

**왜 별도 산출물인가.** 이건 **속성이 아니라 작업**에 붙는 사실이라
`Constraint`(type_id, property, kind) 모델에 안 맞는다. 2번 라운드에서 이 이유로
보류했고, 그 뒤 리전·탄소·수명주기가 같은 모양(타입/리전에 딸린 별도 산출물)으로
자리잡아 이제 따라 담는다.

## 변별력이 있다 — 그래서 담을 값어치가 있다

실측(커밋 76ca9f3e, 최신 stable 기준) 타입 2,008종의 메서드 조합:

    put=LRO delete=LRO patch=LRO   517종     put=동기 delete=동기 patch=동기  220종
    put=LRO delete=LRO             348종     put=동기 delete=동기            292종

**"거의 다 LRO"가 아니라 절반씩 갈린다.** 전부 동기인 타입이 512종이나 되므로
"오래 걸린다"가 실제 정보가 된다.

## 두 가지를 조심한다

**(1) 모순은 담지 않는다.** 같은 타입·같은 메서드를 파일마다 다르게 말하는 것이
20종 있다(실측). `aws_limits`가 "두 공식 소스가 어긋나면 담지 않고 보고한다"고 한
것과 같은 원칙이다 — 어느 쪽이 맞는지 모르는데 하나를 고르면 그건 우리 짐작이다.

**(2) POST 액션 경로는 마지막 마디를 떼고 타입을 구한다.**

    /providers/Microsoft.Compute/virtualMachines/{vm}/start
                                                     ^^^^^ 액션이지 타입이 아니다

`arm_type`을 그대로 부르면 타입/이름 교대 규칙상 액션이 타입 자리에 들어가
`Microsoft.Compute/locations/virtualMachinesBulkCancel` 같은 없는 타입이 나온다
(실측으로 그 상태를 먼저 만들었다). 마지막 마디를 떼고 부르면 제자리를 찾는다.
"""

from __future__ import annotations

import json
import sys
import tarfile
from collections import Counter, defaultdict
from pathlib import Path

from capacitykb.parsers.azure_mutability import _latest_stable, arm_type
from kbcommon.artifact import write_dataset
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES
from kbcommon.type_ids import AzureTypeIndex

#: 리소스 자체를 만들고 지우고 고치는 작업.
_METHODS = ("put", "delete", "patch")

_METHOD_NAMES = {"put": "생성", "delete": "삭제", "patch": "수정"}

SCHEMA = {
    "type": "object",
    "required": ["types", "_source"],
    "properties": {
        "_note": {"type": "string"},
        "_source": {"type": "array"},
        "conflicting": {"type": "integer"},
        "types": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type_id"],
                "additionalProperties": False,
                "properties": {
                    "type_id": {"type": "string", "minLength": 1},
                    # true=오래 걸림, false=즉시, 없음=원본이 말 안 함
                    "create": {"type": ["boolean", "null"]},
                    "delete": {"type": ["boolean", "null"]},
                    "update": {"type": ["boolean", "null"]},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "long_running"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "long_running": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        },
    },
}


class Report:
    def __init__(self) -> None:
        self.scanned = 0
        self.conflicting: Counter = Counter()
        """같은 타입·메서드를 파일마다 다르게 말한 것. 담지 않고 센다."""
        self.unknown_types: Counter = Counter()
        self.actions = 0


def action_type(url: str) -> str | None:
    """POST 액션 경로에서 **액션을 뺀** ARM 타입.

    마지막 마디가 액션이므로 떼고 `arm_type`에 넘긴다. 안 떼면 액션이 타입 자리에
    들어가 없는 타입이 나온다.
    """
    trimmed = url.rstrip("/").rsplit("/", 1)[0]
    return arm_type(trimmed)


def parse_tarball(tar: Path, *, type_index: AzureTypeIndex) -> tuple[list[dict], Report]:
    """tarball → 타입별 작업 소요 정보."""
    report = Report()
    # 타입 → 메서드 → 본 값들(모순 판정용)
    seen: dict[str, dict[str, set[bool]]] = defaultdict(lambda: defaultdict(set))
    actions: dict[str, dict[str, bool]] = defaultdict(dict)
    wanted = _latest_stable(tar)
    report.scanned = len(wanted)

    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or member.name not in wanted:
                continue
            raw = archive.extractfile(member).read()
            if b"x-ms-long-running-operation" not in raw:
                continue
            try:
                doc = json.loads(raw)
            except Exception:
                continue
            for url, operations in (doc.get("paths") or {}).items():
                if not isinstance(operations, dict):
                    continue
                type_name = arm_type(url)
                if type_name and type_name.lower() in type_index.by_lower:
                    key = type_index.type_id(type_name)
                    for method in _METHODS:
                        spec = operations.get(method)
                        if isinstance(spec, dict):
                            seen[key][method].add(
                                spec.get("x-ms-long-running-operation") is True
                            )
                elif type_name:
                    report.unknown_types[type_name] += 1

                post = operations.get("post")
                if not isinstance(post, dict):
                    continue
                owner = action_type(url)
                if not owner or owner.lower() not in type_index.by_lower:
                    continue
                name = url.rstrip("/").rsplit("/", 1)[-1]
                if name.startswith("{"):
                    continue  # 액션 이름이 파라미터면 무엇인지 모른다
                actions[type_index.type_id(owner)][name] = (
                    post.get("x-ms-long-running-operation") is True
                )
                report.actions += 1

    records = []
    for type_id in sorted(set(seen) | set(actions)):
        record: dict = {"type_id": type_id}
        for method, field in (("put", "create"), ("delete", "delete"), ("patch", "update")):
            values = seen.get(type_id, {}).get(method)
            if not values:
                continue
            if len(values) > 1:
                # 원본이 스스로 어긋난다 — 고르지 않고 센다.
                report.conflicting[f"{type_id}:{method}"] += 1
                continue
            record[field] = next(iter(values))
        found = actions.get(type_id)
        if found:
            record["actions"] = [
                {"name": name, "long_running": is_lro}
                for name, is_lro in sorted(found.items())
            ]
        if len(record) > 1:
            records.append(record)
    return records, report


def build(output: Path, *, refresh: bool = False) -> dict:
    from capacitykb.parsers.azure import _fetch_relative
    from kbcommon.type_ids import read_azure_index

    source = SOURCES["azure-rest-api-specs"]
    tar = fetch_cached(source.url, f"azure-specs-{source.pin[:12]}.tar.gz", refresh=refresh)
    index_path = _fetch_relative(SOURCES["bicep-types-az"].url, "index.json", refresh=refresh)
    type_index = read_azure_index(json.loads(index_path.read_text(encoding="utf-8")))

    records, report = parse_tarball(tar, type_index=type_index)
    dataset = {
        "_note": (
            "Azure 작업이 오래 걸리는가(x-ms-long-running-operation). true는 비동기라 "
            "완료를 기다려야 하고, false는 응답이 곧 완료다. 값이 없으면 원본이 그 "
            "메서드를 말하지 않은 것이다 — '빠르다'가 아니라 '모른다'. 같은 타입을 "
            "파일마다 다르게 말하는 것은 담지 않았다."
        ),
        "_source": [describe_source_set([tar], source.key)],
        "conflicting": len(report.conflicting),
        "types": records,
    }
    write_dataset(output, dataset, SCHEMA)

    long_create = sum(1 for r in records if r.get("create") is True)
    sync_create = sum(1 for r in records if r.get("create") is False)
    print(
        f"azure-operations: 타입 {len(records):,}종 "
        f"(생성이 오래 걸림 {long_create:,} · 즉시 {sync_create:,} · 액션 {report.actions:,}개)"
    )
    if report.conflicting:
        print(
            f"  원본이 스스로 어긋나 담지 않은 것 {len(report.conflicting)}건: "
            + ", ".join(list(report.conflicting)[:3]),
            file=sys.stderr,
        )
    return dataset
