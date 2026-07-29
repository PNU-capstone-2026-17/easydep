"""terraform-provider-google(생성된 Go 스키마) → 속성 제약.

**왜 Magic Modules YAML이 아니라 생성된 프로바이더인가.**
둘은 같은 파이프라인의 입력과 출력이다. 출력을 쓰는 이유가 셋 있다.

1. **핀을 박을 수 있다.** MM 저장소는 태그가 **0개**이고 하루 3.6건씩 바뀐다.
   프로바이더는 주간 릴리스 태그가 417개 있다.
2. **선언과 현실이 다르다.** MM이 적어 놓은 교차 필드 조건 중 상당수가 생성 과정에서
   **빈 목록으로 증발한다**(중첩 객체의 형제 이름이 루트 기준 해석에 실패하는데 조용히
   버려진다). 실측: ExactlyOneOf 127 · ConflictsWith 53 · RequiredWith 13 · AtLeastOneOf 1.
   출력을 읽으면 **실제로 강제되는 것만** 담긴다 — YAML을 읽으면 현실보다 엄격한 KB가 된다.
3. **YAML에 없는 축이 출력에는 있다.** `customdiff.ForceNewIfChange`가 그것이다.
   `compute_disk.size` · `subnetwork.ip_cidr_range`가 여기 걸리는데, 뜻은
   **"늘리는 건 되고 줄이면 재생성"**이다. 불변/가변 이분법으로는 안 담기는 축이고,
   MM YAML에는 흔적조차 없다(로직이 Go 코드 안에 있다).

**주의 — 이건 API 불변성이 아니다.** `ForceNew`는 "Terraform이 재생성한다"이지
"API가 거부한다"가 아니다. MM 문서가 *"복잡한 경우 차라리 ForceNew로 표시하는 게 낫다"*고
적어 놓아 의도적으로 과다 표시한다. 그래서 표시 문구는 반드시
`"바꾸면 리소스 재생성"`이어야 하고 `"API가 거부한다"`면 거짓이 된다.

정체성(타입 id·속성 경로)은 **KCC를 따른다.** 여기서 뽑은 것은 KCC가 아는 속성에만
붙이고, 못 붙인 것은 버리지 않고 센다.
"""

from __future__ import annotations

import re
import tarfile
from pathlib import Path

from app.core.cloudkb.capacitykb.model import CapacitySet, Constraint
from app.core.cloudkb.kbcommon.type_ids import make_type_id

#: 생성 코드에서 리소스 스키마의 시작.
_SCHEMA_MAP = "map[string]*schema.Schema{"
_FUNC = re.compile(r"^func (Resource[A-Za-z0-9]+)\(\) \*schema\.Resource \{", re.MULTILINE)
_ENTRY = re.compile(r'"([a-z0-9_]+)":\s*\{')

EVIDENCE = "tpg-schema"

#: 속성 본문에서 뽑을 것. 값이 있는 것은 첫 그룹을 쓴다.
_ATTRS = {
    "ForceNew": re.compile(r"^\s*ForceNew:\s*true,", re.MULTILINE),
    "Default": re.compile(r"^\s*Default:\s*(.+?),\s*$", re.MULTILINE),
    "MaxItems": re.compile(r"^\s*MaxItems:\s*(\d+)", re.MULTILINE),
    "MinItems": re.compile(r"^\s*MinItems:\s*(\d+)", re.MULTILINE),
    "Enum": re.compile(r"verify\.ValidateEnum\(\[\]string\{([^}]*)\}\)"),
    "ExactlyOneOf": re.compile(r"ExactlyOneOf:\s*\[\]string\{([^}]*)\}"),
    "AtLeastOneOf": re.compile(r"AtLeastOneOf:\s*\[\]string\{([^}]*)\}"),
    "ConflictsWith": re.compile(r"ConflictsWith:\s*\[\]string\{([^}]*)\}"),
    "RequiredWith": re.compile(r"RequiredWith:\s*\[\]string\{([^}]*)\}"),
    "Computed": re.compile(r"^\s*Computed:\s*true,", re.MULTILINE),
    "Optional": re.compile(r"^\s*Optional:\s*true,", re.MULTILINE),
    "Required": re.compile(r"^\s*Required:\s*true,", re.MULTILINE),
}
_GROUP_KINDS = {
    "ExactlyOneOf": "exactly_one_of",
    "AtLeastOneOf": "at_least_one_of",
    "ConflictsWith": "conflicts_with",
    "RequiredWith": "required_with",
}
#: `customdiff.ForceNewIf("field", ...)` / `ForceNewIfChange("field", pred)`
_FORCE_NEW_IF = re.compile(
    r"customdiff\.ForceNewIf(?:Change)?\(\s*\"([^\"]+)\"\s*,\s*([A-Za-z0-9_.]+)"
)

#: Terraform/Magic Modules 쪽에만 있는 개념. GCP API의 필드가 아니라 옮길 대상이 아니다.
#: `deletion_policy`는 Terraform이 리소스를 지울 때의 동작을 정하는 것이고(309개 리소스),
#: `params`는 MM이 붙이는 부가 블록이다.
_TF_ONLY = {
    "project", "self_link", "id", "timeouts", "labels", "terraform_labels",
    "effective_labels", "annotations", "effective_annotations", "deletion_protection",
    "deletion_policy", "params",
}


def _scan(text: str, i: int) -> int:
    """i가 `{`를 가리킬 때 짝이 맞는 `}` **다음** 위치.

    문자열·주석 안의 중괄호는 안 센다. 생성 코드의 `Description`이 백틱 문자열이고
    그 안에 중괄호가 들어가는 경우가 있어서, 이걸 안 하면 블록이 통째로 어긋난다.
    """
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "`":
            i = text.find("`", i + 1)
            if i < 0:
                return n
            i += 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] in "/*":
            if text[i + 1] == "/":
                i = text.find("\n", i)
                if i < 0:
                    return n
            else:
                j = text.find("*/", i)
                i = n if j < 0 else j + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _parse_schema_map(text: str, open_brace: int, prefix: str, out: dict) -> None:
    """스키마 맵 본문을 훑어 `속성 경로 → 그 속성 자신의 본문`을 모은다."""
    end = _scan(text, open_brace)
    i, stop = open_brace + 1, end - 1
    while i < stop:
        m = _ENTRY.search(text, i, stop)
        if not m:
            return
        entry_open = m.end() - 1
        entry_end = _scan(text, entry_open)
        path = f"{prefix}.{m.group(1)}" if prefix else m.group(1)
        body = text[entry_open + 1 : entry_end - 1]
        nested = body.find(_SCHEMA_MAP)
        # 중첩(Elem) 부분을 잘라내야 자식의 속성값을 부모 것으로 잘못 읽지 않는다
        out[path] = body[:nested] if nested >= 0 else body
        if nested >= 0:
            _parse_schema_map(
                text, entry_open + 1 + nested + len(_SCHEMA_MAP) - 1, path, out
            )
        i = entry_end


def tf_path_to_kcc(path: str) -> str:
    """Terraform 경로를 KCC 속성 경로로.

    `log_config.0.aggregation_interval` → `logConfig.aggregationInterval`
    `.0.`은 Terraform이 중첩 객체를 리스트로 펼치며 붙이는 색인이라 뜻이 없다.
    `name`은 KCC에서 `resourceID`다 — 실측상 CRD 510개 중 483개가 이 이름을 바꿔 쓴다.
    """
    parts = [p for p in path.split(".") if p != "0"]
    out = []
    for p in parts:
        head, *rest = p.split("_")
        out.append(head + "".join(w.capitalize() for w in rest))
    joined = ".".join(out)
    return "resourceID" if joined == "name" else joined


def resource_to_kind(name: str, known: set[str]) -> str | None:
    """프로바이더 리소스 함수 이름을 KCC kind로. 못 찾으면 None."""
    lowered = {k.lower(): k for k in known}
    for cand in (
        name,
        name.removesuffix("s"),
        re.sub(r"Gcp", "GCP", name),
        re.sub(r"Api", "API", name),
        re.sub(r"Iam", "IAM", name),
        re.sub(r"Sql", "SQL", name),
        re.sub(r"Dns", "DNS", name),
    ):
        if cand in known:
            return cand
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


def _enum_values(raw: str) -> list[str]:
    """`"A", "B", ""` → `["A", "B"]`. 끝의 빈 문자열은 "미지정 허용"이지 값이 아니다."""
    return [v for v in re.findall(r'"([^"]*)"', raw) if v]


def _default_value(raw: str):
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


class Report:
    """무엇을 못 붙였는지. **버리되 세는 것**이 규약이다."""

    def __init__(self) -> None:
        self.unmapped_kinds: set[str] = set()
        self.unmapped_paths = 0
        self.tf_only_paths = 0
        self.empty_groups = 0
        self.output_only = 0
        self.force_new_if: list[tuple[str, str, str]] = []
        self.seen: dict[str, set[str]] = {}
        """kind → 프로바이더가 **본** 속성 경로.

        `forcenew`와 짝을 이뤄 쓴다. 프로바이더가 그 속성을 알면서 ForceNew를 안 달았다면
        그건 침묵이 아니라 **"바꿀 수 있다"는 적극적 근거**다. 이게 없으면 낡은 KCC가
        "불변"이라 적어 둔 것을 지울 방법이 없다(`ComputeSubnetwork.purpose`가 실례다).
        """
        self.forcenew: dict[str, set[str]] = {}


def parse_provider(
    tar_path: Path,
    *,
    kcc_kinds: set[str],
    kcc_paths: dict[str, set[str]] | None = None,
) -> tuple[CapacitySet, Report]:
    """프로바이더 타르볼에서 제약을 뽑는다.

    Args:
        kcc_kinds: KCC가 아는 kind 집합. 여기 없는 리소스는 담지 않는다 —
            정체성은 KCC를 따르기로 했으므로.
        kcc_paths: kind별로 KCC가 아는 속성 경로. 주면 그 경로만 담는다.
    """
    capacity = CapacitySet()
    report = Report()

    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            name = member.name
            if not (
                member.isfile()
                and "/google/services/" in name
                and "/resource_" in name
                and name.endswith(".go")
                and "_test" not in name
                and "_sweeper" not in name
            ):
                continue
            text = tar.extractfile(member).read().decode("utf-8", "replace")
            for match in _FUNC.finditer(text):
                kind = resource_to_kind(match.group(1)[len("Resource"):], kcc_kinds)
                if kind is None:
                    report.unmapped_kinds.add(match.group(1))
                    continue
                _emit(text, match, kind, capacity, report, kcc_paths)
    return capacity, report


def _is_output_only(body: str) -> bool:
    """서버가 채우기만 하는 필드인가 (`Computed`이면서 사용자가 못 넣는 것).

    `Computed: true` 하나만으로는 판단하면 안 된다 — `Optional`과 함께 붙으면
    "안 넣으면 서버가 채운다"는 뜻이라 사용자가 넣을 수 있다(`Subnetwork.purpose`가 그렇다).
    """
    return bool(
        _ATTRS["Computed"].search(body)
        and not _ATTRS["Optional"].search(body)
        and not _ATTRS["Required"].search(body)
    )


def _match_kcc_spelling(
    prop: str, known: set[str], lowered: dict[str, str]
) -> str | None:
    """KCC가 쓰는 철자로 맞춰 준다. 못 맞추면 None.

    Terraform은 snake_case라 두문자어의 대소문자 정보를 잃는다. `peering_cidr_range`를
    기계적으로 바꾸면 `peeringCidrRange`가 되지만 KCC는 `peeringCIDRRange`라고 쓴다
    (`locationURI` · `cloudSQL` · `iamRoleID`도 같다). 실측 108건.

    손으로 두문자어 표를 만들지 않는 이유는, 그게 **우리 취향으로 목록을 짜는 일**이라
    새 두문자어가 나올 때마다 조용히 틀리기 때문이다. 대신 **KCC가 실제로 쓰는 철자를
    찾아서** 그대로 쓴다 — 우리가 없는 이름을 지어내는 게 아니라 있는 이름에 붙이는
    것이므로 안전하다.
    """
    if prop in known:
        return prop
    return lowered.get(prop.lower())


def _emit(text, match, kind, capacity, report, kcc_paths) -> None:
    anchor = text.find("Schema: " + _SCHEMA_MAP, match.end())
    if anchor < 0:
        return
    props: dict[str, str] = {}
    _parse_schema_map(text, anchor + len("Schema: " + _SCHEMA_MAP) - 1, "", props)

    type_id = make_type_id("gcp", kind)
    known = kcc_paths.get(kind) if kcc_paths else None
    lowered = {k.lower(): k for k in known} if known is not None else {}

    def add(prop: str, ckind: str, value, note: str | None = None) -> None:
        capacity.add_constraint(
            Constraint(
                type_id=type_id, property=prop, kind=ckind, value=value,
                evidence=EVIDENCE, note=note,
            )
        )

    for tf_path, body in props.items():
        head = tf_path.split(".", 1)[0]
        if head in _TF_ONLY:
            report.tf_only_paths += 1
            continue
        if _is_output_only(body):
            # 서버가 채우는 값이라 "사용자가 넣을 수 있는 값의 제약"이 아니다.
            # KCC도 이런 필드를 spec이 아니라 status에 두므로 애초에 붙을 자리가 없다.
            # 이름 목록을 손으로 만들지 않고 **프로바이더 자신의 표시**로 거른다.
            report.output_only += 1
            continue
        prop = tf_path_to_kcc(tf_path)
        if known is not None:
            prop = _match_kcc_spelling(prop, known, lowered)
            if prop is None:
                report.unmapped_paths += 1
                continue

        report.seen.setdefault(kind, set()).add(prop)
        if _ATTRS["ForceNew"].search(body):
            report.forcenew.setdefault(kind, set()).add(prop)
            add(prop, "mutability", "create_only")
        for key, ckind in (("MaxItems", "max_items"), ("MinItems", "min_items")):
            m = _ATTRS[key].search(body)
            if m:
                add(prop, ckind, int(m.group(1)))
        m = _ATTRS["Enum"].search(body)
        if m:
            values = _enum_values(m.group(1))
            if values:
                add(prop, "enum", values)
        m = _ATTRS["Default"].search(body)
        if m:
            add(prop, "default", _default_value(m.group(1)))
        for key, ckind in _GROUP_KINDS.items():
            m = _ATTRS[key].search(body)
            if not m:
                continue
            members = [tf_path_to_kcc(v) for v in re.findall(r'"([^"]+)"', m.group(1))]
            if not members:
                # MM이 선언했는데 생성 과정에서 증발한 것. 담지 않되 센다 —
                # 담으면 현실보다 엄격한 KB가 되고, 안 세면 그 사실이 사라진다.
                report.empty_groups += 1
                continue
            add(prop, ckind, members)

    # 조건부 불변: "줄이면 재생성" 류. 함수 본문 전체에서 찾는다(스키마 밖에 있다).
    body_end = _scan(text, match.end() - 1)
    for tf_path, pred in _FORCE_NEW_IF.findall(text[match.end():body_end]):
        prop = tf_path_to_kcc(tf_path)
        if known is not None:
            prop = _match_kcc_spelling(prop, known, lowered)
            if prop is None:
                report.unmapped_paths += 1
                continue
        add(prop, "mutability", "update_restricted",
            note=f"recreated only conditionally (predicate function: {pred})")
        report.force_new_if.append((kind, prop, pred))
