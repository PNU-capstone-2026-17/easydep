"""관심사 링크 측정의 집계 — 쌓인 표에서 네 질문에 답한다. **LLM을 부르지 않는다.**

    python -m app.requirements.evaluation concern-report --dir report/concerns-2026-07-28

표는 `concern_campaign`이 **1표씩** 쌓아 둔 것이고, 다수결은 여기서 계산한다. 그래서 같은
데이터로 `k=1,3,5`를 다 낼 수 있다 — 문턱을 바꿔 보려고 다시 돌리지 않는다.

## 흔들림을 두 눈금으로 잰다

  - **엄격**: 반복마다 링크된 요구 id **집합이 완전히 같은가.**
  - **느슨**: 링크가 **있느냐 없느냐**만 같은가.

둘을 갈라야 하는 이유가 있다. 이 층의 산출물은 "이 관심사가 다뤄졌는가"이므로 실제로
쓰이는 것은 느슨한 쪽이다. 엄격한 눈금만 보면 id 하나가 흔들려도 불안정으로 세어 층이
실제보다 나빠 보이고, 느슨한 눈금만 보면 근거가 통째로 바뀌는 것을 놓친다.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from pathlib import Path

from app.requirements.agent.steps import step_cloud
from app.requirements.evaluation import jsonl
from app.requirements.knowledge import concerns


def load(dir_path: Path) -> list[dict]:
    """쌓인 표를 읽는다. **칸이 중복되면 첫 것만 남긴다.**

    러너가 시작할 때 한 번만 "끝난 칸"을 읽으므로, 두 프로세스가 겹쳐 뜨면 같은 칸을 두 번
    잰다. 그러면 반복 수가 부풀어 흔들림이 **실제보다 안정적으로** 보인다 — 같은 칸의 두
    표가 우연히 같으면 일치 한 건으로 세어지기 때문이다. 조용히 좋아지는 쪽으로 틀리는
    오염이라 특히 위험하다.

    막는 것은 러너 쪽이 옳지만(겹쳐 뜨지 않게 하는 것), 집계도 방어한다 — 실행이 여러 번
    끊기고 재개되는 상황에서 겹침은 배관 사고이지 예외 상황이 아니다.
    """
    kept, seen = [], set()
    for row in jsonl.rows(dir_path / "ballots.jsonl"):
        cell = row.get("cell")
        if cell in seen:
            continue
        seen.add(cell)
        kept.append(row)
    return kept


def _by_cell(rows: list[dict]) -> dict[tuple[str, int, str], list[list[str]]]:
    """(도메인, 청크, 관심사) → 반복마다의 링크 목록. 판정 못 받은 반복은 빼지 않고 `None`."""
    out: dict[tuple[str, int, str], list[list[str]]] = defaultdict(list)
    for row in rows:
        if not row.get("answered"):
            continue
        for concern in concerns.CONCERNS:
            out[(row["domain"], row["chunk"], concern.id)].append(
                sorted(row["links"].get(concern.id, []))
            )
    return out


def stability(rows: list[dict]) -> dict[int, dict]:
    """청크 조건별 흔들림. 엄격·느슨 두 눈금."""
    cells = _by_cell(rows)
    per_chunk: dict[int, dict] = {}
    for (_, chunk, _), reps in cells.items():
        per_chunk.setdefault(chunk, {"cells": 0, "strict": 0, "loose": 0, "repeats": 0})
    for (_domain, chunk, _cid), reps in cells.items():
        if len(reps) < 2:
            continue
        agg = per_chunk[chunk]
        agg["cells"] += 1
        agg["repeats"] += len(reps)
        if all(r == reps[0] for r in reps):
            agg["strict"] += 1
        if len({bool(r) for r in reps}) == 1:
            agg["loose"] += 1
    for agg in per_chunk.values():
        n = agg["cells"] or 1
        agg["strict_ratio"] = round(agg["strict"] / n, 4)
        agg["loose_ratio"] = round(agg["loose"] / n, 4)
        agg["avg_repeats"] = round(agg["repeats"] / n, 2)
    return per_chunk


def majority(rows: list[dict], chunk: int, k: int) -> dict[str, dict[str, list[str]]]:
    """`k`표 과반으로 확정한 링크. (도메인 → 관심사 → 요구 id들).

    문턱은 **그 관심사가 실제로 판정된 횟수**의 과반이다 — 어떤 표에서 통째로 빠진
    관심사를 반대표로 세지 않기 위해서다(`step_cloud._llm_links`와 같은 규율).
    """
    picked: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["chunk"] == chunk and row.get("answered"):
            picked[row["domain"]].append(row)

    out: dict[str, dict[str, list[str]]] = {}
    for domain, ballots in picked.items():
        ballots = sorted(ballots, key=lambda r: r["repeat"])[:k]
        votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        judged: dict[str, int] = defaultdict(int)
        for b in ballots:
            for cid in b["judged"]:
                judged[cid] += 1
                for rid in b["links"].get(cid, []):
                    votes[cid][rid] += 1
        agreed = {}
        for cid, tally in votes.items():
            threshold = judged[cid] // 2 + 1
            ids = sorted(r for r, c in tally.items() if c >= threshold)
            if ids:
                agreed[cid] = ids
        out[domain] = agreed
    return out


def signal_baseline() -> dict[str, dict[str, list[str]]]:
    """결정론 층이 같은 입력에서 내는 링크 — 비교 기준선."""
    from app.requirements.evaluation import concern_campaign

    return {
        name: step_cloud._signal_links(classified)
        for name, classified in concern_campaign.load_domains()
    }


def layer_gain(rows: list[dict], chunk: int, k: int) -> dict:
    """LLM 층이 결정론 층 대비 무엇을 더 줍고 무엇을 놓치는가."""
    llm = majority(rows, chunk, k)
    sig = signal_baseline()
    only_llm: dict[str, int] = defaultdict(int)
    only_sig: dict[str, int] = defaultdict(int)
    both = 0
    for domain, links in llm.items():
        base = sig.get(domain, {})
        for cid in concerns.all_ids():
            a, b = bool(links.get(cid)), bool(base.get(cid))
            if a and b:
                both += 1
            elif a:
                only_llm[cid] += 1
            elif b:
                only_sig[cid] += 1
    return {
        "domains": len(llm),
        "both": both,
        "only_llm_total": sum(only_llm.values()),
        "only_signal_total": sum(only_sig.values()),
        "only_llm_by_concern": dict(sorted(only_llm.items(), key=lambda x: -x[1])),
        "only_signal_by_concern": dict(sorted(only_sig.items(), key=lambda x: -x[1])),
    }


def undifferentiated_pairs(sets: dict[str, set]) -> tuple[list[dict], int]:
    """(미분화 쌍, 검사한 쌍 수). **분화 판정의 정의는 여기 한 곳에 있다.**

    판정은 `app/core/cloudkb/document/archive/cloud-native-requirements.md` §6.7 그대로다: 두 관심사가 갈리려면
    **한쪽만 거는 요구사항이 양방향으로** 있어야 한다. 양쪽 다 안 걸린 쌍은 검사 대상이
    아니다(없는 것끼리는 비교할 수 없다).

    이 함수가 따로 있는 이유는 **같은 정의를 두 코퍼스에 돌리기 때문**이다 — 내부 입력은
    `differentiation()`이, PURE는 `concern_corpus.measure()`가 쓴다. 두 수를 나란히 놓고
    비교하므로 정의가 한쪽만 바뀌면 두 수가 조용히 비교 불가능해진다.
    """
    undiff, tested = [], 0
    for a, b in combinations(sorted(sets), 2):
        A, B = sets[a], sets[b]
        if not A or not B:
            continue
        tested += 1
        if not (A - B) or not (B - A):
            undiff.append({"a": a, "b": b, "only_a": len(A - B), "only_b": len(B - A),
                           "shared": len(A & B)})
    return undiff, tested


def differentiation(rows: list[dict], chunk: int, k: int) -> dict:
    """LLM 링크로 다시 잰 분화 — 열쇠말로는 못 가리던 미분화 쌍의 결론."""
    llm = majority(rows, chunk, k)
    sets: dict[str, set[tuple[str, str]]] = {c.id: set() for c in concerns.CONCERNS}
    for domain, links in llm.items():
        for cid, ids in links.items():
            sets[cid] |= {(domain, i) for i in ids}

    undiff, tested = undifferentiated_pairs(sets)
    empty = [c for c in concerns.all_ids() if not sets[c]]
    return {"tested_pairs": tested, "undifferentiated": undiff, "never_linked": empty}


def report(dir_path: Path, k: int = 3) -> dict:
    rows = load(dir_path)
    chunks = sorted({r["chunk"] for r in rows})
    return {
        "ballots": len(rows),
        "failed_ballots": sum(1 for r in rows if not r.get("answered")),
        "invented_concerns": sum(r.get("invented_concerns", 0) for r in rows),
        "prompt_versions": sorted({r.get("prompt_concerns", "?") for r in rows}),
        "avg_seconds": {
            c: round(
                sum(r["elapsed_s"] for r in rows if r["chunk"] == c)
                / max(1, sum(1 for r in rows if r["chunk"] == c)), 1)
            for c in chunks
        },
        "stability": stability(rows),
        "layer_gain": {c: layer_gain(rows, c, k) for c in chunks},
        "differentiation": {c: differentiation(rows, c, k) for c in chunks},
    }
