from __future__ import annotations

import threading

from app.requirements.common import llm_stall_probe


def test_stall_probe_runs_once_after_the_configured_delay(monkeypatch):
    called = threading.Event()
    operations = []
    monkeypatch.setenv("EASYDEP_LLM_STALL_PROBE_AFTER_SECONDS", "0.01")
    monkeypatch.setenv("EASYDEP_LLM_STALL_PROBE_TIMEOUT_SECONDS", "7")
    monkeypatch.setattr(
        llm_stall_probe,
        "_probe",
        lambda operation, timeout: operations.append((operation, timeout)) or called.set(),
    )

    completed = llm_stall_probe.start_stall_probe("SequenceModel")

    assert called.wait(1)
    completed.set()
    assert operations == [("SequenceModel", 7.0)]


def test_stall_probe_is_cancelled_when_the_main_call_finishes(monkeypatch):
    called = threading.Event()
    monkeypatch.setenv("EASYDEP_LLM_STALL_PROBE_AFTER_SECONDS", "0.05")
    monkeypatch.setattr(
        llm_stall_probe,
        "_probe",
        lambda *_args: called.set(),
    )

    completed = llm_stall_probe.start_stall_probe("BCEExtractionResult")
    completed.set()

    assert not called.wait(0.1)
