# -*- coding: utf-8 -*-
import json, sys, io, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.requirements.runner import load_state
from app.requirements.agent.compare import score_run

RUNS = {
    "baseline":        "artifacts/run_20260711T072336Z_bce98f7ef4",
    "auto(no-fb)":     "artifacts/run_20260711T072437Z_b8dd8871fe",
    "auto(+feedback)": "artifacts/run_20260711T072810Z_b8dd8871fe",
}
LEAK = re.compile(r'pricing service|recommendation service|recommendation engine|email service|payment gateway|inventory service|auth service|database|\bDB\b', re.I)
NFR_WORDS = re.compile(r'capacity|concurrent|load exceeds|response time|within \d+ ?second|scalab|throughput|availab', re.I)

def manual(d):
    specs = [json.load(open(p, encoding='utf-8')) for p in sorted(glob.glob(f"{d}/use_cases/*/spec.json"))]
    leaks, nfr_ext = [], []
    for s in specs:
        # design leak: internal component named anywhere in scenario/extension text
        for st in s.get('main_scenario', []):
            if LEAK.search(st.get('sentence','')): leaks.append((s['use_case_id'],'step',st['sentence'][:60]))
        for e in s.get('extensions', []):
            blob = e.get('condition','')+' '+' '.join(h.get('sentence','') for h in e.get('handling_steps',[]))
            if LEAK.search(blob): leaks.append((s['use_case_id'],'ext',blob[:60]))
            if NFR_WORDS.search(e.get('condition','')): nfr_ext.append((s['use_case_id'],e.get('label'),e.get('condition')[:60]))
    return leaks, nfr_ext

for name, d in RUNS.items():
    st = load_state(d)
    sc = score_run(st, semantic=False)
    leaks, nfr_ext = manual(d)
    print(f"\n===== {name}  ({d.split('/')[-1]}) =====")
    print(f"  FR total / coverage_ratio : {sc['fr_total']} / {sc['coverage_ratio']}")
    print(f"  orphan_fr_ids             : {len(sc['orphan_fr_ids'])}  {sc['orphan_fr_ids']}")
    print(f"  unattached_nfr_ids        : {len(sc['unattached_nfr_ids'])}  {sc['unattached_nfr_ids']}")
    print(f"  spec_validation_issues    : {sc['spec_validation_issues']}   (동일 정적검증기 fresh 적용)")
    for uc, iss in sc['spec_issues_by_uc'].items():
        print(f"      - {uc}: {iss}")
    print(f"  specs_missing_precond     : {sc['specs_missing_preconditions']}")
    print(f"  specs_missing_success_g   : {sc['specs_missing_success_guarantee']}")
    print(f"  compound_fr_issues        : {sc['compound_fr_issues']}  {sc.get('compound_fr_detail')}")
    print(f"  dangling_diagram_refs     : {sc['dangling_diagram_refs']}  {sc['dangling_refs_detail']}")
    print(f"  orphan_actors             : {len(sc['orphan_actors'])}  {sc['orphan_actors']}")
    print(f"  n_relationships           : {sc['n_relationships']}")
    print(f"  total_extensions          : {sc['total_extensions']}  (avg_main_steps={sc['avg_main_steps']})")
    print(f"  [manual] design_leaks     : {len(leaks)}")
    for l in leaks: print(f"      - {l[0]} [{l[1]}] {l[2]}")
    print(f"  [manual] NFR-in-extension : {len(nfr_ext)}")
    for l in nfr_ext: print(f"      - {l[0]} {l[1]}: {l[2]}")
