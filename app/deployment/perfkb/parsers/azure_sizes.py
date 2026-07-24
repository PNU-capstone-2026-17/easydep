"""Azure VM **크기 문서의 표** — NIC 수·네트워크 대역폭 (조사 2026-07-24 채택 1).

## 왜 필요한가

perfkb의 azure 34,846건은 미러에서 온 4필드(sustainedCpu·diskIops·acu·
acceleratedNetworking)뿐이었다 — aws는 거의 전 필드가 차 있는데 azure는 네트워크
대역폭도 NIC 수도 없었다. MicrosoftDocs/azure-compute-docs의 크기 문서에는 크기마다

    | Size Name | Max NICs (Qty.) | Max Network Bandwidth (Mbps) |

표가 있다(실측). **산문이 아니라 표를 파싱한다** — azure-limits-doc과 같은 방법,
같은 라이선스(CC-BY-4.0), basis=stated.

## 무엇을 담고 무엇을 안 담나

`maxNics`·`networkBandwidthMbps` **둘만** 담는다 — 스키마에 이미 있고(IBM이 쓰던
칸) azure가 비어 있던 칸이다. 표에 함께 있는 vCPU·메모리·디스크 IOPS는 미러가
이미 갖고 있어(다른 소스·다른 스냅샷) 담지 않는다 — 두 소스의 값이 섞이면
어느 스냅샷의 값인지 알 수 없게 된다.

각주 표기(`<sup>1</sup>`)와 'Not Supported' 칸은 실측에서 확인한 형식이다 —
숫자가 아닌 칸은 담지 않고 센다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from kbcommon.fetch import fetch_cached, list_github_tree
from kbcommon.sources import SOURCES

EVIDENCE = "azure-sizes-doc"

_API = "https://api.github.com/repos/MicrosoftDocs/azure-compute-docs"

#: 크기 문서가 사는 곳. 문서 재편(경로 이동)은 매칭 급감으로 잡는다.
_SIZES_PREFIX = "virtual-machines/sizes/"

_SUP = re.compile(r"<sup>.*?</sup>")
_SIZE_NAME = re.compile(r"^Standard_[A-Za-z0-9_-]+$")


class Report:
    def __init__(self) -> None:
        self.files = 0
        self.matched = 0
        self.unmatched: Counter = Counter()
        self.non_numeric = 0


def _cells(line: str) -> list[str]:
    return [_SUP.sub("", c).strip() for c in line.strip().strip("|").split("|")]


def _number(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None  # 'Not Supported' · '-' · 빈 칸


def parse_tables(markdown: str) -> dict[str, dict]:
    """한 문서의 표들에서 `크기 이름(소문자) → {maxNics, networkBandwidthMbps}`."""
    out: dict[str, dict] = {}
    lines = markdown.splitlines()
    header: list[str] | None = None
    nic_idx = bw_idx = name_idx = None
    for line in lines:
        if not line.lstrip().startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if header is None:
            header = cells
            name_idx = nic_idx = bw_idx = None
            for i, cell in enumerate(cells):
                low = cell.lower()
                if "size name" in low:
                    name_idx = i
                elif "max nics" in low:
                    nic_idx = i
                elif "network bandwidth" in low:
                    bw_idx = i
            continue
        if set("".join(cells)) <= {"-", ":", " ", ""}:
            continue  # 구분선
        if name_idx is None or (nic_idx is None and bw_idx is None):
            continue
        if len(cells) <= max(i for i in (name_idx, nic_idx, bw_idx) if i is not None):
            continue
        name = cells[name_idx]
        if not _SIZE_NAME.match(name):
            continue
        fields = out.setdefault(name.lower(), {})
        if nic_idx is not None and (nics := _number(cells[nic_idx])) is not None:
            fields["maxNics"] = int(nics)
        if bw_idx is not None and (bw := _number(cells[bw_idx])) is not None:
            fields["networkBandwidthMbps"] = bw
    return {k: v for k, v in out.items() if v}


def fetch(refresh: bool = False) -> tuple[dict[str, dict], list[Path]]:
    """크기 문서 전부를 받아 표를 파싱한다. `(크기별 필드, 캐시 경로들)`."""
    source = SOURCES["azure-compute-docs"]
    paths = list_github_tree(
        _API, source.pin, "articles", cache_prefix="azcompute-tree", refresh=refresh
    )
    wanted = sorted(
        p for p in paths
        if p.startswith(_SIZES_PREFIX) and p.endswith("-series.md")
    )
    table: dict[str, dict] = {}
    fetched: list[Path] = []
    for rel in wanted:
        cache_name = f"azure-compute-docs-{rel.replace('/', '-')}"
        path = fetch_cached(f"{source.url}/articles/{rel}", cache_name, refresh=refresh)
        fetched.append(path)
        for name, fields in parse_tables(path.read_text(encoding="utf-8")).items():
            # 같은 크기가 여러 문서에 나오면 값이 같아야 정상 — 다르면 나중 것을
            # 덮지 않고 첫 값을 지킨다(어느 쪽이 맞는지 우리가 모른다).
            table.setdefault(name, fields)
    if len(wanted) < 100:
        raise RuntimeError(
            f"크기 문서가 {len(wanted)}개뿐이다(실측 기준 200+) — 문서가 재편됐는지 "
            "확인할 것(경로 접두가 낡았을 수 있다)."
        )
    return table, fetched


def enrich(specs: list[dict], table: dict[str, dict]) -> Report:
    """perfkb azure 레코드에 크기 표 사실을 덧붙인다(제자리 수정).

    **빈 칸만 채운다** — 이미 값이 있으면(다른 소스) 덮지 않는다.
    """
    report = Report()
    by_name: dict[str, list[dict]] = {}
    for spec in specs:
        if spec.get("provider") == "azure" and spec.get("specName"):
            by_name.setdefault(spec["specName"].lower(), []).append(spec)

    for name, fields in table.items():
        targets = by_name.get(name)
        if not targets:
            # 미러의 azure 표기(소문자 standard_d2s_v5)와 문서 표기(Standard_D2s_v5)
            # 는 소문자 정규화로 만난다 — 그래도 안 만나는 것은 미러에 없는 크기다.
            report.unmatched[name.split("_")[1][:1] if "_" in name else name] += 1
            continue
        applied = {k: v for k, v in fields.items()}
        if not applied:
            continue
        for spec in targets:
            for key, value in applied.items():
                if spec.get(key) is None:
                    spec[key] = value
            spec["azureSizesEvidence"] = EVIDENCE
        report.matched += 1
    report.files = len(table)
    return report


def format_report(report: Report) -> str:
    lines = [
        f"azure 크기 표: 문서의 크기 {report.files:,}종 중 미러와 매칭 "
        f"{report.matched:,}종에 NIC·대역폭을 덧붙임"
    ]
    if report.unmatched:
        total = sum(report.unmatched.values())
        lines.append(
            f"  미러에 없는 크기 {total}종은 담지 않음 (계열 첫 글자 분포: "
            f"{dict(report.unmatched.most_common(6))})"
        )
    return "\n".join(lines)
