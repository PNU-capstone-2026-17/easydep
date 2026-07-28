"""기본 OS 이미지 — cb-tumblebug이 리전마다 골라 둔 것.

`regions`·`carbon`·`lifecycle`·`latency`와 같은 자리다: **어느 KB에도 안 맞는 클라우드
사실을 kbcommon이 담고 도구 계층이 서빙한다.**

## 왜 필요한가

`bundlekb`가 "VM에는 이미지가 **필수**"라고 말하는데 **어느 이미지인지는 못 말한다.**
그 공백을 메운다.

특히 **아키텍처**가 걸린다. `g5g.xlarge`는 arm64라 x86_64 이미지로는 안 뜬다.
costkb의 `architecture`와 여기 `osArchitecture`가 조인 키다.

## 17만 건 중 5,814건만 담는다

    is_basic_image        5,814  (3.3%)   ← 담는다. tumblebug이 고른 기본값
    is_kubernetes_image     225  (0.1%)   ← 담는다
    is_gpu_image         79,617 (45.6%)   ← **안 담는다**
    is_basic_gpu_image   10,616  (6.1%)

`is_gpu_image`는 79,478건이 AWS다. 45%가 켜져 있는 플래그는 "GPU용으로 고른 것"이
아니라 "GPU 인스턴스에서 돌아갈 수 있는 것"에 가깝다 — **큐레이션 신호가 아니므로
쓰지 않는다.** 전량을 담으면 카탈로그이지 가이드라인이 아니다.

## 이건 "가장 좋은 이미지"가 아니다

**cb-tumblebug이 기본으로 고른 것**이다. 벤더 권장도, 최신도, 최적도 아니다.
`guideline-not-deployer` 원칙대로 그 경계를 답변에 밝힌다.

실측: 전부 `Linux/UNIX`이고 Windows는 기본 이미지가 없다. 프로바이더 8곳·리전 53곳.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.deployment.kbcommon import artifact
from app.deployment.kbcommon.fetch import describe_source_set

EVIDENCE = "tumblebug-basic-image"

#: 담을 큐레이션 플래그. `is_gpu_image`는 45.6%가 켜져 있어 신호가 아니다.
_KEEP_FLAGS = ("is_basic_image", "is_kubernetes_image")


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in ("t", "true", "1", "yes")


def _regions(raw: object) -> list[str]:
    """`region_list` 컬럼은 `["kr","jpn"]` 꼴의 JSON 문자열이다."""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    text = (str(raw) or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    return [str(x) for x in parsed] if isinstance(parsed, list) else [str(parsed)]


def project(rows) -> tuple[list[dict], dict]:
    """`image_infos` 행 → 기본 이미지 레코드."""
    out: list[dict] = []
    stats = {"seen": 0, "kept": 0, "no_region": 0}
    for row in rows:
        stats["seen"] += 1
        kinds = [flag for flag in _KEEP_FLAGS if _truthy(row.get(flag))]
        if not kinds:
            continue
        regions = _regions(row.get("region_list"))
        if not regions:
            stats["no_region"] += 1
            continue
        stats["kept"] += 1
        out.append(
            {
                "provider": (row.get("provider_name") or "").strip().lower(),
                "regions": regions,
                "imageId": str(row.get("csp_image_name") or "").strip(),
                "osType": (row.get("os_type") or "").strip() or None,
                "osArchitecture": (row.get("os_architecture") or "").strip() or None,
                "osDistribution": (row.get("os_distribution") or "").strip() or None,
                "kinds": kinds,
            }
        )
    return out, stats


def _schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["_note", "images"],
        "properties": {
            "_note": {"type": "string"},
            "_source": {"type": "array"},
            "_coverage": {"type": "array"},
            "images": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["provider", "regions", "imageId", "kinds"],
                    "additionalProperties": False,
                    "properties": {
                        "provider": {"type": "string"},
                        "regions": {"type": "array", "items": {"type": "string"}},
                        "imageId": {"type": "string"},
                        "osType": {"type": ["string", "null"]},
                        "osArchitecture": {"type": ["string", "null"]},
                        "osDistribution": {"type": ["string", "null"]},
                        "kinds": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }


NOTE = (
    "The **default OS image** per region. What cb-tumblebug marked with "
    "`is_basic_image` — **not the 'best image' but the one this tool picked as "
    "its default**. Neither vendor-recommended nor the newest. **Match the "
    "architecture** — an x86_64 image on an arm64 spec (g5g and the like) will "
    "not boot. The GPU flag is set on 45.6% of rows, so it is not a curation "
    "signal and is not included."
)

_ARTIFACT = "basic-images.json"


def build(output: Path, *, refresh: bool = False) -> dict:
    from app.deployment.kbcommon.tumblebug_dump import fetch_dump, iter_table_rows

    dump = fetch_dump(refresh=refresh)
    images, stats = project(iter_table_rows(dump, "image_infos"))
    providers = sorted({i["provider"] for i in images})
    regions = {(i["provider"], r) for i in images for r in i["regions"]}
    arches = sorted({i["osArchitecture"] for i in images if i["osArchitecture"]})

    dataset = {
        "_note": NOTE,
        "images": images,
        "_coverage": [
            {
                "providers": providers,
                "images": len(images),
                "regions": len(regions),
                "note": (
                    f"Of {stats['seen']:,} image_infos rows, only the "
                    f"{stats['kept']:,} with a curation flag set are kept (3.3%). "
                    "**is_gpu_image is set on 45.6% of rows, so it means closer to "
                    "'it can run' than to curation, and 79,478 of those are AWS "
                    "alone — it is not used.** Architectures "
                    f"{', '.join(arches)}. In measured runs every row is "
                    "Linux/UNIX and **there is no default Windows image** — read "
                    "that as 'this flag is not set', not as 'it does not exist'."
                ),
            }
        ],
        # **빈 배열로 두고 있었다.** 스키마가 키만 요구하고 커밋 검사도 키 존재만
        # 봐서 조용히 통과했다 — 그러면 "커밋된 데이터"가 출처 불명 덩어리가 된다.
        # 재배포 명시 검사를 넣으며 드러났다.
        "_source": [describe_source_set([dump], "tumblebug-dump")],
    }
    artifact.write_dataset(output, dataset, _schema())
    print(
        f"basic-images: {len(images):,} images ({len(providers)} providers · "
        f"{len(regions)} regions) → {output}"
    )
    return dataset


# ---------------------------------------------------------------- 조회


def _load(output_dir: str | Path = "output") -> dict | None:
    found = artifact.resolve(str(output_dir), _ARTIFACT)
    path = found if found is not None else Path(output_dir) / _ARTIFACT
    if not path.exists():
        return None
    try:
        return artifact.load_json(path)
    except Exception:
        return None


def describe(
    provider: str,
    region: str | None = None,
    architecture: str | None = None,
    *,
    limit: int = 8,
    output_dir: str | Path = "output",
) -> str:
    """이 리전에서 쓸 기본 이미지. 아키텍처를 주면 그것만."""
    data = _load(output_dir)
    if data is None:
        return (
            "No basic image data. Build it with "
            "`python -m envkb build-images`."
        )
    prov = provider.strip().lower()
    rows = [i for i in data["images"] if i["provider"] == prov]
    if not rows:
        known = sorted({i["provider"] for i in data["images"]})
        return (
            f"No basic image for '{provider}'. **That does not mean none exists "
            f"— this flag is not set.** Providers included: {', '.join(known)}"
        )
    if region:
        low = region.strip().lower()
        narrowed = [i for i in rows if any(r.lower() == low for r in i["regions"])]
        if not narrowed:
            spots = sorted({r for i in rows for r in i["regions"]})[:10]
            return (
                f"No basic image for {provider} region '{region}'. "
                f"Regions included, for example: {', '.join(spots)}"
            )
        rows = narrowed
    if architecture:
        arch = architecture.strip().lower()
        matched = [i for i in rows if (i["osArchitecture"] or "").lower() == arch]
        if not matched:
            have = sorted({i["osArchitecture"] for i in rows if i["osArchitecture"]})
            return (
                f"No '{architecture}' basic image for {provider} "
                f"{region or ''}. Architectures present: "
                f"{', '.join(have) or 'none'}"
            )
        rows = matched

    shown = rows[: max(1, limit)]
    lines = [f"{len(shown)} of {len(rows)} basic images for {provider}:"]
    for image in shown:
        tags = "/".join(k.replace("is_", "").replace("_image", "") for k in image["kinds"])
        lines.append(
            f"  - {image['osType'] or '?'} ({image['osArchitecture'] or '?'})"
            f"  {image['imageId']}"
            f"{f'  [{tags}]' if tags else ''}"
        )
    lines.append(
        "\n※ **Not the best image — the one cb-tumblebug picked as its default.** "
        "Match the architecture to the spec — an x86_64 image on an arm64 spec "
        "will not boot."
    )
    return "\n".join(lines)
