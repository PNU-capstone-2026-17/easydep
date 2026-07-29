"""번들 데이터셋 로드·조회.

capacitykb와 같은 방식이다 — 소스마다 산출물을 따로 쓰고 **읽을 때 합친다.**
소스별 핀·라이선스·갱신 주기가 다르므로 한 파일에 섞으면 어느 부분이 언제 것인지
알 수 없게 된다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.cloudkb.bundlekb.model import ALWAYS, Bundle, Companion
from app.core.cloudkb.kbcommon import artifact

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


def resolve_type_id(
    type_id: str, output_dir: Path | str | None = None
) -> tuple[str, ...]:
    """접두어 없는 벤더 타입을 **데이터에 있는 전체 키**로 넓힌다.

    지시문은 `AWS::EC2::Subnet`처럼 그대로 넘겨도 된다고 약속하는데, 이 데이터의
    키는 `aws::AWS::EC2::Subnet`이라 접두어 없이 물으면 정확히 어긋났다. 그때
    돌아간 답이 **"이 데이터셋에 없습니다"** 였다 — 있는 것을 없다고 말한 것이고,
    이 저장소가 가장 경계하는 오답이다(BU1 실측: 10회 중 2회가 이것이었고,
    도구 출력의 그 문장이 답변에 그대로 인용됐다).

    여럿에 걸리면 **고르지 않고 전부 돌려준다** — 임의로 하나를 고르는 것이
    이 저장소가 막아 온 실패다. 못 찾으면 받은 것을 그대로 돌려준다(없다는
    판정은 부르는 쪽의 몫이다).

    **접두어가 붙었는지 문자열로 판정하지 않는다.** `AWS::EC2::Instance`는 접두어
    없이도 `::`를 품고 있고, 소문자로 누르면 첫 마디가 `aws`라 프로바이더 토큰과
    구별되지 않는다. 그래서 **데이터에 그 키가 있는지 먼저 보고**, 없을 때만
    접미사로 넓힌다 — 추측이 필요 없다.
    """
    keys = _type_keys(output_dir)
    wanted = _norm(type_id)
    if wanted in keys:
        return (keys[wanted],)
    found = {
        original: None
        for normalized, original in keys.items()
        if normalized.split("::", 1)[-1] == wanted
    }
    return tuple(found) or (type_id,)


def _type_keys(output_dir: Path | str | None = None) -> dict[str, str]:
    """데이터에 실제로 있는 타입 키 전부 — 소문자 키 → 원문."""
    keys: dict[str, str] = {}
    for bundle in all_bundles(output_dir):
        for candidate in (bundle.anchor, *(m.type_id for m in bundle.members)):
            if candidate:
                keys.setdefault(_norm(candidate), candidate)
    for companion in all_companions(output_dir):
        keys.setdefault(_norm(companion.anchor), companion.anchor)
    return keys


def bundles_for(
    type_id: str, output_dir: Path | str | None = None
) -> tuple[Bundle, ...]:
    """이 타입이 **앵커이거나 무조건 구성원인** 번들.

    선택 구성원으로 걸린 것까지 주면 `roleAssignments`가 148개 번들을 물어 온다 —
    "이 리소스의 번들"이 아니라 "이걸 붙일 수 있는 모든 번들"이 되어 버린다.
    """
    wanted = {_norm(t) for t in resolve_type_id(type_id, output_dir)}
    out = []
    for bundle in all_bundles(output_dir):
        if bundle.anchor and _norm(bundle.anchor) in wanted:
            out.append(bundle)
            continue
        if any(_norm(m.type_id) in wanted and m.tier == ALWAYS for m in bundle.members):
            out.append(bundle)
    return tuple(out)


#: 같은 타입에 번들이 여럿일 때 **먼저 보는 순서**.
#:
#: 동적 생성은 "요청만 하면 도구가 하는 일"이고 나머지는 "골라야 쓰는 것"이다.
#: 둘을 같은 무게로 늘어놓으면 `core::vm`에 17개가 나오는데, 그중 16개는
#: 전 리전에 nano를 하나씩 띄우는 연결 시험 템플릿이라 "VM 하나 올리려면"의
#: 답이 아니다. **골라 주지 않으면 안 고른 것과 같다.**
_DEFAULT_ORDER = ("tumblebug-dynamic", "avm-module", "aws-solutions-construct", "kcc-sample")


def default_bundle_for(
    type_id: str, output_dir: Path | str | None = None
) -> Bundle | None:
    """이 타입에 대해 **기본으로 보여줄** 번들 하나. 없으면 None."""
    found = bundles_for(type_id, output_dir)
    if not found:
        return None

    def rank(bundle: Bundle) -> tuple[int, str]:
        try:
            return _DEFAULT_ORDER.index(bundle.evidence), bundle.id
        except ValueError:
            return len(_DEFAULT_ORDER), bundle.id

    return min(found, key=rank)


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
    wanted = {_norm(t) for t in resolve_type_id(type_id, output_dir)}
    found = [
        c
        for c in all_companions(output_dir)
        if _norm(c.anchor) in wanted and c.samples >= min_samples and c.ratio >= min_ratio
    ]
    return tuple(sorted(found, key=lambda c: -c.ratio))


def anchors(output_dir: Path | str | None = None) -> tuple[str, ...]:
    return tuple(sorted({c.anchor for c in all_companions(output_dir)}))
