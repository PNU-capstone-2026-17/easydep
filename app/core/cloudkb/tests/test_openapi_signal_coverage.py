"""OpenAPI 신호 추출의 **현재 커버리지를 못 박는다** — 조용한 회귀를 막으려고.

**왜 이 파일이 있나**: 설계 산출물에서 배포 신호를 뽑는 경로에만 이 저장소의 규율이
안 적용돼 있었다(감사 2026-07-28). 실무에서 흔한 OpenAPI 변형 9종으로 재 보니
**교과서적 형태 하나만** 세 신호를 다 냈고, 나머지 여덟은 시크릿·업로드를 놓쳤다.
그런데 **놓쳤다는 말이 어디에도 없었다** — 같은 API를 `$ref`로 쪼개 쓰면 계획에서
노드 두 개(시크릿 저장소·객체 스토리지)가 조용히 사라진다.

KB 축들은 이 문제를 이미 두 번 고쳤다(perfkb의 다섯 상태 · capacitykb의 3상태).
입력 파서에는 아직 안 왔다. 자세한 것은
`document/archive/pipeline-big-picture-2026-07-28.md` §2·§5.5.

## 이 파일이 **하지 않는** 것

**"이렇게 고쳐야 한다"를 단언하지 않는다.** 지금 동작을 사실로 적을 뿐이다. 파서를
고치면 이 테스트가 **깨지면서 무엇이 달라졌는지 말해 준다** — 그게 목적이다.
기대를 미래에 맞춰 두면 스위트가 상시 빨갛고, 그러면 진짜 실패가 안 보인다
(`tools/agent_probe.py`가 같은 이유로 지키는 규율).

## 읽는 법

`MISSES`에 있는 변형은 **파서의 알려진 한계**다. 고쳐서 신호가 나오게 되면 그 항목을
`MISSES`에서 빼야 테스트가 통과한다 — **개선이 기록을 강제한다.**
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.nim_agent.design_tools import _collect_signals

#: 변형 이름 → OpenAPI 문서. 전부 **실무에서 실제로 쓰이는 형태**다.
VARIANTS: dict[str, dict] = {
    "inline": {
        "openapi": "3.0.3", "info": {"title": "T", "version": "1"},
        "paths": {"/f": {"post": {"requestBody": {"content": {
            "multipart/form-data": {"schema": {"type": "object"}}}}}}},
        "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
    },
    "security-behind-ref": {
        # OpenAPI를 파일로 쪼개는 것은 **표준 관행**이다. 가장 흔한 실패 형태.
        "openapi": "3.0.3", "info": {"title": "T", "version": "1"},
        "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
        "components": {"$ref": "./security.yaml#/components"},
    },
    "security-used-not-declared": {
        # 루트 `security`로 쓰기만 하고 스킴 선언이 외부에 있는 경우.
        "openapi": "3.0.3", "info": {"title": "T", "version": "1"},
        "security": [{"bearer": []}],
        "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
    },
    "upload-behind-ref": {
        # 내부 참조(`#/components/...`)라 원리적으로는 우리가 펼 수 있다.
        "openapi": "3.0.3", "info": {"title": "T", "version": "1"},
        "paths": {"/f": {"post": {"requestBody": {
            "$ref": "#/components/requestBodies/Up"}}}},
        "components": {"requestBodies": {"Up": {"content": {
            "multipart/form-data": {"schema": {"type": "object"}}}}}},
    },
    "upload-as-image-mime": {
        # 파일 업로드인데 content-type이 목록에 없다. 스펙에는 `format: binary`가 있다.
        "openapi": "3.0.3", "info": {"title": "T", "version": "1"},
        "paths": {"/img": {"put": {"requestBody": {"content": {
            "image/png": {"schema": {"type": "string", "format": "binary"}}}}}}},
    },
    "webhooks-only-31": {
        # OpenAPI 3.1. webhook 선언은 **비동기 신호**인데 지금은 시퀀스에서만 읽는다.
        "openapi": "3.1.0", "info": {"title": "T", "version": "1"},
        "webhooks": {"orderPlaced": {"post": {"responses": {"200": {"description": "ok"}}}}},
    },
    "callbacks": {
        "openapi": "3.0.3", "info": {"title": "T", "version": "1"},
        "paths": {"/sub": {"post": {
            "callbacks": {"onEvent": {"{$request.body#/cb}": {
                "post": {"responses": {"200": {"description": "ok"}}}}}},
            "responses": {"200": {"description": "ok"}}}}},
    },
    "empty-paths": {
        "openapi": "3.0.3", "info": {"title": "T", "version": "1"}, "paths": {},
    },
    "servers-only-hint": {
        "openapi": "3.0.3", "info": {"title": "T", "version": "1"},
        "servers": [{"url": "https://api.example.com/v1"}],
        "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
    },
}

#: 그 변형이 **실제로 담고 있는** 신호. 사람이 문서를 읽고 판단한 것이다.
TRUTH: dict[str, set[str]] = {
    "inline": {"secret", "upload"},
    "security-behind-ref": {"secret"},
    "security-used-not-declared": {"secret"},
    "upload-behind-ref": {"upload"},
    "upload-as-image-mime": {"upload"},
    "webhooks-only-31": {"async"},
    "callbacks": {"async"},
    "empty-paths": set(),
    "servers-only-hint": set(),
}

#: **현재 파서가 놓치는 것** — 변형 이름 → 놓치는 신호.
#: 고쳐서 잡히게 되면 여기서 빼야 테스트가 통과한다.
#:
#: 2026-07-28에 `upload-as-image-mime`이 빠졌다 — content-type 목록 대신 스펙이
#: 주는 `schema.format == "binary"`를 보게 고쳤다. 나머지 다섯은 `$ref` 해소와
#: webhooks 읽기가 필요해 아직 남아 있고, **대신 못 읽었다고 말한다**(`UNREAD`).
MISSES: dict[str, set[str]] = {
    "security-behind-ref": {"secret"},
    "security-used-not-declared": {"secret"},
    "upload-behind-ref": {"upload"},
    "webhooks-only-31": {"async"},
    "callbacks": {"async"},
}

#: **놓치되 놓쳤다고 말해야 하는 것.** 이게 이 파일의 핵심 계약이다.
#:
#: 놓치는 것 자체는 파서의 한계이고 고치면 준다. 하지만 **놓쳤다는 말 없이 놓치는
#: 것**은 거짓말이고, 그건 커버리지와 무관하게 언제나 결함이다. KB 축들이 "수집
#: 범위 밖이라 모른다"를 문장으로 답하는 것과 같은 규율이다.
UNREAD = set(MISSES)


def _signals_for(spec: dict):
    design = {
        "schemaVersion": "1", "name": "t",
        "components": [{"id": "svc", "name": "Svc"}],
        "artifacts": [{"id": "a1", "kind": "openapi", "componentId": "svc",
                       "openapi": spec}],
    }
    return _collect_signals(design)


def _extracted(spec: dict) -> set[str]:
    s = _signals_for(spec)
    found = set()
    if s.needs_secret:
        found.add("secret")
    if s.uploads:
        found.add("upload")
    if s.any_async:
        found.add("async")
    return found


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_extraction_matches_the_recorded_state(name: str) -> None:
    """실제로 담긴 신호 = 뽑힌 신호 + 알려진 누락. **셋이 맞아떨어져야 한다.**

    깨지는 방향이 둘이고 뜻이 다르다:

    - 뽑힌 것이 늘었다 → 파서가 좋아졌다. `MISSES`에서 빼라.
    - 뽑힌 것이 줄었다 → **회귀다.** 조용히 사라지던 것을 여기서 잡는다.
    """
    extracted = _extracted(VARIANTS[name])
    expected = TRUTH[name] - MISSES.get(name, set())
    assert extracted == expected, (
        f"{name}: 뽑힌 신호가 기록과 다르다. 뽑힘={sorted(extracted)} "
        f"기대={sorted(expected)} (실제 담긴 것={sorted(TRUTH[name])} · "
        f"알려진 누락={sorted(MISSES.get(name, set()))})"
    )


def test_has_api_does_not_look_at_paths() -> None:
    """**부재를 긍정으로 승격하는 자리** — `paths`가 비어도 HTTP 서비스로 친다.

    지금 동작을 사실로 적어 둔다. 고치려면 이 테스트가 먼저 말을 걸 것이다.
    """
    assert _signals_for(VARIANTS["empty-paths"]).has_api == {"svc"}


def test_the_known_gap_is_the_majority() -> None:
    """**한 줄 요약을 검사로.** 변형 9종 중 완전 추출되는 것은 몇 종인가.

    이 수가 오르면 좋은 일이고, 그때 이 단언도 함께 고친다. 문서에 적어 두면
    문서가 먼저 늙지만, 여기 적으면 **코드와 함께 늙는다.**
    """
    complete = [n for n in VARIANTS if _extracted(VARIANTS[n]) == TRUTH[n]]
    # inline·upload-as-image-mime(완전) + 신호가 애초에 없는 둘
    assert sorted(complete) == [
        "empty-paths", "inline", "servers-only-hint", "upload-as-image-mime",
    ], f"완전 추출되는 변형이 달라졌다: {sorted(complete)}"
    signal_bearing = [n for n in VARIANTS if TRUTH[n]]
    caught = [n for n in signal_bearing if _extracted(VARIANTS[n]) == TRUTH[n]]
    assert len(caught) == 2, (
        f"신호를 담은 변형 {len(signal_bearing)}종 중 완전 추출 {len(caught)}종 "
        "— 이 비율이 바뀌면 기록을 갱신하라"
    )


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_a_missed_signal_is_never_silent(name: str) -> None:
    """**놓치는 것보다 나쁜 것은 놓쳤다는 말 없이 놓치는 것이다.**

    같은 API를 표준 `$ref` 방식으로 쓰면 계획에서 노드 두 개(시크릿 저장소·객체
    스토리지)가 조용히 사라졌다(실측 2026-07-28). 파서를 더 정교하게 만드는 것보다
    **못 읽었다고 말하는 것이 먼저다** — 그래야 `$ref` 해소가 불완전해도 답이
    거짓말이 되지 않는다.

    KB 축들은 이 규율을 이미 지킨다("수집 범위 밖이라 모른다"). 입력 파서에만
    없었다.
    """
    unread = _signals_for(VARIANTS[name]).unread
    if name in UNREAD:
        assert unread, f"{name}: 신호를 놓쳤는데 못 읽었다는 말이 없다"
    else:
        assert not unread, f"{name}: 다 읽었는데 못 읽었다고 한다 — {unread}"
