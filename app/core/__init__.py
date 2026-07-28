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

## 정의는 여기 두지 않는다

`RESOURCE_SPEC` 스키마(`app/deployment/appkb/request.json`)와 리전 카탈로그는 배포 KB의
것이다. 여기로 **옮기지 않고 접근점만** 둔다 — 옮기면 배포 KB의 자기 도구·테스트가 읽는
자리와 두 벌이 되고, 두 벌은 갈라진다. 이 저장소가 사본으로 물린 자리가 이미 여럿이다.

  - `app/core`  — 누가 무엇을 부를 수 있는가(접근점)
  - `app/deployment` — 무엇이 참인가(정의)

## 규약

  - **`app/core`는 어느 에이전트도 import하지 않는다.** `app.requirements`·`app.design`을
    참조하는 순간 이 층은 공용이 아니게 된다.
  - **요구사항 에이전트가 `app.deployment`에 닿는 유일한 통로다.** 개발·CI 도구
    (`knowledge/verify_concerns.py`)만 예외이고, 그건 런타임 경로가 아니다.

둘 다 `tests/test_core_layer.py`가 지킨다.

## 다음 세입자

`app/requirements/common/`(telemetry·state_contract)이 여기로 올 자리를 기다리고 있다
(`basis.py`가 적어 둔 그 자리다). 이번에는 같이 옮기지 않았다 — import 경로만 바뀌는
기계적 이동이지만 건드리는 파일이 많아서, 클라우드 계약을 여는 변경과 섞으면 둘 중
어느 쪽이 무엇을 깨뜨렸는지 못 가린다.
"""
