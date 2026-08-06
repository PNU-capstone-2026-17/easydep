import json
from datetime import UTC, datetime
from pathlib import Path

from app.core.run_identity import identity_manifest, make_run_id
from evaluation.baselines import cot, metagpt

CASE = Path("evaluation/baselines/cases/p1-stateless-detailed.json")


def test_run_id_format_is_shared_across_systems() -> None:
    now = datetime(2026, 8, 5, 10, 30, tzinfo=UTC)

    assert make_run_id(
        "EasyDep", "No Cloud KB", "P1", now=now, short_id="A1B2C3"
    ) == "easydep-no-cloud-kb-p1-20260805T103000Z-a1b2c3"
    assert make_run_id(
        "MetaGPT", "standard", "P1", now=now, short_id="D4E5F6"
    ) == "metagpt-standard-p1-20260805T103000Z-d4e5f6"


def test_identity_manifest_has_the_same_fields_for_every_runner() -> None:
    manifest = identity_manifest(
        "easydep-full-p1-20260805T103000Z-a1b2c3",
        system="easydep",
        variant="full",
        case_id="P1",
        purpose="evaluation",
        completed_stages=["requirements"],
    )

    assert list(manifest) == [
        "runId",
        "system",
        "variant",
        "caseId",
        "purpose",
        "completedStages",
    ]


def test_baselines_use_the_shared_run_directory_and_manifest(tmp_path: Path) -> None:
    for run in (cot.run, metagpt.run):
        run_dir = run(CASE, output_root=tmp_path, dry_run=True)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        assert manifest["runId"] == run_dir.name
        assert manifest["variant"] == "standard"
        assert manifest["caseId"] == "P1-detailed"
        assert manifest["purpose"] == "evaluation"
