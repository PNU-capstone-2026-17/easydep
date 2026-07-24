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
                "프로바이더별 리전 이름·위치 (cb-tumblebug cloudinfo)"),
    "images": ("images", "basic-images.json",
               "리전별 기본 OS 이미지 (cb-tumblebug 큐레이션)"),
    "latency": ("latency", "region-latency.json",
                "리전 간 네트워크 지연 (cb-tumblebug 벤치마크 실측)"),
    "carbon": ("carbon", "region-carbon.json",
               "리전별 탄소 (GCP 발표 + Cloud Carbon Footprint 추정)"),
    "lifecycle": ("lifecycle", "service-lifecycle.json",
                  "관리형 서비스 버전별 지원 종료일 (endoflife.date)"),
    "cbspider": ("cbspider", "cbspider-support.json",
                 "CSP별로 무엇을 만들 수 있는가 (cb-spider 드라이버)"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="envkb", description="클라우드 환경 사실 KB 빌드")
    sub = parser.add_subparsers(dest="command", required=True)
    for axis, (_, filename, help_text) in _AXES.items():
        cmd = sub.add_parser(f"build-{axis}", help=help_text)
        cmd.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받기")
        cmd.add_argument("--output", type=Path, help=f"출력 경로 (기본: output/{filename})")

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
