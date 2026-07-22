"""alicloud·tencent Terraform provider → 두 CSP의 리소스 타입·제약.

**이건 새 프로바이더를 여는 작업이다.** 다른 소스들은 이미 있는 축(aws·azure·gcp)에
데이터를 더했지만, 여기는 대상 자체가 없었다 — 실측으로 graphkb 노드가
alibaba **0개**, tencent **0개**였다. costkb·perfkb에는 두 CSP의 **스펙과 가격이**
있는데(alibaba 2,494 · tencent 2,865) 리소스 타입 제약은 한 건도 없었다.

## 타입 이름은 Terraform 것이다 — 그 사실을 이름에 남긴다

다른 프로바이더는 벤더 공식 타입 이름을 쓴다(`AWS::EC2::Subnet`,
`Microsoft.Network/virtualNetworks`). 이 둘은 그런 공개 스키마가 없어서
**Terraform 리소스 이름**(`alicloud_instance`)을 쓸 수밖에 없다.

    alibaba::alicloud_instance      tencent::tencentcloud_instance

Alibaba에는 ROS 타입(`ALIYUN::ECS::Instance`)이, Tencent에는 TAT 타입이 따로 있지만
우리에게는 그 소스가 없다. **id에 `alicloud_`가 그대로 보이는 것이 의도다** — 이게
Terraform의 이름이라는 사실이 id에 드러나야, 나중에 공식 스키마가 생겼을 때 무엇을
바꿔야 하는지 알 수 있다.

## 무엇이 나오나 (alicloud v1.285.0 실측)

    provider.go 등록 리소스 1,161종
    ForceNew 4,115 · Required 3,992 · MaxItems 669 ·
    StringInSlice 1,065 · IntBetween 110

## 주의 — Terraform의 주장이지 API의 강제가 아니다

`tpaws`·`tpg`에 적어 둔 것과 같다. `ForceNew`는 "Terraform이 재생성한다"이고
`validation.*`은 **프로바이더 작성자의 주장**이다. AWS는 CFN이라는 독립 소스가 있어
어긋남을 셀 수 있었지만, **여기는 대조할 짝이 없다.** 단일 소스라는 사실을 산출물에
적어 둔다.

## core 매핑은 아직 없다

`core::vNet` ↔ `alicloud_vpc` 대응은 **손 검수가 필요한 별도 작업**이다
(`graphkb/parsers/core_vendor_map.json`이 aws·azure·gcp에 대해 하는 일). 그게 없으면
"알리바바에서 VPC 만들려면?" 같은 CSP 중립 질의는 여전히 답하지 못한다.
지금 열리는 것은 **"alicloud_instance에 어떤 제약이 있나"**까지다.
"""

from __future__ import annotations

import re
import sys
import tarfile
from collections import Counter
from pathlib import Path

from capacitykb.model import CapacitySet, Constraint
from capacitykb.parsers.tpg import _parse_schema_map, _scan
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

EVIDENCE = "tpcsp-schema"

#: 프로바이더별 설정. `prefix`는 TF 리소스 이름 접두사, `provider`는 우리 id 네임스페이스.
PROVIDERS = {
    "alicloud": {"source": "tp-alicloud", "prefix": "alicloud_", "provider": "alibaba"},
    "tencent": {"source": "tp-tencent", "prefix": "tencentcloud_", "provider": "tencent"},
}

_SCHEMA_MAP = "map[string]*schema.Schema{"

#: `func resourceAliCloudInstance() *schema.Resource {` — 리소스 함수 정의.
#: 파일마다 이걸로 한 번만 훑고 등록표와 교집합을 취한다.
_RESOURCE_FUNC = re.compile(r"^func (\w+)\(\)\s*\*schema\.Resource\s*\{", re.M)

#: Terraform이 만들어내는 칸이라 클라우드 리소스의 속성이 아니다.
_TF_ONLY = {"id", "timeouts", "tags", "region", "count", "provider", "lifecycle"}

_ATTRS = {
    "ForceNew": re.compile(r"^\s*ForceNew:\s*true,", re.M),
    "Computed": re.compile(r"^\s*Computed:\s*true,", re.M),
    "Optional": re.compile(r"^\s*Optional:\s*true,", re.M),
    "Required": re.compile(r"^\s*Required:\s*true,", re.M),
    "MaxItems": re.compile(r"^\s*MaxItems:\s*(\d+)", re.M),
    "MinItems": re.compile(r"^\s*MinItems:\s*(\d+)", re.M),
    "IntBetween": re.compile(r"validation\.IntBetween\((-?\d+),\s*(-?\d+)\)"),
    "StringInSlice": re.compile(r"validation\.StringInSlice\(\[\]string\{([^}]*)\}", re.S),
}


class Report:
    def __init__(self) -> None:
        self.registered = 0
        self.parsed = 0
        self.no_schema = 0
        self.tf_only = 0
        self.output_only = 0
        self.kinds: Counter = Counter()


def _registration(prefix: str) -> re.Pattern:
    """provider.go의 등록표에서 (리소스 이름, 함수명).

    파일명(`resource_alicloud_*.go`)으로 찾으면 실제보다 많이 잡힌다(실측 2,270 파일
    vs 등록 1,161종) — 헬퍼·구버전 파일이 섞이기 때문이다.

    **두 저장소의 표기가 다르다.** alicloud는 함수를 그대로 적고
    (`"alicloud_vpc": resourceAliCloudVpc()`), tencent는 패키지를 앞에 붙인다
    (`"tencentcloud_vpc": vpc.ResourceTencentCloudVpc()`). 점을 허용하지 않으면
    tencent가 **0건**이 된다(실측). 함수 정의 자체는 패키지 없이 쓰이므로
    마지막 마디만 쓴다.
    """
    return re.compile(rf'"({re.escape(prefix)}[a-z0-9_]+)":\s*([\w.]+)\(\)')


def _is_output_only(body: str) -> bool:
    """`Computed: true`만 있고 입력 표시가 없으면 응답 전용 칸이다."""
    return bool(_ATTRS["Computed"].search(body)) and not (
        _ATTRS["Optional"].search(body) or _ATTRS["Required"].search(body)
    )


def parse_provider(tar_path: Path, *, key: str) -> tuple[CapacitySet, Report]:
    """provider tarball → 리소스 타입별 제약."""
    config = PROVIDERS[key]
    capacity = CapacitySet()
    report = Report()
    registration = _registration(config["prefix"])

    # 1차: 등록표(어느 함수가 어느 리소스인가)와 파일 내용을 한 번에 모은다.
    func_of: dict[str, str] = {}
    sources: list[str] = []
    with tarfile.open(tar_path, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".go"):
                continue
            if "/vendor/" in member.name or member.name.endswith("_test.go"):
                continue
            try:
                text = archive.extractfile(member).read().decode("utf-8", "replace")
            except Exception:
                continue
            if "provider.go" in member.name.rsplit("/", 1)[-1]:
                for name, func in registration.findall(text):
                    short = func.rsplit(".", 1)[-1]
                    # 데이터소스는 리소스가 아니다 — 만들 수 있는 것이 아니라 조회다.
                    if short.lower().startswith("datasource"):
                        continue
                    func_of[short] = name
            if _SCHEMA_MAP in text:
                sources.append(text)
    report.registered = len(func_of)

    # 2차: **파일마다 한 번만 훑는다.** 파일 × 등록함수로 돌면
    # 1,879 × 1,161 = 218만 번 정규식 검색이라 10분을 넘긴다(실측). 파일에서
    # 리소스 함수 정의를 먼저 뽑고 등록표와 교집합을 취하면 파일 수만큼만 돈다.
    seen: set[str] = set()
    for text in sources:
        for found in _RESOURCE_FUNC.finditer(text):
            func = found.group(1)
            tf_name = func_of.get(func)
            if tf_name is None or tf_name in seen:
                continue
            body_end = _scan(text, found.end() - 1)
            anchor = text.find("Schema: " + _SCHEMA_MAP, found.end(), body_end)
            if anchor < 0:
                continue
            seen.add(tf_name)
            props: dict[str, str] = {}
            _parse_schema_map(
                text, anchor + len("Schema: " + _SCHEMA_MAP) - 1, "", props
            )
            type_id = f"{config['provider']}::{tf_name}"
            if _emit(props, type_id, capacity, report):
                report.parsed += 1
    report.no_schema = report.registered - report.parsed
    return capacity, report


def _emit(props: dict[str, str], type_id: str, capacity: CapacitySet, report: Report) -> bool:
    def add(prop: str, kind: str, value) -> None:
        report.kinds[kind] += 1
        capacity.add_constraint(
            Constraint(
                type_id=type_id, property=prop, kind=kind, value=value, evidence=EVIDENCE
            )
        )

    emitted = False
    for path, body in props.items():
        if path.split(".", 1)[0] in _TF_ONLY:
            report.tf_only += 1
            continue
        if _is_output_only(body):
            report.output_only += 1
            continue
        if _ATTRS["ForceNew"].search(body):
            add(path, "mutability", "create_only")
            emitted = True
        if _ATTRS["Required"].search(body):
            add(path, "required", True)
            emitted = True
        for key, kind in (("MaxItems", "max_items"), ("MinItems", "min_items")):
            found = _ATTRS[key].search(body)
            if found:
                add(path, kind, int(found.group(1)))
                emitted = True
        found = _ATTRS["IntBetween"].search(body)
        if found:
            add(path, "min", int(found.group(1)))
            add(path, "max", int(found.group(2)))
            emitted = True
        found = _ATTRS["StringInSlice"].search(body)
        if found:
            values = re.findall(r'"([^"]+)"', found.group(1))
            if values:
                add(path, "enum", values)
                emitted = True
    return emitted


def write_graph_nodes(
    types: set[str], provider: str, path: Path, provenance: list[dict] | None = None
) -> int:
    """제약이 붙은 타입을 graphkb 노드로도 남긴다.

    **왜 여기서 만드나.** `kbcommon verify`의 capacity-joins-graph는 모든 제약의
    `type_id`가 graphkb 노드로 이어지길 요구한다. aws·azure·gcp는 그래프 파서가
    따로 있어 노드가 먼저 생기지만, 이 두 CSP는 **타입 목록의 출처가 이 파서뿐**이라
    여기서 같이 만들지 않으면 조인이 깨진다(실측: alibaba 1,590건 전부 실패).

    엣지는 만들지 않는다 — Terraform 스키마의 참조 관계를 타입으로 잇는 일은
    별도 작업이고, 짐작으로 이으면 없는 의존성을 만들어낸다.
    """
    import json

    nodes = [
        {
            "id": type_id,
            "layer": "vendor",
            "provider": provider,
            "kind": "resource_type",
            "display_name": type_id.split("::", 1)[-1],
            "source": "tpcsp-schema",
        }
        for type_id in sorted(types)
    ]
    # **출처를 반드시 함께 쓴다.** 저장소에 커밋되는 산출물이라 어느 소스 어느
    # 버전에서 나왔는지가 파일 안에 남아야 한다(tests/test_bundled_artifacts.py가
    # 이걸 강제한다 — 처음엔 빠뜨렸고 그 테스트가 잡았다).
    data: dict = {"nodes": nodes, "edges": []}
    if provenance:
        data["_source"] = provenance
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return len(nodes)


def build(output: Path, *, key: str, refresh: bool = False) -> CapacitySet:
    config = PROVIDERS[key]
    source = SOURCES[config["source"]]
    tar = fetch_cached(source.url, f"{config['source']}-{source.pin}.tar.gz", refresh=refresh)

    capacity, report = parse_provider(tar, key=key)
    types = {c.type_id for c in capacity.constraints}
    capacity.provenance = [describe_source_set([tar], source.key)]
    capacity.coverage = [{
        "provider": config["provider"],
        "types": len(types),
        "type_ids": sorted(types),
        "note": (
            f"{key} Terraform provider의 스키마. 타입 이름이 Terraform 것이다 — "
            "이 CSP에는 우리가 쓸 공개 리소스 스키마가 없다. **대조할 짝이 없는 "
            "단일 소스**라 ForceNew·validation은 프로바이더 작성자의 주장이며 "
            "API가 그대로 강제한다는 보장은 없다."
        ),
    }]

    print(
        f"{key}: 제약 {len(capacity.constraints):,}건 · 타입 {len(types):,}종 "
        f"(등록 {report.registered:,}종 중 {report.parsed:,}종 파싱)"
    )
    print("  종류: " + ", ".join(f"{k} {v:,}" for k, v in report.kinds.most_common()))
    if report.no_schema:
        print(
            f"  스키마를 못 읽은 리소스 {report.no_schema:,}종 "
            "(Plugin Framework 등 형태가 다른 것)",
            file=sys.stderr,
        )
    capacity.save(output)

    graph_path = output.with_name(f"{config['provider']}-graph.json")
    count = write_graph_nodes(
        types, config["provider"], graph_path, capacity.provenance
    )
    print(f"  graphkb 노드 {count:,}개도 함께 씀 ({graph_path.name}) — 엣지는 없음")
    return capacity
