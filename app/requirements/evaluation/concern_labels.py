"""사람 라벨 계기 — `concern_linker_llm` 기본값을 정하기 위한 눈가림 파일. **LLM 없음.**

## 왜 이것이 필요한가

§6.10이 잰 것: 열쇠말 층만 잡은 링크가 24건, LLM 층만 잡은 것이 33건이다. 두 층 다
**판정자이지 정답이 아니라서**, 어느 쪽이 참인지는 서로를 대조해서는 나오지 않는다.
LLM으로 라벨을 붙이면 판정자를 판정자로 채점하는 순환이 된다(`campaign.py`가 적어 둔
한계와 같다). 그래서 사람이 붙인다.

## 무엇을 재는가 — **정밀도이지 재현율이 아니다**

항목 하나는 **링크 하나**다: "이 요구사항이 저 관심사를 다루는가."

  - 셀 단위("이 도메인에서 이 관심사가 다뤄졌는가")로 물으면 인용된 요구사항만 읽고
    답하게 되는데, 그건 커버리지 판정이 아니다 — 커버리지를 제대로 물으려면 그 도메인의
    요구사항을 **전부** 읽어야 한다.
  - 링크 단위로 물으면 각 층이 **자기만 주장한 링크가 참인 비율**이 나온다. 이것이 기본값
    결정에 필요한 값이다: 거짓 링크는 관심사를 `covered`로 바꿔 **인계 목록에서 지운다** —
    없는 것을 드러내려고 만든 층이 없는 것을 덮는 방향의 오류다.

재현율(둘 다 놓친 링크)은 이 계기가 못 잰다. 그건 도메인 전수 읽기가 필요한 별도 작업이고,
결론을 쓸 때 이 한계를 함께 적어야 한다.

## 눈가림

항목에는 **어느 층이 주장했는지가 없다.** 층 표시는 열쇠 파일(`--key`)에만 있고, 항목
순서도 섞는다(고정 씨앗) — 층별로 뭉쳐 있으면 순서만으로 층을 알 수 있기 때문이다.

**대조 항목**을 섞어 넣는다: 두 층이 **합의한** 링크에서 뽑은 것들이다. 라벨러가 전부
"예"라고 답하는 상태를 잡아내고, 라벨 자체의 신뢰도를 볼 수 있게 한다. 대조 항목도
눈가림이라 라벨러는 무엇이 대조인지 모른다.

    python -m app.requirements.evaluation concern-labels --dir report/concerns-2026-07-28 \\
        --out report/labels/concern-links.json --key report/labels/concern-links.key.json
    # ... 사람이 verdict를 채운다 ...
    python -m app.requirements.evaluation concern-label-score \\
        --labels report/labels/concern-links.json --key report/labels/concern-links.key.json
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from app.requirements.evaluation import concern_report
from app.requirements.knowledge import concerns

#: 눈가림 순서를 고정하는 씨앗. 같은 표에서 같은 파일이 나와야 **라벨을 나눠서 붙일 수
#: 있다** — 순서가 흔들리면 어제 붙인 라벨이 오늘 다른 항목에 붙는다. 섞는 것은 해시로
#: 한다(`random`은 구현이 바뀌면 순서가 바뀔 수 있고, 여기서는 재현성이 기능이다).
SEED = "42"

#: 라벨러가 고를 수 있는 값. `unsure`를 두는 이유는 **억지 이분법이 라벨을 오염시키기**
#: 때문이다 — 애매한 것을 억지로 가르게 하면 그 오차가 층 비교에 그대로 실린다.
VERDICTS = ("yes", "no", "unsure")

LAYER_LLM = "llm"
LAYER_SIGNAL = "signal"
LAYER_BOTH = "both"

INSTRUCTIONS = (
    "각 항목은 요구사항 한 줄과 관심사 질문 하나다. 물음은 하나뿐이다: "
    "**이 요구사항이 그 질문에 답하고 있는가?** "
    'verdict에 "yes" / "no" / "unsure" 중 하나를 적는다.\n'
    "  - yes  — 이 요구사항을 읽으면 그 관심사에 대해 무엇이 정해졌는지 알 수 있다.\n"
    "  - no   — 같은 영역을 스칠 뿐, 그 질문에는 답하지 않는다.\n"
    "  - unsure — 문장만으로 못 가리겠다.\n"
    "'같은 주제를 다룬다'가 아니라 '그 결정을 내려 준다'가 기준이다. 예: "
    "'시스템은 사용자 프로필을 저장한다'는 데이터 소재지를 정하지 않는다."
)


def _cited(rows: list[dict], chunk: int, k: int) -> tuple[dict, dict]:
    """(LLM 다수결 링크, 열쇠말 링크). 둘 다 도메인 → 관심사 → 요구 id들."""
    return concern_report.majority(rows, chunk, k), concern_report.signal_baseline()


def _texts() -> dict[str, dict[str, str]]:
    """도메인 → 요구 id → 문장."""
    from app.requirements.evaluation import concern_campaign

    return {
        name: {item["id"]: item.get("text", "") for item in classified}
        for name, classified in concern_campaign.load_domains()
    }


def cases(rows: list[dict], chunk: int, k: int, controls: int) -> list[dict]:
    """라벨 붙일 링크들. 분쟁 링크 전부 + 합의 링크에서 뽑은 대조 항목.

    대조를 **고르게** 뽑는다(관심사별로 돌아가며) — 한 관심사에서 몰아 뽑으면 대조군이
    그 관심사의 성질을 재게 된다.
    """
    llm, sig = _cited(rows, chunk, k)
    disputed: list[dict] = []
    agreed: list[dict] = []
    for domain in sorted(llm):
        for cid in concerns.all_ids():
            a = set(llm[domain].get(cid) or [])
            b = set(sig.get(domain, {}).get(cid) or [])
            if a and not b:
                disputed += [{"domain": domain, "concern_id": cid, "requirement_id": r,
                              "layer": LAYER_LLM} for r in sorted(a)]
            elif b and not a:
                disputed += [{"domain": domain, "concern_id": cid, "requirement_id": r,
                              "layer": LAYER_SIGNAL} for r in sorted(b)]
            elif a and b:
                agreed += [{"domain": domain, "concern_id": cid, "requirement_id": r,
                            "layer": LAYER_BOTH} for r in sorted(a & b)]

    by_concern: dict[str, list[dict]] = defaultdict(list)
    for case in agreed:
        by_concern[case["concern_id"]].append(case)
    picked: list[dict] = []
    while len(picked) < controls and by_concern:
        for cid in sorted(by_concern):
            if len(picked) >= controls:
                break
            picked.append(by_concern[cid].pop(0))
            if not by_concern[cid]:
                del by_concern[cid]
    return disputed + picked


def build(dir_path: Path, chunk: int = 0, k: int = 3, controls: int = 16) -> tuple[dict, dict]:
    """(눈가림 항목 파일, 열쇠 파일). **층 표시는 열쇠에만 있다.**"""
    rows = concern_report.load(dir_path)
    texts = _texts()
    selected = cases(rows, chunk, k, controls)

    def blind_order(case: dict) -> str:
        seed = f"{SEED}|{case['domain']}|{case['concern_id']}|{case['requirement_id']}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    # 층을 섞이게 하는 것이 목적이다. 도메인·관심사 순으로 두면 층이 뭉쳐 보이고,
    # 그러면 항목 파일에 층 이름이 없어도 순서만으로 짐작할 수 있다.
    ordered = sorted(selected, key=blind_order)

    items, key = [], {}
    for index, case in enumerate(ordered, start=1):
        item_id = f"L{index:03d}"
        concern = concerns.BY_ID[case["concern_id"]]
        items.append({
            "item_id": item_id,
            "requirement": texts.get(case["domain"], {}).get(case["requirement_id"], ""),
            "concern_question": concern.question,
            "verdict": "",
        })
        key[item_id] = case
    return (
        {
            "instructions": INSTRUCTIONS,
            "verdicts": list(VERDICTS),
            "source": {"dir": str(dir_path), "chunk": chunk, "k": k,
                       "ballots": len(rows), "seed": SEED},
            "items": items,
        },
        {"source": {"dir": str(dir_path), "chunk": chunk, "k": k}, "key": key},
    )


def score(labels: dict, key: dict) -> dict:
    """층별 정밀도. **`unsure`는 분모에서 뺀다** — 모르는 것을 오답으로 세면 정밀도가 아니다.

    셀 단위 결론도 함께 낸다: 그 층이 혼자 주장한 (도메인, 관심사) 중 **참인 링크를 하나라도
    가진** 셀의 비율. 커버리지 주장이 실제로 서는 비율이다.
    """
    table = key["key"]
    verdicts = {i["item_id"]: (i.get("verdict") or "").strip().lower()
                for i in labels["items"]}
    unknown = sorted(v for v in set(verdicts.values()) if v and v not in VERDICTS)

    per_layer: dict[str, dict] = {
        layer: {"yes": 0, "no": 0, "unsure": 0, "unlabelled": 0}
        for layer in (LAYER_LLM, LAYER_SIGNAL, LAYER_BOTH)
    }
    cells: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for item_id, case in table.items():
        verdict = verdicts.get(item_id, "")
        bucket = per_layer[case["layer"]]
        bucket[verdict if verdict in VERDICTS else "unlabelled"] += 1
        cells[(case["layer"], case["domain"], case["concern_id"])].append(verdict)

    for bucket in per_layer.values():
        judged = bucket["yes"] + bucket["no"]
        bucket["precision"] = round(bucket["yes"] / judged, 4) if judged else None
        bucket["judged"] = judged

    per_cell: dict[str, dict] = {}
    for layer in (LAYER_LLM, LAYER_SIGNAL, LAYER_BOTH):
        mine = [v for (lay, _d, _c), v in cells.items() if lay == layer]
        supported = sum(1 for v in mine if "yes" in v)
        settled = sum(1 for v in mine if any(x in ("yes", "no") for x in v))
        per_cell[layer] = {
            "cells": len(mine), "supported": supported, "settled": settled,
            "supported_ratio": round(supported / settled, 4) if settled else None,
        }

    total = len(table)
    labelled = sum(1 for item_id in table if verdicts.get(item_id) in VERDICTS)
    return {
        "items": total,
        "labelled": labelled,
        "remaining": total - labelled,
        "unknown_verdicts": unknown,
        "per_layer": per_layer,
        "per_cell": per_cell,
        # 대조 항목이 낮으면 라벨러와 두 층이 애초에 다른 것을 재고 있다는 뜻이다 —
        # 그때는 층 비교보다 관심사 질문문을 먼저 의심해야 한다.
        "control_note": "both = 두 층이 합의한 대조 항목. 여기가 낮으면 층 비교 이전의 문제다.",
    }
