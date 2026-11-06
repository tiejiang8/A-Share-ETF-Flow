"""Atomic file replacement resilient to transient Windows file locks.

On Windows, ``os.replace`` / ``Path.replace`` can raise ``PermissionError``
(WinError 5 "拒绝访问" or WinError 32 "另一个程序正在使用此文件") when an OS
component (antivirus real-time scan, Search indexer) or a *concurrent* pipeline
run briefly holds the destination file open. The daily pipeline writes several
shared parquet/csv artifacts and a single failed rename used to abort the whole
run. Retrying with exponential backoff lets the rename succeed once the lock
clears, which also makes two overlapping ``run --date auto`` invocations safe to
co-exist instead of killing each other.
"""
from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def atomic_replace(
    src: Path,
    dst: Path,
    *,
    attempts: int = 15,
    base_delay: float = 0.15,
) -> None:
    """Replace ``dst`` with ``src`` atomically, retrying on transient locks.

    ``src`` is expected to be a just-written temporary file. On success the
    temporary file is gone (renamed to ``dst``); on exhaustion the original
    ``PermissionError`` is re-raised and the caller's cleanup is expected to
    remove the temporary file, leaving ``dst`` intact.
    """
    src = Path(src)
    dst = Path(dst)
    for attempt in range(attempts):
        try:
            src.replace(dst)
            return
        except PermissionError as exc:
            if attempt == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))


def atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        atomic_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class FileLockTimeout(TimeoutError):
    """The lock was still held by another process at the acquisition deadline."""

    def __init__(self, path: Path):
        super().__init__(f"文件锁被占用: {path}")
        self.path = path


@contextmanager
def exclusive_file_lock(path: Path, *, timeout: float):
    """Hold an OS lock on a stable file that stays in place after release."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        if os.name == "nt":
            import msvcrt
        else:
            import fcntl
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(path) from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
