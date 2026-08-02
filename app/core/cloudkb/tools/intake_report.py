"""상류 산출물 한 벌이 **배포 계획을 만들기에 충분한가** — 눈이 아니라 기계로.

    python -m app.core.cloudkb.tools.intake_report app/core/cloudkb/appkb/samples/<이름>
    python -m app.core.cloudkb.tools.intake_report <디렉터리> --plan   # 계획·다이어그램까지

## 왜 필요한가

2026-07-28에 어댑터 경로로 시연했더니 손으로 쓴 예제보다 결과가 나빴다 — 컴포넌트가
하나로 뭉치고, 시크릿 저장소가 빠지고, 큐가 선 없이 떴다. 그런데 **입력이 테스트
픽스처라 어댑터 탓인지 입력 탓인지 가릴 수 없었다.**

눈으로 읽어서는 못 가린다. 산출물마다 어떤 신호를 내야 하고 실제로 냈는지를 **세어야**
한다. 이 도구가 그 일을 한다.

## 무엇을 내나

세 갈래로 가른다 — 이 저장소가 다른 축에서 계속 지켜 온 구분 그대로다.

    있음      그 신호가 실제로 나왔다
    없음      산출물은 있는데 그 신호가 안 나왔다 (**입력에 그 정보가 없다**)
    못 읽음   산출물을 읽다 막혔다 (`$ref`·모르는 구조 — **우리 파서의 한계**)

가운데와 오른쪽을 섞으면 상류에 무엇을 더 달라고 해야 할지 알 수 없다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..kbcommon.console import use_utf8

#: 한 벌의 구성. `필수`는 없으면 배포 계획 자체가 못 서는 것.
_LAYOUT = {
    "design/api_spec.json": ("OpenAPI", False),
    "design/erd.puml": ("ER", False),
    "design/sequence_diagram.puml": ("시퀀스", False),
    "design/class_diagram.puml": ("클래스", False),
    "requirements/resource_spec.json": ("RESOURCE_SPEC", True),
    "requirements/cloud_concerns.json": ("관심사 커버리지", False),
}

#: 신호 → 그것이 없으면 계획에서 빠지는 것. 문장은 `samples/README.md`와 같은 표다.
_MEANS = {
    "has_api": "컴퓨트를 HTTP 서비스로 못 봄",
    "needs_secret": "**시크릿 저장소 노드가 안 생김**",
    "uploads": "**객체 스토리지 노드가 안 생김**",
    "owners": "**DB 노드가 안 생김**",
    "exposed": "**공개 노출을 몰라 로드밸런서가 안 생김**",
    "any_async": "**큐가 안 생김**",
    "sync_calls": "다이어그램에 **선이 없음**",
}


def _read(root: Path, rel: str) -> tuple[object | None, str]:
    path = root / rel
    if not path.exists():
        return None, "없는 파일"
    text = path.read_text(encoding="utf-8")
    if rel.endswith(".json"):
        try:
            return json.loads(text), ""
        except json.JSONDecodeError as exc:
            return None, f"JSON을 못 읽음: {exc}"
    return text, ""


def _design_from(root: Path, resource_spec: dict | None) -> tuple[dict | None, list[str]]:
    """샘플 한 벌 → 설계 JSON. 어댑터를 **실제로 태운다**(우회하지 않는다).

    `resource_spec`도 어댑터에 넘긴다 — 여기서 손으로 `requirements`에 꽂지
    않는다. RESOURCE_SPEC과 설계 계약의 `requirements`는 **같은 칸이 아니고**
    (`schemaVersion`·`regionAsWritten`은 안 내려간다), 그 투영의 진실은
    `appkb/easydep.py`의 `_REQ_FIELDS`다. 원본을 통째로 꽂았더니 계약이
    `schemaVersion was unexpected`로 막혀 계획이 0노드로 나왔다(2026-07-29) —
    입력이 나빠서가 아니라 **점검 도구가 어댑터를 우회해서**였다.
    """
    from ..appkb.easydep import design_from_easydep

    problems: list[str] = []
    api_spec, err = _read(root, "design/api_spec.json")
    if err:
        problems.append(f"api_spec: {err}")
    parts = {}
    for rel, key in (("design/class_diagram.puml", "class_puml"),
                     ("design/sequence_diagram.puml", "sequence_puml"),
                     ("design/erd.puml", "erd_puml")):
        text, err = _read(root, rel)
        if err:
            problems.append(f"{rel}: {err}")
        parts[key] = text or ""
    design, skipped = design_from_easydep(
        root.name, api_spec=api_spec if isinstance(api_spec, dict) else None,
        resource_spec=resource_spec, **parts,
    )
    problems.extend(skipped)
    return design, problems


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(
        prog="intake_report",
        description="상류 산출물 한 벌이 배포 계획을 만들기에 충분한가")
    parser.add_argument("sample_dir", help="samples/<이름> 디렉터리")
    parser.add_argument("--plan", action="store_true",
                        help="계획과 다이어그램까지 만들어 본다")
    args = parser.parse_args(argv)

    root = Path(args.sample_dir)
    if not root.is_dir():
        print(f"디렉터리가 아닙니다: {root}", file=sys.stderr)
        return 2

    print(f"\n{'=' * 74}\n 상류 산출물 점검 — {root.name}\n{'=' * 74}")

    # ── 1. 한 벌이 갖춰졌나
    print("\n[1] 파일")
    missing_required = []
    for rel, (label, required) in _LAYOUT.items():
        path = root / rel
        mark = "O" if path.exists() else ("**없음**" if required else "·")
        size = f"{path.stat().st_size:,}B" if path.exists() else ""
        print(f"  {mark:8s} {label:16s} {rel:34s} {size}")
        if required and not path.exists():
            missing_required.append(rel)

    # **출처가 없으면 손으로 쓴 것과 구별되지 않는다.** 이 저장소가 소스마다 핀을
    # 박는 것과 같은 이유다 — 어디서 왔는지 모르는 입력으로 낸 결과는 재현이 안 된다.
    if not (root / "PROVENANCE.md").exists():
        print("\n  ⚠ PROVENANCE.md가 없습니다 — 이 샘플은 **손으로 쓴 것과 구별되지 "
              "않습니다.** 누가·언제·무엇으로 만들었는지 적으십시오.")

    # ── 2. 어댑터가 무엇을 읽었나
    #
    # 사양을 먼저 읽는다 — [4]에서 다시 읽지만, 어댑터가 이것을 받아야 계약의
    # `requirements`가 선다. 형식이 dict가 아니면 없는 것으로 넘긴다(그 사실은 [4]가 적는다).
    spec, spec_err = _read(root, "requirements/resource_spec.json")
    design, adapter_problems = _design_from(
        root, spec if isinstance(spec, dict) else None)
    print("\n[2] 어댑터")
    if design is None:
        print("  설계 JSON을 만들지 못했습니다.")
        for p in adapter_problems:
            print(f"    · {p}")
        return 1
    print(f"  컴포넌트 {len(design.get('components') or [])}개 · "
          f"외부 {len(design.get('externals') or [])}개 · "
          f"산출물 {len(design.get('artifacts') or [])}종")
    for p in adapter_problems:
        print(f"    ⚠ 못 읽음·짐작: {p}")

    # ── 3. 신호가 실제로 나왔나
    from ..nim_agent.design_tools import _collect_signals

    s = _collect_signals(design)
    print("\n[3] 신호 — 없으면 계획에서 무엇이 빠지나")
    got = {
        "has_api": bool(s.has_api), "needs_secret": bool(s.needs_secret),
        "uploads": bool(s.uploads), "owners": bool(s.owners),
        "exposed": bool(s.exposed), "any_async": bool(s.any_async),
        "sync_calls": bool(s.sync_calls),
    }
    for name, present in got.items():
        print(f"  {'있음' if present else '**없음**':8s} {name:14s} "
              f"{'' if present else '→ ' + _MEANS[name]}")
    if s.unread:
        print("\n  못 읽음(우리 파서의 한계 — 입력에 없는 것과 다르다):")
        for u in s.unread:
            print(f"    · {u}")

    # ── 4. 요구사항 쪽 계약
    print("\n[4] RESOURCE_SPEC")
    if spec_err or not isinstance(spec, dict):
        print(f"  **없음** — {spec_err or '형식이 dict가 아님'}")
        print("    → provider·region이 없으면 값·성능·번들 조인이 **전부 닫힙니다**")
    else:
        from ..appkb.contract import validate_request

        problems = validate_request(spec)
        if problems:
            for p in problems:
                print(f"  **위반** {p}")
        else:
            print("  계약 통과")
        print(f"  채워진 칸: {', '.join(sorted(k for k in spec if spec[k] is not None))}")
        # 계약 통과와 **계획에 닿는 것**은 다르다 — 투영에서 빠지는 칸이 있다.
        descended = sorted(design.get("requirements") or {})
        dropped = sorted(set(spec) - set(descended))
        print(f"  계획으로 내려간 칸: {', '.join(descended) or '(없음)'}")
        if dropped:
            print(f"  안 내려간 칸: {', '.join(dropped)} (설계 계약의 투영 밖 — "
                  "appkb/easydep.py의 _REQ_FIELDS)")

    if args.plan:
        from ..appkb import diagram
        from ..nim_agent.design_tools import compose

        # **측정 하한**(있으면). 생성된 앱을 재서 나온 capacity.json을 계획의
        # requirements에 `_capacity`로 실어 sizing_floor의 measured 층이 읽게
        # 한다 — 공식이 아니라 측정이라 배제 규율 밖이다(measure_capacity).
        cap_path = root / "design" / "capacity.json"
        if cap_path.exists() and isinstance(design.get("requirements"), dict):
            try:
                cap = json.loads(cap_path.read_text(encoding="utf-8"))
                design["requirements"]["_capacity"] = cap
                print(f"  측정 하한 실림: {cap.get('mem_gib')} GiB "
                      f"(under {cap.get('under')})")
            except (json.JSONDecodeError, OSError):
                pass

        plan = compose(design)
        print(f"\n[5] 계획 — 노드 {len(plan.nodes)} · 선 {len(plan.edges)}")
        for u in plan.unresolved:
            print(f"    ⚠ {u}")
        print()
        print(diagram.render(plan))

        # ── 6. 하류가 먹는 산출물 ────────────────────────────────────────
        # **사슬의 마지막 이음매.** 구현 단계는 `cloud` 산출물을 기다리는데
        # 아무도 내지 않고 있었다(2026-08-01). 여기서 내고 파일로 남긴다 —
        # 표본을 태울 때마다 그 자리가 채워지는지 눈에 보이게.
        from app.core.cloud_artifact import write as write_cloud

        out = root / "design" / "cloud.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        art = write_cloud(plan, design, out, name=root.name)
        print("\n[6] 하류 산출물 — design/cloud.json")
        measured = art.get("_measured") or {}
        print(f"  앵커: {', '.join(measured.get('anchors') or []) or '(없음)'}")
        if measured.get("createOrder"):
            print(f"  생성 순서: {' → '.join(measured['createOrder'])}")
        for line in art.get("_unsupported") or []:
            print(f"  ⚠ {line}")
        if not art.get("resources"):
            print("  자원 0 — 하류 렌더러는 이 계획을 매니페스트로 못 바꾼다")

        # ── 7. 배포 후 검증 ──────────────────────────────────────────────
        # 기능 결속은 컨트롤 플레인이 막지 않아 apply 전 검사로는 안 잡힌다.
        checks = (art.get("_deployChecks") or {}).get("checks") or []
        print(f"\n[7] 배포 후 검증 — 점검 {len(checks)}건")
        for check in checks:
            because = check["because"]
            print(f"  [{check['where']:7}] {check['signal']:18} "
                  f"{because['subject']}→{because['object']}")
        if not checks:
            print("  (없음 — 이 구성의 자원에 걸리는 기능 결속 실측이 없다. "
                  "**문제없다는 뜻이 아니다**)")

        # ── 8. 구현 이후 테스팅 — 요구 도출 수용 스위트 ──────────────────
        # 앱의 자기 테스트가 아니라 **요구가 잣대**다(블랙박스). 요구+OpenAPI만
        # 있으면 도출되므로 구현 전에도 낸다 — 배포된 앱에 run_functional로 건다.
        classified, _ = _read(root, "requirements/classified.json")
        api_spec, _ = _read(root, "design/api_spec.json")
        if isinstance(classified, list) and isinstance(api_spec, dict):
            from ..appkb import acceptance

            suite = acceptance.derive(classified, api_spec)
            cov = acceptance.coverage(suite)
            print(f"\n[8] 구현 이후 테스팅 — 수용 검사 {cov['total']}건 "
                  f"(매핑 {cov['mapped']} · unmapped {cov['unmapped']} · "
                  f"기능 {cov['functional']}·NFR {cov['nfr']})")
            for c in suite:
                if c.unmapped:
                    print(f"  ✗ {c.requirement_id} unmapped — {c.unmapped[:50]}")
            acc_path = root / "design" / "acceptance.json"
            acc_path.write_text(
                json.dumps({"schemaVersion": "easydep-acceptance/v1alpha1",
                            "coverage": cov,
                            "checks": [c.as_dict() for c in suite]},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  기록: design/acceptance.json — 배포 후 "
                  "`acceptance.run_functional(base_url, ...)`로 실행")

    print()
    return 1 if missing_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
