"""제품 평가 입력 보호와 보고서 집계 규칙을 검증한다.

이 테스트의 manifest는 실제 실행기가 저장하는 최상위 필드를 같이
사용한다. 단순히 평가기가 알아보는 필드만 나열한 모의 JSON을 사용하면
실제 저장 형식이 바뀌었을 때 테스트가 문제를 놓칠 수 있기 때문이다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evaluation.easydep.product.catalog import (
    EvaluationProfile,
    HoldoutAccessError,
    load_profile,
    load_profile_catalog,
)
from evaluation.easydep.product.cli import main
from evaluation.easydep.product.report import aggregate_manifests
from evaluation.easydep.product.runner import ProductEvaluationRunner, RunEnvironment


def _source(path: Path, text: str) -> None:
    path.write_text(
        json.dumps(
            {
                "description": text,
                "classified": [{"id": "FR1", "type": "FR", "text": text}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_development_profile_does_not_read_holdout_source(tmp_path: Path) -> None:
    """development profile은 카탈로그의 holdout 원문을 열지 않는다."""
    development_source = tmp_path / "development.json"
    _source(development_source, "개발용 요구사항")
    missing_holdout_source = tmp_path / "holdout-must-not-be-read.json"
    quick = load_profile("quick")
    datasets = [
        {
            "id": dataset_id,
            "partition": "development",
            "domain": "개발",
            "source": str(development_source),
        }
        for dataset_id in quick.dataset_ids
    ]
    datasets.append(
        {
            "id": "holdout_logistics",
            "partition": "holdout",
            "domain": "확인",
            "source": str(missing_holdout_source),
        }
    )
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({"datasets": datasets}, ensure_ascii=False), encoding="utf-8"
    )

    catalog = load_profile_catalog(quick, catalog_path)

    assert set(catalog) == set(quick.dataset_ids)
    assert not missing_holdout_source.exists()


def test_direct_holdout_profile_cannot_bypass_source_guard(tmp_path: Path) -> None:
    """EvaluationProfile을 직접 만들어도 확인 없이 holdout을 열 수 없다."""
    manually_created = EvaluationProfile(
        "holdout", ("holdout_logistics",), 1, "testing", "holdout"
    )

    with pytest.raises(HoldoutAccessError):
        load_profile_catalog(manually_created, tmp_path / "missing-catalog.json")


def test_python_runner_uses_the_profile_scoped_catalog_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI를 거치지 않아도 전체 catalog 원문을 한꺼번에 읽지 않는다."""
    from evaluation.easydep.product import runner as runner_module

    quick = load_profile("quick")
    seen: list[EvaluationProfile] = []
    monkeypatch.setattr(
        runner_module,
        "load_profile_catalog",
        lambda profile: seen.append(profile) or {},
    )
    def unused_transport() -> object:
        return object()

    runner = ProductEvaluationRunner(unused_transport, tmp_path)
    environment = RunEnvironment(
        commit="abc123",
        provider="nvidia-nim",
        model="test-model",
        settings={},
    )

    with pytest.raises(ValueError, match="profile 입력이 없습니다"):
        runner.run_profile(quick, environment)

    assert seen == [quick]


def _manifest(run_id: str) -> dict[str, object]:
    """실제 runner manifest와 같은 큰 구조의 테스트 데이터를 만든다."""
    return {
        "schemaVersion": "easydep-product-evaluation-run/v1",
        "runId": run_id,
        "dataset": {
            "id": f"dataset-{run_id}",
            "partition": "development",
            "domain": "주문",
            "source": "inputs/example.json",
            "inputDigest": "input-digest",
        },
        "profile": {"name": "quick", "targetStage": "design", "repetition": 1},
        "environment": {
            "commit": "abc123",
            "provider": "nvidia-nim",
            "model": "test-model",
            "settings": {"temperature": 0},
            "settingsDigest": "settings-digest",
        },
        "status": "COMPLETED",
        "finalStage": "design",
        "firstFailure": None,
        "finalFailure": None,
        "appId": f"app-{run_id}",
        "startedAt": "2026-08-29T00:00:00+00:00",
        "finishedAt": "2026-08-29T00:01:00+00:00",
        "wallSeconds": 60.0,
        "stageTimings": [],
        "events": [],
        "actions": [],
        "llm": {
            "totalTokens": 100,
            "repairs": {"total": 0},
            "cache": {},
            "providerErrors": {},
            "measuredUnavailable": [],
        },
        "artifactVersions": {},
        "resumeRecord": {},
        "resumeHistory": [],
        "attempts": [],
    }


def _write_manifests(tmp_path: Path, *manifests: dict[str, object]) -> list[Path]:
    paths: list[Path] = []
    for manifest in manifests:
        path = tmp_path / str(manifest["runId"]) / "manifest.json"
        path.parent.mkdir()
        path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        paths.append(path)
    return paths


def test_stage_time_uses_one_sample_per_run_and_reports_missing_target(
    tmp_path: Path,
) -> None:
    """재개된 단계는 run 안에서 합치고, 미도달 목표도 표시한다."""
    resumed = _manifest("resumed")
    resumed["stageTimings"] = [
        {"attempt": 1, "stage": "requirements", "wallSeconds": 3.0},
        {"attempt": 2, "stage": "requirements", "wallSeconds": 2.0},
    ]
    reached_target = _manifest("target")
    reached_target["stageTimings"] = [
        {"attempt": 1, "stage": "requirements", "wallSeconds": 4.0},
        {"attempt": 1, "stage": "design", "wallSeconds": 10.0},
    ]

    report = aggregate_manifests(
        _write_manifests(tmp_path, resumed, reached_target)
    )

    requirements = report["stageWallSeconds"]["requirements"]
    assert requirements == {
        "p50": 4.5,
        "p95": 4.95,
        "sampleCount": 2,
        "unavailableCount": 0,
    }
    design = report["stageWallSeconds"]["design"]
    assert design["sampleCount"] == 1
    assert design["unavailableCount"] == 1


def test_target_stage_is_reported_when_no_run_reaches_it(tmp_path: Path) -> None:
    failed = _manifest("failed")
    failed["status"] = "FAILED"
    failed["finalStage"] = "requirements"
    failed["stageTimings"] = [
        {"attempt": 1, "stage": "requirements", "wallSeconds": 5.0}
    ]

    report = aggregate_manifests(_write_manifests(tmp_path, failed))

    assert report["stageWallSeconds"]["design"] == {
        "p50": None,
        "p95": None,
        "sampleCount": 0,
        "unavailableCount": 1,
    }


@pytest.mark.parametrize(
    ("section", "field", "different_value", "reported_field"),
    [
        ("profile", "name", "stability", "profile"),
        ("profile", "targetStage", "testing", "targetStage"),
        ("environment", "commit", "def456", "commit"),
        ("environment", "settingsDigest", "other", "settingsDigest"),
        ("environment", "model", "other-model", "declaredModel"),
        ("environment", "provider", "other-provider", "declaredProvider"),
        ("dataset", "partition", "holdout", "datasetPartition"),
    ],
)
def test_report_rejects_mixed_comparison_conditions(
    tmp_path: Path,
    section: str,
    field: str,
    different_value: str,
    reported_field: str,
) -> None:
    """비교 조건이 다른 run을 한 집계로 조용히 섞지 않는다."""
    first = _manifest("first")
    second = copy.deepcopy(_manifest("second"))
    section_value = second[section]
    assert isinstance(section_value, dict)
    section_value[field] = different_value

    with pytest.raises(ValueError, match=reported_field):
        aggregate_manifests(_write_manifests(tmp_path, first, second))


def test_report_exposes_comparison_provenance_in_summary_and_each_run(
    tmp_path: Path,
) -> None:
    manifest = _manifest("one")

    report = aggregate_manifests(_write_manifests(tmp_path, manifest))

    assert report["provenance"]["profile"] == "quick"
    assert report["provenance"]["declaredProvider"] == "nvidia-nim"
    assert report["provenance"]["serverConfigurationVerified"] is False
    assert report["runs"][0]["targetStage"] == "design"
    assert report["runs"][0]["settingsDigest"] == "settings-digest"


def test_cli_help_says_environment_values_are_unverified_labels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI 도움말이 provider/model 값의 의미를 과장하지 않는다."""
    with pytest.raises(SystemExit) as raised:
        main(["run", "--help"])

    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    assert "서버에 적용하지 않는 사용자 제공 provider label" in help_text
    assert "서버 설정을 변경하지 않고" in help_text
