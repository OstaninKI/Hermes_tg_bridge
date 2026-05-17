import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from adapter import AdapterConfig, BridgeClient, TelegramUserAdapter, build_policy_headers


def _make_bridge_client(handler, config: AdapterConfig | None = None):
    config = config or AdapterConfig(
        bridge_url="http://bridge.test",
        api_token="token",
    )
    client = BridgeClient(config)
    client.client = httpx.AsyncClient(
        base_url=config.bridge_url,
        headers=build_policy_headers(config),
        transport=httpx.MockTransport(handler),
    )
    return client


def test_env_config_loads_defaults_and_env(monkeypatch):
    monkeypatch.setenv("HERMES_TG_BRIDGE_URL", "http://localhost:9000")
    monkeypatch.setenv("HERMES_TG_BRIDGE_API_TOKEN", "secret")
    monkeypatch.setenv("HERMES_TG_ALLOWED_USERS", "alice,bob")
    monkeypatch.setenv("HERMES_TG_POLL_TIMEOUT_MS", "12345")

    config = AdapterConfig.from_env_and_extra({})

    assert config.bridge_url == "http://localhost:9000"
    assert config.api_token == "secret"
    assert config.allow_from == ["alice", "bob"]
    assert config.write_to == ["alice", "bob"]
    assert config.poll_timeout_ms == 12345
    assert config.policy_profile == "dm_inbox"
    assert config.mark_read_on_inbound is True
    assert config.typing_while_replying is True
    assert config.poll_interval_ms == 1500


def test_extra_config_used_when_env_missing(monkeypatch):
    monkeypatch.delenv("HERMES_TG_BRIDGE_URL", raising=False)
    monkeypatch.delenv("HERMES_TG_BRIDGE_API_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_TG_ALLOWED_USERS", raising=False)

    config = AdapterConfig.from_env_and_extra(
        {
            "bridge_url": "http://extra:8765",
            "api_token": "extra-token",
            "allowed_users": ["carol"],
            "write_to": ["dave"],
            "poll_interval_ms": 2000,
        }
    )

    assert config.bridge_url == "http://extra:8765"
    assert config.api_token == "extra-token"
    assert config.allow_from == ["carol"]
    assert config.write_to == ["dave"]
    assert config.poll_interval_ms == 2000


def test_build_policy_headers():
    config = AdapterConfig(
        api_token="token",
        allow_from=["alice", "bob"],
        deny_from=["mallory"],
        write_to=["alice"],
        deny_write_to=["eve"],
    )

    headers = build_policy_headers(config)

    assert headers["Authorization"] == "Bearer token"
    assert headers["X-Hermes-Policy-Profile"] == "dm_inbox"
    assert headers["X-Hermes-Allow-From"] == "alice,bob"
    assert headers["X-Hermes-Deny-From"] == "mallory"
    assert headers["X-Hermes-Write-To"] == "alice"
    assert headers["X-Hermes-Deny-Write-To"] == "eve"


def test_poll_inbox_returns_events():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"events": [{"id": 1, "sender_id": 42}]})

    client = _make_bridge_client(handler)

    events = asyncio.run(client.poll_inbox(limit=3))

    assert events == [{"id": 1, "sender_id": 42}]
    assert seen["method"] == "GET"
    assert seen["path"] == "/dm/inbox/poll"
    assert seen["query"]["limit"] == "3"
    assert seen["query"]["timeout_ms"] == str(client.config.poll_timeout_ms)
    assert seen["headers"]["authorization"] == "Bearer token"


def test_send_message_posts_to_backend():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True, "id": 7})

    client = _make_bridge_client(handler)

    payload = asyncio.run(client.send_message("peer-1", "hello", reply_to=11))

    assert payload == {"ok": True, "id": 7}
    assert seen["method"] == "POST"
    assert seen["path"] == "/send_message"
    assert seen["body"] == {"peer": "peer-1", "text": "hello", "reply_to": 11}
    assert seen["headers"]["authorization"] == "Bearer token"


def test_ack_and_read_use_sender_payload():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "path": request.url.path,
                "body": json.loads(request.content.decode()),
                "headers": dict(request.headers),
            }
        )
        return httpx.Response(200, json={"ok": True})

    client = _make_bridge_client(handler)
    event = {"id": "9", "sender_id": 123, "sender_username": "alice"}

    asyncio.run(client.ack(event))
    asyncio.run(client.mark_read(event))

    first_headers = seen[0]["headers"]
    second_headers = seen[1]["headers"]
    assert seen == [
        {
            "path": "/dm/inbox/ack",
            "body": {"sender_id": "123", "sender_username": "alice", "message_id": 9},
            "headers": first_headers,
        },
        {
            "path": "/dm/read",
            "body": {"sender_id": "123", "sender_username": "alice", "message_id": 9},
            "headers": second_headers,
        },
    ]


def test_send_typing_posts_sender_payload():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = _make_bridge_client(handler)

    asyncio.run(client.send_typing(77, sender_username="bot"))

    assert seen["path"] == "/dm/typing"
    assert seen["body"] == {"sender_id": 77, "sender_username": "bot"}
    assert seen["headers"]["authorization"] == "Bearer token"


def test_event_to_message_event_shape():
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=None,
    )

    message = adapter.event_to_message(
        {
            "id": 11,
            "sender_id": "123",
            "sender_name": "Alice",
            "sender_username": "alice",
            "text": "hello",
            "date": "2026-05-17T10:00:00Z",
        }
    )

    assert message == {
        "platform": "telegram_user",
        "chat_id": "123",
        "chat_type": "dm",
        "user_id": "123",
        "user_name": "Alice",
        "content": "hello",
        "metadata": {
            "telegram_message_id": 11,
            "sender_username": "alice",
        },
    }


def test_event_to_message_uses_media_marker_when_text_empty():
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=None,
    )

    message = adapter.event_to_message(
        {
            "id": 12,
            "sender_id": "123",
            "sender_name": "Alice",
            "sender_username": "alice",
            "text": "",
            "has_media": True,
            "media_type": "MessageMediaPhoto",
            "date": "2026-05-17T10:00:00Z",
        }
    )

    assert message["content"] == "[Telegram media: MessageMediaPhoto]"


def test_handle_inbound_event_dispatches_then_acks_and_reads():
    class RecordingBridge:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def ack(self, event):
            self.calls.append(("ack", event["id"]))

        async def mark_read(self, event):
            self.calls.append(("read", event["id"]))

    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=RecordingBridge(),
    )
    seen_messages: list[dict[str, object]] = []

    async def handle_message(message):
        seen_messages.append(message)

    adapter.handle_message = handle_message

    asyncio.run(
        adapter._handle_inbound_event(
            {
                "id": 13,
                "sender_id": "123",
                "sender_name": "Alice",
                "sender_username": "alice",
                "text": "hello",
                "date": "2026-05-17T10:00:00Z",
            }
        )
    )

    assert seen_messages[0]["content"] == "hello"
    assert adapter.bridge.calls == [("ack", 13), ("read", 13)]


def test_send_returns_success_result():
    bridge = SimpleNamespace(send_message=AsyncMock(return_value={"id": 77}))
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=bridge,
    )

    result = asyncio.run(adapter.send("123", "hello", reply_to="11", metadata={"sender_username": "alice"}))

    assert result == {"success": True, "message_id": 77, "error": None}
    bridge.send_message.assert_awaited_once_with("123", "hello", 11)


def test_send_returns_failure_result():
    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    bridge = SimpleNamespace(send_message=boom)
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=bridge,
    )

    result = asyncio.run(adapter.send("123", "hello"))

    assert result == {"success": False, "message_id": None, "error": "boom"}
