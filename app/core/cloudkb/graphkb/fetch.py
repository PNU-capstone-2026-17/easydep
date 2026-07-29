"""공개 스키마 다운로드 + 로컬 캐시.

구현은 `kbcommon.fetch`로 옮겼다 — `capacitykb`(리소스 용량·제약 KB)가
같은 소스를 내려받으므로 캐시를 공유하기 위함이다. 이 모듈은 기존
`graphkb.parsers.*`의 import 경로를 유지하기 위한 얇은 re-export다.
"""

from __future__ import annotations

from app.core.cloudkb.kbcommon.fetch import cache_dir, fetch_cached

__all__ = ["cache_dir", "fetch_cached"]
