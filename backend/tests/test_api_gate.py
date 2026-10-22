"""End-to-end checks of the auth + extended-API middleware, driven over real HTTP."""

import importlib
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:  # pragma: no cover
    TestClient = None


CONFIG = {
    "api_token": "secret-token",
    "reply_delay_sec": 0.0,
    "reply_delay_max_sec": None,
    "allow_chat_ids": [],
    "deny_chat_ids": [],
    "write_allow_chat_ids": [],
    "write_deny_chat_ids": [],
    "policy_default_profile": None,
    "dm_inbox_backfill": "unread",
}


def _client(extended_disabled: bool) -> "TestClient":
    """Build an app whose module-level extended-API flag reflects the env var."""
    import hermes_tg_bridge.server as server

    with patch.dict(
        "os.environ",
        {"TELEGRAM_DISABLE_EXTENDED_API": "1" if extended_disabled else "0"},
        clear=False,
    ):
        server = importlib.reload(server)
    # Bypass lifespan: these tests exercise middleware, not the Telegram client.
    server._config = dict(CONFIG)
    return TestClient(server.app), server


@unittest.skipIf(TestClient is None, "fastapi is not installed in this test environment")
class TestAuthMiddleware(unittest.TestCase):
    def tearDown(self) -> None:
        import hermes_tg_bridge.server as server

        with patch.dict("os.environ", {"TELEGRAM_DISABLE_EXTENDED_API": "0"}, clear=False):
            importlib.reload(server)

    def test_missing_token_is_rejected(self) -> None:
        client, _ = _client(extended_disabled=False)
        response = client.post("/send_message", json={"peer": "1", "text": "hi"})
        self.assertEqual(response.status_code, 401)

    def test_wrong_token_is_rejected(self) -> None:
        client, _ = _client(extended_disabled=False)
        response = client.post(
            "/send_message",
            json={"peer": "1", "text": "hi"},
            headers={"Authorization": "Bearer nope"},
        )
        self.assertEqual(response.status_code, 401)

    def test_health_needs_no_token(self) -> None:
        client, _ = _client(extended_disabled=False)
        # No bridge is connected, so health reports 503 — but never 401.
        self.assertNotEqual(client.get("/health").status_code, 401)

    def test_unauthenticated_caller_cannot_probe_the_extended_api_flag(self) -> None:
        """Both disabled-extended and enabled-extended must answer 401 without a token."""
        for disabled in (True, False):
            client, _ = _client(extended_disabled=disabled)
            response = client.post("/create_group", json={"title": "x", "users": []})
            self.assertEqual(response.status_code, 401, f"extended_disabled={disabled}")


@unittest.skipIf(TestClient is None, "fastapi is not installed in this test environment")
class TestExtendedApiGate(unittest.TestCase):
    def tearDown(self) -> None:
        import hermes_tg_bridge.server as server

        with patch.dict("os.environ", {"TELEGRAM_DISABLE_EXTENDED_API": "0"}, clear=False):
            importlib.reload(server)

    def test_privileged_endpoints_are_gone_when_disabled(self) -> None:
        client, _ = _client(extended_disabled=True)
        auth = {"Authorization": "Bearer secret-token"}
        for path, payload in (
            ("/create_group", {"title": "x", "users": []}),
            ("/create_channel", {"title": "x"}),
            ("/promote_admin", {"peer": "1", "user_peer": "2"}),
            ("/ban_user", {"peer": "1", "user_peer": "2"}),
            ("/join_chat_by_link", {"link": "t.me/x"}),
        ):
            response = client.post(path, json=payload, headers=auth)
            self.assertEqual(response.status_code, 404, path)
            self.assertEqual(response.json()["detail"], "Extended API disabled")

    def test_gateway_endpoints_survive_when_disabled(self) -> None:
        client, _ = _client(extended_disabled=True)
        auth = {"Authorization": "Bearer secret-token"}
        # No Telegram client is wired up, so these reach the handler and fail with
        # 503 "bridge not ready" — the point is that they are not gated to 404.
        for path in ("/dm/inbox/poll", "/me"):
            self.assertNotEqual(client.get(path, headers=auth).status_code, 404, path)
        response = client.post("/send_message", json={"peer": "1", "text": "hi"}, headers=auth)
        self.assertNotEqual(response.status_code, 404)

    def test_transcribe_voice_works_when_disabled_and_keeps_auth_and_read_scope(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from hermes_tg_bridge.client import BridgeClient
        from hermes_tg_bridge.config import PolicyStore

        client, server = _client(extended_disabled=True)
        telegram = AsyncMock(return_value=SimpleNamespace(pending=False, text="hello"))
        telegram.is_connected = MagicMock(return_value=True)
        telegram.get_entity = AsyncMock(side_effect=lambda peer: SimpleNamespace(id=int(peer)))
        bridge = BridgeClient(telegram, reply_delay_sec=0, rpc_timeout_sec=5, flood_wait_max_sleep_sec=0)
        bridge._is_premium = True
        server._bridge = bridge
        server._policy_store = PolicyStore(None)
        headers = {"Authorization": "Bearer secret-token", "X-Hermes-Allow-From": "111"}

        allowed = client.post("/transcribe_voice", json={"peer": "111", "message_id": 5}, headers=headers)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json(), {"ok": True, "text": "hello"})

        no_auth = client.post("/transcribe_voice", json={"peer": "111", "message_id": 5})
        self.assertEqual(no_auth.status_code, 401)

        foreign = client.post("/transcribe_voice", json={"peer": "222", "message_id": 5}, headers=headers)
        self.assertEqual(foreign.status_code, 403, foreign.text)
        self.assertEqual(telegram.await_count, 1)  # only the allowed peer reached Telegram

    def test_privileged_endpoints_reachable_when_extended_api_is_on(self) -> None:
        client, _ = _client(extended_disabled=False)
        auth = {"Authorization": "Bearer secret-token"}
        response = client.post("/create_group", json={"title": "x", "users": []}, headers=auth)
        self.assertNotEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
