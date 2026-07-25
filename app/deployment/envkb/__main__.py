"""envkb 빌드 CLI. `python -m envkb build-<축>`

kbcommon CLI에서 이사 왔다(재편 계획 ⑤) — 빌드는 이 KB의 일이고, kbcommon에는
KB 사이의 정합성 검사(verify)만 남는다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from kbcommon.console import use_utf8

DEFAULT_OUTPUT = Path("output")

#: 축 이름 → (모듈 이름, 기본 산출물 파일, 도움말).
_AXES = {
    "regions": ("cloudinfo", "cloud-regions.json",
                "Region names and locations per provider (cb-tumblebug cloudinfo)"),
    "images": ("images", "basic-images.json",
               "Default OS image per region (cb-tumblebug curation)"),
    "latency": ("latency", "region-latency.json",
                "Network latency between regions (cb-tumblebug benchmark)"),
    "carbon": ("carbon", "region-carbon.json",
               "Carbon per region (GCP published + Cloud Carbon Footprint estimate)"),
    "lifecycle": ("lifecycle", "service-lifecycle.json",
                  "End-of-support dates per managed service version (endoflife.date)"),
    "cbspider": ("cbspider", "cbspider-support.json",
                 "What can be created on each CSP (cb-spider drivers)"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="envkb", description="Build the cloud environment facts KB"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for axis, (_, filename, help_text) in _AXES.items():
        cmd = sub.add_parser(f"build-{axis}", help=help_text)
        cmd.add_argument(
            "--refresh", action="store_true", help="Ignore the cache and re-fetch"
        )
        cmd.add_argument(
            "--output", type=Path, help=f"Output path (default: output/{filename})"
        )

    args = parser.parse_args(argv)
    axis = args.command.removeprefix("build-")
    module_name, filename, _ = _AXES[axis]
    import importlib

    module = importlib.import_module(f"envkb.{module_name}")
    module.build(args.output or (DEFAULT_OUTPUT / filename), refresh=args.refresh)
    return 0


if __name__ == "__main__":
    use_utf8()
    raise SystemExit(main())
