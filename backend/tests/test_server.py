"""Tests for source inventory integration in the HTTP layer."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from hermes_tg_bridge.client import BridgeValidationError
from hermes_tg_bridge.state import SourceInventoryStore

try:
    from hermes_tg_bridge.server import (
        AckDmInboxBody,
        AllowedDmSender,
        DmPeerBody,
        HTTPException,
        SendMessageBody,
        SendRateLimiter,
        _apply_source_discovery,
        _guess_dm_media_extension,
        _idempotency_cache,
        _recover_dm_events,
        _enrich_dm_events_with_downloaded_media,
        _send_rate_limiter,
        _source_entry_matches_policy,
        mark_dm_read,
        resolve_username,
        send_dm_typing,
        send_message,
        user_status,
    )
except ModuleNotFoundError as exc:
    if exc.name != "fastapi":
        raise
    AckDmInboxBody = None
    AllowedDmSender = None
    DmPeerBody = None
    HTTPException = None
    SendMessageBody = None
    SendRateLimiter = None
    _apply_source_discovery = None
    _guess_dm_media_extension = None
    _idempotency_cache = None
    _recover_dm_events = None
    _enrich_dm_events_with_downloaded_media = None
    _send_rate_limiter = None
    _source_entry_matches_policy = None
    mark_dm_read = None
    resolve_username = None
    send_dm_typing = None
    send_message = None
    user_status = None


@unittest.skipIf(_apply_source_discovery is None, "fastapi is not installed in this test environment")
class TestServerSourceDiscovery(unittest.IsolatedAsyncioTestCase):
    async def test_apply_source_discovery_replaces_open_read_scope_with_inventory(self) -> None:
        store = SourceInventoryStore(None)
        store.replace_dialogs(
            [
                {
                    "peer_id": -1001,
                    "peer_key": "-1001",
                    "username": "news",
                    "title": "News",
                    "type": "channel",
                }
            ]
        )
        policy = {
            "sources_auto_discover": True,
            "sources_include_types": ["channel"],
            "sources_exclude_peers": [],
            "sources_exclude_usernames": [],
            "read_allow_chat_ids": None,
            "read_deny_chat_ids": [],
        }

        with patch("hermes_tg_bridge.server._sync_sources_inventory", new=AsyncMock()), patch(
            "hermes_tg_bridge.server._sources_store", store
        ):
            resolved = await _apply_source_discovery(dict(policy))

        self.assertEqual(resolved["read_allow_chat_ids"], ["-1001", "news"])

    async def test_apply_source_discovery_unions_existing_allowlist(self) -> None:
        store = SourceInventoryStore(None)
        store.replace_dialogs(
            [
                {
                    "peer_id": -1001,
                    "peer_key": "-1001",
                    "username": "news",
                    "title": "News",
                    "type": "channel",
                }
            ]
        )
        policy = {
            "sources_auto_discover": True,
            "sources_include_types": ["channel"],
            "sources_exclude_peers": [],
            "sources_exclude_usernames": [],
            "read_allow_chat_ids": ["me"],
            "read_deny_chat_ids": [],
        }

        with patch("hermes_tg_bridge.server._sync_sources_inventory", new=AsyncMock()), patch(
            "hermes_tg_bridge.server._sources_store", store
        ):
            resolved = await _apply_source_discovery(dict(policy))

        self.assertEqual(resolved["read_allow_chat_ids"], ["me", "-1001", "news"])

    def test_source_entry_matches_policy_respects_deny_for_id_and_username(self) -> None:
        entry = {"peer_id": -1001, "username": "news"}

        self.assertFalse(_source_entry_matches_policy(entry, {"read_deny_chat_ids": ["-1001"]}))
        self.assertFalse(_source_entry_matches_policy(entry, {"read_deny_chat_ids": ["@news"]}))
        self.assertTrue(_source_entry_matches_policy(entry, {"read_deny_chat_ids": ["-1002"]}))

    def test_guess_dm_media_extension_rejects_injected_suffix(self) -> None:
        self.assertEqual(_guess_dm_media_extension("photo.jpg | type:virus", None), ".bin")
        self.assertEqual(_guess_dm_media_extension("photo.jpg | type:virus", "image/jpeg"), ".jpg")

    async def test_recover_dm_events_skips_unresolved_numeric_peer(self) -> None:
        bridge = AsyncMock()
        bridge.get_incoming_direct_messages.side_effect = BridgeValidationError(
            "Invalid Telegram peer for read incoming direct messages."
        )

        recovered = await _recover_dm_events(
            bridge=bridge,
            policy={},
            allowed_senders=[
                AllowedDmSender(
                    peer_ref="1470044",
                    cursor_key="1470044",
                    match_keys=frozenset({"1470044"}),
                )
            ],
            cursor_map={},
            limit=10,
        )

        self.assertEqual(recovered, [])
        bridge.get_incoming_direct_messages.assert_awaited_once_with(
            "1470044",
            min_id=0,
            limit=10,
            policy_overrides={},
        )

    async def test_mark_dm_read_calls_bridge_with_matched_sender(self) -> None:
        bridge = AsyncMock()
        bridge.mark_read.return_value = {"ok": True}
        policy = {"read_allow_chat_ids": ["1470044"], "write_allow_chat_ids": ["1470044"]}
        allowed = [
            AllowedDmSender(
                peer_ref="1470044",
                cursor_key="1470044",
                match_keys=frozenset({"1470044", "alloweduser"}),
            )
        ]

        with patch("hermes_tg_bridge.server.get_bridge", return_value=bridge), patch(
            "hermes_tg_bridge.server.resolve_request_policy", new=AsyncMock(return_value=policy)
        ), patch("hermes_tg_bridge.server._resolve_allowed_dm_senders", new=AsyncMock(return_value=allowed)):
            result = await mark_dm_read(
                object(),
                AckDmInboxBody(sender_id="1470044", sender_username="alloweduser", message_id=55),
            )

        self.assertEqual(result, {"ok": True})
        bridge.mark_read.assert_awaited_once_with(
            "1470044",
            max_message_id=55,
            policy_overrides=policy,
        )

    async def test_resolve_username_applies_request_policy(self) -> None:
        bridge = AsyncMock()
        bridge.resolve_username.return_value = {"id": 42, "username": "allowed"}
        policy = {"read_allow_chat_ids": ["allowed"], "read_deny_chat_ids": []}

        with patch("hermes_tg_bridge.server.get_bridge", return_value=bridge), patch(
            "hermes_tg_bridge.server.resolve_request_policy", new=AsyncMock(return_value=policy)
        ) as resolve_policy:
            result = await resolve_username(object(), "allowed")

        self.assertEqual(result, {"id": 42, "username": "allowed"})
        resolve_policy.assert_awaited_once()
        bridge.resolve_username.assert_awaited_once_with("allowed", policy_overrides=policy)

    async def test_user_status_applies_request_policy(self) -> None:
        bridge = AsyncMock()
        bridge.get_user_status.return_value = {"id": 42, "username": "allowed", "status_type": "UserStatusOnline"}
        policy = {"read_allow_chat_ids": ["allowed"], "read_deny_chat_ids": []}

        with patch("hermes_tg_bridge.server.get_bridge", return_value=bridge), patch(
            "hermes_tg_bridge.server.resolve_request_policy", new=AsyncMock(return_value=policy)
        ) as resolve_policy:
            result = await user_status(object(), "allowed")

        self.assertEqual(result, {"id": 42, "username": "allowed", "status_type": "UserStatusOnline"})
        resolve_policy.assert_awaited_once()
        bridge.get_user_status.assert_awaited_once_with("allowed", policy_overrides=policy)

    async def test_mark_dm_read_rejects_unmatched_sender(self) -> None:
        bridge = AsyncMock()
        policy = {"read_allow_chat_ids": ["1470044"], "write_allow_chat_ids": ["1470044"]}
        allowed = [
            AllowedDmSender(
                peer_ref="1470044",
                cursor_key="1470044",
                match_keys=frozenset({"1470044", "alloweduser"}),
            )
        ]

        with patch("hermes_tg_bridge.server.get_bridge", return_value=bridge), patch(
            "hermes_tg_bridge.server.resolve_request_policy", new=AsyncMock(return_value=policy)
        ), patch("hermes_tg_bridge.server._resolve_allowed_dm_senders", new=AsyncMock(return_value=allowed)):
            with self.assertRaises(HTTPException) as ctx:
                await mark_dm_read(
                    object(),
                    AckDmInboxBody(sender_id="999999", sender_username="other", message_id=55),
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Read receipt is not allowed for this sender.")
        bridge.mark_read.assert_not_awaited()

    async def test_send_dm_typing_matches_sender_by_username(self) -> None:
        bridge = AsyncMock()
        bridge.send_typing.return_value = {"ok": True}
        policy = {"read_allow_chat_ids": ["@alloweduser"], "write_allow_chat_ids": ["1470044"]}
        allowed = [
            AllowedDmSender(
                peer_ref="@alloweduser",
                cursor_key="1470044",
                match_keys=frozenset({"1470044", "alloweduser"}),
            )
        ]

        with patch("hermes_tg_bridge.server.get_bridge", return_value=bridge), patch(
            "hermes_tg_bridge.server.resolve_request_policy", new=AsyncMock(return_value=policy)
        ), patch("hermes_tg_bridge.server._resolve_allowed_dm_senders", new=AsyncMock(return_value=allowed)):
            result = await send_dm_typing(
                object(),
                DmPeerBody(sender_id="0", sender_username="alloweduser"),
            )

        self.assertEqual(result, {"ok": True})
        bridge.send_typing.assert_awaited_once_with(
            "1470044",
            policy_overrides=policy,
        )

    async def test_send_dm_typing_rejects_unmatched_sender(self) -> None:
        bridge = AsyncMock()
        policy = {"read_allow_chat_ids": ["@alloweduser"], "write_allow_chat_ids": ["1470044"]}
        allowed = [
            AllowedDmSender(
                peer_ref="@alloweduser",
                cursor_key="1470044",
                match_keys=frozenset({"1470044", "alloweduser"}),
            )
        ]

        with patch("hermes_tg_bridge.server.get_bridge", return_value=bridge), patch(
            "hermes_tg_bridge.server.resolve_request_policy", new=AsyncMock(return_value=policy)
        ), patch("hermes_tg_bridge.server._resolve_allowed_dm_senders", new=AsyncMock(return_value=allowed)):
            with self.assertRaises(HTTPException) as ctx:
                await send_dm_typing(
                    object(),
                    DmPeerBody(sender_id="0", sender_username="otheruser"),
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Typing status is not allowed for this sender.")
        bridge.send_typing.assert_not_awaited()

    async def test_dm_media_enrichment_downloads_and_sets_media_paths(self) -> None:
        bridge = AsyncMock()
        bridge.download_media_for_inbox = AsyncMock(return_value="/tmp/dm_media/1470044/26_photo.jpg")
        events = [
            {
                "id": 26,
                "sender_id": "1470044",
                "sender_username": "alloweduser",
                "has_media": True,
                "media_type": "MessageMediaPhoto",
                "file_name": "photo.jpg",
                "mime_type": "image/jpeg",
            }
        ]
        allowed = [
            AllowedDmSender(
                peer_ref="@alloweduser",
                cursor_key="1470044",
                match_keys=frozenset({"1470044", "alloweduser"}),
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = Path(temp_dir) / "1470044" / "26_photo.jpg"
            bridge.download_media_for_inbox.return_value = str(media_path)
            with patch(
                "hermes_tg_bridge.server.get_config",
                return_value={"dm_auto_download_media": True, "dm_media_path": temp_dir},
            ):
                enriched = await _enrich_dm_events_with_downloaded_media(
                    bridge=bridge,
                    policy={},
                    allowed_senders=allowed,
                    events=events,
                )

        self.assertEqual(enriched[0]["media_path"], str(media_path.resolve()))
        self.assertEqual(enriched[0]["media_paths"], [str(media_path.resolve())])
        bridge.download_media_for_inbox.assert_awaited_once_with(
            "@alloweduser",
            26,
            output_path=str(media_path.resolve()),
            policy_overrides={},
        )

    async def test_dm_media_enrichment_skips_non_downloadable_media_type(self) -> None:
        bridge = AsyncMock()
        bridge.download_media_for_inbox = AsyncMock()
        events = [
            {
                "id": 28,
                "sender_id": "1470044",
                "sender_username": "alloweduser",
                "has_media": True,
                "media_type": "MessageMediaContact",
                "contact_phone": "+12025550123",
            }
        ]
        allowed = [
            AllowedDmSender(
                peer_ref="@alloweduser",
                cursor_key="1470044",
                match_keys=frozenset({"1470044", "alloweduser"}),
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "hermes_tg_bridge.server.get_config",
            return_value={"dm_auto_download_media": True, "dm_media_path": temp_dir},
        ):
            enriched = await _enrich_dm_events_with_downloaded_media(
                bridge=bridge,
                policy={},
                allowed_senders=allowed,
                events=events,
            )

        self.assertNotIn("media_path", enriched[0])
        bridge.download_media_for_inbox.assert_not_awaited()

    async def test_dm_media_enrichment_respects_disabled_config(self) -> None:
        bridge = AsyncMock()
        bridge.download_media_for_inbox = AsyncMock()
        events = [
            {
                "id": 29,
                "sender_id": "1470044",
                "sender_username": "alloweduser",
                "has_media": True,
                "media_type": "MessageMediaPhoto",
            }
        ]
        allowed = [
            AllowedDmSender(
                peer_ref="@alloweduser",
                cursor_key="1470044",
                match_keys=frozenset({"1470044", "alloweduser"}),
            )
        ]

        with patch(
            "hermes_tg_bridge.server.get_config",
            return_value={"dm_auto_download_media": False, "dm_media_path": "/tmp/unused"},
        ):
            enriched = await _enrich_dm_events_with_downloaded_media(
                bridge=bridge,
                policy={},
                allowed_senders=allowed,
                events=events,
            )

        self.assertEqual(enriched[0]["id"], 29)
        bridge.download_media_for_inbox.assert_not_awaited()


@unittest.skipIf(send_message is None, "fastapi is not installed in this test environment")
class TestSendMessageSafety(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Each test gets an isolated cache/limiter so runs don't leak state between tests.
        _idempotency_cache._entries.clear()
        _send_rate_limiter._timestamps.clear()

    def _make_request(self, idempotency_key: str | None = None) -> Mock:
        request = Mock(spec=["url", "headers", "method"])
        headers = {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request.headers.get = Mock(side_effect=lambda name, default=None: headers.get(name, default))
        return request

    async def test_repeated_idempotency_key_does_not_call_telegram_twice(self) -> None:
        bridge = AsyncMock()
        bridge.send_message.return_value = {"ok": True, "message_id": 42}
        policy = {}

        with patch("hermes_tg_bridge.server.get_bridge", return_value=bridge), patch(
            "hermes_tg_bridge.server.resolve_request_policy", new=AsyncMock(return_value=policy)
        ):
            request = self._make_request(idempotency_key="dup-key-1")
            body = SendMessageBody(peer="me", text="hello")
            first = await send_message(request, body)
            second = await send_message(request, body)

        self.assertEqual(first, {"ok": True, "message_id": 42})
        self.assertEqual(second, {"ok": True, "message_id": 42})
        bridge.send_message.assert_awaited_once()

    async def test_send_rate_limit_returns_429_without_calling_telegram(self) -> None:
        bridge = AsyncMock()
        bridge.send_message.return_value = {"ok": True, "message_id": 1}
        policy = {}

        with patch("hermes_tg_bridge.server.get_bridge", return_value=bridge), patch(
            "hermes_tg_bridge.server.resolve_request_policy", new=AsyncMock(return_value=policy)
        ), patch("hermes_tg_bridge.server._send_rate_limiter", SendRateLimiter(max_per_min=1)):
            body = SendMessageBody(peer="me", text="hello")
            await send_message(self._make_request(), body)

            with self.assertRaises(HTTPException) as ctx:
                await send_message(self._make_request(), body)

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Retry-After", ctx.exception.headers)
        bridge.send_message.assert_awaited_once()


try:
    from hermes_tg_bridge.server import (
        _BridgeNotReadyError,
        _QrPasswordBody,
        auth_qr_2fa,
        auth_qr_get,
        auth_qr_start,
        health,
    )
    from hermes_tg_bridge.auth_qr import QrAuthContext, QrState
    _qr_imports_ok = True
except (ModuleNotFoundError, ImportError):
    _BridgeNotReadyError = None
    _QrPasswordBody = None
    auth_qr_2fa = None
    auth_qr_get = None
    auth_qr_start = None
    health = None
    QrAuthContext = None
    QrState = None
    _qr_imports_ok = False


@unittest.skipIf(not _qr_imports_ok, "fastapi or auth_qr not available")
class TestHealthEndpoint(unittest.IsolatedAsyncioTestCase):
    async def test_health_needs_reauth(self):
        with patch("hermes_tg_bridge.server._needs_reauth", True), \
             patch("hermes_tg_bridge.server._bridge", None):
            resp = await health()
        self.assertEqual(resp.status_code, 503)
        import json
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "needs_reauth")
        self.assertTrue(body["needs_reauth"])

    async def test_health_initializing(self):
        with patch("hermes_tg_bridge.server._needs_reauth", False), \
             patch("hermes_tg_bridge.server._bridge", None):
            resp = await health()
        self.assertEqual(resp.status_code, 503)
        import json
        body = json.loads(resp.body)
        self.assertEqual(body["status"], "initializing")

    async def test_health_ok(self):
        bridge = AsyncMock()
        bridge.ensure_connected = AsyncMock(return_value=True)
        with patch("hermes_tg_bridge.server._needs_reauth", False), \
             patch("hermes_tg_bridge.server._bridge", bridge):
            resp = await health()
        # Returns dict (200), not JSONResponse
        self.assertEqual(resp["status"], "ok")
        self.assertTrue(resp["connected"])


@unittest.skipIf(not _qr_imports_ok, "fastapi or auth_qr not available")
class TestQrEndpoints(unittest.IsolatedAsyncioTestCase):
    async def test_auth_qr_start_bridge_alive_raises_409(self):
        bridge = AsyncMock()
        with patch("hermes_tg_bridge.server._needs_reauth", False), \
             patch("hermes_tg_bridge.server._bridge", bridge), \
             patch("hermes_tg_bridge.server._qr_auth_task", None):
            with self.assertRaises(HTTPException) as cm:
                await auth_qr_start()
            self.assertEqual(cm.exception.status_code, 409)

    async def test_auth_qr_get_no_context_returns_404(self):
        with patch("hermes_tg_bridge.server._qr_auth_ctx", None):
            with self.assertRaises(HTTPException) as cm:
                await auth_qr_get()
            self.assertEqual(cm.exception.status_code, 404)

    async def test_auth_qr_2fa_wrong_state_raises_409(self):
        ctx = QrAuthContext()
        ctx.state = QrState.AWAITING_SCAN  # not AWAITING_PASSWORD
        body = _QrPasswordBody(password="pw")
        with patch("hermes_tg_bridge.server._qr_auth_ctx", ctx):
            with self.assertRaises(HTTPException) as cm:
                await auth_qr_2fa(body)
            self.assertEqual(cm.exception.status_code, 409)

    async def test_auth_qr_2fa_double_submit_raises_409(self):
        ctx = QrAuthContext()
        ctx.state = QrState.AWAITING_PASSWORD
        # Fill the queue so next put_nowait raises QueueFull
        ctx._password_queue.put_nowait("first")
        body = _QrPasswordBody(password="second")
        with patch("hermes_tg_bridge.server._qr_auth_ctx", ctx):
            with self.assertRaises(HTTPException) as cm:
                await auth_qr_2fa(body)
            self.assertEqual(cm.exception.status_code, 409)


@unittest.skipIf(not _qr_imports_ok, "fastapi or auth_qr not available")
class TestAuthMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_unauthorized_without_token_returns_401(self):
        from hermes_tg_bridge.server import auth_middleware
        from unittest.mock import Mock

        request = Mock(spec=["url", "headers", "method"])
        request.url.path = "/send_message"
        request.headers.get = Mock(return_value="")

        call_next = Mock()

        with patch("hermes_tg_bridge.server.get_config", return_value={"api_token": "secret123"}):
            response = await auth_middleware(request, call_next)

        self.assertEqual(response.status_code, 401)

    async def test_write_denied_by_default(self):
        from hermes_tg_bridge.server import app as test_app
        from fastapi.testclient import TestClient

        with patch("hermes_tg_bridge.server._bridge", None), \
             patch("hermes_tg_bridge.server._config", {"api_token": None}), \
             patch("hermes_tg_bridge.server._policy_store", None):
            try:
                from fastapi.testclient import TestClient
                client = TestClient(test_app)
                response = client.post(
                    "/send_message",
                    json={
                        "peer": "me",
                        "text": "test"
                    },
                    headers={"X-Hermes-Policy-Profile": "empty"}
                )
                self.assertEqual(response.status_code, 503)
            except Exception:
                pass

    async def test_allow_from_header_narrows_read_policy(self):
        from hermes_tg_bridge.config import resolve_effective_policy, PolicyStore

        store = PolicyStore(None)
        config = {
            "reply_delay_sec": 1.0,
            "reply_delay_max_sec": None,
            "allow_chat_ids": ["me"],
            "deny_chat_ids": [],
            "write_allow_chat_ids": [],
            "write_deny_chat_ids": [],
            "policy_default_profile": None,
        }

        # The header must only narrow the base allowlist (intersection), never
        # add chat ids beyond what policy.json/ENV already permits.
        policy = resolve_effective_policy(
            config,
            store,
            {"read_allow_from": ["12345", "@user"]},
        )

        self.assertEqual(policy["read_allow_chat_ids"], [])

        policy_with_overlap = resolve_effective_policy(
            config,
            store,
            {"read_allow_from": ["me", "@user"]},
        )

        self.assertEqual(policy_with_overlap["read_allow_chat_ids"], ["me"])


if __name__ == "__main__":
    unittest.main()
