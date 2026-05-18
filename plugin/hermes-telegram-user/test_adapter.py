import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from adapter import (
    AdapterConfig,
    BasePlatformAdapter,
    BridgeClient,
    MessageEvent,
    MessageType,
    PlatformConfig,
    SendResult,
    TelegramUserAdapter,
    build_policy_headers,
    register,
)


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
        allow_from=[],
        deny_from=[],
        write_to=[],
        deny_write_to=[],
    )

    headers = build_policy_headers(config)

    assert headers["Authorization"] == "Bearer token"
    assert headers["X-Hermes-Policy-Profile"] == "dm_inbox"
    assert "X-Hermes-Allow-From" not in headers
    assert "X-Hermes-Deny-From" not in headers
    assert "X-Hermes-Write-To" not in headers
    assert "X-Hermes-Deny-Write-To" not in headers


def test_build_policy_headers_includes_lists():
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

    assert isinstance(message, MessageEvent)
    assert message.text == "hello"
    assert message.message_type == MessageType.TEXT
    assert message.message_id == "11"
    assert message.raw_message["sender_username"] == "alice"
    assert message.source.chat_id == "123"
    assert message.source.chat_name == "Alice"
    assert message.source.chat_type == "dm"
    assert message.source.user_id == "123"
    assert message.source.user_name == "Alice"


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

    assert message.text == "[Telegram media: MessageMediaPhoto]"


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
    seen_messages: list[MessageEvent] = []

    async def handle_message(message):
        seen_messages.append(message)

    adapter._message_handler = handle_message

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

    assert seen_messages[0].text == "hello"
    assert seen_messages[0].source.chat_id == "123"
    assert adapter.bridge.calls == [("ack", 13), ("read", 13)]


def test_send_returns_success_result():
    bridge = SimpleNamespace(send_message=AsyncMock(return_value={"id": 77}))
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=bridge,
    )

    result = asyncio.run(adapter.send("123", "hello", reply_to="11", metadata={"sender_username": "alice"}))

    assert isinstance(result, SendResult)
    assert result.success is True
    assert result.message_id == "77"
    assert result.error is None
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

    assert isinstance(result, SendResult)
    assert result.success is False
    assert result.message_id is None
    assert result.error == "boom"


def test_adapter_extends_hermes_base_adapter():
    adapter = TelegramUserAdapter(
        PlatformConfig(extra={"api_token": "token", "bridge_url": "http://bridge.test"}),
        bridge=SimpleNamespace(),
    )

    assert isinstance(adapter, BasePlatformAdapter)
    assert str(adapter.platform) == "telegram_user"
    assert adapter.adapter_config.api_token == "token"


def test_register_uses_hermes_plugin_registry_contract():
    seen = {}

    class Ctx:
        def register_platform(self, **kwargs):
            seen.update(kwargs)
            return "registered"

    result = register(Ctx())

    assert result == "registered"
    assert seen["name"] == "telegram_user"
    assert seen["label"] == "Telegram User"
    assert callable(seen["adapter_factory"])
    assert callable(seen["check_fn"])
    assert callable(seen["validate_config"])
    assert "HERMES_TG_BRIDGE_API_TOKEN" in seen["required_env"]

    adapter = seen["adapter_factory"](
        PlatformConfig(extra={"api_token": "token", "bridge_url": "http://bridge.test"})
    )
    assert isinstance(adapter, TelegramUserAdapter)
    assert adapter.adapter_config.bridge_url == "http://bridge.test"


def test_poll_loop_401_sets_fatal_and_exits():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.HTTPStatusError("Unauthorized", request=request, response=httpx.Response(401))

    config = AdapterConfig(
        bridge_url="http://bridge.test",
        api_token="token",
    )
    bridge = _make_bridge_client(handler, config)
    adapter = TelegramUserAdapter(config, bridge=bridge)
    adapter._mark_connected()

    asyncio.run(adapter._poll_loop())

    assert adapter._fatal_error_code == "bridge_unauthorized"
    assert adapter.is_connected is False


class _Status429Bridge:
    def __init__(self, retry_after: str | None) -> None:
        self.retry_after = retry_after
        self.calls = 0

    async def poll_inbox(self, limit: int = 10):
        self.calls += 1
        request = httpx.Request("GET", "http://bridge.test/dm/inbox/poll")
        response = httpx.Response(429, request=request)
        if self.retry_after is not None:
            response.headers["Retry-After"] = self.retry_after
        raise httpx.HTTPStatusError("Too Many Requests", request=request, response=response)


def test_poll_loop_429_respects_retry_after():
    sleep_calls: list[float] = []

    async def mock_sleep(duration):
        sleep_calls.append(duration)
        adapter._running = False

    config = AdapterConfig(bridge_url="http://bridge.test", api_token="token")
    adapter = TelegramUserAdapter(config, bridge=_Status429Bridge("3"))
    adapter._mark_connected()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        asyncio.run(adapter._poll_loop())

    assert any(call == 3 for call in sleep_calls), f"Expected 3s sleep, got {sleep_calls}"


def test_poll_loop_429_invalid_retry_after_falls_back():
    sleep_calls: list[float] = []

    async def mock_sleep(duration):
        sleep_calls.append(duration)
        adapter._running = False

    config = AdapterConfig(bridge_url="http://bridge.test", api_token="token", poll_interval_ms=1500)
    adapter = TelegramUserAdapter(config, bridge=_Status429Bridge("bogus"))
    adapter._mark_connected()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        asyncio.run(adapter._poll_loop())

    bounded_delay = min(max(1500 / 1000.0, 0.1), 5.0)
    assert any(call == bounded_delay for call in sleep_calls), f"Expected {bounded_delay}s sleep, got {sleep_calls}"


def test_allow_from_filters_unknown_sender():
    class RecordingBridge:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def ack(self, event):
            self.calls.append(("ack", event["id"]))

        async def mark_read(self, event):
            self.calls.append(("read", event["id"]))

    adapter = TelegramUserAdapter(
        AdapterConfig(
            allow_from=["999"],
            write_to=["999"],
            allow_all_users=False,
        ),
        bridge=RecordingBridge(),
    )
    seen_messages: list[MessageEvent] = []

    async def handle_message(message):
        seen_messages.append(message)

    adapter._message_handler = handle_message

    asyncio.run(
        adapter._handle_inbound_event(
            {
                "id": 1,
                "sender_id": "111",
                "sender_name": "Unknown",
                "sender_username": "unknown",
                "text": "hello",
            }
        )
    )

    assert len(seen_messages) == 0
    assert len(adapter.bridge.calls) == 0


def test_allow_all_users_bypasses_filter():
    class RecordingBridge:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def ack(self, event):
            self.calls.append(("ack", event["id"]))

        async def mark_read(self, event):
            self.calls.append(("read", event["id"]))

    adapter = TelegramUserAdapter(
        AdapterConfig(
            allow_from=["999"],
            write_to=["999"],
            allow_all_users=True,
        ),
        bridge=RecordingBridge(),
    )
    seen_messages: list[MessageEvent] = []

    async def handle_message(message):
        seen_messages.append(message)

    adapter._message_handler = handle_message

    asyncio.run(
        adapter._handle_inbound_event(
            {
                "id": 2,
                "sender_id": "111",
                "sender_name": "Unknown",
                "sender_username": "unknown",
                "text": "hello",
            }
        )
    )

    assert len(seen_messages) == 1
    assert len(adapter.bridge.calls) == 2
