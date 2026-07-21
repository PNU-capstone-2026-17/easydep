"""PURE 데이터셋(materials/PURE_Dataset)에서 요구사항 문장을 추출한다.

PURE는 XML SRS 문서 모음이다. 실제 요구사항은 `<req>...</req>` 블록에 들어있다(문서에 따라
있기도/없기도). 내부 태그를 제거하고 텍스트만 뽑아, 너무 짧거나 긴 것은 걸러 요구사항 문장으로 쓴다.

`load_pure(path, sample=N)` — 한 문서에서 요구사항 리스트(균등 샘플 N개)를 반환.
`pure_docs()` — <req>를 가진 문서(경로) 목록.
CLI: `python -m scripts.pure_extract [--sample N]` — 문서별 추출 개수 미리보기.
"""
from __future__ import annotations

import glob
import os
import re

PURE_DIR = "materials/PURE_Dataset/req_documents"
_REQ_RE = re.compile(r"<req\b[^>]*>(.*?)</req>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(block: str) -> str:
    txt = _TAG_RE.sub(" ", block)
    txt = txt.replace("“", '"').replace("”", '"')  # PURE의 깨진 따옴표 정리
    return _WS_RE.sub(" ", txt).strip()


def extract_reqs(path: str, min_len: int = 20, max_len: int = 400) -> list[str]:
    """한 PURE XML 문서에서 정제된 요구사항 문장 전체를 순서대로 반환."""
    raw = open(path, encoding="utf-8", errors="replace").read()
    out: list[str] = []
    seen: set[str] = set()
    for block in _REQ_RE.findall(raw):
        t = _clean(block)
        if min_len <= len(t) <= max_len and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _even_sample(items: list[str], n: int) -> list[str]:
    """앞/중간/뒤가 고루 섞이도록 균등 간격 샘플(결정론적, 순서 보존)."""
    if n <= 0 or n >= len(items):
        return items
    step = len(items) / n
    idx = sorted({min(len(items) - 1, int(i * step)) for i in range(n)})
    return [items[i] for i in idx]


def load_pure(path: str, sample: int = 0) -> list[str]:
    """문서 경로에서 요구사항을 추출하고, sample>0이면 균등 샘플 N개로 줄인다."""
    reqs = extract_reqs(path)
    return _even_sample(reqs, sample) if sample else reqs


def pure_docs() -> list[str]:
    """<req> 요구사항을 가진 PURE 문서 경로 목록(요구 수 오름차순)."""
    docs = [(p, len(extract_reqs(p))) for p in sorted(glob.glob(os.path.join(PURE_DIR, "*.xml")))]
    return [p for p, n in sorted(docs, key=lambda x: x[1]) if n > 0]


def doc_name(path: str) -> str:
    """파일 경로 → 짧은 데이터셋 이름(예: '2008 - keepass.xml' → 'keepass')."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"^\d[\d\s\-\.]*", "", stem)  # 앞의 연도/번호 제거
    return _WS_RE.sub("_", stem.strip()).lower() or "doc"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PURE 요구사항 추출 미리보기")
    parser.add_argument("--sample", type=int, default=0, help="문서당 균등 샘플 개수(0=전체)")
    parser.add_argument("--show", action="store_true", help="샘플 문장도 출력")
    args = parser.parse_args()

    for path in pure_docs():
        reqs = load_pure(path, sample=args.sample)
        print(f"{len(reqs):4d}  {doc_name(path):14s}  ({os.path.basename(path)})")
        if args.show:
            for r in reqs[:3]:
                print(f"      - {r[:120]}")
