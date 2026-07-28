"""terraform-provider-aws(생성된 Go 스키마) → AWS 속성 제약.

**왜 필요한가.** CloudFormation은 조건부를 표현할 방법이 없다 — 메타스키마가
`if`/`then`을 금지하고, `oneOf`는 값이 아니라 존재 조건만 말한다. 그래서 우리 AWS
데이터에는 **교차 필드 조건이 0건**이고 **조건부 불변도 0건**이었다.
프로바이더에는 둘 다 있다(실측 v6.55.0: 교차 조건 1,219 · `ForceNewIf` 56).

**google 프로바이더와 성격이 다르다.** 생성 코드 비율을 재보면 google 100% /
aws 19%다. google은 Magic Modules가 생성해서 "선언은 있는데 생성 중 증발"이
문제였지만(빈 목록 194건), aws는 **사람이 쓴 Go**라 빈 목록이 **0건**이다.
대신 손 큐레이션이라 **다르게 틀릴 수 있다** — 그래서 근거 라벨을 나눈다.

**주의 — 이건 API 불변성이 아니다.** `ForceNew`는 "Terraform이 재생성한다"이고
`validation.IntBetween`은 **프로바이더 작성자의 주장**이다. CFN이라는 독립 소스가
있으므로 어긋나면 그 사실을 세어 보고한다.

**이름 잇기.** TF 리소스 이름과 CFN 타입 이름은 규칙이 다르다(`aws_prometheus_scraper`
→ `AWS::APS::Scraper`, `aws_vpc_dhcp_options` → `AWS::EC2::DHCPOptions`). 프로바이더가
가진 `names/data/names_data.hcl`의 `arn_namespace`로 서비스를 잇고 나머지는 이름
후보로 맞춘다. **실측 매칭률 50%**이고, 못 맞춘 것은 버리되 센다 — 상당수는 매핑
실패가 아니라 **CFN에 그 리소스가 아예 없는 것**이다(`aws_account_alternate_contact` 등).
"""

from __future__ import annotations

import re
import sys
import tarfile
from collections import Counter, defaultdict
from pathlib import Path

from app.deployment.capacitykb.model import CapacitySet, Constraint
from app.deployment.capacitykb.parsers.tpg import _parse_schema_map, _scan
from app.deployment.kbcommon.fetch import describe_source_set
from app.deployment.kbcommon.sources import SOURCES

EVIDENCE = "tpaws-schema"
_SCHEMA_MAP = "map[string]*schema.Schema{"
#: 리소스가 스스로 밝히는 Terraform 타입 이름.
_ANNOTATION = re.compile(r'@(?:SDK|Framework)Resource\(\s*"([a-z0-9_]+)"')
#: `func resourceX() *schema.Resource {` — 애너테이션 바로 아래에 온다.
_FUNC = re.compile(r"^func [Rr]esource\w*\(\)\s*\*schema\.Resource\s*\{", re.MULTILINE)

_SERVICE = re.compile(r'service\s+"([a-z0-9_]+)"\s*\{(.*?)\n\}', re.DOTALL)
_ARN_NS = re.compile(r'arn_namespace\s*=\s*"([^"]+)"')

_ATTRS = {
    "ForceNew": re.compile(r"^\s*ForceNew:\s*true,", re.MULTILINE),
    "Computed": re.compile(r"^\s*Computed:\s*true,", re.MULTILINE),
    "Optional": re.compile(r"^\s*Optional:\s*true,", re.MULTILINE),
    "Required": re.compile(r"^\s*Required:\s*true,", re.MULTILINE),
    "MaxItems": re.compile(r"^\s*MaxItems:\s*(\d+)", re.MULTILINE),
    "MinItems": re.compile(r"^\s*MinItems:\s*(\d+)", re.MULTILINE),
    "IntBetween": re.compile(r"validation\.IntBetween\((-?\d+),\s*(-?\d+)\)"),
    "StringInSlice": re.compile(r"validation\.StringInSlice\(\[\]string\{([^}]*)\}"),
    "ExactlyOneOf": re.compile(r"ExactlyOneOf:\s*\[\]string\{([^}]*)\}"),
    "AtLeastOneOf": re.compile(r"AtLeastOneOf:\s*\[\]string\{([^}]*)\}"),
    "ConflictsWith": re.compile(r"ConflictsWith:\s*\[\]string\{([^}]*)\}"),
    "RequiredWith": re.compile(r"RequiredWith:\s*\[\]string\{([^}]*)\}"),
}
_GROUPS = {
    "ExactlyOneOf": "exactly_one_of",
    "AtLeastOneOf": "at_least_one_of",
    "ConflictsWith": "conflicts_with",
    "RequiredWith": "required_with",
}
_FORCE_NEW_IF = re.compile(
    r"customdiff\.ForceNewIf(?:Change)?\(\s*\"([^\"]+)\"\s*,\s*([A-Za-z0-9_.]+)"
)

#: Terraform 쪽 개념이라 CFN 속성으로 옮길 게 아니다.
_TF_ONLY = {"id", "arn", "tags", "tags_all", "timeouts", "region"}


class Report:
    def __init__(self) -> None:
        self.unmapped: list[tuple[str, str]] = []
        self.no_schema = 0
        self.framework = 0
        """Plugin Framework 경로로 읽은 리소스 수."""
        self.output_only = 0
        self.tf_only = 0
        self.force_new_if: list[tuple[str, str, str]] = []


def read_service_namespaces(hcl: str) -> dict[str, str]:
    """`names_data.hcl` → {프로바이더 패키지: arn_namespace}.

    프로바이더가 **자기 서비스 이름표를 갖고 있다.** 손으로 표를 만들지 않는다 —
    TF 디렉터리(`amp`)·TF 접두사(`prometheus`)·CFN 서비스(`APS`)가 셋 다 다르고,
    그 대응을 아는 건 프로바이더 자신뿐이다.
    """
    out: dict[str, str] = {}
    for name, body in _SERVICE.findall(hcl):
        match = _ARN_NS.search(body)
        if match:
            out[name] = match.group(1)
    return out


def _candidates(service: str, namespace: str | None, tf_name: str):
    tail = tf_name[len("aws_"):]
    for prefix in (service, namespace):
        if prefix and tail.startswith(prefix + "_"):
            tail = tail[len(prefix) + 1:]
            break
    camel = "".join(w.capitalize() for w in tail.split("_"))
    yield camel
    yield "DB" + camel          # aws_rds_cluster → DBCluster
    yield camel + "s"
    if "_" in tail:             # 접두사를 못 뗀 경우(aws_vpc_endpoint in ec2)
        rest = "".join(w.capitalize() for w in tail.split("_")[1:])
        yield rest
        yield "DB" + rest


def resolve_type(
    service: str, tf_name: str, *, namespaces: dict[str, str], cfn: dict[str, dict[str, str]]
) -> str | None:
    """TF 리소스 이름 → CFN 타입 id. 못 맞추면 None."""
    for key in (service, namespaces.get(service)):
        pool = cfn.get(key or "")
        if not pool:
            continue
        for cand in _candidates(service, namespaces.get(service), tf_name):
            hit = pool.get(cand.lower())
            if hit:
                return hit
    return None


def index_cfn(type_ids: set[str]) -> dict[str, dict[str, str]]:
    """`aws::AWS::EC2::Instance` → {ec2: {instance: 'aws::AWS::EC2::Instance'}}."""
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for type_id in type_ids:
        parts = type_id.split("::")
        if len(parts) == 4:  # aws::AWS::EC2::Instance
            out[parts[2].lower()][parts[3].lower()] = type_id
    return out


def _is_output_only(body: str) -> bool:
    """서버가 채우기만 하는 필드인가. `Computed` 하나로 판단하면 안 된다 —
    `Optional`과 함께면 "안 넣으면 서버가 채운다"라 사용자가 넣을 수 있다."""
    return bool(
        _ATTRS["Computed"].search(body)
        and not _ATTRS["Optional"].search(body)
        and not _ATTRS["Required"].search(body)
    )


def tf_path_to_cfn(path: str) -> str:
    """`root_block_device.0.volume_size` → `RootBlockDevice.VolumeSize`.

    CFN 속성은 PascalCase다. `.0.`은 Terraform이 중첩 객체를 리스트로 펼치며 붙이는
    색인이라 뜻이 없다.
    """
    parts = [p for p in path.split(".") if p != "0"]
    return ".".join("".join(w.capitalize() for w in p.split("_")) for p in parts)


def parse_provider(tar_path: Path, *, cfn_types: set[str]) -> tuple[CapacitySet, Report]:
    capacity = CapacitySet()
    report = Report()
    cfn = index_cfn(cfn_types)

    with tarfile.open(tar_path) as tar:
        hcl_member = next(
            (m for m in tar.getmembers() if m.name.endswith("names/data/names_data.hcl")),
            None,
        )
        namespaces = (
            read_service_namespaces(tar.extractfile(hcl_member).read().decode("utf-8", "replace"))
            if hcl_member
            else {}
        )
        csv_member = next(
            (m for m in tar.getmembers() if m.name.endswith("names/attr_constants.csv")),
            None,
        )
        consts = (
            read_attr_constants(tar.extractfile(csv_member).read().decode("utf-8", "replace"))
            if csv_member
            else {}
        )
        for member in tar.getmembers():
            name = member.name
            if not (
                member.isfile()
                and "/internal/service/" in name
                and name.endswith(".go")
                and "_test" not in name
            ):
                continue
            text = tar.extractfile(member).read().decode("utf-8", "replace")
            service = name.split("/internal/service/")[1].split("/")[0]
            for match in _ANNOTATION.finditer(text):
                tf_name = match.group(1)
                type_id = resolve_type(
                    service, tf_name, namespaces=namespaces, cfn=cfn
                )
                if type_id is None:
                    report.unmapped.append((service, tf_name))
                    continue
                # **애너테이션 위치부터** 스키마를 찾는다. 파일 단위로 첫 스키마를
                # 찾으면 한 파일에 리소스가 여럿일 때 서로 오염된다 — 처음 구현이
                # 그랬고, 수확이 1/6로 줄고 값이 엉뚱한 타입에 붙었다.
                if not _emit(text, match.end(), type_id, capacity, report):
                    # SDK 스키마가 없으면 Plugin Framework 모양으로 다시 본다.
                    # 새 AWS 리소스가 그쪽으로 가고 있어서, 안 읽으면 공백이 계속 커진다.
                    if _emit_framework(text, type_id, capacity, report, consts):
                        report.framework += 1
                        report.no_schema -= 1
    return capacity, report


def _emit(text: str, start: int, type_id: str, capacity: CapacitySet, report: Report) -> bool:
    # 애너테이션 다음의 리소스 함수 본문 안에서만 찾는다.
    func = _FUNC.search(text, start)
    if func is None:
        report.no_schema += 1
        return False
    body_end = _scan(text, func.end() - 1)
    anchor = text.find("Schema: " + _SCHEMA_MAP, func.end(), body_end)
    if anchor < 0:
        # Plugin Framework 리소스는 스키마 모양이 완전히 다르다(Attributes 맵).
        # 여기서는 안 읽고 센다 — 억지로 읽으면 조용히 틀린 걸 담게 된다.
        report.no_schema += 1
        return False
    props: dict[str, str] = {}
    _parse_schema_map(text, anchor + len("Schema: " + _SCHEMA_MAP) - 1, "", props)

    def add(prop: str, kind: str, value, note: str | None = None) -> None:
        capacity.add_constraint(
            Constraint(type_id=type_id, property=prop, kind=kind, value=value,
                       evidence=EVIDENCE, note=note)
        )

    for tf_path, body in props.items():
        if tf_path.split(".", 1)[0] in _TF_ONLY:
            report.tf_only += 1
            continue
        if _is_output_only(body):
            report.output_only += 1
            continue
        prop = tf_path_to_cfn(tf_path)

        if _ATTRS["ForceNew"].search(body):
            add(prop, "mutability", "create_only")
        for key, kind in (("MaxItems", "max_items"), ("MinItems", "min_items")):
            match = _ATTRS[key].search(body)
            if match:
                add(prop, kind, int(match.group(1)))
        match = _ATTRS["IntBetween"].search(body)
        if match:
            add(prop, "min", int(match.group(1)))
            add(prop, "max", int(match.group(2)))
        match = _ATTRS["StringInSlice"].search(body)
        if match:
            values = re.findall(r'"([^"]+)"', match.group(1))
            if values:
                add(prop, "enum", values)
        for key, kind in _GROUPS.items():
            match = _ATTRS[key].search(body)
            if not match:
                continue
            members = [tf_path_to_cfn(v) for v in re.findall(r'"([^"]+)"', match.group(1))]
            if members:
                add(prop, kind, members)

    for tf_path, pred in _FORCE_NEW_IF.findall(text[func.end():body_end]):
        prop = tf_path_to_cfn(tf_path)
        add(prop, "mutability", "update_restricted",
            note=f"recreated only conditionally (predicate function: {pred})")
        report.force_new_if.append((type_id, prop, pred))
    return True


def build(output: Path, *, refresh: bool = False, cfn_types: set[str] | None = None) -> CapacitySet:
    from app.deployment.kbcommon.fetch import fetch_cached

    source = SOURCES["tpaws-provider"]
    tar_path = fetch_cached(source.url, f"tpaws-{source.pin}.tar.gz", refresh=refresh)

    if cfn_types is None:
        import json
        graph = Path("output") / "aws-graph.json"
        cfn_types = {
            n["id"] for n in json.loads(graph.read_text(encoding="utf-8")).get("nodes", [])
            if n.get("provider") == "aws"
        }

    capacity, report = parse_provider(tar_path, cfn_types=cfn_types)
    capacity.provenance = [describe_source_set([tar_path], source.key)]
    capacity.coverage = [{
        "provider": "aws",
        "types": len({c.type_id for c in capacity.constraints}),
        "note": (
            "the terraform-provider-aws SDK schema. the cross-field conditions and "
            "conditional immutability that CloudFormation cannot express live here. "
            "**this is Terraform's judgment, not the API's** — ForceNew means 'it "
            "recreates', and IntBetween is the provider author's claim."
        ),
    }]

    kinds = Counter(c.kind for c in capacity.constraints)
    print(f"tpaws: 제약 {len(capacity.constraints):,}건 / "
          f"{len({c.type_id for c in capacity.constraints})}종 "
          f"(그중 Plugin Framework {report.framework}종) — {dict(kinds)}")
    print(
        f"  안 담은 것: CFN 타입에 못 이은 리소스 {len(report.unmapped)}종 · "
        f"스키마를 못 찾음 {report.no_schema}종 · "
        f"서버가 채우는 필드 {report.output_only:,} · Terraform 전용 {report.tf_only:,}",
        file=sys.stderr,
    )
    if report.force_new_if:
        print(f"  조건부 불변 {len(report.force_new_if)}건:", file=sys.stderr)
        for type_id, prop, pred in report.force_new_if[:6]:
            print(f"    - {type_id.split('::', 1)[1]}.{prop} ({pred})", file=sys.stderr)
    capacity.save(output)
    return capacity

# ---------------------------------------------------------------- Plugin Framework
#
# 새 AWS 리소스는 SDK가 아니라 Plugin Framework로 간다(실측 v6.55.0: 애너테이션
# 1,679개 중 Framework가 440개). 스키마 모양이 완전히 달라서 SDK 파서로는 못 읽고,
# **안 읽으면 공백이 계속 커진다.**
#
#     response.Schema = schema.Schema{
#         Attributes: map[string]schema.Attribute{
#             "workspace_id": schema.StringAttribute{
#                 Required: true,
#                 PlanModifiers: []planmodifier.String{
#                     stringplanmodifier.RequiresReplace(),   ← ForceNew에 해당
#                 },
#             },
#         },
#         Blocks: map[string]schema.Block{ ... },
#     }
#
#: `resp.Schema =` 와 `response.Schema =` 둘 다 쓰인다(284:2로 후자가 다수다).
_FW_SCHEMA = re.compile(r"(?:resp|response)\.Schema\s*=\s*schema\.Schema\{")
_FW_MAP = re.compile(r"(?:Attributes|Blocks):\s*map\[string\]schema\.(?:Attribute|Block)\{")
#: 키가 문자열 리터럴이거나 **Go 상수**다(`names.AttrDestination`).
_FW_ENTRY = re.compile(r'(?:"([a-z0-9_]+)"|names\.Attr(\w+)):\s*schema\.\w+\{')

_FW_ATTRS = {
    # RequiresReplace가 SDK의 ForceNew에 해당한다.
    "RequiresReplace": re.compile(r"RequiresReplace(?:IfConfigured)?\(\)"),
    "Required": re.compile(r"^\s*Required:\s*true,", re.MULTILINE),
    "Computed": re.compile(r"^\s*Computed:\s*true,", re.MULTILINE),
    "Optional": re.compile(r"^\s*Optional:\s*true,", re.MULTILINE),
    "OneOf": re.compile(r"\w+validator\.OneOf\(([^)]*)\)"),
    "Between": re.compile(r"\w+validator\.Between\((-?\d+),\s*(-?\d+)\)"),
    "AtLeast": re.compile(r"\w+validator\.AtLeast\((-?\d+)\)"),
    "AtMost": re.compile(r"\w+validator\.AtMost\((-?\d+)\)"),
    "SizeAtMost": re.compile(r"listvalidator\.SizeAtMost\((\d+)\)"),
    "SizeAtLeast": re.compile(r"listvalidator\.SizeAtLeast\((\d+)\)"),
}


def read_attr_constants(csv_text: str) -> dict[str, str]:
    """`names/attr_constants.csv` → {상수 접미사: 속성 이름}.

    `names.AttrDestination`이 `"destination"`이다. 프로바이더가 이 표를 갖고 있으므로
    상수 이름을 우리가 짐작해 풀지 않는다.
    """
    out: dict[str, str] = {}
    for line in csv_text.splitlines():
        parts = line.split(",")
        if len(parts) == 2 and parts[0] and parts[1]:
            out[parts[1].strip()] = parts[0].strip()
    return out


def _fw_walk(text: str, open_brace: int, prefix: str, out: dict, consts: dict) -> None:
    """`Attributes:`/`Blocks:` 맵을 훑어 속성 경로 → 그 속성 자신의 본문."""
    end = _scan(text, open_brace)
    i, stop = open_brace + 1, end - 1
    while i < stop:
        m = _FW_ENTRY.search(text, i, stop)
        if not m:
            return
        literal, const = m.group(1), m.group(2)
        name = literal or consts.get(const or "")
        entry_open = m.end() - 1
        entry_end = _scan(text, entry_open)
        if name:
            path = f"{prefix}.{name}" if prefix else name
            body = text[entry_open + 1 : entry_end - 1]
            nested = _FW_MAP.search(body)
            out[path] = body[: nested.start()] if nested else body
            if nested:
                _fw_walk(text, entry_open + 1 + nested.end() - 1, path, out, consts)
        i = entry_end


def _emit_framework(
    text: str, type_id: str, capacity: CapacitySet, report: Report, consts: dict
) -> bool:
    """Framework 스키마에서 제약을 뽑는다. 스키마를 못 찾으면 False."""
    schema = _FW_SCHEMA.search(text)
    if schema is None:
        return False
    body_end = _scan(text, schema.end() - 1)
    props: dict[str, str] = {}
    for m in _FW_MAP.finditer(text, schema.end(), body_end):
        _fw_walk(text, m.end() - 1, "", props, consts)

    def add(prop: str, kind: str, value) -> None:
        capacity.add_constraint(
            Constraint(type_id=type_id, property=prop, kind=kind, value=value,
                       evidence=EVIDENCE)
        )

    for tf_path, body in props.items():
        if tf_path.split(".", 1)[0] in _TF_ONLY:
            report.tf_only += 1
            continue
        if (_FW_ATTRS["Computed"].search(body)
                and not _FW_ATTRS["Optional"].search(body)
                and not _FW_ATTRS["Required"].search(body)):
            report.output_only += 1
            continue
        prop = tf_path_to_cfn(tf_path)
        if _FW_ATTRS["RequiresReplace"].search(body):
            add(prop, "mutability", "create_only")
        m = _FW_ATTRS["OneOf"].search(body)
        if m:
            values = re.findall(r'"([^"]+)"', m.group(1))
            if values:
                add(prop, "enum", values)
        m = _FW_ATTRS["Between"].search(body)
        if m:
            add(prop, "min", int(m.group(1)))
            add(prop, "max", int(m.group(2)))
        for key, kind in (("AtLeast", "min"), ("AtMost", "max"),
                          ("SizeAtLeast", "min_items"), ("SizeAtMost", "max_items")):
            m = _FW_ATTRS[key].search(body)
            if m:
                add(prop, kind, int(m.group(1)))
    return True
