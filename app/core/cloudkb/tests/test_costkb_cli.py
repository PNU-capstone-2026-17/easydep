"""costkb CLI 테스트 (query / coverage — build는 없다)."""

from __future__ import annotations

import pytest

from app.core.cloudkb.costkb.cli import main


def test_query_lists_candidates(capsys) -> None:
    assert main(["query", "--vcpu-min", "4", "--provider", "aws", "--region", "us-east-1"]) == 0
    stdout = capsys.readouterr().out
    assert "후보" in stdout
    assert "aws" in stdout
    assert "/월" in stdout


def test_query_respects_limit(capsys) -> None:
    main(["query", "--limit", "2"])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  ")]
    assert len(lines) == 2


def test_query_sort_by_memory(capsys) -> None:
    main(["query", "--sort-by", "memory", "--limit", "3"])
    stdout = capsys.readouterr().out
    assert "GiB" in stdout


def test_impossible_query_shows_coverage_and_fails(capsys) -> None:
    """경계를 넘으면 exit 1 + 커버리지 안내 (조용히 빈 출력 금지)."""
    assert main(["query", "--vcpu-min", "1024"]) == 1
    captured = capsys.readouterr()
    assert "조건을 만족하는 스펙이 없습니다" in captured.err
    assert "커버리지" in captured.out


def test_coverage_reports_boundaries(capsys) -> None:
    assert main(["coverage"]) == 0
    stdout = capsys.readouterr().out
    assert "36건" in stdout
    assert "ap-northeast-2" in stdout
    assert "청구서가 아니" in stdout  # _note 고지


def test_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        main([])
