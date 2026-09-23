"""Focused Windows subprocess lifecycle coverage for the PlantUML renderer."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-only")


def _wait_until_exited(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel32.CloseHandle(handle)


def _assert_eventually_exited(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _wait_until_exited(pid):
            return
        time.sleep(0.05)
    pytest.fail(f"child process {pid} survived its Job Object owner")


def _terminate_if_running(pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if handle:
        try:
            kernel32.TerminateProcess(handle, 1)
            kernel32.WaitForSingleObject(handle, 5000)
        finally:
            kernel32.CloseHandle(handle)


def test_job_close_terminates_child_and_normal_stop_is_clean() -> None:
    from app.design.services.common.plantuml import PlantUmlRenderer, _WindowsJob

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    job = _WindowsJob(child)
    renderer = PlantUmlRenderer()
    renderer._process = child
    renderer._job = job
    renderer.stop()
    assert child.poll() is not None


def test_hard_killed_parent_does_not_leave_child_running() -> None:
    code = (
        "import subprocess,sys,time; "
        "from app.design.services.common.plantuml import _WindowsJob; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "job=_WindowsJob(child); print(child.pid, flush=True); time.sleep(60)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
    )
    assert parent.stdout is not None
    child_pid = None
    try:
        child_pid = int(parent.stdout.readline().strip())
        parent.kill()
        parent.wait(timeout=5)
        _assert_eventually_exited(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_pid is not None:
            _terminate_if_running(child_pid)
