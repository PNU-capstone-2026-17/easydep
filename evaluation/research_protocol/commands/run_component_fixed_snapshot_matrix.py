"""고정 앱·요구분석 입력에서 component VM-delivery 절제 매트릭스를 실행한다."""

from __future__ import annotations

import argparse
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.orchestration.adapters.testing import TestingAdapter
from evaluation.research_protocol.core.component_snapshot_delivery import (
    read_protocol_json,
    run_delivery_cell,
)
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT
from evaluation.research_protocol.core.snapshot_support import (
    copy_source,
    tree_sha256,
)
from evaluation.research_protocol.core.support import (
    canonical_json_sha256,
    read_json,
    write_json_atomic,
)

DEFAULT_CONFIG = Path(
    "evaluation/research_protocol/protocols/component-fixed-input-config.json"
)
ROOT = REPOSITORY_ROOT


def _measurement_needs(measurement: dict[str, Any], axis: str) -> dict[str, Any]:
    matching = [
        item for item in measurement.get("cells") or [] if item.get("axis") == axis
    ]
    if len(matching) != 1:
        raise ValueError(f"측정 축은 정확히 한 셀이어야 한다: {axis}")
    return deepcopy(matching[0].get("deploymentNeeds") or {})


def _needs_for_condition(
    measurement: dict[str, Any], pair: dict[str, Any], condition: str
) -> dict[str, Any]:
    if condition == "treatment":
        return _measurement_needs(measurement, pair["measurementAxis"])
    control_axis = pair.get("controlMeasurementAxis")
    if control_axis:
        return _measurement_needs(measurement, control_axis)
    capability_ids = set(pair.get("capabilityIds") or [])
    treatment = _measurement_needs(measurement, pair["measurementAxis"])
    return {
        key: value
        for key, value in treatment.items()
        if not capability_ids.intersection(
            set((value or {}).get("dependencyCapabilityIds") or [])
        )
    }


def _neutral_design() -> dict[str, Any]:
    diagram = "\n".join(
        [
            "@startuml",
            'component "Generated application" as app',
            'artifact "Docker image" as image',
            "image --> app : runs",
            "@enduml",
        ]
    )
    return {
        "status": "completed",
        "deployment_diagram_puml": diagram,
        "artifacts": {"deployment_diagram": diagram},
    }


def _selected_cases(
    suite: dict[str, Any], *, conditions: set[str], case_ids: set[str]
) -> list[dict[str, Any]]:
    cases = []
    for filename in suite["development"]:
        case = read_protocol_json(
            Path("evaluation/baselines/component-cases") / filename
        )
        condition = str((case.get("scope") or {}).get("condition") or "")
        if condition not in conditions:
            continue
        if case_ids and case["caseId"] not in case_ids:
            continue
        cases.append(case)
    return cases


def run(
    *,
    config_path: Path,
    output_root: Path,
    output: Path,
    conditions: set[str],
    arms: list[str],
    case_ids: set[str],
    resume: bool,
) -> dict[str, Any]:
    config = read_protocol_json(config_path)
    suite = read_protocol_json(Path(config["suite"]))
    oracle = read_protocol_json(Path(config["oracle"]))
    measurement = read_protocol_json(Path(config["measurement"]))
    cases = _selected_cases(suite, conditions=conditions, case_ids=case_ids)
    if not cases:
        raise ValueError("선택된 component case가 없다")
    config_hash = canonical_json_sha256(config)
    result: dict[str, Any]
    if resume and output.is_file():
        result = read_json(output)
        if result.get("configSha256") != config_hash:
            raise ValueError("기존 결과의 설정 hash가 현재 설정과 다르다")
    else:
        if output_root.exists():
            raise FileExistsError(f"출력 경로가 이미 존재한다: {output_root}")
        output_root.mkdir(parents=True)
        result = {
            "schemaVersion": "easydep-component-fixed-snapshot-matrix/v1",
            "createdAt": datetime.now(UTC).isoformat(),
            "config": config_path.as_posix(),
            "configSha256": config_hash,
            "configuration": {
                "conditions": sorted(conditions),
                "arms": arms,
                "requirementsLlmCalls": 0,
                "designLlmCalls": 0,
                "applicationGenerationCalls": 0,
                "cloudApply": False,
            },
            "snapshots": {},
            "cells": [],
        }
    completed = {(item["caseId"], item["arm"]) for item in result["cells"]}
    design = _neutral_design()
    for case in cases:
        scope = case["scope"]
        pair_id = scope["pairId"]
        pair = config["pairs"][pair_id]
        source = (ROOT / pair["sourceApplication"]).resolve()
        source_artifact_sha = tree_sha256(source)
        if pair_id not in result["snapshots"]:
            snapshot_root = output_root / "snapshot-tests" / pair_id
            application = snapshot_root / "application"
            application.parent.mkdir(parents=True, exist_ok=True)
            copy_source(source, application)
            shutil.rmtree(application / "infra", ignore_errors=True)
            source_sha = tree_sha256(application)
            result["snapshots"][pair_id] = {
                "source": pair["sourceApplication"],
                "sourceArtifactSha256": source_artifact_sha,
                "sha256": source_sha,
                "applicationTests": TestingAdapter(timeout_seconds=900).run(
                    implementation_result={"run_root": str(snapshot_root)},
                    case_id=f"{pair_id}-fixed-snapshot",
                ),
            }
            write_json_atomic(output, result)
        source_sha = result["snapshots"][pair_id]["sha256"]
        condition = scope["condition"]
        needs = _needs_for_condition(measurement, pair, condition)
        for arm in arms:
            if (case["caseId"], arm) in completed:
                continue
            row = run_delivery_cell(
                source=source,
                output_root=output_root,
                condition=condition,
                arm=arm,
                case=case,
                cell_name=f"{case['caseId'].lower()}-{arm}",
                base_requirements={},
                design=design,
                needs=needs,
                oracle=oracle,
            )
            row["pairId"] = pair_id
            row["sourceApplicationSha256"] = source_sha
            result["cells"].append(row)
            write_json_atomic(output, result)
    result["completedAt"] = datetime.now(UTC).isoformat()
    result["summary"] = {
        "cellCount": len(result["cells"]),
        "completedDeliveryCount": sum(
            item["stepStatus"] == "completed" for item in result["cells"]
        ),
        "sameSnapshotCount": sum(
            item["inputApplicationSha256"] == item["sourceApplicationSha256"]
            for item in result["cells"]
        ),
    }
    write_json_atomic(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--condition", action="append", choices=("control", "treatment")
    )
    parser.add_argument("--arm", action="append", choices=("full", "no-depkb"))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(
        config_path=args.config,
        output_root=args.output_root,
        output=args.output,
        conditions=set(args.condition or ["treatment"]),
        arms=args.arm or ["full", "no-depkb"],
        case_ids=set(args.case),
        resume=args.resume,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
