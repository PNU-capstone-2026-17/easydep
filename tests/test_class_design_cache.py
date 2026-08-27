"""Contract tests for the required accepted class-design unit cache."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache


@pytest.fixture
def accepted_cache():
    return ProcessLocalAcceptedUnitCache(capacity=2)


def _get_or_compute(cache: Any):
    method = getattr(cache, "get_or_compute", None)
    if method is None:
        pytest.fail("AcceptedUnitCache must expose get_or_compute(key, producer)")
    return method


def _compute(cache: Any, key: str, producer):
    return _get_or_compute(cache)(key, producer)


def test_accepted_cache_is_bounded_and_returns_deep_copies(accepted_cache):
    one = _compute(
        accepted_cache, "one", lambda: {"items": [{"name": "A"}]}
    )
    two = _compute(
        accepted_cache, "two", lambda: {"items": [{"name": "B"}]}
    )

    returned = one.value
    returned["items"][0]["name"] = "mutated"
    assert _compute(accepted_cache, "one", lambda: {"bad": True}).value == {
        "items": [{"name": "A"}]
    }

    assert two.value == {"items": [{"name": "B"}]}


def test_accepted_cache_evicts_the_oldest_completed_unit(accepted_cache):
    _compute(accepted_cache, "one", lambda: {"name": "A"})
    _compute(accepted_cache, "two", lambda: {"name": "B"})
    _compute(accepted_cache, "three", lambda: {"name": "C"})

    one = _compute(accepted_cache, "one", lambda: {"name": "A2"})
    assert one.status == "miss"
    assert one.value == {"name": "A2"}
    # The untouched newest entry remains available after the replacement.
    assert _compute(accepted_cache, "three", lambda: {"bad": True}).status == "hit"


def test_accepted_cache_single_flight_computes_a_same_key_once(accepted_cache):
    get_or_compute = _get_or_compute(accepted_cache)
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()

    def produce() -> dict[str, list[str]]:
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        release.wait(timeout=5)
        return {"items": ["accepted"]}

    def request() -> dict[str, list[str]]:
        return get_or_compute("same-key", produce)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(request) for _index in range(8)]
        assert entered.wait(timeout=5)
        release.set()
        results = [future.result() for future in futures]

    assert calls == 1
    assert [result.value for result in results] == [{"items": ["accepted"]}] * 8
    assert {result.status for result in results} == {"miss", "coalesced"}
    assert results[0].value is not results[1].value


def test_accepted_cache_does_not_store_a_failed_computation(accepted_cache):
    get_or_compute = _get_or_compute(accepted_cache)
    calls = 0

    def fail() -> dict[str, str]:
        nonlocal calls
        calls += 1
        raise RuntimeError("candidate was not accepted")

    with pytest.raises(RuntimeError, match="candidate was not accepted"):
        get_or_compute("failed", fail)

    value = get_or_compute("failed", lambda: {"status": "accepted"})
    assert value.status == "miss"
    assert value.value == {"status": "accepted"}
    assert calls == 1


def test_sealed_cache_refuses_a_miss_before_running_the_producer(accepted_cache):
    produced = 0
    _compute(accepted_cache, "accepted", lambda: {"status": "accepted"})
    accepted_cache.seal()

    assert _compute(accepted_cache, "accepted", lambda: {"bad": True}).status == "hit"

    def produce_missing():
        nonlocal produced
        produced += 1
        return {"status": "unexpected"}

    with pytest.raises(LookupError, match="cache miss while sealed"):
        _compute(accepted_cache, "missing", produce_missing)
    assert produced == 0


def test_operation_cache_hit_is_revalidated_before_it_is_accepted():
    """A cache hit cannot bypass the typed and deterministic operation checks."""

    from app.design.services.class_diagram import operations
    from app.design.services.class_diagram.cache import CacheResult
    from app.design.services.class_diagram.models import AcceptedInventory
    from app.design.services.class_diagram.scenario import build_scenario_index
    from tests.class_design_fixtures import operation_fragment, single_use_case

    index = build_scenario_index(single_use_case())
    inventory = AcceptedInventory.from_payload({
        "Classes": [
            {"className": "RequestBoundary", "stereotype": "Boundary"},
            {"className": "RequestControl", "stereotype": "Control"},
        ],
        "DataTypes": [],
        "Relationships": [],
    })
    invalid = operation_fragment()
    # The model is schema-valid, but this system step is owned by Control while
    # the actor entry step must remain owned by Boundary.
    invalid["Classes"][1]["operations"][0]["stepRefs"] = ["UC1:main:1"]

    class InvalidHit:
        def get_or_compute(self, key, _compute):
            return CacheResult(invalid, "hit", key)

    with pytest.raises(ValueError, match="cached operation fragment"):
        operations.checked_fragment(
            index,
            inventory,
            index.use_case("UC1"),
            cache=InvalidHit(),
        )
