"""번들 데이터셋 로드·조회.

capacitykb와 같은 방식이다 — 소스마다 산출물을 따로 쓰고 **읽을 때 합친다.**
소스별 핀·라이선스·갱신 주기가 다르므로 한 파일에 섞으면 어느 부분이 언제 것인지
알 수 없게 된다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from bundlekb.model import ALWAYS, Bundle, Companion
from kbcommon import artifact

_SCHEMA_PATH = Path(__file__).with_name("schema.json")

DEFAULT_OUTPUT_DIR = Path("output")

#: 소스마다 하나씩. 없는 파일은 조용히 건너뛴다 — 일부만 빌드해도 동작해야 한다.
BUNDLE_FILES = (
    "avm-bundles.json",
    "tumblebug-bundles.json",
    "aqt-cooccurrence.json",
    "aws-pattern-bundles.json",
    "awscfn-cooccurrence.json",
    "kcc-bundles.json",
)

#: 이 아래 표본에서는 비율을 내지 않는다. 6/17을 35.3%로 읽게 하지 않기 위한 선.
MIN_SAMPLES = 20


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve(output_dir: Path | str | None) -> str:
    return str(DEFAULT_OUTPUT_DIR if output_dir is None else output_dir)


@lru_cache(maxsize=4)
def _load(output_dir: str) -> tuple[tuple[Bundle, ...], tuple[Companion, ...], tuple[str, ...]]:
    bundles: list[Bundle] = []
    companions: list[Companion] = []
    warnings: list[str] = []
    for name in BUNDLE_FILES:
        found = artifact.resolve(output_dir, name)
        path = found if found is not None else Path(output_dir) / name
        if not path.exists():
            continue
        data, error = artifact.read_dataset(path, schema())
        if error:
            # 조용한 폴백은 "커버리지가 왜 좁아졌지?"를 미궁으로 만든다.
            warnings.append(error)
            continue
        bundles.extend(Bundle.from_dict(r) for r in data.get("bundles") or [])
        companions.extend(Companion.from_dict(r) for r in data.get("cooccurrence") or [])
    return tuple(bundles), tuple(companions), tuple(warnings)


def clear_caches() -> None:
    """테스트가 output_dir을 갈아끼울 때. 캐시가 늘면 여기만 고친다."""
    _load.cache_clear()
    schema.cache_clear()


def is_built(output_dir: Path | str | None = None) -> bool:
    loaded = _load(_resolve(output_dir))
    return bool(loaded[0] or loaded[1])


def load_warnings(output_dir: Path | str | None = None) -> tuple[str, ...]:
    return _load(_resolve(output_dir))[2]


def all_bundles(output_dir: Path | str | None = None) -> tuple[Bundle, ...]:
    return _load(_resolve(output_dir))[0]


def all_companions(output_dir: Path | str | None = None) -> tuple[Companion, ...]:
    return _load(_resolve(output_dir))[1]


def _norm(type_name: str) -> str:
    return type_name.strip().lower()


def bundles_for(
    type_id: str, output_dir: Path | str | None = None
) -> tuple[Bundle, ...]:
    """이 타입이 **앵커이거나 무조건 구성원인** 번들.

    선택 구성원으로 걸린 것까지 주면 `roleAssignments`가 148개 번들을 물어 온다 —
    "이 리소스의 번들"이 아니라 "이걸 붙일 수 있는 모든 번들"이 되어 버린다.
    """
    wanted = _norm(type_id)
    out = []
    for bundle in all_bundles(output_dir):
        if bundle.anchor and _norm(bundle.anchor) == wanted:
            out.append(bundle)
            continue
        if any(_norm(m.type_id) == wanted and m.tier == ALWAYS for m in bundle.members):
            out.append(bundle)
    return tuple(out)


def find_bundle(name: str, output_dir: Path | str | None = None) -> Bundle | None:
    wanted = _norm(name)
    for bundle in all_bundles(output_dir):
        if _norm(bundle.id) == wanted or _norm(bundle.name) == wanted:
            return bundle
    return None


def companions_of(
    type_id: str,
    *,
    min_ratio: float = 0.0,
    min_samples: int = MIN_SAMPLES,
    output_dir: Path | str | None = None,
) -> tuple[Companion, ...]:
    """앵커와 함께 나온 타입들. 비율 높은 순.

    `min_samples` 아래는 **아예 돌려주지 않는다.** 17개 중 6개를 35.3%로 보여주면
    숫자에 없는 확신을 주게 된다.
    """
    wanted = _norm(type_id)
    found = [
        c
        for c in all_companions(output_dir)
        if _norm(c.anchor) == wanted and c.samples >= min_samples and c.ratio >= min_ratio
    ]
    return tuple(sorted(found, key=lambda c: -c.ratio))


def anchors(output_dir: Path | str | None = None) -> tuple[str, ...]:
    return tuple(sorted({c.anchor for c in all_companions(output_dir)}))
