"""패턴 코퍼스 로드·조회."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.cloudkb.kbcommon import artifact
from app.core.cloudkb.patternkb.model import Doc

_SCHEMA_PATH = Path(__file__).with_name("schema.json")

DEFAULT_OUTPUT_DIR = Path("output")

CORPUS_FILE = "pattern-corpus.json"

#: AWS WAF 코퍼스 — 별도 산출물로 병합해 읽는다. 원본이 All rights reserved라
#: **교육 목적 공정이용 판단으로 수록**했다(사용자 결정 2026-07-25 — 소스 등록부
#: `redistribution="fair-use"`와 NOTICE 참조). 파일이 없는 환경(공정이용 판단을
#: 공유하지 않는 포크 등)과 수록 없음을 가르는 것은 `aws_built()`다.
AWS_CORPUS_FILE = "aws-pattern-corpus.json"


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve(output_dir: Path | str | None) -> str:
    return str(DEFAULT_OUTPUT_DIR if output_dir is None else output_dir)


@lru_cache(maxsize=4)
def _load_file(output_dir: str, filename: str) -> tuple[tuple[Doc, ...], tuple[str, ...]]:
    found = artifact.resolve(output_dir, filename)
    path = found if found is not None else Path(output_dir) / filename
    if not path.exists():
        return (), ()
    data, error = artifact.read_dataset(path, schema())
    if error:
        return (), (error,)
    return tuple(Doc.from_dict(d) for d in data.get("docs") or []), ()


def _load(output_dir: str) -> tuple[tuple[Doc, ...], tuple[str, ...]]:
    """커밋 코퍼스 + (있으면) 로컬 전용 aws 코퍼스. 경고는 합친다."""
    docs, warnings = _load_file(output_dir, CORPUS_FILE)
    aws_docs, aws_warnings = _load_file(output_dir, AWS_CORPUS_FILE)
    return docs + aws_docs, warnings + aws_warnings


def aws_built(output_dir: Path | str | None = None) -> bool:
    """aws 지침 코퍼스가 이 환경에 있는가.

    병합된 뒤에는 "검색에 안 나옴"과 "미빌드"를 가를 수 없다 — aws 코퍼스는
    커밋되지 않아 보통 없으므로, 그 부재가 "aws 지침은 존재하지 않는다"로
    읽히면 거짓이 된다. 표시층이 이걸로 가른다.
    """
    return artifact.resolve(_resolve(output_dir), AWS_CORPUS_FILE) is not None


def clear_caches() -> None:
    _load_file.cache_clear()
    schema.cache_clear()
    # 색인은 문서 튜플에서 만들므로 함께 비워야 한다 — 아니면 옛 코퍼스를 검색한다.
    from app.core.cloudkb.patternkb import query

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
