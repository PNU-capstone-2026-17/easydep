"""perfkb 보강 2종 — azure 크기 표 · gcp 시리즈 카탈로그 (조사 2026-07-24 채택 1·2).

고정하는 것: 표·SQL 파싱의 함정(각주 sup·Not Supported·LIKE 패턴 순서),
빈 칸만 채우는 규율, 근거 라벨의 등급 차이(문서 표=stated vs 큐레이션=inferred).
"""

from __future__ import annotations

from kbcommon.basis import INFERRED, STATED, basis_of
from perfkb.parsers.azure_sizes import parse_tables
from perfkb.parsers.azure_sizes import enrich as azure_enrich
from perfkb.parsers.gcp_series import parse_series_sql
from perfkb.parsers.gcp_series import enrich as gcp_enrich


# --- 근거 등급 -----------------------------------------------------------------

def test_evidence_grades_differ_by_source_kind() -> None:
    """문서 표를 직접 파싱한 것(stated)과 커뮤니티가 옮긴 것(inferred)은 등급이
    다르다 — 그 차이가 라벨을 가른 이유다."""
    assert basis_of("azure-sizes-doc") == STATED
    assert basis_of("cyclenerd-gcp-catalog") == INFERRED


# --- azure 크기 표 파싱 ---------------------------------------------------------

_DOC = """
| Size Name | vCPUs (Qty.) | Memory (GB) |
| --- | --- | --- |
| Standard_D2_v5 | 2 | 8 |

| Size Name | Max NICs (Qty.) | Max Network Bandwidth (Mbps) |
| --- | --- | --- |
| Standard_D2_v5 | 2<sup>1</sup> | 12,500 |
| Standard_D4_v5 | 4 | Not Supported |
| 헤더행아님 | 1 | 1 |
"""


def test_azure_tables_parse_footnotes_and_refuse_non_numeric() -> None:
    table = parse_tables(_DOC)
    assert table["standard_d2_v5"] == {"maxNics": 2, "networkBandwidthMbps": 12500.0}
    # 'Not Supported'는 숫자가 아니다 — 담지 않는다(0으로 읽으면 거짓이 된다).
    assert "networkBandwidthMbps" not in table["standard_d4_v5"]
    assert "헤더행아님" not in table  # Standard_ 이름 규칙 밖은 크기가 아니다


def test_azure_enrich_fills_only_empty_fields() -> None:
    specs = [
        {"provider": "azure", "specName": "Standard_D2_v5", "maxNics": None},
        {"provider": "azure", "specName": "Standard_D2_v5",
         "networkBandwidthMbps": 999.0},  # 다른 소스 값 — 덮으면 안 된다
        {"provider": "aws", "specName": "Standard_D2_v5"},  # 프로바이더 밖
    ]
    report = azure_enrich(specs, {"standard_d2_v5": {"maxNics": 2,
                                                    "networkBandwidthMbps": 12500.0}})
    assert report.matched == 1
    assert specs[0]["maxNics"] == 2 and specs[0]["networkBandwidthMbps"] == 12500.0
    assert specs[1]["networkBandwidthMbps"] == 999.0  # 기존 값 유지
    assert specs[0]["azureSizesEvidence"] == "azure-sizes-doc"
    assert "azureSizesEvidence" not in specs[2]


# --- gcp 시리즈 SQL 파싱 --------------------------------------------------------

_SQL = """
UPDATE instances SET
series      = 'n2',
family      = 'General-purpose',
cpuPlatform = 'Cascade Lake, Ice Lake',
localSsd    = '1'
WHERE name LIKE 'n2-%';
UPDATE instances SET bandwidth = '10' WHERE name LIKE 'n2-%-4';
UPDATE instances SET bandwidth = '32' WHERE name LIKE 'n2-%-32';
"""


def test_gcp_series_sql_rules_apply_in_order() -> None:
    """뒤의 좁은 패턴이 앞의 넓은 패턴을 덮는다 — 원본 SQL의 실행 의미 그대로."""
    rules = parse_series_sql(_SQL)
    specs = [{"provider": "gcp", "specName": "n2-standard-4"},
             {"provider": "gcp", "specName": "n2-standard-32"}]
    gcp_enrich(specs, rules, {})
    assert specs[0]["cpuFamily"] == "Cascade Lake, Ice Lake"
    assert specs[0]["family"] == "General-purpose"
    assert specs[0]["networkBandwidthMbps"] == 10_000.0
    assert specs[1]["networkBandwidthMbps"] == 32_000.0
    assert specs[0]["gcpSeriesEvidence"] == "cyclenerd-gcp-catalog"


def test_unknown_pricing_keys_do_not_become_gpus() -> None:
    """모르는 키를 GPU로 승격하지 않는다 — 세기만 한다(조용한 승격이 지어내기다)."""
    from collections import Counter

    instances = {"x-standard-4": {"gpuModel": "L4", "gpuCount": 1.0},
                 "_unknown_keys": Counter({"tpu": 3})}
    specs = [{"provider": "gcp", "specName": "x-standard-4"}]
    report = gcp_enrich(specs, [], instances)
    assert specs[0]["gpuModel"] == "L4"
    assert report.unknown_keys == Counter({"tpu": 3})


def test_fractional_gpu_count_is_preserved() -> None:
    """g4처럼 GPU를 나눠 쓰는 크기는 0.25가 사실이다 — 반올림하면 4배 거짓."""
    specs = [{"provider": "gcp", "specName": "g4-standard-12"}]
    gcp_enrich(specs, [], {"g4-standard-12": {"gpuModel": "RTX 6000",
                                              "gpuCount": 0.25}})
    assert specs[0]["gpuCount"] == 0.25
