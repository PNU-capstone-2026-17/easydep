"""RTM(요구사항 추적 매트릭스) — Phase 1: 읽기전용 물질화.

파이프라인이 이미 생성한 추적 정보(분류된 FR/NFR, UC의 requirement_ids/nfr_ids, 명세 스텝의
covered_req_ids)를 하나의 매트릭스로 집계한다. 새 LLM 호출 없이 state만 읽으므로 결정론적이다.

한 장에서 커버리지가 보인다:
  - FR 커버리지: FR 행이 하나 이상의 UC로 추적되면 covered, 아니면 orphan(누락 위험).
  - NFR 커버리지: NFR 행이 UC에 제약으로 부착되면 attached, 아니면 unattached(전역 제약 후보).
  - UC 커버리지(역방향): UC별로 어떤 요구를 커버하는지 + 아무 요구도 안 걸린 orphan UC.
  - 환각: 존재하지 않는 요구 id를 참조하면 unknown_refs로 표면화.

Phase 2(qualifies 링크: NFR→부모 FR)·Phase 3(realized 판정 컬럼)은 이후 단계에서 채운다.
지금은 구조적 추적(누가 무엇을 커버한다고 주장하는가)까지 물질화한다.
"""
from __future__ import annotations

from app.core import traceability


def build_rtm(state: dict, verdicts: list[dict] | None = None) -> dict:
    """state에서 요구사항 추적 매트릭스를 집계한다(순수 함수, LLM 없음).

    반환: {"rows": [...], "use_case_view": [...], "unknown_refs": [...], "summary": {...}}
    각 row: {id, type, text, use_cases, scenario_steps, qualifies, status, realized}
      - status(FR): covered | orphan
      - status(NFR): attached | linked | unattached

    Phase 3(realized/검증): verdicts(semantic coverage judge 결과, [{"use_case_id",
    "requirement_id","realized"}])가 주어지면 FR 행의 realized를 채운다 —
      True(하나 이상의 커버 UC가 실현) / False(커버 주장 있으나 아무도 실현 못 함=거짓주장) / None(미판정).
    NFR 행의 realized는 구조적 ack — attached/linked(어느 UC로든 라우팅됨)면 True, unattached면 False.
    verdicts가 없으면(run_pipeline 등) FR realized는 None(판정 안 함).
    """
    classified = state.get("classified") or []
    use_cases = state.get("use_cases") or []
    deployment_needs = state.get("deployment_needs") or {}

    # 추적 집계는 `core/traceability.py` 한 곳에서 한다 — 여기서 따로 굴리던 것을 옮겼다.
    # `check_coverage`가 같은 사실을 다르게 세고 있었고, 갈린 것을 알아채는 데 오래 걸렸다.
    trace = traceability.index(state)
    by_id = trace.by_id

    # Phase 3: 요구 id → 그 요구를 (어느 UC에서든) 실현했는지 판정 리스트.
    #
    # **id 없는 판정은 버린다.** 예전에는 `None`을 키로 넣었는데, 그러면 어느 행과도 안
    # 맞아 판정이 **조용히 사라진다**(행의 `realized`는 미판정으로 남는다). 버리는 것은
    # 같지만, 이렇게 두면 왜 사라졌는지가 코드에 적혀 있다. 타입 검사가 `app/core`로
    # 옮겨오면서 드러난 자리다.
    real_by_req: dict[str, list[bool]] = {}
    for v in (verdicts or []):
        req_id = v.get("requirement_id")
        if not isinstance(req_id, str):
            continue
        real_by_req.setdefault(req_id, []).append(bool(v.get("realized")))

    def uc_of_req(rid: str) -> tuple[str, ...]:
        return trace.ucs_of(rid)

    needs_by_req: dict[str, list[str]] = {}
    deployment_unknown_refs: list[str] = []
    known_requirement_ids = set(by_id)
    for need_id, need in deployment_needs.items():
        for requirement_id in need.get("requirementIds", []):
            if requirement_id not in known_requirement_ids:
                deployment_unknown_refs.append(requirement_id)
                continue
            needs_by_req.setdefault(requirement_id, []).append(need_id)

    rows = []
    for r in classified:
        rid, typ = r["id"], r.get("type")
        ucs = sorted(set(uc_of_req(rid)))
        steps = list(trace.steps_of.get(rid, ()))
        # Phase 2: NFR이 한정하는 부모 FR과, 그 FR을 커버하는 UC들(제약이 간접 추적되는 곳).
        qualifies = list(r.get("qualifies", [])) if typ == "NFR" else []
        q_ucs = sorted({u for fr in qualifies for u in uc_of_req(fr)})
        if typ == "FR":
            status = "covered" if ucs else "orphan"
            vs = real_by_req.get(rid)
            realized = (any(vs) if vs else None)   # 판정 있으면 하나라도 실현했는지, 없으면 None
        elif ucs:
            status = "attached"          # UC에 직접 부착(nfr_ids)
            realized = True              # 구조적 ack: 라우팅됨
        elif q_ucs:
            status = "linked"            # 직접 부착은 없으나 부모 FR을 통해 추적됨(부착 갭 후보)
            realized = True
        else:
            status = "unattached"        # 어디에도 안 걸린 횡단/전역 제약 후보
            realized = False             # ack 갭
        rows.append({
            "id": rid, "type": typ, "text": r.get("text", ""),
            "use_cases": ucs, "scenario_steps": steps,
            "deployment_needs": sorted(needs_by_req.get(rid, [])),
            "qualifies": qualifies, "qualifies_use_cases": q_ucs,
            "status": status, "realized": realized,
        })

    # UC 커버리지(역방향): 각 UC가 커버하는 알려진 요구 + orphan UC(아무 요구도 안 걸림)
    use_case_view = []
    for uc in use_cases:
        covers = [rid for rid in (uc.get("requirement_ids", []) + uc.get("nfr_ids", []))
                  if rid in by_id]
        use_case_view.append({
            "id": uc["id"], "name": uc.get("name", ""),
            "covers": covers, "orphan_uc": not covers,
        })

    # 환각: UC가 참조했지만 분류 목록에 없는 id
    unknown_refs = list(trace.unknown_refs)

    fr_rows = [r for r in rows if r["type"] == "FR"]
    nfr_rows = [r for r in rows if r["type"] == "NFR"]
    summary = {
        "fr_total": len(fr_rows),
        "fr_covered": sum(1 for r in fr_rows if r["status"] == "covered"),
        "fr_orphan": sum(1 for r in fr_rows if r["status"] == "orphan"),
        "fr_coverage_ratio": round(
            sum(1 for r in fr_rows if r["status"] == "covered") / len(fr_rows), 4
        ) if fr_rows else 1.0,
        "nfr_total": len(nfr_rows),
        "nfr_attached": sum(1 for r in nfr_rows if r["status"] == "attached"),
        "nfr_linked": sum(1 for r in nfr_rows if r["status"] == "linked"),
        "nfr_unattached": sum(1 for r in nfr_rows if r["status"] == "unattached"),
        # Phase 3(검증): FR judge 판정 요약 + NFR ack.
        "fr_verified": sum(1 for r in fr_rows if r["realized"] is True),
        "fr_false_claim": sum(1 for r in fr_rows if r["realized"] is False),
        "fr_unjudged": sum(1 for r in fr_rows if r["realized"] is None),
        "nfr_ack": sum(1 for r in nfr_rows if r["realized"] is True),
        "nfr_gap": sum(1 for r in nfr_rows if r["realized"] is False),
        "orphan_use_cases": [u["id"] for u in use_case_view if u["orphan_uc"]],
        "unknown_refs": unknown_refs,
        "deployment_needs": len(deployment_needs),
        "deployment_unknown_refs": sorted(set(deployment_unknown_refs)),
    }
    return {"rows": rows, "use_case_view": use_case_view,
            "deployment_needs": deployment_needs,
            "unknown_refs": unknown_refs, "summary": summary}


def _clip(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _verdict_cell(row: dict) -> str:
    """realized 값을 유형에 맞는 표기로. FR=judge 실현, NFR=구조적 ack."""
    v = row.get("realized")
    if row["type"] == "FR":
        return {True: "✓ verified", False: "✗ 거짓주장", None: "—"}[v]
    return "✓ ack" if v else "✗ gap"


def render_rtm_md(rtm: dict, title: str = "") -> str:
    """RTM을 markdown 매트릭스로 렌더링한다."""
    s = rtm["summary"]
    L: list[str] = []
    L.append(f"# 요구사항 추적 매트릭스 (RTM){f' — {title}' if title else ''}")
    L.append("")
    L.append(f"- FR 커버리지: **{s['fr_covered']}/{s['fr_total']}** "
             f"({s['fr_coverage_ratio']:.0%}) · orphan {s['fr_orphan']}")
    L.append(f"- NFR 부착: **{s['nfr_attached']}/{s['nfr_total']}** · "
             f"linked(부모 FR 경유) {s.get('nfr_linked', 0)} · unattached {s['nfr_unattached']}")
    # Phase 3: 검증(realized) 요약 — 판정이 있었을 때만 표기.
    if s.get("fr_unjudged", 0) < s["fr_total"]:
        L.append(f"- FR 검증(judge): **verified {s.get('fr_verified', 0)}/{s['fr_total']}** · "
                 f"거짓주장 {s.get('fr_false_claim', 0)} · 미판정 {s.get('fr_unjudged', 0)}")
    L.append(f"- NFR 검증(ack): **{s.get('nfr_ack', 0)}/{s['nfr_total']}** · gap {s.get('nfr_gap', 0)}")
    L.append(f"- 배포 필요사항: **{s.get('deployment_needs', 0)}개**")
    if s["orphan_use_cases"]:
        L.append(f"- **orphan UC**(요구 미추적): {', '.join(s['orphan_use_cases'])}")
    if s["unknown_refs"]:
        L.append(f"- **환각 참조**(없는 요구 id): {', '.join(s['unknown_refs'])}")
    L.append("")
    L.append("## 요구 → 설계 추적")
    L.append("")
    L.append("| 요구 | 유형 | 내용 | →FR | UC | 배포 필요사항 | 명세 스텝 | 상태 | 검증 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rtm["rows"]:
        qual = ", ".join(r.get("qualifies", [])) or "—"
        if r["use_cases"]:
            ucs = ", ".join(r["use_cases"])
        elif r.get("qualifies_use_cases"):
            ucs = ", ".join(r["qualifies_use_cases"]) + " (via →FR)"
        else:
            ucs = "—"
        steps = ", ".join(r["scenario_steps"]) or "—"
        needs = ", ".join(r.get("deployment_needs", [])) or "—"
        verdict = _verdict_cell(r)
        L.append(f"| {r['id']} | {r['type']} | {_clip(r['text'])} | {qual} | {ucs} | {needs} | {steps} "
                 f"| {r['status']} | {verdict} |")
    L.append("")
    L.append("## UC → 요구 추적 (역방향)")
    L.append("")
    L.append("| UC | 이름 | 커버 요구 | orphan |")
    L.append("|---|---|---|---|")
    for u in rtm["use_case_view"]:
        covers = ", ".join(u["covers"]) or "—"
        L.append(f"| {u['id']} | {_clip(u['name'], 40)} | {covers} | {'⚠' if u['orphan_uc'] else ''} |")
    L.append("")
    return "\n".join(L) + "\n"
