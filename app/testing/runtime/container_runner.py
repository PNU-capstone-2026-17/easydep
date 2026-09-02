"""Testing이 생성 앱을 실행할 때 공유하는 toolchain image 설정."""

from __future__ import annotations

import os

from app.config import settings

RUNNER_IMAGE_ENV = "EASYDEP_TOOLCHAIN_IMAGE"
DEFAULT_RUNNER_IMAGE = "easydep-toolchain:local"
GRADLE_CACHE_VOLUME = "easydep-member-gradle-cache"


def configured_runner_image(environment: dict[str, str] | None = None) -> str:
    """환경변수와 공용 설정에서 로컬 toolchain image 이름을 고른다."""

    source = os.environ if environment is None else environment
    value = source.get(RUNNER_IMAGE_ENV, "").strip()
    return value or (settings.easydep_toolchain_image or "").strip() or DEFAULT_RUNNER_IMAGE
