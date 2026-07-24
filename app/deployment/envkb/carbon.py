"""리전별 탄소 — 완전히 새로운 축.

**무엇을 답하나.** "같은 성능이면 어느 리전이 탄소를 덜 쓰나?" 지금까지 이 질문에
답할 데이터가 **한 건도** 없었다(`document/archive/gap-map-2026-07-22.md`의 "아예 없는 축").

리전에 딸린 사실이라 리전 이름(`cloudinfo.py`)과 같은 자리에 둔다 — 어느 KB에도
속하지 않고 넷이 다 쓸 수 있다.

## 두 소스, 두 방법론 — 섞으면 안 된다

| 소스 | 무엇 | 어떻게 나온 값인가 |
|---|---|---|
| GCP `region-carbon-info` | CFE % · 그리드 집약도 | **Google이 직접 발표** |
| Cloud Carbon Footprint | AWS·Azure 배출계수 | **공개 그리드 데이터로 추정** |

실측이 이걸 분명히 보여준다 (gCO2eq/kWh):

    서울   aws 477.4 · gcp 356.6 · azure 415.6    ← gcp가 최저
    도쿄   aws 439.8 · gcp 452.9 · azure 465.8    ← aws가 최저

**같은 도시, 같은 전력망인데 값이 다르고 순서까지 뒤집힌다.** 실제 차이가 아니라
방법론과 시점의 차이다. 그래서 이 축은 **프로바이더 안에서만 비교**한다 —
perfkb가 "프로바이더 간 성능 비교는 불가"라고 답하는 것과 같은 이유다
(`perfkb/agent_api.py`). 레코드마다 `method`를 남겨 어느 방법론인지 밝힌다.

## CCF는 파일 셋을 함께 읽어야 한다

TypeScript 상수라 정규식으로 뽑는데, 계수 표가 다른 상수를 **참조**한다:

    [AWS_REGIONS.US_EAST_1]: US_NERC_REGIONS_EMISSIONS_FACTORS.SERC
    [AWS_REGIONS.AP_NORTHEAST_2]: 0.00047739

그래서 세 파일이 필요하다 — 리전 enum(`US_EAST_1 = 'us-east-1'`), 계수 표,
NERC 상수. 참조를 못 풀면 그 리전은 **담지 않고 센다**. 실측으로 AWS는 39개 전부
풀렸다.

## 단위

CCF는 **메트릭톤/kWh**, GCP는 **g/kWh**다. 산출물은 g/kWh로 맞춘다(×1,000,000) —
사람이 읽는 단위이고 GCP 원본이 이미 그것이다. 원본 단위는 `note`에 남긴다.
"""

from __future__ import annotations

import csv
from functools import lru_cache
import re
from pathlib import Path

from kbcommon.artifact import load_json, resolve, write_dataset
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

#: 메트릭톤 → 그램
_TON_TO_GRAM = 1_000_000

#: CCF 파일 셋 (소스 URL 뒤에 붙는 상대 경로)
CCF_FILES = {
    "core": "core/src/FootprintEstimationConstants.ts",
    "aws_regions": "aws/src/lib/AWSRegions.ts",
    "aws_factors": "aws/src/domain/AwsFootprintEstimationConstants.ts",
    "azure_regions": "azure/src/lib/AzureRegions.ts",
    "azure_factors": "azure/src/domain/AzureFootprintEstimationConstants.ts",
}

SCHEMA = {
    "type": "object",
    "required": ["regions", "_source"],
    "properties": {
        "_note": {"type": "string"},
        "_source": {"type": "array"},
        "unresolved": {"type": "integer"},
        "regions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["provider", "region", "gramsPerKWh", "method"],
                "additionalProperties": False,
                "properties": {
                    "provider": {"enum": ["aws", "azure", "gcp"]},
                    "region": {"type": "string", "minLength": 1},
                    "gramsPerKWh": {"type": "number"},
                    # GCP만 준다. 무탄소 에너지로 돌아간 비율(0~1).
                    "carbonFreeEnergy": {"type": ["number", "null"]},
                    "location": {"type": ["string", "null"]},
                    "method": {"enum": ["provider-published", "grid-estimate"]},
                    "source": {"type": "string"},
                },
            },
        },
    },
}


class Report:
    def __init__(self) -> None:
        self.unresolved: list[str] = []
        """참조를 못 푼 리전. 담지 않고 센다 — 조용히 빠지면 커버리지가 거짓이 된다."""


def parse_nerc(text: str) -> dict[str, float]:
    """미국 NERC 지역 상수. 계수 표가 이걸 참조한다."""
    return {
        name: float(value)
        for name, value in re.findall(r"^\s*([A-Z]{2,5}):\s*([0-9.]+),", text, re.M)
    }


def _factors(
    block: str, pattern: str, codes: dict[str, str], nerc: dict[str, float],
    report: Report, label: str,
) -> dict[str, float]:
    """`[X.KEY]: 값|참조` 꼴에서 리전코드 → 계수(g/kWh)."""
    out: dict[str, float] = {}
    for key, raw in re.findall(pattern, block):
        code = codes.get(key)
        raw = raw.strip()
        if code is None:
            report.unresolved.append(f"{label}:{key}(리전 이름을 못 찾음)")
            continue
        found = re.match(r"US_NERC_REGIONS_EMISSIONS_FACTORS\.([A-Z]+)", raw)
        if found:
            value = nerc.get(found.group(1))
            if value is None:
                report.unresolved.append(f"{label}:{key}({raw})")
                continue
        elif re.fullmatch(r"[0-9.]+", raw):
            value = float(raw)
        else:
            report.unresolved.append(f"{label}:{key}={raw}")
            continue
        out[code] = value * _TON_TO_GRAM
    return out


def parse_ccf(files: dict[str, str]) -> tuple[list[dict], Report]:
    """CCF 파일 셋 → AWS·Azure 리전 배출계수 레코드."""
    report = Report()
    nerc = parse_nerc(files["core"])

    aws_codes = dict(
        re.findall(r"^\s*([A-Z0-9_]+)\s*=\s*'([^']+)'", files["aws_regions"], re.M)
    )
    aws_block = files["aws_factors"]
    aws_block = aws_block[aws_block.index("AWS_EMISSIONS_FACTORS_METRIC_TON_PER_KWH"):]
    aws = _factors(
        aws_block, r"\[AWS_REGIONS\.([A-Z0-9_]+)\]:\s*([^,\n]+),",
        aws_codes, nerc, report, "aws",
    )

    # Azure는 `{ name: 'koreacentral', options: [...] }` 꼴이라 name을 뽑는다.
    az_codes = dict(
        re.findall(r"([A-Z0-9_]+):\s*\{\s*name:\s*'([^']+)'", files["azure_regions"])
    )
    az_block = files["azure_factors"]
    az_block = az_block[az_block.index("AZURE_EMISSIONS_FACTORS_METRIC_TON_PER_KWH"):]
    azure = _factors(
        az_block, r"\[AZURE_REGIONS\.([A-Z0-9_]+)\.name\]:\s*([^,\n]+),",
        az_codes, nerc, report, "azure",
    )

    records = [
        {
            "provider": provider, "region": code, "gramsPerKWh": round(grams, 4),
            "carbonFreeEnergy": None, "location": None,
            "method": "grid-estimate", "source": "ccf-emissions",
        }
        for provider, table in (("aws", aws), ("azure", azure))
        for code, grams in sorted(table.items())
    ]
    return records, report


def parse_gcp(rows: list[dict]) -> list[dict]:
    """GCP CSV → 리전 레코드. CFE는 GCP만 주는 축이다."""
    out = []
    for row in rows:
        code = (row.get("Google Cloud Region") or "").strip()
        intensity = (row.get("Grid carbon intensity (gCO2eq / kWh)") or "").strip()
        if not code or not intensity:
            continue
        cfe = (row.get("Google CFE") or "").strip()
        out.append({
            "provider": "gcp",
            "region": code,
            "gramsPerKWh": float(intensity),
            "carbonFreeEnergy": float(cfe) if cfe else None,
            "location": (row.get("Location") or "").strip() or None,
            "method": "provider-published",
            "source": "gcp-carbon",
        })
    return out


def build(output: Path, *, refresh: bool = False) -> dict:
    gcp_src = SOURCES["gcp-carbon"]
    ccf_src = SOURCES["ccf-emissions"]

    gcp_path = fetch_cached(
        gcp_src.url, f"gcp-carbon-{gcp_src.pin}.csv", refresh=refresh
    )
    with open(gcp_path, encoding="utf-8") as handle:
        gcp_records = parse_gcp(list(csv.DictReader(handle)))

    paths = [gcp_path]
    files: dict[str, str] = {}
    for name, rel in CCF_FILES.items():
        path = fetch_cached(
            ccf_src.url + rel,
            f"ccf-{name}-{ccf_src.pin}.ts",
            refresh=refresh,
        )
        files[name] = path.read_text(encoding="utf-8")
        paths.append(path)
    ccf_records, report = parse_ccf(files)

    records = sorted(
        gcp_records + ccf_records, key=lambda r: (r["provider"], r["region"])
    )
    dataset = {
        "_note": (
            "리전별 탄소. **프로바이더 안에서만 비교하세요** — GCP는 Google이 직접 "
            "발표한 값(provider-published)이고 AWS·Azure는 공개 그리드 데이터 "
            "추정(grid-estimate)이라 방법론이 다릅니다. 실측상 같은 도시에서 값이 "
            "다르고 순서까지 뒤집힙니다. 단위는 gCO2eq/kWh이며 CCF 원본은 "
            "메트릭톤/kWh입니다(×1,000,000)."
        ),
        "_source": [
            describe_source_set([gcp_path], gcp_src.key),
            describe_source_set(paths[1:], ccf_src.key),
        ],
        "unresolved": len(report.unresolved),
        "regions": records,
    }
    write_dataset(output, dataset, SCHEMA)

    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["provider"]] = counts.get(rec["provider"], 0) + 1
    print(
        "region-carbon: 리전 "
        + " · ".join(f"{p} {n}" for p, n in sorted(counts.items()))
        + f" (합계 {len(records)})"
    )
    if report.unresolved:
        print(
            f"  참조를 못 풀어 담지 않은 것 {len(report.unresolved)}건: "
            + ", ".join(report.unresolved[:4])
        )
    return dataset


# --------------------------------------------------------------------------
# 조회 — 데이터는 여기서 읽고, 사람이 읽는 문장까지 만든다.
#
# 탄소는 어느 KB에도 속하지 않는 축이라 `agent_api`를 둘 자리가 없다. 대신
# `regions.py`와 같은 자리에서 조회까지 맡고, 도구 계층은 얇게 감싸기만 한다.
# --------------------------------------------------------------------------

ARTIFACT = "region-carbon.json"

_MISSING = (
    "탄소 산출물이 없습니다. `python -m kbcommon build-carbon` 로 생성하세요."
)

#: 방법론이 다른 값을 나란히 놓으면 비교로 읽힌다. 실측상 같은 도시에서 순서까지
#: 뒤집히므로(서울은 gcp가 최저, 도쿄는 aws가 최저) 이 고지를 빼면 안 된다.
_METHOD_CAVEAT = (
    "※ **프로바이더끼리 비교하지 마세요.** GCP는 Google이 직접 발표한 값이고 "
    "AWS·Azure는 공개 그리드 데이터 추정이라 방법론이 다릅니다 — 같은 도시에서도 "
    "값이 다르고 순서가 뒤집힙니다. 같은 프로바이더 안에서 리전을 고를 때만 쓰세요."
)

_METHOD_NAMES = {
    "provider-published": "프로바이더 발표값",
    "grid-estimate": "공개 그리드 데이터 추정",
}


@lru_cache(maxsize=4)
def _load(output_dir: str) -> tuple[dict, ...]:
    path = resolve(Path(output_dir), ARTIFACT)
    if path is None:
        return ()
    try:
        data = load_json(path)
    except Exception:
        return ()
    return tuple(data.get("regions") or ())


def for_region(
    provider: str, region: str, *, output_dir: str = "output"
) -> dict | None:
    """한 리전의 탄소 레코드. 없으면 None."""
    want = (provider.strip().lower(), region.strip().lower())
    for rec in _load(output_dir):
        if (rec["provider"], rec["region"].lower()) == want:
            return rec
    return None


def cleanest(
    provider: str, *, limit: int = 8, output_dir: str = "output"
) -> list[dict]:
    """그 프로바이더에서 탄소집약도가 낮은 리전 순."""
    wanted = provider.strip().lower()
    rows = [r for r in _load(output_dir) if r["provider"] == wanted]
    return sorted(rows, key=lambda r: r["gramsPerKWh"])[:limit]


def _line(rec: dict) -> str:
    text = f"  - {rec['region']}: {rec['gramsPerKWh']:,.1f} gCO2eq/kWh"
    if rec.get("carbonFreeEnergy") is not None:
        text += f" · 무탄소 에너지 {rec['carbonFreeEnergy'] * 100:.0f}%"
    if rec.get("location"):
        text += f" ({rec['location']})"
    return text


def describe(
    provider: str, region: str | None = None, *, output_dir: str = "output"
) -> str:
    """탄소 질의의 사람이 읽는 답. 리전을 안 주면 깨끗한 순으로 보여준다."""
    if not _load(output_dir):
        return _MISSING
    wanted = provider.strip().lower()
    if wanted not in {"aws", "azure", "gcp"}:
        return (
            f"'{provider}'의 탄소 데이터가 없습니다. 지금 수록된 프로바이더는 "
            "aws · azure · gcp 셋입니다 — 다른 프로바이더는 '탄소가 없다'가 아니라 "
            "**이 축을 추적하지 않는다**는 뜻입니다."
        )

    if region is not None:
        rec = for_region(wanted, region, output_dir=output_dir)
        if rec is None:
            known = cleanest(wanted, limit=100, output_dir=output_dir)
            return (
                f"{wanted} '{region}' 리전의 탄소 값이 없습니다 — 그 리전이 깨끗하다는 "
                f"뜻이 아니라 **이 소스에 없다**는 뜻입니다(수록 {len(known)}곳).\n"
                "  수록된 리전 예: "
                + ", ".join(r["region"] for r in known[:6])
            )
        method = _METHOD_NAMES.get(rec["method"], rec["method"])
        return (
            f"{wanted} {rec['region']} 탄소:\n{_line(rec)}\n"
            f"  근거: {method}\n{_METHOD_CAVEAT}"
        )

    rows = cleanest(wanted, output_dir=output_dir)
    if not rows:
        return f"{wanted}의 탄소 데이터가 없습니다."
    method = _METHOD_NAMES.get(rows[0]["method"], rows[0]["method"])
    lines = [f"{wanted} 리전 중 탄소집약도가 낮은 순 {len(rows)}곳:"]
    lines.extend(_line(r) for r in rows)
    lines.append(f"  근거: {method}")
    lines.append(_METHOD_CAVEAT)
    return "\n".join(lines)
