"""공개 스키마 다운로드 + 로컬 캐시 (지식베이스 패키지 공유).

캐시 위치는 CLOUDKB_CACHE_DIR 환경변수, 없으면 GRAPHKB_CACHE_DIR(하위 호환),
그것도 없으면 프로젝트 로컬 `.cache/<namespace>`.

여러 KB 패키지가 같은 소스(CFN zip 2.8MB, bicep types 수십 MB 등)를 쓰므로
기본 네임스페이스를 공유해 중복 다운로드를 피한다.

부분 다운로드가 캐시를 오염시키지 않도록 `.part` 임시 파일에 쓴 뒤
os.replace로 원자적으로 교체한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

DEFAULT_NAMESPACE = "cloudkb"


def cache_dir(namespace: str = DEFAULT_NAMESPACE) -> Path:
    """캐시 디렉터리 경로를 반환한다 (생성은 fetch_cached에서).

    Args:
        namespace: 캐시를 나눌 이름. 기본값은 모든 KB가 공유하는 "cloudkb".
    """
    env = os.environ.get("CLOUDKB_CACHE_DIR") or os.environ.get("GRAPHKB_CACHE_DIR")
    return Path(env) if env else Path(".cache") / namespace


def fetch_cached(
    url: str,
    filename: str,
    *,
    refresh: bool = False,
    namespace: str = DEFAULT_NAMESPACE,
) -> Path:
    """URL을 내려받아 캐시된 파일 경로를 반환한다.

    Args:
        url: 다운로드할 URL. 기존 로컬 파일 경로면 다운로드 없이 그대로 사용
            (테스트/오프라인 경로).
        filename: 캐시 디렉터리 안에 저장할 파일 이름.
        refresh: True면 캐시가 있어도 다시 내려받는다.
        namespace: 캐시 네임스페이스.
    """
    local = Path(url)
    if local.exists():
        return local

    dest = cache_dir(namespace) / filename
    if dest.exists() and not refresh:
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in response.iter_bytes():
                fh.write(chunk)
    os.replace(tmp, dest)
    return dest
