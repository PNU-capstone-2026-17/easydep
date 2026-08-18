"""Generate the complete deterministic Docker-on-VM deployment diagram corpus.

The finite topology model has 9 logical families.  Projecting each family to
AWS, Azure, and Google Cloud yields 27 provider-labelled families.  Each family
has two deliberately separate views:

* runtime: placement plus request/data flow
* provisioning: prerequisite -> dependent creation relationships

PUML is the source artifact.  SVG is rendered with the exact PlantUML image
digest used by the application.  ``--check`` renders into one system temporary
directory and byte-compares the result with the checked-in corpus.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.design.services.common.plantuml import PLANTUML_IMAGE  # noqa: E402
from app.design.services.deployment_diagram.bundle import (  # noqa: E402
    build_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.provider_plantuml import (  # noqa: E402
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.topology import (  # noqa: E402
    TopologyFamily,
    enumerate_topology_families,
)

OUTPUT_ROOT = ROOT / "docs" / "examples" / "deployment-diagrams"
TARGETS = {
    "aws": ("ap-northeast-2", ("ap-northeast-2a", "ap-northeast-2b")),
    "azure": ("koreacentral", ("1", "2")),
    "gcp": ("asia-northeast3", ("asia-northeast3-a", "asia-northeast3-b")),
}


def _logical_model(database_placement: str) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {
            "name": "Application Runtime",
            "kind": "executionEnvironment",
            "source_classes": ["Application"],
        }
    ]
    connections: list[dict[str, Any]] = []
    if database_placement != "none":
        nodes.append(
            {
                "name": "PostgreSQL",
                "kind": "database",
                "source_classes": ["Database"],
            }
        )
        connections.append(
            {
                "source": "Application Runtime",
                "target": "PostgreSQL",
                "protocol": "PostgreSQL protocol",
            }
        )
    return {
        "Nodes": nodes,
        "Artifacts": [
            {
                "name": "application-image",
                "deployed_on": "Application Runtime",
                "source_classes": ["Application"],
            }
        ],
        "Connections": connections,
    }


def _resource_spec(family: TopologyFamily) -> dict[str, Any]:
    if family.provider is None:
        raise ValueError("Example generation requires a provider-labelled family.")
    region, available_zones = TARGETS[family.provider]
    multi_zone = family.compute_profile == "managedGroupManyMultiZone"
    many = family.compute_profile.startswith("managedGroupMany")
    selected_zones = list(available_zones if multi_zone else available_zones[:1])
    return {
        "schemaVersion": "3",
        "workloads": ["vm"],
        "provider": family.provider,
        "region": region,
        "deploymentTargets": [
            {
                "provider": family.provider,
                "region": region,
                "zones": selected_zones,
            }
        ],
        "computeProfile": family.compute_profile,
        "replicaCount": 2 if many else 1,
        "applicationStateless": bool(many),
        "publicIngress": family.public_ingress,
        "ingressZones": (
            list(available_zones[:2])
            if family.provider == "aws" and family.public_ingress == "loadBalanced"
            else selected_zones
        ),
        "databasePlacement": family.database_placement,
    }


def _relative_sources() -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    families = enumerate_topology_families(include_providers=True)
    if len(families) != 27:
        raise RuntimeError(f"Expected 27 provider-labelled families, got {len(families)}.")
    for family in families:
        assert family.provider is not None
        logical_model = _logical_model(family.database_placement)
        resource_spec = _resource_spec(family)
        first = build_deployment_diagram_bundle(logical_model, resource_spec)
        second = build_deployment_diagram_bundle(logical_model, resource_spec)
        if first != second:
            raise RuntimeError(f"Bundle generation is not deterministic: {family.id}")
        projection = first["projections"][0]
        if projection.get("status") != "completed":
            raise RuntimeError(f"Unresolved example family {family.id}: {projection}")
        actual_family_id = projection.get("topology", {}).get("familyId")
        if actual_family_id != family.id:
            raise RuntimeError(
                f"Family mismatch: enumerated {family.id}, projected {actual_family_id}"
            )
        stem = family.id.removeprefix(f"{family.provider}.")
        provider_dir = Path(family.provider)
        outputs[provider_dir / f"{stem}.runtime.puml"] = (
            deployment_bundle_runtime_puml(first).rstrip() + "\n"
        )
        outputs[provider_dir / f"{stem}.provisioning.puml"] = (
            deployment_bundle_provisioning_puml(first).rstrip() + "\n"
        )
    if len(outputs) != 54:
        raise RuntimeError(f"Expected 54 semantic views, got {len(outputs)}.")
    return outputs


def _write_sources(root: Path, sources: dict[Path, str]) -> None:
    for relative, source in sources.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8", newline="\n")


def _render_svgs(root: Path, sources: dict[Path, str]) -> None:
    container_inputs = [f"/work/{path.as_posix()}" for path in sorted(sources)]
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{root.resolve()}:/work",
        PLANTUML_IMAGE,
        "-charset",
        "UTF-8",
        "-tsvg",
        *container_inputs,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"PlantUML SVG rendering failed:\n{detail}")
    missing = [
        relative.with_suffix(".svg")
        for relative in sources
        if not (root / relative.with_suffix(".svg")).is_file()
    ]
    if missing:
        raise RuntimeError(f"PlantUML did not create {len(missing)} SVG files: {missing}")


def _expected_files(sources: dict[Path, str]) -> set[Path]:
    return set(sources) | {path.with_suffix(".svg") for path in sources}


def generate() -> None:
    sources = _relative_sources()
    _write_sources(OUTPUT_ROOT, sources)
    _render_svgs(OUTPUT_ROOT, sources)
    actual = {path.relative_to(OUTPUT_ROOT) for path in OUTPUT_ROOT.rglob("*") if path.is_file()}
    unexpected = actual - _expected_files(sources)
    if unexpected:
        raise RuntimeError(f"Unexpected files in example corpus: {sorted(unexpected)}")
    print(
        f"Generated {len(sources)} diagrams / {len(actual)} PUML+SVG files with {PLANTUML_IMAGE}."
    )


def check() -> None:
    sources = _relative_sources()
    expected = _expected_files(sources)
    checked_in = {
        path.relative_to(OUTPUT_ROOT) for path in OUTPUT_ROOT.rglob("*") if path.is_file()
    }
    if checked_in != expected:
        missing = sorted(expected - checked_in)
        extra = sorted(checked_in - expected)
        raise RuntimeError(f"Corpus file mismatch. Missing={missing}; extra={extra}")
    with tempfile.TemporaryDirectory(prefix="easydep-diagram-examples-") as temp_name:
        temporary_root = Path(temp_name)
        _write_sources(temporary_root, sources)
        _render_svgs(temporary_root, sources)
        mismatches = [
            relative
            for relative in sorted(expected)
            if (OUTPUT_ROOT / relative).read_bytes() != (temporary_root / relative).read_bytes()
        ]
    if mismatches:
        raise RuntimeError(f"Non-reproducible checked-in diagrams: {mismatches}")
    print(
        f"Verified {len(sources)} deterministic diagrams / {len(expected)} files "
        f"with {PLANTUML_IMAGE}."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in a temporary directory and byte-compare with checked-in files.",
    )
    arguments = parser.parse_args()
    if arguments.check:
        check()
    else:
        generate()


if __name__ == "__main__":
    main()
