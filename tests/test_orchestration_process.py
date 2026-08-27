from __future__ import annotations

import subprocess

import pytest

from app.implementation.runtime import process as process_boundary


def test_timeout_terminates_the_worker_tree(monkeypatch):
    class FakeProcess:
        pid = 1234
        returncode = -1
        calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["worker"], timeout)
            return "partial stdout", "partial stderr"

    worker = FakeProcess()
    terminated = []
    monkeypatch.setattr(process_boundary.subprocess, "Popen", lambda *_a, **_k: worker)
    monkeypatch.setattr(
        process_boundary,
        "_terminate_process_tree",
        lambda process: terminated.append(process.pid),
    )

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        process_boundary.run_process_tree(
            ["worker"], capture_output=True, text=True, timeout=1
        )

    assert terminated == [1234]
    assert caught.value.output == "partial stdout"
    assert caught.value.stderr == "partial stderr"


def test_success_returns_a_completed_process(monkeypatch):
    class FakeProcess:
        pid = 4321
        returncode = 0

        def communicate(self, timeout=None):
            assert timeout == 2
            return "ok", ""

    monkeypatch.setattr(
        process_boundary.subprocess, "Popen", lambda *_a, **_k: FakeProcess()
    )

    completed = process_boundary.run_process_tree(
        ["worker"], capture_output=True, text=True, timeout=2, check=True
    )

    assert completed.returncode == 0
    assert completed.stdout == "ok"
