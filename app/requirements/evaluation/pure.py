"""PURE 코퍼스에서 파이프라인 입력을 뽑는다 — **외부에서 온 요구사항**으로 재기 위해.

## 왜 외부 코퍼스인가

`inputs/*.json`은 우리가 쓴 요구사항이다. 그걸로 잰 수치는 "우리가 만든 입력에서 이렇게
동작한다"까지만 말한다. 규칙의 신뢰도나 파이프라인 품질을 주장하려면 **입력의 근거가 우리
밖에** 있어야 한다.

PURE는 공개 요구사항 문서 79건을 모은 데이터셋이고(그중 18건이 XML), 인용 가능하다:

    Ferrari, A., Spagnolo, G. O., Gnesi, S. (2017).
    "PURE: A Dataset of Public Requirements Documents." RE'17, pp. 502-505.
    DOI 10.5281/zenodo.7118517 · 데이터셋 라이선스 CC-BY-4.0

## 저장소에는 원문을 담지 않는다

데이터셋은 CC-BY지만 **큐레이터가 "포함된 요구사항 문서들의 IP는 알지 못한다"고 명시한다.**
이 저장소는 같은 이유로 도서 한 권을 히스토리에서 지웠다(`d1a7ec5`). 그래서 같은 규율을 쓴다:

  - 원본은 `materials/PURE/`에 로컬로 두고 **gitignore**한다.
  - 파생 입력(JSON)도 원문 문장을 담으므로 그 아래에 쓰고 커밋하지 않는다.
  - 저장소에 남는 것은 **이 추출기와 인용**이다. 사본을 가진 사람이 같은 입력을 재현한다.

## FR/NFR은 우리 분류기가 붙인다

PURE의 `req`에는 FR/NFR 라벨이 없다. 손으로 붙이면 그 라벨이 우리 판단이 되므로,
이 프로젝트의 파인튜닝 BERT(`classifier.classify_bert`)에 맡긴다 — step1이 하는 그 일이고
결정론이다. 분류기를 못 쓰면 전부 FR로 두고 그 사실을 산출물에 적는다.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

#: 원본이 놓이는 자리(gitignore됨). 파생 입력도 이 아래에 쓴다.
PURE_ROOT = Path("materials/PURE")
DOCUMENTS = PURE_ROOT / "req_documents"
DERIVED = PURE_ROOT / "derived"

_NS = "{req_document.xsd}"
#: 산문에서 요구사항 문장을 고르는 신호. RFC 2119의 조동사이고, PURE 문서 16/18에 나온다.
#: **완전하지 않다** — 조동사 없이 쓴 요구사항은 놓친다. 그 대신 잘못 넣는 것이 적다.
_MODAL = re.compile(r"\b(shall|must|should)\b", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+")
#: 조동사가 있어도 **요구사항이 아닌** 문장들. 실제로 뽑아 보고 걸러낸 것들이다:
#:   themas   "Only those conditions expressed with the imperative \"shall\" are to be
#:             interpreted as binding" — 문서가 자기 표기법을 설명하는 문장
#:   peppol   "It should be noted that such a VCD concept currently does not exist."
#:   blitdraft "Use Case: xxx Page, UC ID: UC_RR_xxx_xxx_000" — 채우지 않은 템플릿
#: 이런 것을 요구사항으로 넣으면 그 아래 명세도 쓰레기가 되고, **외부 근거가 외부 잡음**이 된다.
#: 완전한 목록이 아니다 — 남는 잡음은 파생 파일에 세어 적는다.
_META = re.compile(
    r"(should be noted|shall be (interpreted|construed)|is to be interpreted"
    r"|are to be interp\s*reted|this (document|section|specification|srs)"
    r"|following subsections|use case:\s*xxx|UC_[A-Z]{2}_|\bxxx\b"
    r"|shall mean|for the purpose[s]? of this)",
    re.IGNORECASE,
)
#: `REQ-12:` / `3.1.4` 같은 머리표를 문장에서 떼어낸다 — 우리 파이프라인은 문장을 받는다.
_PREFIX = re.compile(r"^\s*(REQ[-_ ]?\d+[.:]?|\d+(\.\d+)*[.):]?)\s*", re.IGNORECASE)
_WS = re.compile(r"\s+")

#: 이 인용이 곧 근거다. 파생 파일마다 함께 싣는다.
CITATION = (
    "Ferrari, Spagnolo, Gnesi. \"PURE: A Dataset of Public Requirements Documents.\" "
    "RE'17, pp.502-505. DOI 10.5281/zenodo.7118517 (dataset licence CC-BY-4.0)"
)


def documents() -> list[tuple[str, int, str]]:
    """`(문서 이름, req 개수, 제목)` 목록. req가 없는 문서는 이 파이프라인에 못 쓴다."""
    out: list[tuple[str, int, str]] = []
    for path in sorted(DOCUMENTS.glob("*.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        reqs = [e for e in root.iter() if e.tag == f"{_NS}req"]
        title = next(
            (e.text or "").strip() for e in root.iter() if e.tag == f"{_NS}title"
        )
        out.append((path.stem, len(reqs), title))
    return out


def _prose_sentences(root: ET.Element, min_words: int, max_words: int) -> list[str]:
    """`<req>`가 없는 문서에서 요구사항 문장을 캔다.

    PURE 18개 중 12개는 `<req>` 요소가 없고 요구사항이 단락(`p`·`item`) 산문에 들어 있다.
    그 문서를 못 쓰면 외부 근거가 6개 문서로 줄어든다 — 그래서 조동사(shall/must/should)로
    문장을 고른다. **재현율을 포기하고 정밀도를 택한 것이다**: 조동사 없이 쓴 요구사항은
    놓치지만, 설명문을 요구사항으로 잘못 넣는 일이 적다.
    """
    out: list[str] = []
    dropped = 0
    for element in root.iter():
        if element.tag not in (f"{_NS}p", f"{_NS}item"):
            continue
        text = _WS.sub(" ", " ".join(t.strip() for t in element.itertext() if t and t.strip()))
        for sentence in _SENTENCE_SPLIT.split(text):
            sentence = _PREFIX.sub("", sentence).strip()
            if not _MODAL.search(sentence):
                continue
            if not (min_words <= len(sentence.split()) <= max_words):
                continue
            if _META.search(sentence):
                dropped += 1
                continue
            out.append(sentence)
    # 버린 수를 돌려준다 — 조용히 버리면 추출이 얼마나 걸러 냈는지 알 수 없다.
    _prose_sentences.dropped_meta = dropped  # type: ignore[attr-defined]
    return out


def _even_sample(items: list[str], limit: int) -> list[str]:
    """문서 전체에서 **고르게** 고른다.

    앞에서 N개를 자르면 문서 구조에 편향된다 — cctns의 앞 40개를 뽑았더니 NFR 34 / FR 6이
    나왔다(문서 앞부분이 사용성·도움말 절이었다). 고르게 뽑으면 그 편향이 사라진다.
    """
    if limit >= len(items):
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def _sentence(element: ET.Element) -> str:
    """`req` 하나의 텍스트를 한 문장으로 평평하게 만든다(머리표 제거)."""
    text = " ".join(t.strip() for t in element.itertext() if t and t.strip())
    return _PREFIX.sub("", _WS.sub(" ", text)).strip()


def extract(name: str, limit: int | None = None, min_words: int = 5) -> dict:
    """문서 하나에서 파이프라인 입력을 만든다(FR/NFR은 BERT가 붙인다).

    `min_words`보다 짧은 조각은 버린다 — 표 제목·번호만 있는 `req`가 섞여 있고, 그것들은
    요구사항이 아니라 문서 구조다.
    """
    path = DOCUMENTS / f"{name}.xml"
    root = ET.parse(path).getroot()
    tagged = [
        s for s in (_sentence(e) for e in root.iter() if e.tag == f"{_NS}req")
        if len(s.split()) >= min_words
    ]
    # `<req>`가 있으면 그것이 문서가 스스로 표시한 요구사항이니 먼저 쓴다. 없으면 산문에서 캔다.
    source_kind = "req-elements" if tagged else "prose-modal"
    sentences = tagged or _prose_sentences(root, min_words, max_words=60)
    if limit:
        sentences = _even_sample(sentences, limit)

    from app.requirements.classifier import bert_available, classify_bert

    available = bert_available()
    counters = {"FR": 0, "NFR": 0}
    classified = []
    for text in sentences:
        req_type = "FR"
        if available:
            verdict = classify_bert(text)
            if verdict is not None and verdict[0] == "NFR":
                req_type = "NFR"
        counters[req_type] += 1
        classified.append({
            "id": f"{req_type}{counters[req_type]}", "text": text, "type": req_type,
        })

    title = next((e.text or "").strip() for e in root.iter() if e.tag == f"{_NS}title")
    return {
        "name": f"pure-{name.strip().replace(' ', '-')}",
        "description": (
            f"PURE 코퍼스 문서 '{title}'에서 추출 — **외부에서 온 요구사항**이다. "
            f"FR/NFR은 이 프로젝트의 BERT 분류기가 붙였다"
            f"{'' if available else ' (분류기를 쓸 수 없어 전부 FR로 뒀다)'}."
        ),
        "source": {
            "corpus": "PURE",
            "document": path.name,
            "extraction": source_kind,
            # 조동사는 있었지만 요구사항이 아니라고 보고 버린 문장 수(`_META`).
            # 남는 잡음이 있다는 뜻이기도 하다 — 목록이 완전하지 않다.
            "dropped_meta": getattr(_prose_sentences, "dropped_meta", 0)
            if source_kind == "prose-modal" else 0,
            "title": title,
            "citation": CITATION,
            "note": (
                "원문은 저장소에 없다(materials/PURE/는 gitignore). 이 파일도 원문 문장을 "
                "담으므로 커밋하지 않는다 — 사본을 가진 사람이 추출기로 재현한다."
            ),
        },
        "classified": classified,
    }


def write(name: str, limit: int | None = None, out_dir: Path = DERIVED) -> Path:
    """추출 결과를 gitignore된 자리에 쓴다. 경로를 돌려준다."""
    payload = extract(name, limit=limit)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{payload['name']}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
