"""판정 데이터셋 — **사람 라벨**로 규칙의 정밀도·재현율을 재는 자리.

## 왜 안정성만으로는 부족한가

`measure_stability`는 같은 입력에 답이 갈리는지를 잰다. 그건 "믿을 수 있는가"의 절반이다.
나머지 절반은 **옳은가**다. 판정이 5/5로 안정되게 걸려도 그게 맞는 지적인지는 별개다.

그 절반에는 **사람 라벨이 필요하다.** LLM으로 라벨을 만들면 검증자를 검증자로 채점하는
순환이 된다 — 그래서 이 모듈은 라벨을 **만들지 않고 받는다.**

## 세 가지를 분리한다

  1. `build()` — 라벨 붙일 항목을 뽑아 **눈가림 파일**로 낸다. 모델이 뭐라고 했는지는
     넣지 않는다. 넣으면 사람이 그 답에 끌린다(anchoring).
  2. 사람이 `label`을 채운다: `"violated"` 또는 `"clean"`.
  3. `score()` — 그때 비로소 모델 판정을 측정해(프로브 N회, 과반) 라벨과 맞춰 본다.

## 표본은 무작위다

걸린 것만 뽑으면 정밀도만 재고 재현율은 못 잰다. 그래서 **모델 판정을 보지 않고** 도메인마다
앞에서부터 고른다. 대신 희소한 규칙에서는 진짜 위반이 적게 들어올 수 있다 — 그러면 정밀도
추정이 흔들리므로, 그때는 걸린 것을 따로 보태고 **두 수를 따로 보고해야 한다**(섞으면
표본이 무엇이었는지 알 수 없다).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.requirements.knowledge import rules

#: 라벨의 허용 값. 빈 문자열은 "아직 안 붙였다"다.
VIOLATED = "violated"
CLEAN = "clean"
LABELS = (VIOLATED, CLEAN)

_INSTRUCTIONS = (
    "각 항목의 문장들을 읽고 `label`에 "
    f'"{VIOLATED}"(규칙 위반) 또는 "{CLEAN}"(위반 아님)을 적는다. '
    "판단이 안 서면 비워 두면 된다 — 빈 항목은 채점에서 빠진다. "
    "모델이 무엇을 지적했는지는 이 파일에 없다(끌려가지 않도록 일부러 뺐다). "
    "`note`에 근거를 적어 두면 나중에 규칙 문장을 다듬을 때 쓴다."
)


def _item_id(domain: str, use_case_id: str, sentences: list[str]) -> str:
    """도메인·UC·문장에서 만든 안정 id. 같은 명세면 다시 빌드해도 같은 id다."""
    payload = json.dumps([domain, use_case_id, sentences], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def domain_of(run_dir: str) -> str:
    """실행이 어느 데이터셋인지. **매니페스트에서 읽는다** — 경로에서 뽑으면 아티팩트를
    어디에 뒀는지에 따라 이름이 달라진다(실제로 toystore가 `r0_1`로 나왔다)."""
    manifest = Path(run_dir) / "manifest.json"
    if manifest.exists():
        name = json.loads(manifest.read_text(encoding="utf-8")).get("dataset", "")
        if name:
            return name
    return Path(run_dir).parent.name or Path(run_dir).name


def _sentences(spec: dict) -> list[str]:
    """검증자가 보는 문장들 — 사람도 같은 것을 봐야 같은 것을 판정한다."""
    out = [f"trigger: {spec.get('trigger', '')}"]
    out += [f"{s['step_number']}. {s['sentence']}" for s in spec.get("main_scenario", [])]
    out += [
        f"{h['sub_step']}. {h['sentence']}"
        for ext in spec.get("extensions", [])
        for h in ext.get("handling_steps", [])
    ]
    return out


def build(rule_id: str, run_dirs: list[str], per_domain: int = 5) -> dict:
    """도메인마다 앞에서 `per_domain`개 명세를 뽑아 눈가림 라벨 파일을 만든다.

    **모델 판정을 넣지 않는다.** 넣으면 라벨이 그 답의 함수가 된다.
    """
    from app.requirements.agent.steps.step3_specifications import (
        requirement_view,
        spec_review_payload,
    )
    from app.requirements.runner import load_state

    rule = rules.rule(rule_id)
    items = []
    for run_dir in run_dirs:
        state = load_state(run_dir)
        domain = domain_of(run_dir)
        by_id = {r["id"]: r for r in (state.get("classified") or [])}
        ucs = {uc["id"]: uc for uc in (state.get("use_cases") or [])}
        for spec in (state.get("use_case_specs") or [])[:per_domain]:
            uc = ucs.get(spec.get("use_case_id"), {})
            sentences = _sentences(spec)
            items.append({
                "id": _item_id(domain, spec.get("use_case_id", "?"), sentences),
                "domain": domain,
                "use_case_id": spec.get("use_case_id", "?"),
                "use_case": spec.get("name", ""),
                "goal": uc.get("goal", ""),
                # scope 계열 규칙은 요구사항 없이는 판정할 수 없다. 사람에게도 준다.
                "requirements": [
                    f"{rid}: {by_id[rid]['text']}"
                    for rid in list(uc.get("requirement_ids", [])) + list(uc.get("nfr_ids", []))
                    if rid in by_id
                ],
                # 사람이 읽는 자리. 아래 payload와 같은 문장들을 평평하게 편 것이다.
                "sentences": sentences,
                # **검증자가 받을 그 payload 그대로.** 이걸 담아 두는 이유가 둘이다:
                #   1. 아티팩트는 커밋되지 않는다(`artifacts/`). 라벨 파일이 자기 완결적이어야
                #      나중에 아티팩트 없이도 채점할 수 있다.
                #   2. 문장에서 되만들면 검증자가 **파이프라인과 다른 것**을 판정한다
                #      (전제조건·확장·보증이 빠진 명세가 된다). 그러면 그 수는 파이프라인에
                #      대한 말이 아니다.
                # 모델 판정이 아니므로 눈가림을 깨지 않는다.
                "payload": spec_review_payload(spec, requirement_view(uc, by_id)),
                "label": "",
                "note": "",
            })
    return {
        "rule_id": rule.id,
        "rule_statement": rule.statement,
        "citation": rule.citation,
        "caveat": rule.caveat,
        "instructions": _INSTRUCTIONS,
        "sampling": (
            f"도메인마다 앞에서 {per_domain}개 — **모델 판정을 보지 않고** 골랐다. "
            "걸린 것만 뽑으면 재현율을 못 잰다."
        ),
        "items": items,
    }


def score(labelled: dict, repeats: int = 5) -> dict:
    """라벨과 **측정한 모델 판정**을 맞춰 본다(프로브 N회, 과반을 모델의 답으로 본다).

    라벨이 빈 항목은 뺀다 — 사람이 판단을 보류한 것을 0이나 1로 채우면 안 된다.
    """
    from app.requirements.evaluation import semantic

    rule_id = labelled["rule_id"]
    items = [i for i in labelled["items"] if i.get("label") in LABELS]
    if not items:
        raise SystemExit("라벨이 하나도 없다. 파일의 `label`을 채운 뒤 다시 돌린다.")

    # **파일에 담긴 payload를 그대로 쓴다** — 아티팩트가 없어도 채점되고, 검증자가 파이프라인과
    # 같은 것을 본다(build의 `payload` 주석 참고).
    #
    # 모델의 답은 **N회 중 전부 걸렸을 때만** 위반으로 본다. 흔들리는 판정을 위반으로 세면
    # 정밀도가 동전 던지기의 함수가 된다 — 흔들림 자체는 `measure_stability`가 재는 것이고,
    # 여기서 재려는 것은 "안정된 판정이 옳은가"다.
    verdicts: dict[str, bool] = {}
    for item in items:
        row = semantic.probe_rule(rule_id, [(item["id"], item["payload"])], repeats=repeats)
        verdicts[item["id"]] = row["always"] > 0

    tp = sum(1 for i in items if i["label"] == VIOLATED and verdicts[i["id"]])
    fp = sum(1 for i in items if i["label"] == CLEAN and verdicts[i["id"]])
    fn = sum(1 for i in items if i["label"] == VIOLATED and not verdicts[i["id"]])
    tn = sum(1 for i in items if i["label"] == CLEAN and not verdicts[i["id"]])
    return {
        "rule_id": rule_id,
        "repeats": repeats,
        "labelled": len(items),
        "skipped_unlabelled": len(labelled["items"]) - len(items),
        "true_positive": tp, "false_positive": fp,
        "false_negative": fn, "true_negative": tn,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        # 사람이 위반이라고 본 것이 몇 건인지. 너무 적으면 정밀도·재현율이 흔들린다.
        "labelled_violations": tp + fn,
    }
