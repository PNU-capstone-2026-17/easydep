"""Azure 서비스 쿼터 → azure-docs의 `includes/*-limits.md` 마크다운 표 파싱.

세 클라우드 중 **Azure만 자격증명 없이 기계 판독이 가능**하다:
- AWS: 공식 공개 기본 쿼터 데이터셋 없음(Service Quotas API는 자격증명 필요).
  대안 후보 awslimitchecker는 AGPL + 2021년 이후 정체 → Phase 2에서 큐레이션.
- GCP: 문서 저장소 자체가 비공개(HTML만) → Phase 2에서 큐레이션.
- Azure: `MicrosoftDocs/azure-docs`가 네이티브 마크다운 파이프 표를 CC-BY-4.0으로
  공개하고 활발히 갱신한다.

표는 두 형태다:
    | Resource | Limit |                        → 값 하나 (default)
    | Resource | Default limit | Maximum limit |  → 기본값/최대값 분리

실측 함정(전부 처리함): 천단위 콤마(`1,000`), 각주(`<sup>1</sup>`), 셀 안의 마크다운
링크, 비수치 값(`Contact support`, `/28`, `256 * N (N is number of NICs on VM)`,
`500,000, up to 1,000,000 for two or more NICs.`).

**타임스탬프는 기록하지 않는다** — 산출물을 재현 가능하게 유지하기 위함이다
(신선도는 `--refresh`로 관리).
"""

from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from capacitykb.model import CapacitySet, Quota
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.invariants import announce
from kbcommon.sources import SOURCES

DEFAULT_BASE_URL = SOURCES["azure-limits-doc"].url
#: 기본은 **전체**다(`includes/*-limits.md` 82개). 예전에는 두 개만 읽었는데,
#: 그 둘을 고른 이유가 "코어 리소스를 덮는다"는 우리 판단이었을 뿐이라
#: 나머지 80개 문서의 쿼터는 물어봐도 "없음"이 나왔다. 좁히려면 --includes.
DEFAULT_INCLUDES: tuple[str, ...] = ()

#: 파일 목록을 얻을 트리 API. 핀은 `azure-limits-doc`의 커밋 SHA와 같은 것을 쓴다 —
#: 목록과 내용이 다른 시점이면 "있다고 한 파일이 없는" 상태가 된다.
_TREE_API = "https://api.github.com/repos/MicrosoftDocs/azure-docs/git/trees"

PROVIDER = "azure"
EVIDENCE = "azure-limits-doc"   # 표의 숫자 — 원본이 명시
EVIDENCE_NOTE = "azure-limits-note"  # 각주가 붙었거나 숫자가 아닌 값 — 짐작

# 링크 대상에 괄호가 중첩될 수 있다 — 한 단계까지 허용해야 한다.
# (실측: `[Local networks](/previous-versions/azure/reference/jj157100(v=azure.100))`
#  를 단순 정규식으로 자르면 이름에 `)` 가 남는다.)
_LINK = re.compile(r"\[([^\]\n]*)\]\((?:[^()\s]|\([^()\s]*\))*\)")
_SUP = re.compile(r"<sup>.*?</sup>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
# 볼드 마커만 제거한다. 단독 `*`는 값의 일부일 수 있다
# (실측: "256 * N (N is number of NICs on VM)").
_BOLD = re.compile(r"\*\*")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SCOPE = re.compile(r"(?i)\bper\s+(.+)$")


@lru_cache(maxsize=1)
def _type_map() -> dict[str, str]:
    path = Path(__file__).with_name("azure_quota_types.json")
    return json.loads(path.read_text(encoding="utf-8"))["mappings"]


def _clean(cell: str) -> str:
    """셀에서 마크다운 링크/각주/태그를 제거하고 공백을 정리한다."""
    text = _LINK.sub(r"\1", cell)
    text = _SUP.sub("", text)
    text = _TAG.sub("", text)
    return " ".join(_BOLD.sub("", text).split()).strip()


def _as_value(cell: str) -> float | str | None:
    """숫자로만 이뤄진 셀은 숫자로, 아니면 문자열 그대로 (정보 손실 방지)."""
    text = _clean(cell)
    if not text:
        return None
    if _NUMBER.fullmatch(text):
        number = float(text.replace(",", ""))
        return int(number) if number.is_integer() else number
    return text


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    cells = stripped.strip("|").split("|")
    return [c.strip() for c in cells]


def _column_roles(header: list[str]) -> list[str] | None:
    """헤더에서 각 열의 역할을 판별한다. 첫 열은 이름, 나머지는 default/maximum."""
    if len(header) < 2:
        return None
    if "resource" not in header[0].strip().lower():
        return None  # 쿼터 표가 아님
    roles = ["name"]
    for cell in header[1:]:
        label = cell.strip().lower()
        if "maximum" in label or "max " == label[:4]:
            roles.append("maximum")
        elif "default" in label or "limit" in label:
            roles.append("default")
        else:
            roles.append("skip")
    if "default" not in roles and "maximum" not in roles:
        return None
    return roles


def _scope_of(name: str) -> str | None:
    """"Subnets per virtual network" → "virtual network"."""
    match = _SCOPE.search(name)
    return match.group(1).strip() if match else None


def parse_markdown(text: str, source_doc: str) -> list[Quota]:
    """limits 마크다운 문서 하나에서 쿼터 레코드를 뽑는다."""
    quotas: list[Quota] = []
    type_map = _type_map()
    roles: list[str] | None = None

    for line in text.splitlines():
        cells = _split_row(line)
        if not cells:
            roles = None  # 표 밖으로 나감
            continue
        if all(set(c) <= {"-", ":", " "} and c for c in cells):
            continue  # 구분선
        if roles is None:
            roles = _column_roles(cells)
            continue  # 헤더 행

        name = _clean(cells[0])
        if not name or len(cells) != len(roles):
            continue

        values: dict[str, float | str | None] = {}
        for role, cell in zip(roles[1:], cells[1:], strict=False):
            if role != "skip":
                values[role] = _as_value(cell)
        default = values.get("default")
        maximum = values.get("maximum")
        if default is None and maximum is None:
            continue

        # 각주가 붙었거나 값이 수치가 아니면 **라벨을 바꾼다.** 예전엔 같은 라벨에
        # 신뢰도만 0.9/0.7로 갈렸는데, 그건 한 라벨이 성격이 다른 두 가지를
        # 뭉뚱그렸다는 뜻이었다. 표에서 읽은 숫자는 원본이 명시한 것이고,
        # "varies"나 각주가 달린 값은 우리가 해석한 것이다.
        footnoted = bool(_SUP.search(cells[0]))
        non_numeric = any(isinstance(v, str) for v in (default, maximum) if v is not None)
        note = None
        if footnoted:
            note = "원문에 각주가 있어 조건에 따라 값이 다를 수 있음"
        quotas.append(
            Quota(
                provider=PROVIDER,
                name=name,
                source_doc=source_doc,
                evidence=EVIDENCE_NOTE if (footnoted or non_numeric) else EVIDENCE,
                scope=_scope_of(name),
                default=default,
                maximum=maximum,
                type_id=type_map.get(name.lower()),
                note=note,
            )
        )
    return quotas


def list_limits_files(commit: str, *, refresh: bool = False) -> tuple[str, ...]:
    """고정 커밋의 `includes/` 아래 `*-limits.md` 전체를 나열한다."""
    root_path = fetch_cached(
        f"{_TREE_API}/{commit}", f"azure-docs-tree-{commit}.json", refresh=refresh
    )
    root = json.loads(root_path.read_text(encoding="utf-8"))
    sha = next(
        (e["sha"] for e in root.get("tree", []) if e.get("path") == "includes"), None
    )
    if sha is None:
        raise FileNotFoundError("azure-docs 트리에서 includes/ 를 찾지 못했습니다.")
    inc_path = fetch_cached(
        f"{_TREE_API}/{sha}", f"azure-docs-includes-{sha[:12]}.json", refresh=refresh
    )
    inc = json.loads(inc_path.read_text(encoding="utf-8"))
    if inc.get("truncated"):
        print("경고: includes/ 목록이 잘렸습니다 — 일부 문서가 누락될 수 있음", file=sys.stderr)
    return tuple(
        sorted(
            e["path"]
            for e in inc.get("tree", [])
            if e.get("type") == "blob" and e["path"].endswith("-limits.md")
        )
    )


def build(
    output: Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
    includes: tuple[str, ...] = DEFAULT_INCLUDES,
    refresh: bool = False,
) -> CapacitySet:
    """limits 문서를 받아 파싱하고 output에 저장한 뒤 결과를 반환한다."""
    if not includes:
        # 목록을 안 주면 전체를 읽는다. 로컬 디렉터리를 넘겼으면 거기서,
        # 아니면 고정 커밋의 트리에서 찾는다. 여기서 빈 목록을 그냥 두면
        # **아무것도 안 읽고 조용히 성공**한다 — 테스트가 그걸 잡았다.
        local_dir = Path(base_url)
        includes = (
            tuple(sorted(p.name for p in local_dir.glob("*-limits.md")))
            if local_dir.is_dir()
            else list_limits_files(SOURCES["azure-limits-doc"].pin, refresh=refresh)
        )
        print(f"azure-quota: limits 문서 {len(includes)}개 읽는 중")
    capacity = CapacitySet()
    read_paths: list[Path] = []
    for name in includes:
        local = Path(base_url) / name
        try:
            path = (
                local
                if local.exists()
                else fetch_cached(f"{base_url.rstrip('/')}/{name}", f"azure-limits-{name}", refresh=refresh)
            )
            text = path.read_text(encoding="utf-8")
            read_paths.append(path)
        except Exception as exc:  # noqa: BLE001 — 한 문서 실패가 전체를 막지 않게
            print(f"경고: limits 문서 처리 실패, 건너뜀 — {name}: {exc}", file=sys.stderr)
            continue
        for quota in parse_markdown(text, name):
            capacity.add_quota(quota)

    capacity.provenance = [describe_source_set(read_paths, "azure-limits-doc")]
    capacity.coverage = [{
        "provider": "azure",
        "types": len({q.type_id for q in capacity.quotas if q.type_id}),
        "note": (
            f"azure-docs의 includes/*-limits.md {len(read_paths)}개를 읽는다. "
            "**쿼터는 Azure만 수록한다** — AWS Service Quotas와 GCP Cloud Quotas는 "
            "둘 다 자격증명이 필요해 이 빌드에서 받을 수 없다. "
            "타입 연결은 사람이 검수한 표(azure_quota_types.json)에 있는 것만 되고, "
            "나머지는 **이름 검색으로만** 찾힌다."
        ),
    }]
    announce(capacity.save(output), "capacitykb/azure_quota")
    linked = sum(1 for q in capacity.quotas if q.type_id)
    total = len(capacity.quotas)
    print(
        f"azure-quota: 쿼터 {total}개 (문서 {len(includes)}개) → {output}"
    )
    # 조용히 두면 "쿼터가 494건이나 있다"만 보이고, 교차 축 질의
    # (타입 → 그 타입의 쿼터)가 대부분 안 붙는다는 사실이 가려진다.
    print(
        f"  타입 연결 {linked}/{total}건 ({linked / max(total, 1):.0%}) — "
        "나머지는 이름 검색으로만 찾힙니다. 연결을 늘리려면 "
        "capacitykb/parsers/azure_quota_types.json 에 검수해 추가하세요.",
        file=sys.stderr,
    )
    return capacity
