"""Cross-platform non-blocking lock for resource-intensive implementation work."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

DEFAULT_WORKER_LOCK = Path(".easydep/orchestration/implementation-worker.lock")
DEFAULT_RUN_LOCK_ROOT = Path(".easydep/orchestration/run-locks")


def _lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_implementation_worker(
    path: str | Path = DEFAULT_WORKER_LOCK,
) -> Iterator[None]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        _lock(handle)
    except OSError as error:
        handle.close()
        raise RuntimeError(
            "Another EasyDep implementation worker is running; concurrent execution is rejected."
        ) from error
    try:
        yield
    finally:
        handle.seek(0)
        _unlock(handle)
        handle.close()


@contextmanager
def exclusive_run_execution(
    run_id: str, root: str | Path = DEFAULT_RUN_LOCK_ROOT
) -> Iterator[None]:
    """같은 run의 start/resume/retry가 겹치지 않도록 프로세스 경계를 잠근다."""
    digest = sha256(run_id.encode("utf-8")).hexdigest()
    with exclusive_implementation_worker(Path(root) / f"{digest}.lock"):
        yield
