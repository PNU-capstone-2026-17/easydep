"""테스트 공통 픽스처.

costkb는 `output/tumblebug-cost.json`이 있으면 그걸 쓰고 없으면 번들 36건으로 폴백한다.
이 편의성이 테스트에서는 함정이다 — 개발자가 `costkb build`를 한 번 돌리면 같은 테스트가
갑자기 73k건 미러를 보게 되어, 어제 통과하던 단언이 오늘 깨진다(실제로 깨졌다).

그래서 output_dir을 빈 tmp_path로 고정한다. 단위 테스트는 **번들 36건**을 대상으로 하고,
미러 투영은 `test_costkb_projection.py`가 행 dict fixture로 따로 검증한다.
"""

from __future__ import annotations

import pytest

from costkb import dataset


@pytest.fixture(autouse=True)
def _costkb_uses_bundle(tmp_path_factory, monkeypatch):
    """빌드 산출물이 있어도 테스트는 번들을 보게 하고, lru_cache 누수를 막는다."""
    monkeypatch.setattr(dataset, "DEFAULT_OUTPUT_DIR", tmp_path_factory.mktemp("no-build"))
    dataset.clear_caches()
    yield
    dataset.clear_caches()
