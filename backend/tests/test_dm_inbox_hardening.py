"""Regression tests for the DM inbox hardening: cursor seeding, buffer overflow,
media retention, and the extended-API gate."""

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from hermes_tg_bridge.state import DmCursorStore

try:
    from hermes_tg_bridge.server import (
        CORE_API_PATHS,
        AllowedDmSender,
        DmInboxBroker,
        SendRateLimiter,
        _merge_dm_events,
        _prune_dm_media,
        _seed_dm_cursors,
        _text_send_cost,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - mirrors test_server.py
    if exc.name != "fastapi":
        raise
    CORE_API_PATHS = None
    AllowedDmSender = None
    DmInboxBroker = None
    SendRateLimiter = None
    _merge_dm_events = None
    _prune_dm_media = None
    _seed_dm_cursors = None
    _text_send_cost = None


def _sender(peer: str) -> "AllowedDmSender":
    return AllowedDmSender(peer_ref=peer, cursor_key=peer, match_keys=frozenset({peer}))


class TestCursorStoreBaseline(unittest.TestCase):
    def test_has_cursor_distinguishes_absent_from_zero(self) -> None:
        store = DmCursorStore(None)

        self.assertFalse(store.has_cursor("dm_inbox", "123"))
        store.ensure_cursor("dm_inbox", "123", 0)
        self.assertTrue(store.has_cursor("dm_inbox", "123"))
        self.assertEqual(store.get_cursor("dm_inbox", "123"), 0)

    def test_ensure_cursor_does_not_overwrite_existing_progress(self) -> None:
        store = DmCursorStore(None)
        store.ack("dm_inbox", "123", 50)

        self.assertEqual(store.ensure_cursor("dm_inbox", "123", 10), 50)
        self.assertEqual(store.get_cursor("dm_inbox", "123"), 50)

    def test_save_is_atomic_and_leaves_no_temp_files(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "cursors.json"
            store = DmCursorStore(path)
            store.ack("dm_inbox", "123", 7)

            self.assertEqual(store.get_cursor("dm_inbox", "123"), 7)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["cursors.json"])


@unittest.skipIf(_seed_dm_cursors is None, "fastapi is not installed in this test environment")
class TestCursorSeeding(unittest.IsolatedAsyncioTestCase):
    async def _seed(self, mode: str, read_state: dict) -> DmCursorStore:
        store = DmCursorStore(None)
        bridge = AsyncMock()
        bridge.get_dm_read_state = AsyncMock(return_value=read_state)
        with patch("hermes_tg_bridge.server.get_config", return_value={"dm_inbox_backfill": mode}):
            await _seed_dm_cursors(
                bridge=bridge,
                policy={},
                allowed_senders=[_sender("123")],
                consumer_id="dm_inbox",
                cursor_store=store,
            )
        return store

    async def test_unread_mode_starts_at_the_telegram_read_pointer(self) -> None:
        store = await self._seed(
            "unread", {"read_inbox_max_id": 100, "top_message_id": 120, "unread_count": 2}
        )
        # Messages 101..120 are unread and will still be delivered.
        self.assertEqual(store.get_cursor("dm_inbox", "123"), 100)

    async def test_none_mode_skips_everything_that_already_exists(self) -> None:
        store = await self._seed(
            "none", {"read_inbox_max_id": 100, "top_message_id": 120, "unread_count": 2}
        )
        self.assertEqual(store.get_cursor("dm_inbox", "123"), 120)

    async def test_all_mode_leaves_the_cursor_unseeded(self) -> None:
        store = await self._seed(
            "all", {"read_inbox_max_id": 100, "top_message_id": 120, "unread_count": 2}
        )
        self.assertFalse(store.has_cursor("dm_inbox", "123"))

    async def test_seeding_runs_once_per_sender(self) -> None:
        store = DmCursorStore(None)
        bridge = AsyncMock()
        bridge.get_dm_read_state = AsyncMock(
            return_value={"read_inbox_max_id": 5, "top_message_id": 9, "unread_count": 1}
        )
        with patch("hermes_tg_bridge.server.get_config", return_value={"dm_inbox_backfill": "unread"}):
            for _ in range(3):
                await _seed_dm_cursors(
                    bridge=bridge,
                    policy={},
                    allowed_senders=[_sender("123")],
                    consumer_id="dm_inbox",
                    cursor_store=store,
                )
        self.assertEqual(bridge.get_dm_read_state.await_count, 1)


@unittest.skipIf(DmInboxBroker is None, "fastapi is not installed in this test environment")
class TestBrokerOverflow(unittest.IsolatedAsyncioTestCase):
    async def test_overflow_is_reported_once_then_cleared(self) -> None:
        broker = DmInboxBroker(max_events=2)
        for message_id in (1, 2):
            await broker.push({"sender_id": "123", "id": message_id})
        self.assertFalse(broker.take_dropped_flag())

        await broker.push({"sender_id": "123", "id": 3})
        self.assertTrue(broker.take_dropped_flag())
        self.assertFalse(broker.take_dropped_flag())


@unittest.skipIf(_merge_dm_events is None, "fastapi is not installed in this test environment")
class TestMergeDmEvents(unittest.TestCase):
    def test_duplicates_are_dropped_and_order_is_oldest_first(self) -> None:
        buffered = [{"sender_id": "123", "id": 2, "date_unix": 20}]
        recovered = [
            {"sender_id": "123", "id": 2, "date_unix": 20},
            {"sender_id": "123", "id": 1, "date_unix": 10},
        ]

        merged = _merge_dm_events(buffered, recovered, limit=10)

        self.assertEqual([event["id"] for event in merged], [1, 2])

    def test_limit_is_respected(self) -> None:
        events = [{"sender_id": "123", "id": i, "date_unix": i} for i in range(1, 6)]
        self.assertEqual(len(_merge_dm_events(events, [], limit=2)), 2)


@unittest.skipIf(_prune_dm_media is None, "fastapi is not installed in this test environment")
class TestMediaRetention(unittest.TestCase):
    def test_expired_files_are_removed_and_fresh_ones_kept(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_file = root / "123" / "1_old.jpg"
            new_file = root / "123" / "2_new.jpg"
            old_file.parent.mkdir(parents=True)
            old_file.write_bytes(b"old")
            new_file.write_bytes(b"new")
            stale = time.time() - 30 * 86400
            import os

            os.utime(old_file, (stale, stale))

            _prune_dm_media(root, retention_days=7.0)

            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())

    def test_zero_retention_disables_pruning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ancient = root / "old.bin"
            ancient.write_bytes(b"x")
            stale = time.time() - 400 * 86400
            import os

            os.utime(ancient, (stale, stale))

            _prune_dm_media(root, retention_days=0.0)

            self.assertTrue(ancient.exists())


@unittest.skipIf(_text_send_cost is None, "fastapi is not installed in this test environment")
class TestSendCost(unittest.TestCase):
    def test_cost_tracks_the_number_of_telegram_messages(self) -> None:
        self.assertEqual(_text_send_cost("hi"), 1)
        self.assertEqual(_text_send_cost("x" * 4096), 1)
        self.assertEqual(_text_send_cost("x" * 4097), 2)
        self.assertEqual(_text_send_cost("x" * 12289), 4)

    def test_limiter_charges_the_full_cost_of_a_split_message(self) -> None:
        limiter = SendRateLimiter(max_per_min=5)

        self.assertIsNone(limiter.check(cost=4))
        # Only one slot left, so a 2-message send must be refused.
        self.assertIsNotNone(limiter.check(cost=2))
        self.assertIsNone(limiter.check(cost=1))


@unittest.skipIf(CORE_API_PATHS is None, "fastapi is not installed in this test environment")
class TestExtendedApiGate(unittest.TestCase):
    def test_gateway_endpoints_stay_available_when_extended_api_is_off(self) -> None:
        for path in ("/health", "/send_message", "/dm/inbox/poll", "/dm/inbox/ack", "/dm/read", "/dm/typing"):
            self.assertIn(path, CORE_API_PATHS)

    def test_privileged_endpoints_are_not_core(self) -> None:
        for path in (
            "/create_group",
            "/create_channel",
            "/promote_admin",
            "/ban_user",
            "/join_chat_by_link",
            "/invite_to_group",
            "/download_media",
            "/messages",
        ):
            self.assertNotIn(path, CORE_API_PATHS)


if __name__ == "__main__":
    unittest.main()
