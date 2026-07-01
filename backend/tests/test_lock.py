"""Tests for backend process lock."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_tg_bridge.lock import ProcessLock, ProcessLockError


class TestProcessLock(unittest.TestCase):
    def test_acquire_and_release_creates_and_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "bridge.lock"
            lock = ProcessLock(lock_path)

            lock.acquire()
            self.assertTrue(lock_path.exists())

            lock.release()
            self.assertFalse(lock_path.exists())

    def test_stale_pid_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "bridge.lock"
            lock_path.write_text("999999\n", encoding="utf-8")
            lock = ProcessLock(lock_path)

            with patch("hermes_tg_bridge.lock._pid_alive", return_value=False):
                lock.acquire()

            self.assertTrue(lock_path.exists())
            lock.release()

    def test_live_pid_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "bridge.lock"
            lock_path.write_text("12345\n", encoding="utf-8")
            lock = ProcessLock(lock_path)

            with patch("hermes_tg_bridge.lock._pid_alive", return_value=True):
                with self.assertRaises(ProcessLockError):
                    lock.acquire()

            # the live owner's lock file must survive an unsuccessful acquire attempt
            self.assertEqual(lock_path.read_text(encoding="utf-8").strip(), "12345")

    def test_second_acquire_by_same_process_raises_since_pid_is_alive(self) -> None:
        # Simulates two processes racing for the same lock file: the first
        # acquire() call wins atomically via O_EXCL, and any concurrent/second
        # attempt against the now-existing file must fail rather than silently
        # believe it owns the lock too.
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "bridge.lock"
            first = ProcessLock(lock_path)
            first.acquire()
            self.assertTrue(lock_path.exists())

            second = ProcessLock(lock_path)
            with self.assertRaises(ProcessLockError):
                second.acquire()

            first.release()

    def test_acquire_succeeds_after_stale_file_removed_race(self) -> None:
        # Regression guard for the TOCTOU window: even if the stale file is
        # removed by a concurrent unlink between our exists-check and our own
        # unlink attempt, acquire() must still succeed via the retry loop.
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "bridge.lock"
            lock_path.write_text("999999\n", encoding="utf-8")
            lock = ProcessLock(lock_path)

            real_unlink = Path.unlink

            def flaky_unlink(self: Path, *args, **kwargs):
                real_unlink(self, *args, **kwargs)
                raise OSError("simulated concurrent removal")

            with patch("hermes_tg_bridge.lock._pid_alive", return_value=False):
                with patch.object(Path, "unlink", flaky_unlink):
                    lock.acquire()

            self.assertTrue(lock_path.exists())
            lock.release()


if __name__ == "__main__":
    unittest.main()
