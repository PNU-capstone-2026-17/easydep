"""CLI 엔드투엔드 테스트 — fixture 로컬 경로로 build → query → export."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.cloudkb.graphkb.cli import main

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SWAGGER_FIXTURE = str(FIXTURE_DIR / "tumblebug-swagger-min.json")
OOB_FIXTURE = str(FIXTURE_DIR / "cdk-relationships-min.json")


@pytest.fixture
def cfn_zip(tmp_path) -> str:
    """fixture 스키마들을 담은 임시 zip을 만든다."""
    import zipfile

    zip_path = tmp_path / "schemas.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for schema in (FIXTURE_DIR / "cfn").glob("*.json"):
            archive.write(schema, schema.name)
    return str(zip_path)


def test_build_tumblebug_and_query(tmp_path, capsys) -> None:
    out = tmp_path / "core-graph.json"
    assert main(["build", "--source", "tumblebug", "--swagger-url", SWAGGER_FIXTURE, "--output", str(out)]) == 0
    assert out.exists()

    assert main(["query", "--deps", "vm", "--graph", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "core::vm" in stdout
    assert "core::vNet" in stdout
    # 위상순: vNet이 vm보다 먼저
    assert stdout.index("core::vNet") < stdout.rindex("core::vm")


def test_build_cfn_and_query(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-graph.json"
    assert main([
        "build", "--source", "cfn",
        "--zip-url", cfn_zip, "--oob-url", OOB_FIXTURE,
        "--output", str(out),
    ]) == 0
    assert out.exists()

    assert main(["query", "--deps", "AWS::EC2::Subnet", "--graph", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "aws::AWS::EC2::VPC" in stdout


def test_query_dependents(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-graph.json"
    main(["build", "--source", "cfn", "--zip-url", cfn_zip, "--oob-url", OOB_FIXTURE, "--output", str(out)])
    capsys.readouterr()
    assert main(["query", "--dependents", "AWS::EC2::VPC", "--graph", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "aws::AWS::EC2::Subnet" in stdout


def test_export_dot_and_graphml(tmp_path, capsys) -> None:
    out = tmp_path / "core-graph.json"
    main(["build", "--source", "tumblebug", "--swagger-url", SWAGGER_FIXTURE, "--output", str(out)])
    for fmt, suffix in (("dot", ".dot"), ("graphml", ".graphml"), ("cypher", ".cypher")):
        assert main(["export", "--format", fmt, "--graph", str(out)]) == 0
        assert out.with_suffix(suffix).exists()
    cypher = out.with_suffix(".cypher").read_text(encoding="utf-8")
    assert "MERGE (n:ResourceType {id: 'core::vNet'})" in cypher


def test_load_neo4j_requires_password(tmp_path, monkeypatch, capsys) -> None:
    out = tmp_path / "core-graph.json"
    main(["build", "--source", "tumblebug", "--swagger-url", SWAGGER_FIXTURE, "--output", str(out)])
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    assert main(["load-neo4j", "--graph", str(out)]) == 1
    assert "비밀번호" in capsys.readouterr().err


def test_build_azure_and_query(tmp_path, capsys) -> None:
    out = tmp_path / "azure-graph.json"
    assert main([
        "build", "--source", "azure",
        "--base-url", str(FIXTURE_DIR / "azure"),
        "--providers", "microsoft.network",
        "--output", str(out),
    ]) == 0
    assert main(["query", "--deps", "Microsoft.Network/virtualNetworks/subnets", "--graph", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "azure::Microsoft.Network/virtualNetworks" in stdout


def test_build_gcp_and_query(tmp_path, capsys) -> None:
    out = tmp_path / "gcp-graph.json"
    assert main([
        "build", "--source", "gcp",
        "--crd-dir", str(FIXTURE_DIR / "gcp"),
        "--output", str(out),
    ]) == 0
    assert main(["query", "--deps", "ComputeSubnetwork", "--graph", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "gcp::ComputeNetwork" in stdout


def test_build_mapping(tmp_path, capsys) -> None:
    out = tmp_path / "mapping-graph.json"
    assert main(["build", "--source", "mapping", "--output", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "equivalent_to" in text
    assert "aws::AWS::EC2::VPC" in text


def test_suggest_mapping(tmp_path, capsys) -> None:
    core = tmp_path / "core-graph.json"
    vendor = tmp_path / "gcp-graph.json"
    main(["build", "--source", "tumblebug", "--swagger-url", SWAGGER_FIXTURE, "--output", str(core)])
    main(["build", "--source", "gcp", "--crd-dir", str(FIXTURE_DIR / "gcp"), "--output", str(vendor)])
    out = tmp_path / "candidates.json"
    assert main([
        "suggest-mapping",
        "--core-graph", str(core),
        "--vendor-graph", str(vendor),
        "--output", str(out),
    ]) == 0
    assert out.exists()


def test_query_without_graphs_fails_cleanly(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["query", "--deps", "vm"]) == 1
    assert "오류" in capsys.readouterr().err


def test_query_unknown_type_fails_cleanly(tmp_path, capsys) -> None:
    out = tmp_path / "core-graph.json"
    main(["build", "--source", "tumblebug", "--swagger-url", SWAGGER_FIXTURE, "--output", str(out)])
    assert main(["query", "--deps", "nope", "--graph", str(out)]) == 1
    assert "오류" in capsys.readouterr().err


def test_query_requires_a_mode(capsys) -> None:
    assert main(["query"]) == 1
    assert "--deps" in capsys.readouterr().err


def test_query_rank(tmp_path, capsys) -> None:
    out = tmp_path / "core-graph.json"
    main(["build", "--source", "tumblebug", "--swagger-url", SWAGGER_FIXTURE, "--output", str(out)])
    capsys.readouterr()

    assert main(["query", "--rank", "dependencies", "--limit", "3", "--graph", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "상위 3개" in stdout
    assert "core::vm" in stdout


def test_query_rank_provider_filter(cfn_zip, tmp_path, capsys) -> None:
    out = tmp_path / "aws-graph.json"
    main(["build", "--source", "cfn", "--zip-url", cfn_zip, "--oob-url", OOB_FIXTURE, "--output", str(out)])
    capsys.readouterr()

    assert main([
        "query", "--rank", "dependents", "--provider", "aws", "--limit", "2", "--graph", str(out)
    ]) == 0
    stdout = capsys.readouterr().out
    assert "aws " in stdout
    assert "aws::AWS::EC2::VPC" in stdout
