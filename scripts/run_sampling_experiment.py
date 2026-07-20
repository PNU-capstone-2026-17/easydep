"""few-shot 예시 샘플링 전략 × 백엔드 비교 실험 드라이버.

추상 소프트웨어 요구사항 10문장에 대해 5개 구성으로 top-N 예시를 뽑고,
관련성(mean_query_sim)·다양성(diversity) 지표와 함께 markdown 리포트를 생성한다.

구성 5종 (random 은 선택 자체가 백엔드 무관 → 점수 표기용으로 nim 사용):
  1. random          (baseline)
  2. cosine + tfidf
  3. cosine + nim
  4. mmr    + tfidf
  5. mmr    + nim

실행:  python -m scripts.run_sampling_experiment
출력:  docs/research/fewshot-sampling-experiment-results.md
"""
from __future__ import annotations

import numpy as np

from app.legacy.example_sampler import (
    DEFAULT_DATASET,
    _diversity,
    _load_corpus,
    _rank,
    sample_examples,
)
from app.config import settings

TOP_N = 5
SEED = 42
OUT_PATH = "docs/research/fewshot-sampling-experiment-results.md"

# 추상적(모호·미검증) 소프트웨어 요구사항 쿼리 10종.
QUERIES = [
    "I want to build a shopping mall service.",
    "The app should let users chat with each other in real time.",
    "We need a system to manage employee attendance and payroll.",
    "Make a platform where people can book hotels and flights.",
    "The website must be secure and protect customer data.",
    "Build a mobile app for tracking daily fitness and workouts.",
    "I want an online learning platform with video courses and quizzes.",
    "The system should recommend products based on what users like.",
    "Create a food delivery service that shows nearby restaurants.",
    "We want a dashboard that shows sales analytics in real time.",
]

# (라벨, strategy, backend)
CONFIGS = [
    ("random",       "random", "nim"),
    ("cosine+tfidf", "cosine", "tfidf"),
    ("cosine+nim",   "cosine", "nim"),
    ("mmr+tfidf",    "mmr",    "tfidf"),
    ("mmr+nim",      "mmr",    "nim"),
]


def _run_config(query, texts, index_of, strategy, backend):
    """한 구성 실행 → (items[(text,score)], mean_query_sim, diversity)."""
    items = sample_examples(
        query=query, sample_size=TOP_N, strategy=strategy, backend=backend, seed=SEED,
    )
    idx = [index_of[t] for t, _ in items]
    # 다양성은 백엔드의 pairwise 로 계산(random 은 선택 백엔드=nim 기준).
    ranking = _rank(query, texts, backend, settings.bert_model_path, DEFAULT_DATASET,
                    settings.embed_model)
    mean_sim = float(np.mean([s for _, s in items])) if items else 0.0
    div = _diversity(idx, ranking)
    return items, mean_sim, div


def main():
    texts = _load_corpus(DEFAULT_DATASET)
    index_of = {t: i for i, t in enumerate(texts)}
    print(f"코퍼스 {len(texts)}행 로드. NIM 코퍼스 임베딩 캐시 준비 중(최초 1회 느림)...")

    lines: list[str] = []
    lines.append("# Few-shot 예시 샘플링 실험 결과")
    lines.append("")
    lines.append(f"- 데이터셋: `{DEFAULT_DATASET}` ({len(texts)}개 요구사항, 결측/중복 제거 후)")
    lines.append(f"- NIM 임베딩 모델: `{settings.embed_model}` (2048-d)")
    lines.append(f"- top-N = {TOP_N}, random seed = {SEED}")
    lines.append("- 지표: **관련성**(mean_query_sim, 쿼리와 선택 예시의 평균 코사인 ↑좋음) / "
                 "**다양성**(diversity, 선택 예시 간 평균 코사인 ↓다양)")
    lines.append("")

    # 전역 집계용 누산기.
    agg: dict[str, dict[str, list]] = {label: {"sim": [], "div": []} for label, _, _ in CONFIGS}

    for qi, query in enumerate(QUERIES, 1):
        print(f"[{qi}/{len(QUERIES)}] {query}")
        lines.append(f"## Q{qi}. \"{query}\"")
        lines.append("")
        # 요약 표.
        lines.append("| 구성 | 관련성 | 다양성 |")
        lines.append("|---|---|---|")
        detail_blocks: list[str] = []
        for label, strategy, backend in CONFIGS:
            items, mean_sim, div = _run_config(query, texts, index_of, strategy, backend)
            agg[label]["sim"].append(mean_sim)
            agg[label]["div"].append(div)
            lines.append(f"| {label} | {mean_sim:.4f} | {div:.4f} |")

            block = [f"**{label}** (관련성 {mean_sim:.4f}, 다양성 {div:.4f})", ""]
            for text, score in items:
                block.append(f"- `[{score:+.3f}]` {text}")
            block.append("")
            detail_blocks.append("\n".join(block))
        lines.append("")
        lines.extend(detail_blocks)
        lines.append("---")
        lines.append("")

    # 전역 평균 요약.
    lines.append("## 전체 평균 (10문장)")
    lines.append("")
    lines.append("| 구성 | 평균 관련성 | 평균 다양성 |")
    lines.append("|---|---|---|")
    for label, _, _ in CONFIGS:
        s = float(np.mean(agg[label]["sim"]))
        d = float(np.mean(agg[label]["div"]))
        lines.append(f"| {label} | {s:.4f} | {d:.4f} |")
    lines.append("")
    lines.append("> 해석: random 은 관련성이 가장 낮다(baseline). cosine 은 관련성 최고이나 "
                 "top-k 특성상 다양성이 떨어질 수 있고, mmr 은 관련성을 약간 양보하는 대신 "
                 "다양성을 확보한다. tfidf(어휘) vs nim(의미)의 차이도 표에서 비교 가능하다.")

    report = "\n".join(lines) + "\n"
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n리포트 저장: {OUT_PATH}")

    # 콘솔에도 전체 평균 표를 출력.
    print("\n=== 전체 평균 (10문장) ===")
    print(f"{'구성':14s} {'관련성':>8s} {'다양성':>8s}")
    for label, _, _ in CONFIGS:
        s = float(np.mean(agg[label]["sim"]))
        d = float(np.mean(agg[label]["div"]))
        print(f"{label:14s} {s:8.4f} {d:8.4f}")


if __name__ == "__main__":
    main()
