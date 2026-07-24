"""패턴 코퍼스 로드·조회."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from kbcommon import artifact
from patternkb.model import Doc

_SCHEMA_PATH = Path(__file__).with_name("schema.json")

DEFAULT_OUTPUT_DIR = Path("output")

CORPUS_FILE = "pattern-corpus.json"


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve(output_dir: Path | str | None) -> str:
    return str(DEFAULT_OUTPUT_DIR if output_dir is None else output_dir)


@lru_cache(maxsize=4)
def _load(output_dir: str) -> tuple[tuple[Doc, ...], tuple[str, ...]]:
    found = artifact.resolve(output_dir, CORPUS_FILE)
    path = found if found is not None else Path(output_dir) / CORPUS_FILE
    if not path.exists():
        return (), ()
    data, error = artifact.read_dataset(path, schema())
    if error:
        return (), (error,)
    return tuple(Doc.from_dict(d) for d in data.get("docs") or []), ()


def clear_caches() -> None:
    _load.cache_clear()
    schema.cache_clear()
    # 색인은 문서 튜플에서 만들므로 함께 비워야 한다 — 아니면 옛 코퍼스를 검색한다.
    from patternkb import query

    query.clear_caches()


def is_built(output_dir: Path | str | None = None) -> bool:
    return bool(_load(_resolve(output_dir))[0])


def load_warnings(output_dir: Path | str | None = None) -> tuple[str, ...]:
    return _load(_resolve(output_dir))[1]


def all_docs(output_dir: Path | str | None = None) -> tuple[Doc, ...]:
    return _load(_resolve(output_dir))[0]


def sections(output_dir: Path | str | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for doc in all_docs(output_dir):
        counts[doc.section] = counts.get(doc.section, 0) + 1
    return counts
