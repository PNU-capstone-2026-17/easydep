"""app/requirements/common/telemetry.py — 관측 계층 테스트.

이 계층의 목적은 **조용한 실패를 만들지 않는 것**이라, 테스트도 "실패가 성공과
구별되는가"를 중심으로 본다. 네트워크는 쓰지 않는다.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.requirements.common import telemetry


def test_stats_only_accumulate_inside_a_scope():
    """스코프 밖 호출도 죽지 않는다 — 집계만 안 될 뿐이다."""
    assert telemetry.current_run() is None
    with telemetry.record_llm_call("outside") as call:
        call.observe_usage({"input_tokens": 5, "output_tokens": 7})
    # 예외 없이 통과하면 된다.

    with telemetry.run_scope("inside") as stats:
        with telemetry.record_llm_call("op") as call:
            call.observe_usage({"input_tokens": 5, "output_tokens": 7})
    assert stats.as_dict()["llm_calls"] == 1
    assert stats.as_dict()["prompt_tokens"] == 5
    assert stats.as_dict()["completion_tokens"] == 7
    event = stats.as_dict()["llm_timing_events"][0]
    assert event["operation"] == "op"
    assert event["status"] == "completed"
    assert event["elapsedSeconds"] >= 0
    assert event["startedAt"]
    assert event["finishedAt"]


def test_scope_is_restored_after_exit():
    with telemetry.run_scope("a"):
        assert telemetry.current_run() is not None
    assert telemetry.current_run() is None


def test_progress_scope_receives_llm_start_and_finish_events():
    events = []

    with telemetry.progress_scope(lambda event, fields: events.append((event, fields))):
        with telemetry.record_llm_call("structured:Example") as call:
            call.observe_usage({"input_tokens": 3, "output_tokens": 2})

    assert [event for event, _fields in events] == [
        "llmOperationStarted",
        "llmOperationFinished",
    ]
    assert events[-1][1]["promptTokens"] == 3
    assert events[-1][1]["completionTokens"] == 2


def test_progress_sink_failure_does_not_fail_the_observed_work():
    def fail(_event, _fields):
        raise RuntimeError("observer unavailable")

    with telemetry.progress_scope(fail):
        with telemetry.record_llm_call("op"):
            pass


def test_usage_accumulates_across_retries_in_one_call():
    """논리적 호출 1건이 실제 요청 2건일 수 있다(폴백). 토큰은 합산돼야 한다."""
    with telemetry.run_scope("run") as stats:
        with telemetry.record_llm_call("op") as call:
            call.observe_usage({"input_tokens": 10, "output_tokens": 1})
            call.observe_usage({"input_tokens": 20, "output_tokens": 2})
    summary = stats.as_dict()
    assert summary["llm_calls"] == 1          # 호출은 한 건
    assert summary["prompt_tokens"] == 30     # 토큰은 양쪽 다 나갔다
    assert summary["completion_tokens"] == 3


def test_missing_usage_metadata_is_not_an_error():
    """사용량을 안 주는 게이트웨이가 계측 때문에 본 작업을 죽이면 안 된다."""
    with telemetry.run_scope("run") as stats:
        with telemetry.record_llm_call("op") as call:
            call.observe_usage(None)
            call.observe_usage({"input_tokens": None, "output_tokens": None})
    assert stats.as_dict()["prompt_tokens"] == 0


def test_failure_is_counted_and_reraised():
    """계측은 예외를 삼키지 않는다 — 삼킬지는 부르는 쪽이 정한다."""
    with telemetry.run_scope("run") as stats:
        with pytest.raises(RuntimeError):
            with telemetry.record_llm_call("op"):
                raise RuntimeError("boom")
    summary = stats.as_dict()
    assert summary["llm_calls"] == 1
    assert summary["llm_failures"] == 1


def test_fallback_is_counted_separately_from_failure():
    with telemetry.run_scope("run") as stats:
        with telemetry.record_llm_call("op") as call:
            call.mark_fallback("parsed 없음")
    summary = stats.as_dict()
    assert summary["structured_fallbacks"] == 1
    assert summary["llm_failures"] == 0


def test_degradation_records_component_reason_and_subject():
    with telemetry.run_scope("run") as stats:
        telemetry.record_degradation("spec.semantic_validator", "timeout", subject="UC3")
    assert stats.as_dict()["degradations"] == [
        {"component": "spec.semantic_validator", "reason": "timeout", "subject": "UC3"}
    ]


def test_thread_pool_loses_the_scope_without_bind_context():
    """이 테스트는 bind_context가 왜 있는지를 고정한다.

    ThreadPoolExecutor는 contextvars를 복사하지 않으므로, 감싸지 않고 submit 하면
    워커의 계측이 통째로 사라진다 — 조용히.
    """
    with telemetry.run_scope("run") as stats:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: telemetry.current_run(), range(2)))
    assert stats.as_dict()["llm_calls"] == 0


def test_bind_context_carries_the_scope_into_worker_threads():
    def work(index: int) -> None:
        with telemetry.record_llm_call(f"op{index}") as call:
            call.observe_usage({"input_tokens": 1, "output_tokens": 1})

    with telemetry.run_scope("run") as stats:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(telemetry.bind_context(work), i) for i in range(8)]
            for fut in futures:
                fut.result()

    summary = stats.as_dict()
    assert summary["llm_calls"] == 8          # 락이 걸려 있어 세다가 유실되지 않는다
    assert summary["prompt_tokens"] == 8


def test_configure_logging_is_idempotent():
    telemetry.configure_logging()
    logger = logging.getLogger(telemetry.LOGGER_NAME)
    # 핸들러 개수를 직접 세지 않는다 — pytest의 caplog가 자기 핸들러를 붙여 둔다.
    before = len(logger.handlers)
    telemetry.configure_logging()
    assert len(logger.handlers) == before
    # 이 저장소를 쓰는 쪽의 루트 로깅을 오염시키지 않는다.
    assert logger.propagate is False


def test_run_summary_does_not_crash_the_logger(caplog):
    """`extra=`가 LogRecord 예약 이름과 부딪히면 logging이 KeyError를 던진다.

    RunStats에는 `name`이 있어서 실제로 그랬다 — 로그 레벨이 INFO로 올라가는 순간
    모든 분석 요청이 스코프를 닫으면서 죽었다. 예약 이름은 접두사로 피한다.
    """
    telemetry.configure_logging()
    with caplog.at_level(logging.INFO, logger=telemetry.LOGGER_NAME):
        with telemetry.run_scope("analyze:t1"):
            pass
    record = next(r for r in caplog.records if r.message == "run finished")
    assert record.field_name == "analyze:t1"   # 접두사가 붙어 살아남았다
    assert record.name.startswith(telemetry.LOGGER_NAME)  # 로거 이름은 그대로


def test_log_fields_only_renames_what_it_must():
    renamed = telemetry._log_fields({"name": "x", "llm_calls": 3})
    assert renamed == {"field_name": "x", "llm_calls": 3}


# ---------------------------------------------------------------------------
# 재현성 — seed를 보냈다는 사실이 아니라, 서버가 같은 구성으로 답했다는 사실이 근거다.
# ---------------------------------------------------------------------------
def test_fingerprints_are_collected_from_calls():
    with telemetry.run_scope("run") as stats:
        with telemetry.record_llm_call("a") as call:
            call.observe_metadata({"system_fingerprint": "fp_1"})
        with telemetry.record_llm_call("b") as call:
            call.observe_metadata({"system_fingerprint": "fp_1"})
    assert stats.as_dict()["model_fingerprints"] == ["fp_1"]
    assert stats.as_dict()["degradations"] == []      # 한 구성이면 문제 없다


def test_backend_change_mid_run_is_a_degradation():
    """한 실행에서 백엔드가 바뀌면 seed를 고정해도 앞뒤 표본 조건이 다르다."""
    with telemetry.run_scope("run") as stats:
        with telemetry.record_llm_call("a") as call:
            call.observe_metadata({"system_fingerprint": "fp_1"})
        with telemetry.record_llm_call("b") as call:
            call.observe_metadata({"system_fingerprint": "fp_2"})

    summary = stats.as_dict()
    assert summary["model_fingerprints"] == ["fp_1", "fp_2"]
    assert [d["component"] for d in summary["degradations"]] == ["llm.fingerprint"]
    assert summary["degradations"][0]["subject"] == "fp_1,fp_2"


def test_metadata_without_a_fingerprint_is_ignored():
    with telemetry.run_scope("run") as stats:
        with telemetry.record_llm_call("a") as call:
            call.observe_metadata(None)
            call.observe_metadata({"model_name": "x"})
            call.observe_metadata({"system_fingerprint": ""})
    assert stats.as_dict()["model_fingerprints"] == []


def test_extra_fields_are_rendered_next_to_the_message():
    formatter = telemetry._KeyValueFormatter("%(message)s")
    record = logging.LogRecord(
        "n", logging.INFO, "path", 1, "llm call", None, None
    )
    record.operation = "structured:UseCaseSpec"
    record.seconds = 1.5
    rendered = formatter.format(record)
    assert rendered.startswith("llm call | ")
    assert "operation='structured:UseCaseSpec'" in rendered
    assert "seconds=1.5" in rendered
