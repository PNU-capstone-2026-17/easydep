"""envkb: 클라우드 환경 사실 지식베이스 — 리전·탄소·지연·수명주기·드라이버 커버리지·기본 이미지.

## 왜 갈라져 나왔나 (재편 계획 ⑤, 2026-07-24)

이 여섯 축은 kbcommon("공용 배관") 안에서 자랐다 — 합쳐 ~1,900줄, 자체 산출물
6개짜리 지식베이스가 "공용"이라는 이름 아래 있었고, "KB냐 공용이냐"의 기준이
없어서 다음 축도 거기 들어올 참이었다. 기준을 세웠다: **kbcommon에는 데이터셋이
없다. 자체 산출물을 소유하면 그것은 KB다** (tests/test_architecture.py가 고정).

지식 차원 분담:
- `graphkb`    — 타입 간 의존성        `capacitykb` — 용량·제약
- `costkb`     — 스펙·가격             `perfkb`     — 성능 특성
- `bundlekb`   — 무엇이 딸려 오나      `sizingkb`   — 사이징 규칙
- `envkb`      — 이 패키지: **리소스가 아니라 환경**의 사실 — 리전이 어디고
  무엇이라 불리며, 탄소가 얼마고, 리전 사이가 얼마나 멀고, 언제 지원이 끝나고,
  멀티클라우드 도구가 어느 CSP를 다루고, 기본 이미지가 무엇인가.

## 모양이 다른 이유

다른 KB는 model/dataset/agent_api/cli로 갈라져 있지만 여기는 축마다 모듈
하나다(carbon.py·latency.py·…). 코드 이동이지 재작성이 아니다 — 동작이 같은
것을 모양 때문에 다시 짓지 않는다(재편 계획의 "하지 않는 것"). 공개 표면은
여섯 모듈 자체이고, cloudinfo.py는 regions의 파서라 비공개다.

빌드: `python -m envkb build-regions` 등 (kbcommon CLI에서 함께 이사).
산출물 6종은 data/에 커밋돼 있어 클론 직후에도 동작한다.
"""

from __future__ import annotations
