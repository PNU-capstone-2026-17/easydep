"""audit_results.jsonl 집계 — 질의별 일관성·실패 패턴."""
from __future__ import annotations

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
# 인자로 결과 파일 경로를 받거나, 없으면 최신/보존본 순으로 찾는다.
_here = Path(__file__).parent
if len(sys.argv) > 1:
    P = Path(sys.argv[1])
elif (_here / "results-latest.jsonl").exists():
    P = _here / "results-latest.jsonl"
else:
    P = _here / "results-2026-07-18.jsonl"

SPEC_Q = {
    "1-1a": "VM 만들려면 뭐부터?", "1-1b": "AWS VPC 삭제 영향?", "1-1c": "AWS VPC=Azure/GCP?",
    "1-1d": "GCP ComputeInstance 참조?", "1-1e": "AWS rds 타입 검색", "1-1f": "AWS 의존성 top5",
    "1-2a": "EBS 100TB 가능?", "1-2b": "Lambda MemorySize 제약?", "1-2c": "서브넷 불변 속성?",
    "1-2d": "ECS LaunchType 허용값?", "1-2e": "Azure vNet 쿼터?",
    "1-3": "GCP vCPU8 mem60 3대 월비용(다단계)",
    "1-4a": "m5.large 세대/성능", "1-4b": "m5 vs m6i 비교", "1-4c": "EBS baseline>=4000",
    "2-1": "RDS 순서+불변(교차)", "2-2": "서브넷 Azure명+쿼터(교차)", "2-3": "1000명 API 순서+비용(교차·계획)",
    "3-1": "떠있는 VM 목록(거절)", "3-2": "EBS 100TB(불확실 유지)", "3-3": "t3.medium 단가(검색금지)",
    "3-4": "n2-highmem-8 메모리(64+62.5)", "3-5": "vCPU 2000(경계안내)", "3-6": "VPC=Azure(계획금지)",
    "3-7": "최저가 상시서버(버스트경고)", "3-8": "AWS vs Azure 비교(거부)",
}

rows = [json.loads(l) for l in P.read_text(encoding="utf-8").splitlines() if l.strip()]
by_id = defaultdict(list)
for r in rows:
    by_id[r["id"]].append(r)

print(f"총 {len(rows)}런 / 질의 {len(by_id)}개\n")
print(f"{'id':6} {'pass':7} {'도구 일관성':32} 실패체크(횟수)")
print("-" * 100)

fail_detail = []
for qid, runs in by_id.items():
    K = len(runs)
    passes = sum(1 for r in runs if r.get("hard_pass"))
    # 도구 호출 시퀀스 분포
    seqs = Counter(tuple(r.get("tools", [])) for r in runs)
    seq_str = " | ".join(f"{list(s)}×{c}" for s, c in seqs.most_common(3))
    # 실패한 체크 집계
    failed = Counter()
    leaks = Counter()
    for r in runs:
        for k, v in (r.get("checks") or {}).items():
            if v is False:
                failed[k] += 1
        for lk in r.get("leaks", []):
            leaks[lk] += 1
        if r.get("error"):
            failed[f"ERROR:{r['error'][:30]}"] += 1
    fail_str = ", ".join(f"{k}×{c}" for k, c in failed.most_common()) or "-"
    flag = "" if passes == K else "  ⚠"
    print(f"{qid:6} {passes}/{K:<5} {seq_str[:32]:32} {fail_str}{flag}")
    if passes < K or leaks:
        fail_detail.append((qid, runs, failed, leaks))

print("\n\n=== 실패/불안정 질의 상세 ===")
for qid, runs, failed, leaks in fail_detail:
    print(f"\n[{qid}] q={runs[0].get('answer_head','')[:0]}")
    print(f"  질의: {SPEC_Q.get(qid,'?')}")
    for r in runs:
        line = f"  run{r['run']}: tools={r.get('tools')} pass={r.get('hard_pass')}"
        if r.get("error"): line += f" ERROR={r['error']}"
        print(line)
    if leaks:
        print(f"  누출: {dict(leaks)}")
    # 대표 답변 앞부분
    print(f"  답변예: {runs[0].get('answer_head','')[:220]}")

# soft/want 판정도 따로 보여준다(참고용)
print("\n\n=== want(답변 substring) 판정 — 참고용 ===")
for qid, runs in by_id.items():
    wchecks = defaultdict(list)
    for r in runs:
        for k, v in (r.get("checks") or {}).items():
            if k.startswith("want:"):
                wchecks[k].append(v)
    if wchecks:
        summ = ", ".join(f"{k.split(':',1)[1]}={sum(vs)}/{len(vs)}" for k, vs in wchecks.items())
        print(f"  {qid:6} {summ}")
