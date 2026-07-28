"""에이전트들이 공유하는 얇은 층 — **문을 하나로 둔다.**

## 왜 생겼나

`app/requirements`(요구사항 분석)가 `RESOURCE_SPEC`을 생산하려면 그 계약이 무엇을
요구하는지 알아야 하고, 리전 지명을 코드로 풀어야 한다. 둘 다 `app/deployment` 안에
있다. 그런데 `app/requirements`는 `app/deployment` 없이 돌아야 한다는 규약이 있었다
(`app/requirements/knowledge/basis.py`).

그 규약의 근거는 *"배포 KB는 데이터셋 수백 MB를 끌고 온다"*였다. **좁은 표면에서는
그게 참이 아니다** — 2026-07-28 실측: `appkb.contract` + `envkb.regions` 두 모듈을
import하면 446ms · 모듈 168개이고, torch·pandas 같은 것도, import 시점의 데이터셋
로드도 없다(데이터는 호출할 때 `lru_cache`로 들어온다). 무거운 것은 `jsonschema`
계열뿐이고, BERT를 이미 들고 있는 서빙 프로세스에서는 잡음이다.

그래서 규약을 없애지 않고 **문을 하나로 좁힌다**: 요구사항 에이전트는 `app/deployment`를
직접 import하지 않고 여기를 거친다. 규약이 지키던 것(의존이 조용히 번지지 않게 하는 것)은
그대로 지켜지고, 근거가 성립하지 않는 부분만 열린다.

## 남의 정의는 두지 않고, 공용의 집은 여기다

**갈래가 둘이고 섞으면 안 된다.**

  - **집이 따로 있는 정의는 옮기지 않는다.** `RESOURCE_SPEC` 스키마
    (`app/deployment/appkb/request.json`)와 리전 카탈로그는 배포 KB의 것이다. 여기로
    옮기면 배포 KB의 자기 도구·테스트가 읽는 자리와 **두 벌**이 되고, 두 벌은 갈라진다.
    그래서 접근점만 둔다 — `app/core`는 *누가 무엇을 부를 수 있는가*, `app/deployment`는
    *무엇이 참인가*.
  - **여러 에이전트가 쓸 것인데 집이 한 에이전트 안에 있는 것은 여기가 집이다.**
    추적성(`traceability.py`·`rtm.py`)이 그 첫 사례다(2026-07-28에 옮겼다). 요구사항
    에이전트 안에 있었지만 요구사항만의 것이 아니다 — 전 단계 산출물이 요구사항에 어떻게
    닿는지가 과제 목표(전 과정 일관 기준)의 축이다.

    ⚠ 처음에는 이 이동의 근거로 *"구현 엔진이 자기 `traceability-matrix.csv`를 쓴다"*를
    들었는데 **그건 중복이 아니다**(2026-07-28 정정). 그 CSV는
    `source_artifact / source_sha256 / generated_file`, 즉 **설계 산출물 → 생성 파일**
    출처 기록이고 한 실행에 고정된다. 여기 `rtm.py`는 **요구사항 → 유스케이스 → 명세 스텝**
    커버리지이고 요구 id로 색인된다. 겹치는 칸이 하나도 없다. 이동은 위 문장으로 정당하고,
    **둘을 합치려 들면 양쪽이 망가진다.**

가르는 질문은 하나다: *정의가 이미 다른 곳에 살고 있는가.* 살고 있으면 접근점만,
아니면 여기가 집이다.

## 규약

  - **`app/core`는 어느 에이전트도 import하지 않는다.** `app.requirements`·`app.design`을
    참조하는 순간 이 층은 공용이 아니게 된다.
  - **요구사항 에이전트가 `app.deployment`에 닿는 유일한 통로다.** 개발·CI 도구
    (`knowledge/verify_concerns.py`)만 예외이고, 그건 런타임 경로가 아니다.

둘 다 `tests/test_core_layer.py`가 지킨다.

## 이 층이 자란다 — 목적지 목록

**`app/core`는 클라우드 계약을 여는 임시 문이 아니라 공용 층이다.** 전체 시스템이 쓸
것들이 최종적으로 여기 모인다(2026-07-28 확정).

  - ✅ `cloud_contract.py`·`regions.py` — 배포 KB 접근점(2026-07-28)
  - ✅ `traceability.py`·`rtm.py` — 추적성(2026-07-28)
  - ⬜ **클라우드 네이티브 지식베이스** — `app/requirements/knowledge/concerns.py`와
    그 검증기. `basis.py`(근거 등급)가 함께 와야 한다. 옮길 때
    `prompts.fingerprint()`의 `concerns` 해시가 **바뀌지 않는지** 반드시 확인한다 —
    바뀌면 이전 실측과 이후 실측이 다른 판이 된다.
  - ⬜ `app/requirements/common/`(telemetry·state_contract) — `basis.py`가 적어 둔 자리.

한 번에 하나씩 옮긴다. 기계적 이동이라도 다른 변경과 섞으면 무엇이 무엇을 깨뜨렸는지
못 가린다.
"""
