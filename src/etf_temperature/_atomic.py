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

import time
from pathlib import Path


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
