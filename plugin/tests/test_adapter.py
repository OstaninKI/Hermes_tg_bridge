import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from adapter import (
    MAX_EVENT_ATTEMPTS,
    AdapterConfig,
    BasePlatformAdapter,
    BridgeClient,
    MessageEvent,
    MessageType,
    PlatformConfig,
    SendResult,
    TelegramUserAdapter,
    _env_enablement,
    _standalone_send,
    build_policy_headers,
    register,
)


class RecordingBridge:
    """Minimal bridge double that records the DM state calls it receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def ack(self, event):
        self.calls.append(("ack", event["id"]))

    async def mark_read(self, event):
        self.calls.append(("read", event["id"]))


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
    call = bridge.send_message.await_args
    assert call.args == ("123", "hello", 11)
    assert call.kwargs["idempotency_key"]


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
    assert result.retryable is False


def test_send_reuses_idempotency_key_across_retries_of_the_same_message():
    """A retry after a client-side timeout must not deliver the message twice."""
    keys: list[str] = []

    async def capture(peer, text, reply_to=None, idempotency_key=None):
        keys.append(idempotency_key)
        return {"message_id": 1}

    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=SimpleNamespace(send_message=capture),
    )

    async def scenario():
        await adapter.send("123", "same text")
        await adapter.send("123", "same text")
        await adapter.send("123", "different text")

    asyncio.run(scenario())

    assert keys[0] == keys[1]
    assert keys[2] != keys[0]


def test_send_marks_429_retryable_with_retry_after():
    request = httpx.Request("POST", "http://bridge.test/send_message")
    response = httpx.Response(429, request=request, headers={"Retry-After": "7"})

    async def rate_limited(*args, **kwargs):
        raise httpx.HTTPStatusError("Too Many Requests", request=request, response=response)

    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=SimpleNamespace(send_message=rate_limited),
    )

    result = asyncio.run(adapter.send("123", "hello"))

    assert result.success is False
    assert result.retryable is True
    assert result.retry_after == 7
    assert result.error_kind == "rate_limited"


def test_send_marks_timeout_retryable():
    async def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=SimpleNamespace(send_message=timeout),
    )

    result = asyncio.run(adapter.send("123", "hello"))

    assert result.success is False
    assert result.retryable is True


def test_adapter_extends_hermes_base_adapter():
    adapter = TelegramUserAdapter(
        PlatformConfig(extra={"api_token": "token", "bridge_url": "http://bridge.test"}),
        bridge=SimpleNamespace(),
    )

    assert isinstance(adapter, BasePlatformAdapter)
    assert getattr(adapter.platform, "value", adapter.platform) == "telegram_user"
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
    assert seen["allowlist_suppresses_pairing"] is True
    assert seen["cron_deliver_env_var"] == "HERMES_TG_HOME_CHANNEL"
    assert seen["standalone_sender_fn"] is _standalone_send


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


def test_allow_from_filters_unknown_sender_but_still_acks():
    """A filtered event must be acked, or the backend re-serves it on every poll."""
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
    assert adapter.bridge.calls == [("ack", 1)]


def test_allow_from_matches_username_regardless_of_at_and_case():
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["@Alice"], write_to=["@Alice"]),
        bridge=RecordingBridge(),
    )
    seen_messages: list[MessageEvent] = []

    async def handle_message(message):
        seen_messages.append(message)

    adapter._message_handler = handle_message

    asyncio.run(
        adapter._handle_inbound_event(
            {"id": 5, "sender_id": "111", "sender_username": "alice", "text": "hi"}
        )
    )

    assert len(seen_messages) == 1
    assert ("ack", 5) in adapter.bridge.calls


def test_allow_all_users_bypasses_filter():
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


def test_plugin_loads_the_way_hermes_loads_it():
    """__init__.py must reach adapter.py from the hermes_plugins.<slug> package."""
    from conftest import MODULE_NAME, load_plugin_package

    package = load_plugin_package()

    assert package.__name__ == MODULE_NAME
    assert callable(package.register)


def test_connect_accepts_is_reconnect_keyword():
    """The gateway's reconnect watcher calls connect(is_reconnect=True)."""
    bridge = SimpleNamespace(health=AsyncMock(return_value={"status": "ok"}))
    adapter = TelegramUserAdapter(AdapterConfig(api_token="token"), bridge=bridge)

    async def scenario():
        connected = await adapter.connect(is_reconnect=True)
        await adapter.disconnect()
        return connected

    assert asyncio.run(scenario()) is True
    bridge.health.assert_awaited_once()


def test_connect_reports_fatal_error_when_bridge_is_down():
    async def unreachable():
        raise httpx.ConnectError("connection refused")

    adapter = TelegramUserAdapter(
        AdapterConfig(api_token="token"),
        bridge=SimpleNamespace(health=unreachable),
    )

    assert asyncio.run(adapter.connect()) is False
    assert adapter._fatal_error_code == "bridge_unavailable"
    assert adapter.is_connected is False


def test_failing_event_is_retried_then_dropped_to_unblock_the_inbox():
    bridge = RecordingBridge()
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=bridge,
    )

    async def always_fails(_message):
        raise RuntimeError("agent exploded")

    adapter.handle_message = always_fails  # admission failure; agent errors run in a background task upstream
    event = {"id": 42, "sender_id": "123", "text": "poison"}

    async def scenario():
        for _ in range(MAX_EVENT_ATTEMPTS):
            await adapter._handle_inbound_event(event)

    asyncio.run(scenario())

    # Nine silent retries, then a single forced ack so the queue moves on.
    assert bridge.calls == [("ack", 42)]
    assert adapter._failed_events == {}


def test_successful_event_clears_previous_failures():
    bridge = RecordingBridge()
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=bridge,
    )
    fail = True

    async def flaky(_message):
        if fail:
            raise RuntimeError("transient")

    adapter.handle_message = flaky
    event = {"id": 43, "sender_id": "123", "text": "hi"}

    async def scenario():
        nonlocal fail
        await adapter._handle_inbound_event(event)
        fail = False
        await adapter._handle_inbound_event(event)

    asyncio.run(scenario())

    assert adapter._failed_events == {}
    assert bridge.calls == [("ack", 43), ("read", 43)]


def test_media_paths_are_attached_to_the_message(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xdbfake-jpeg")

    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=RecordingBridge(),
    )
    event = {
        "id": 44,
        "sender_id": "123",
        "text": "",
        "has_media": True,
        "media_type": "MessageMediaPhoto",
        "mime_type": "image/jpeg",
        "file_name": "photo.jpg",
        "media_paths": [str(photo)],
    }

    message = adapter.event_to_message(event)
    asyncio.run(adapter._attach_media(event, message))

    assert message.message_type == MessageType.PHOTO
    # Standalone shim passes the path through; real Hermes copies it into its media cache.
    assert len(message.media_urls) == 1
    assert Path(message.media_urls[0]).read_bytes() == photo.read_bytes()
    assert message.media_types == ["image/jpeg"]


def test_missing_media_file_does_not_break_dispatch(tmp_path):
    adapter = TelegramUserAdapter(
        AdapterConfig(allow_from=["123"], write_to=["123"]),
        bridge=RecordingBridge(),
    )
    event = {
        "id": 45,
        "sender_id": "123",
        "text": "look",
        "has_media": True,
        "media_paths": [str(tmp_path / "gone.bin")],
    }

    message = adapter.event_to_message(event)
    asyncio.run(adapter._attach_media(event, message))

    assert message.media_urls == []
    assert message.text == "look"


def test_message_type_mapping():
    adapter = TelegramUserAdapter(AdapterConfig(), bridge=RecordingBridge())
    cases = [
        ({"id": 1, "sender_id": "1"}, MessageType.TEXT),
        ({"id": 1, "sender_id": "1", "has_media": True, "media_type": "MessageMediaGeo"}, MessageType.LOCATION),
        ({"id": 1, "sender_id": "1", "has_media": True, "mime_type": "video/mp4"}, MessageType.VIDEO),
        ({"id": 1, "sender_id": "1", "has_media": True, "mime_type": "audio/ogg", "is_voice_note": True}, MessageType.VOICE),
        ({"id": 1, "sender_id": "1", "has_media": True, "mime_type": "application/pdf"}, MessageType.DOCUMENT),
    ]
    for event, expected in cases:
        assert adapter._message_type_for(event) is expected, event


def test_reply_to_is_carried_into_the_message_event():
    adapter = TelegramUserAdapter(AdapterConfig(), bridge=RecordingBridge())

    message = adapter.event_to_message(
        {"id": 50, "sender_id": "123", "text": "yes", "reply_to_message_id": 49}
    )

    assert message.reply_to_message_id == "49"


def test_typing_is_suppressed_when_disabled():
    typing = AsyncMock()
    adapter = TelegramUserAdapter(
        AdapterConfig(typing_while_replying=False),
        bridge=SimpleNamespace(send_typing=typing),
    )

    asyncio.run(adapter.send_typing("123"))

    typing.assert_not_awaited()


def test_typing_failure_does_not_propagate():
    async def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    adapter = TelegramUserAdapter(
        AdapterConfig(typing_while_replying=True),
        bridge=SimpleNamespace(send_typing=boom),
    )

    asyncio.run(adapter.send_typing("123"))  # must not raise


def test_enforces_own_access_policy_reflects_allowlist_mode():
    restricted = TelegramUserAdapter(AdapterConfig(allow_from=["123"]), bridge=RecordingBridge())
    open_access = TelegramUserAdapter(AdapterConfig(allow_all_users=True), bridge=RecordingBridge())

    assert restricted.enforces_own_access_policy is True
    assert open_access.enforces_own_access_policy is False


def test_register_wires_hermes_native_user_authorization():
    seen = {}

    class Ctx:
        def register_platform(self, **kwargs):
            seen.update(kwargs)

    register(Ctx())

    assert seen["allowed_users_env"] == "HERMES_TG_ALLOWED_USERS"
    assert seen["allow_all_env"] == "HERMES_TG_ALLOW_ALL_USERS"
    assert seen["max_message_length"] == 4096


def test_explicit_empty_write_to_is_not_overridden_by_allow_from():
    config = AdapterConfig.from_env_and_extra({"allow_from": ["123"], "write_to": []})

    assert config.allow_from == ["123"]
    assert config.write_to == []


def test_standalone_send_posts_through_bridge_and_closes_client(monkeypatch):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/send_message":
            return httpx.Response(200, json={"message_id": 77})
        return httpx.Response(429, headers={"Retry-After": "3"}, json={"detail": "slow down"})

    clients = []

    def fake_client(config):
        clients.append(_make_bridge_client(handler, config))
        return clients[-1]

    monkeypatch.setattr(sys.modules["adapter"], "BridgeClient", fake_client)
    pconfig = PlatformConfig(extra={"api_token": "token", "bridge_url": "http://bridge.test"})

    ok = asyncio.run(_standalone_send(pconfig, "123", "cron says hi"))
    assert ok == {"success": True, "message_id": "77"}
    assert json.loads(requests[0].content) == {"peer": "123", "text": "cron says hi", "reply_to": None}
    assert requests[0].headers["Authorization"] == "Bearer token"
    assert clients[0].client.is_closed

    assert "text only" in asyncio.run(_standalone_send(pconfig, "123", "x", media_files=["/a.png"]))["error"]


def test_standalone_send_reports_bridge_error_and_missing_token(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "peer not in write_to"})

    monkeypatch.setattr(sys.modules["adapter"], "BridgeClient", lambda c: _make_bridge_client(handler, c))
    monkeypatch.delenv("HERMES_TG_BRIDGE_API_TOKEN", raising=False)

    err = asyncio.run(_standalone_send(PlatformConfig(extra={"api_token": "t"}), "9", "hi"))
    assert err == {"error": "HTTP 403: peer not in write_to"}
    assert "API_TOKEN" in asyncio.run(_standalone_send(PlatformConfig(), "9", "hi"))["error"]


def test_env_enablement_seeds_home_channel(monkeypatch):
    monkeypatch.setenv("HERMES_TG_BRIDGE_API_TOKEN", "t")
    monkeypatch.setenv("HERMES_TG_HOME_CHANNEL", "123")
    assert _env_enablement()["home_channel"] == {"chat_id": "123", "name": "Home"}
    monkeypatch.delenv("HERMES_TG_HOME_CHANNEL")
    assert "home_channel" not in _env_enablement()


def test_startup_summary_reports_settings_without_the_token(monkeypatch):
    from adapter import startup_summary

    monkeypatch.setenv("HERMES_HOME", "/home/u/.hermes/profiles/wife")
    monkeypatch.setenv("HERMES_TG_HOME_CHANNEL", "42")
    config = AdapterConfig(api_token="s3cret-token", allow_from=["1", "2"])

    line = startup_summary(config, {"status": "ok", "connected": True})

    assert "s3cret-token" not in line
    for part in (
        "plugin=0.1.0", "hermes_home=/home/u/.hermes/profiles/wife", "bridge_status=ok",
        "token_set=True", "allowed_users=2", "home_channel=env",
    ):
        assert part in line


def test_startup_summary_reports_the_hermes_cli_version(monkeypatch):
    from adapter import startup_summary

    monkeypatch.setitem(sys.modules, "hermes_cli", SimpleNamespace(__version__="0.22.4"))

    assert "hermes=0.22.4" in startup_summary(AdapterConfig(api_token="t"))


def test_startup_summary_sees_home_channel_from_platform_config(monkeypatch):
    from adapter import startup_summary

    monkeypatch.delenv("HERMES_TG_HOME_CHANNEL", raising=False)
    config = AdapterConfig(api_token="t")

    assert "home_channel=none" in startup_summary(config)
    from_config = SimpleNamespace(extra={}, home_channel={"chat_id": "42", "name": "Home"})
    assert "home_channel=config" in startup_summary(config, platform_config=from_config)
    from_extra = PlatformConfig(extra={"home_channel": {"chat_id": "42"}})
    assert "home_channel=config" in startup_summary(config, platform_config=from_extra)


def test_plugin_refuses_to_load_on_a_changed_hermes_api(monkeypatch):
    """An installed gateway missing a name must fail the load, not fall back to the shim."""
    import importlib.util
    import types

    import pytest

    gateway = types.ModuleType("gateway")
    gateway.__path__ = []
    gateway_config = types.ModuleType("gateway.config")  # no Platform / PlatformConfig
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.config", gateway_config)

    path = Path(sys.modules["adapter"].__file__)
    spec = importlib.util.spec_from_file_location("adapter_api_changed", path)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ImportError, match="Hermes gateway API changed"):
        spec.loader.exec_module(module)


def test_register_names_the_plugin_when_register_platform_rejects_an_argument():
    import pytest

    class OldCtx:
        def register_platform(self, *, name, label):
            return "registered"

    with pytest.raises(TypeError, match="register_platform\\(\\) API changed"):
        register(OldCtx())


def test_premium_transcript_replaces_voice_and_falls_back_on_failure():
    class TranscribingBridge(RecordingBridge):
        async def transcribe_voice(self, peer, message_id):
            self.calls.append(("transcribe", message_id))
            if message_id == 2:
                return {"ok": False, "error": "transcription_unavailable"}
            return {"ok": True, "text": " привет "}

    bridge = TranscribingBridge()
    adapter = TelegramUserAdapter(AdapterConfig(allow_from=["1"]), bridge=bridge)
    seen: list[MessageEvent] = []

    async def handle_message(message):
        seen.append(message)

    adapter._message_handler = handle_message
    voice = {"sender_id": "1", "has_media": True, "mime_type": "audio/ogg", "is_voice_note": True}

    async def run():
        await adapter._handle_inbound_event({**voice, "id": 1, "can_transcribe": True})
        await adapter._handle_inbound_event({**voice, "id": 2, "can_transcribe": True})
        await adapter._handle_inbound_event({**voice, "id": 3})

    asyncio.run(run())

    assert seen[0].text == "[Voice message transcript]: привет"
    assert seen[0].message_type == MessageType.TEXT
    assert [m.message_type for m in seen[1:]] == [MessageType.VOICE, MessageType.VOICE]
    assert ("transcribe", 3) not in bridge.calls
