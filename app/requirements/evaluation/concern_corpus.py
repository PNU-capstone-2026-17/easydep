"""분화를 **넓힌 요구사항 코퍼스**에서 다시 잰다 — 결정론 층만, LLM 없음.

## 왜 코퍼스를 바꾸는가

§6.8·§6.10의 분화 측정은 도구를 둘(열쇠말·LLM) 썼지만 **읽는 대상은 하나**였다 —
우리가 쓴 입력 11종. 도구를 바꿔도 대상이 그대로면 대상 쪽 편향은 두 번 다 실린다.
실제로 `cn.traffic-shape`는 그 코퍼스 전체에서 요구사항 1건에만 걸렸고, 그래서
`cn.scale-out`의 부분집합으로 보였다.

PURE 코퍼스(외부 SRS 18편)는 **우리가 안 쓴 문장**이다. 같은 계기를 그대로 돌리면
"안 갈린 것이 개념 때문인가 표본 때문인가"를 가를 수 있다.

## 무엇을 읽을 수 있고 무엇을 못 읽는가

PURE 문서는 1995~2010년의 국방·통신·임베디드 SRS다. 클라우드 결정을 적을 이유가 없는
문서라 **링크가 없다는 사실에서는 아무것도 못 읽는다.** 이 측정이 쓸 수 있는 것은
"A에는 걸리는데 B에는 안 걸리는 요구사항이 있다" 하나뿐이다 — 즉 **부분집합 관계를
깨는 데만** 쓴다(§6.11).

열쇠말 층은 오탐이 많다. 여기서 나오는 수는 링크 수가 아니라 **후보 수**이고, 결론에
쓰려면 문장을 직접 읽어 등급을 매겨야 한다(`--show`).

    python -m app.requirements.evaluation concern-differentiation
    python -m app.requirements.evaluation concern-differentiation --show cn.traffic-shape
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from app.requirements.agent.steps import step_cloud
from app.requirements.evaluation import concern_report
from app.requirements.knowledge import concerns


def requirements() -> list[dict]:
    """PURE 문서 전부의 요구사항 문장. **BERT를 부르지 않는다** — FR/NFR 라벨이 필요 없다.

    id에 문서 이름을 넣는 이유는 분화 판정이 요구사항 단위이기 때문이다. 문서마다 1부터
    다시 세면 서로 다른 문서의 요구사항이 같은 id로 겹친다.
    """
    from app.requirements.evaluation import pure

    root_dir = Path(pure.DOCUMENTS)
    if not root_dir.exists():
        raise FileNotFoundError(
            f"PURE 코퍼스가 없다: {root_dir} — 저장소 밖(gitignore)에 두는 자료다."
        )
    items: list[dict] = []
    for path in sorted(root_dir.glob("*.xml")):
        # 문장 선택 규칙은 `pure.sentences_of`가 정한다 — 여기 다시 적으면 이 측정만
        # 다른 문장 집합을 보게 되고, 그 사실이 표에는 안 남는다.
        sentences, _kind = pure.sentences_of(ET.parse(path).getroot())
        for index, text in enumerate(sentences, start=1):
            items.append({"id": f"{path.stem}#{index}", "text": text, "doc": path.stem})
    return items


def measure(items: list[dict] | None = None) -> dict:
    """열쇠말 층으로 잰 링크와 미분화 쌍.

    **판정은 `concern_report.undifferentiated_pairs`가 한다.** 내부 입력 측정과 같은
    함수를 써야 두 수를 나란히 놓을 수 있다 — 여기 다시 적으면 한쪽만 고쳐질 때
    비교가 조용히 무너진다(이 모듈의 존재 이유가 그 비교다).
    """
    items = items if items is not None else requirements()
    links = step_cloud._signal_links(items)
    sets = {cid: set(ids) for cid, ids in links.items()}
    undifferentiated, tested = concern_report.undifferentiated_pairs(sets)
    return {
        "requirements": len(items),
        "documents": len({i["doc"] for i in items}),
        "linked_concerns": len(sets),
        "never_linked": sorted(set(concerns.all_ids()) - set(sets)),
        "links_by_concern": {c: len(v) for c, v in sorted(sets.items(), key=lambda x: -len(x[1]))},
        "documents_by_concern": {c: len({i.split("#")[0] for i in v}) for c, v in sets.items()},
        "tested_pairs": tested,
        "undifferentiated": undifferentiated,
    }


def pair(a: str, b: str, items: list[dict] | None = None, show: int = 5) -> dict:
    """두 관심사의 한쪽만 거는 요구사항들. **문장을 함께 낸다** — 등급을 매기려면 읽어야 한다."""
    items = items if items is not None else requirements()
    links = step_cloud._signal_links(items)
    text = {i["id"]: i["text"] for i in items}
    A, B = set(links.get(a, [])), set(links.get(b, []))

    def sample(ids: set[str]) -> list[dict]:
        # 같은 문장이 문서 안에서 여러 번 나온다(표·목차 중복). 등급을 매길 때는 문장이
        # 단위이므로 중복을 접는다 — 안 접으면 같은 판단을 다섯 번 하게 된다.
        seen, out = set(), []
        for rid in sorted(ids):
            body = text[rid][:120]
            if body in seen:
                continue
            seen.add(body)
            out.append({"id": rid, "text": text[rid]})
            if len(out) >= show:
                break
        return out

    # **판정을 계기 수준으로 적는다.** 여기 걸린 것은 후보이지 링크가 아니므로 "분화"라고
    # 쓰면 도구가 사람의 등급 매기기를 건너뛰고 결론을 낸 것이 된다(§6.11에서 실제로
    # `scale-out` 쪽 28건이 전부 오탐이었다).
    verdict = (
        "비-부분집합(후보 수준)" if (A - B and B - A)
        else ("부분집합(후보 수준)" if A and B else "검사 불가")
    )
    return {
        "a": a, "b": b, "verdict": verdict,
        "only_a": len(A - B), "only_b": len(B - A), "shared": len(A & B),
        "only_a_sample": sample(A - B), "only_b_sample": sample(B - A),
    }
