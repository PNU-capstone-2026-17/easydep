"""capacitykb CLI 엔드투엔드 테스트 (fixture 로컬 경로로 오프라인 실행)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from capacitykb.cli import main

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "capacity"
AZURE_BASE = str(FIXTURE_DIR / "azure")
LIMITS_BASE = str(FIXTURE_DIR / "azure-limits")


@pytest.fixture()
def cfn_zip(tmp_path) -> str:
    """fixture 스키마들을 담은 임시 zip (zip은 저장소에 커밋하지 않는다)."""
    zip_path = tmp_path / "schemas.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for schema in (FIXTURE_DIR / "cfn").glob("*.json"):
            archive.write(schema, schema.name)
    return str(zip_path)


def test_build_cfn_and_query_limits(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-capacity.json"
    assert main(["build", "--source", "cfn", "--zip-url", cfn_zip, "--output", str(out)]) == 0
    assert out.exists()
    capsys.readouterr()

    assert main([
        "query", "--limits", "AWS::EC2::Volume", "--property", "Size", "--data", str(out)
    ]) == 0
    stdout = capsys.readouterr().out
    assert "최대 65536 GiB" in stdout
    assert "조건부" in stdout


def test_check_rejects_out_of_range(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-capacity.json"
    main(["build", "--source", "cfn", "--zip-url", cfn_zip, "--output", str(out)])
    capsys.readouterr()

    # Lambda Timeout 최대 900 (산문 0.8) → 기본 신뢰도 문턱(0.8)으로 판정된다
    assert main([
        "query", "--check", "AWS::Lambda::Function", "--property", "Timeout",
        "--value", "1200", "--data", str(out),
    ]) == 0
    stdout = capsys.readouterr().out
    assert stdout.startswith("불가")
    assert "900" in stdout


def test_check_holds_when_only_low_confidence(cfn_zip, tmp_path, capsys) -> None:
    """조건부 envelope(0.6)로는 거부하지 않되(fail-open), '가능'이라고도 하지 않는다."""
    out = tmp_path / "aws-capacity.json"
    main(["build", "--source", "cfn", "--zip-url", cfn_zip, "--output", str(out)])
    capsys.readouterr()

    assert main([
        "query", "--check", "AWS::EC2::Volume", "--property", "Size",
        "--value", "100000", "--data", str(out),
    ]) == 0
    stdout = capsys.readouterr().out
    assert stdout.startswith("판정 보류")
    assert "참고" in stdout

    # 문턱을 낮추면 거부한다
    assert main([
        "query", "--check", "AWS::EC2::Volume", "--property", "Size",
        "--value", "100000", "--min-confidence", "0.5", "--data", str(out),
    ]) == 0
    assert capsys.readouterr().out.startswith("불가")


def test_query_immutable(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-capacity.json"
    main(["build", "--source", "cfn", "--zip-url", cfn_zip, "--output", str(out)])
    capsys.readouterr()

    assert main(["query", "--immutable", "AWS::EC2::Subnet", "--data", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "VpcId" in stdout
    assert "재생성" in stdout


def test_no_prose_flag(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-capacity.json"
    assert main([
        "build", "--source", "cfn", "--zip-url", cfn_zip, "--no-prose", "--output", str(out)
    ]) == 0
    text = out.read_text(encoding="utf-8")
    assert "cfn-description" not in text
    assert "cfn-schema" in text


def test_build_azure(tmp_path, capsys) -> None:
    out = tmp_path / "azure-capacity.json"
    assert main([
        "build", "--source", "azure", "--base-url", AZURE_BASE,
        "--providers", "microsoft.containerservice", "--output", str(out),
    ]) == 0
    capsys.readouterr()
    assert main([
        "query", "--limits", "Microsoft.ContainerService/managedClusters",
        "--property", "properties.agentPoolProfiles.osDiskSizeGB", "--data", str(out),
    ]) == 0
    assert "최대 2048" in capsys.readouterr().out


def test_build_azure_quota_and_search(tmp_path, capsys) -> None:
    out = tmp_path / "azure-quota.json"
    assert main([
        "build", "--source", "azure-quota", "--base-url", LIMITS_BASE, "--output", str(out)
    ]) == 0
    capsys.readouterr()
    assert main(["query", "--quota", "subnet", "--data", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "Subnets per virtual network" in stdout
    assert "3000" in stdout


def test_query_requires_a_mode(capsys) -> None:
    assert main(["query"]) == 1
    assert "--limits" in capsys.readouterr().err


def test_check_requires_property_and_value(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-capacity.json"
    main(["build", "--source", "cfn", "--zip-url", cfn_zip, "--output", str(out)])
    capsys.readouterr()
    assert main(["query", "--check", "AWS::EC2::Volume", "--data", str(out)]) == 1
    assert "--property" in capsys.readouterr().err


def test_query_without_data_fails_cleanly(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["query", "--limits", "AWS::EC2::Volume"]) == 1
    assert "오류" in capsys.readouterr().err


def test_query_unknown_type_fails_cleanly(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-capacity.json"
    main(["build", "--source", "cfn", "--zip-url", cfn_zip, "--output", str(out)])
    capsys.readouterr()
    assert main(["query", "--limits", "nope", "--data", str(out)]) == 1
    assert "오류" in capsys.readouterr().err


def test_audit_reports_no_mismatch(cfn_zip, capsys) -> None:
    """산문 추출이 스키마 값과 겹치는 곳에서 일치해야 한다 (정밀도 프록시)."""
    assert main(["audit", "--zip-url", cfn_zip]) == 0
    assert "불일치 0건" in capsys.readouterr().out
