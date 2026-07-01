"""PID lock file to prevent concurrent bridge instances for one Telegram session."""

from __future__ import annotations

import os
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class ProcessLockError(RuntimeError):
    """Raised when a live process already holds the lock."""


class ProcessLock:
    def __init__(self, path: str | Path | None) -> None:
        self._path = Path(path).expanduser().resolve() if path else None
        self._acquired = False

    @property
    def path(self) -> Path | None:
        return self._path

    def acquire(self) -> None:
        if self._path is None or self._acquired:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ponytail: O_CREAT|O_EXCL makes file creation atomic, so two processes
        # racing to acquire the lock can't both believe they own it. On EEXIST
        # we inspect the stale file and retry a bounded number of times.
        for _ in range(5):
            try:
                fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    raw_pid = self._path.read_text(encoding="utf-8").strip()
                except OSError:
                    raw_pid = ""
                if raw_pid.isdigit() and _pid_alive(int(raw_pid)):
                    raise ProcessLockError(
                        f"Bridge lock is already held by PID {raw_pid}: {self._path}"
                    )
                try:
                    self._path.unlink()
                except OSError:
                    pass
                continue
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"{os.getpid()}\n")
                self._acquired = True
                return
        raise ProcessLockError(f"Could not acquire bridge lock: {self._path}")

    def release(self) -> None:
        if self._path is None or not self._acquired:
            return
        try:
            if self._path.exists():
                self._path.unlink()
        finally:
            self._acquired = False
