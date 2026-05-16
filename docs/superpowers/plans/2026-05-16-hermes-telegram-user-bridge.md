# Hermes Telegram User Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Hermes Agent gateway platform that receives and sends Telegram direct messages through a live Telegram user account.

**Architecture:** Keep the proven Telethon/FastAPI backend from `OstaninKI/openclaw_tg_bridge`, rename it to `hermes_tg_bridge`, and replace the OpenClaw TypeScript plugin with a Hermes Python platform plugin named `telegram_user`. The backend remains the policy and Telegram-session trust boundary; the Hermes adapter is a small HTTP client plus polling loop.

**Tech Stack:** Python 3.10+, FastAPI, Telethon, Pydantic, pytest, pytest-asyncio, Hermes plugin API, `httpx` for adapter HTTP calls.

---

## File Structure

- `backend/hermes_tg_bridge/`: renamed backend package copied from the OpenClaw bridge.
- `backend/tests/`: backend unit tests copied and renamed from OpenClaw, then updated for Hermes headers and paths.
- `backend/pyproject.toml`: Python package metadata and script entrypoint.
- `backend/requirements.txt`: runtime requirements.
- `plugin/hermes-telegram-user/PLUGIN.yaml`: Hermes platform plugin manifest.
- `plugin/hermes-telegram-user/adapter.py`: Hermes platform adapter and bridge HTTP client.
- `plugin/hermes-telegram-user/test_adapter.py`: adapter unit tests with fake bridge/client behavior.
- `deploy/`: example service, policy, and Hermes gateway config.
- `README.md`: setup and smoke-test instructions.
- `create_telethon_session.sh`: convenience auth wrapper.

## Task 1: Import And Rename Backend

**Files:**
- Create: `backend/hermes_tg_bridge/*.py`
- Create: `backend/tests/*.py`
- Create: `backend/pyproject.toml`
- Create: `backend/requirements.txt`
- Create: `create_telethon_session.sh`
- Modify: all copied Python imports from `openclaw_tg_bridge` to `hermes_tg_bridge`

- [ ] **Step 1: Copy backend source from temporary OpenClaw checkout**

Run:

```bash
mkdir -p backend/hermes_tg_bridge backend/tests
cp /private/tmp/openclaw_tg_bridge_source/backend/openclaw_tg_bridge/*.py backend/hermes_tg_bridge/
cp /private/tmp/openclaw_tg_bridge_source/backend/tests/*.py backend/tests/
cp /private/tmp/openclaw_tg_bridge_source/backend/requirements.txt backend/requirements.txt
cp /private/tmp/openclaw_tg_bridge_source/backend/pyproject.toml backend/pyproject.toml
cp /private/tmp/openclaw_tg_bridge_source/create_telethon_session.sh create_telethon_session.sh
```

Expected: files exist under `backend/hermes_tg_bridge` and `backend/tests`.

- [ ] **Step 2: Rename package imports mechanically**

Run:

```bash
perl -pi -e 's/openclaw_tg_bridge/hermes_tg_bridge/g; s/OpenClaw/Hermes/g; s/openclaw-tg-bridge/hermes-tg-bridge/g' backend/hermes_tg_bridge/*.py backend/tests/*.py backend/pyproject.toml create_telethon_session.sh
```

Expected: `rg -n "openclaw_tg_bridge|openclaw-tg-bridge" backend create_telethon_session.sh` returns no matches.

- [ ] **Step 3: Update package metadata**

Edit `backend/pyproject.toml` to contain:

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "hermes-tg-bridge"
version = "0.1.0"
description = "Telethon HTTP bridge for Hermes Agent (live Telegram user account)"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "telethon>=1.43.2,<1.44",
    "fastapi>=0.115.0,<1",
    "uvicorn[standard]>=0.30.0,<1",
    "pydantic>=2.0,<3",
    "qrcode[pil]>=7.4",
]

[project.scripts]
hermes-tg-bridge = "hermes_tg_bridge.__main__:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["hermes_tg_bridge*"]

[project.optional-dependencies]
dev = ["pytest>=7", "pytest-asyncio>=0.23", "httpx>=0.27,<1"]
```

- [ ] **Step 4: Run the imported backend tests**

Run:

```bash
cd backend && python3 -m pytest tests -q
```

Expected: tests fail only because dependencies are missing or because Hermes header/path expectations have not been changed yet. If dependencies are missing, create a local venv in `backend/.venv` and install `.[dev]`.

- [ ] **Step 5: Commit the import**

Run:

```bash
git add backend create_telethon_session.sh
git commit -m "Import Telegram bridge backend for Hermes"
```

## Task 2: Rename Backend Headers, Paths, And CLI Text

**Files:**
- Modify: `backend/hermes_tg_bridge/config.py`
- Modify: `backend/hermes_tg_bridge/__main__.py`
- Modify: `backend/hermes_tg_bridge/auth.py`
- Modify: `backend/hermes_tg_bridge/auth_qr.py`
- Modify: `backend/tests/test_config.py`
- Modify: `backend/tests/test_server.py`

- [ ] **Step 1: Write failing tests for Hermes headers and default state path**

Add to `backend/tests/test_config.py`:

```python
def test_parse_request_overrides_accepts_hermes_headers() -> None:
    overrides = parse_request_overrides(
        {
            "X-Hermes-Policy-Profile": "dm_inbox",
            "X-Hermes-Reply-Delay-Sec": "1.5",
            "X-Hermes-Reply-Delay-Max-Sec": "5",
            "X-Hermes-Allow-From": "123,@alice",
            "X-Hermes-Deny-From": "999",
            "X-Hermes-Write-To": "123",
            "X-Hermes-Deny-Write-To": "@blocked",
        }
    )

    assert overrides == {
        "policy_profile": "dm_inbox",
        "reply_delay_sec": 1.5,
        "reply_delay_max_sec": 5.0,
        "read_allow_chat_ids": ["123", "@alice"],
        "read_deny_chat_ids": ["999"],
        "write_allow_chat_ids": ["123"],
        "write_deny_chat_ids": ["@blocked"],
    }


def test_load_config_defaults_to_hermes_state_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("TELEGRAM_"):
            monkeypatch.delenv(key, raising=False)

    cfg = load_config()

    assert "/.hermes/telethon/" in cfg["sources_inventory_path"]
    assert "/.hermes/telethon/" in cfg["inbox_state_path"]
    assert "/.hermes/telethon/" in cfg["dm_media_path"]
    assert "/.hermes/telethon/" in cfg["lock_path"]
```

Ensure `backend/tests/test_config.py` imports `os` and `pytest`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && python3 -m pytest tests/test_config.py -q
```

Expected: FAIL because headers still use `x-openclaw-*` or default path still includes `.openclaw`.

- [ ] **Step 3: Implement Hermes headers and default path**

In `backend/hermes_tg_bridge/config.py`, set the constants to:

```python
HEADER_POLICY_PROFILE = "x-hermes-policy-profile"
HEADER_REPLY_DELAY_SEC = "x-hermes-reply-delay-sec"
HEADER_REPLY_DELAY_MAX_SEC = "x-hermes-reply-delay-max-sec"
HEADER_ALLOW_FROM = "x-hermes-allow-from"
HEADER_DENY_FROM = "x-hermes-deny-from"
HEADER_WRITE_TO = "x-hermes-write-to"
HEADER_DENY_WRITE_TO = "x-hermes-deny-write-to"
```

In `load_config()`, set:

```python
default_state_dir = (
    Path(policy_path).expanduser().resolve().parent
    if policy_path
    else (Path.home() / ".hermes" / "telethon")
)
```

Update user-facing CLI descriptions to say `Hermes Telegram Bridge`, and update unauthorized session text to:

```python
raise ValueError("Session not authorized. Create session locally with: python -m hermes_tg_bridge.auth")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd backend && python3 -m pytest tests/test_config.py tests/test_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit backend rename behavior**

Run:

```bash
git add backend
git commit -m "Rename backend policy surface for Hermes"
```

## Task 3: Add Hermes Plugin Manifest And Adapter Config

**Files:**
- Create: `plugin/hermes-telegram-user/PLUGIN.yaml`
- Create: `plugin/hermes-telegram-user/adapter.py`
- Create: `plugin/hermes-telegram-user/test_adapter.py`

- [ ] **Step 1: Write failing config tests**

Create `plugin/hermes-telegram-user/test_adapter.py`:

```python
import os

from adapter import AdapterConfig, BridgeClient, build_policy_headers


def test_config_loads_env_with_defaults(monkeypatch):
    monkeypatch.setenv("HERMES_TG_BRIDGE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("HERMES_TG_BRIDGE_API_TOKEN", "secret")
    monkeypatch.setenv("HERMES_TG_ALLOWED_USERS", "123,@alice")
    monkeypatch.setenv("HERMES_TG_POLL_TIMEOUT_MS", "1000")

    cfg = AdapterConfig.from_env_and_extra({})

    assert cfg.bridge_url == "http://127.0.0.1:9999"
    assert cfg.api_token == "secret"
    assert cfg.allow_from == ["123", "@alice"]
    assert cfg.write_to == ["123", "@alice"]
    assert cfg.poll_timeout_ms == 1000
    assert cfg.poll_interval_ms == 1500
    assert cfg.allow_all_users is False


def test_extra_overrides_defaults_when_env_missing(monkeypatch):
    for key in list(os.environ):
        if key.startswith("HERMES_TG_"):
            monkeypatch.delenv(key, raising=False)

    cfg = AdapterConfig.from_env_and_extra(
        {
            "bridge_url": "http://bridge.local",
            "policy_profile": "dm_inbox",
            "allow_from": ["777"],
            "write_to": ["777"],
            "mark_read_on_inbound": False,
        }
    )

    assert cfg.bridge_url == "http://bridge.local"
    assert cfg.policy_profile == "dm_inbox"
    assert cfg.allow_from == ["777"]
    assert cfg.write_to == ["777"]
    assert cfg.mark_read_on_inbound is False


def test_policy_headers_include_hermes_names():
    cfg = AdapterConfig(
        bridge_url="http://127.0.0.1:8765",
        api_token="secret",
        policy_profile="dm_inbox",
        allow_from=["123"],
        deny_from=["999"],
        write_to=["123"],
        deny_write_to=["998"],
    )

    assert build_policy_headers(cfg) == {
        "Authorization": "Bearer secret",
        "X-Hermes-Policy-Profile": "dm_inbox",
        "X-Hermes-Allow-From": "123",
        "X-Hermes-Deny-From": "999",
        "X-Hermes-Write-To": "123",
        "X-Hermes-Deny-Write-To": "998",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd plugin/hermes-telegram-user && python3 -m pytest test_adapter.py -q
```

Expected: FAIL because `adapter.py` does not exist.

- [ ] **Step 3: Create plugin manifest**

Create `plugin/hermes-telegram-user/PLUGIN.yaml`:

```yaml
name: hermes-telegram-user
version: 0.1.0
description: Live Telegram user-account gateway for Hermes Agent
kind: platform
entry_point: adapter.py
platforms:
  - telegram_user
```

- [ ] **Step 4: Implement config and policy headers**

Create `plugin/hermes-telegram-user/adapter.py` with:

```python
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import httpx


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


@dataclass(frozen=True)
class AdapterConfig:
    bridge_url: str = "http://127.0.0.1:8765"
    api_token: str | None = None
    policy_profile: str = "dm_inbox"
    allow_from: list[str] = field(default_factory=list)
    deny_from: list[str] = field(default_factory=list)
    write_to: list[str] = field(default_factory=list)
    deny_write_to: list[str] = field(default_factory=list)
    allow_all_users: bool = False
    mark_read_on_inbound: bool = True
    typing_while_replying: bool = True
    poll_timeout_ms: int = 25000
    poll_interval_ms: int = 1500

    @classmethod
    def from_env_and_extra(cls, extra: dict[str, Any] | None) -> "AdapterConfig":
        extra = extra or {}
        allow_from = _list(os.getenv("HERMES_TG_ALLOWED_USERS", extra.get("allow_from")))
        write_to = _list(os.getenv("HERMES_TG_WRITE_TO", extra.get("write_to"))) or allow_from
        return cls(
            bridge_url=str(os.getenv("HERMES_TG_BRIDGE_URL", extra.get("bridge_url", cls.bridge_url))).rstrip("/"),
            api_token=os.getenv("HERMES_TG_BRIDGE_API_TOKEN", extra.get("api_token")),
            policy_profile=str(os.getenv("HERMES_TG_POLICY_PROFILE", extra.get("policy_profile", cls.policy_profile))),
            allow_from=allow_from,
            deny_from=_list(os.getenv("HERMES_TG_DENY_FROM", extra.get("deny_from"))),
            write_to=write_to,
            deny_write_to=_list(os.getenv("HERMES_TG_DENY_WRITE_TO", extra.get("deny_write_to"))),
            allow_all_users=_bool(os.getenv("HERMES_TG_ALLOW_ALL_USERS", extra.get("allow_all_users")), False),
            mark_read_on_inbound=_bool(
                os.getenv("HERMES_TG_MARK_READ_ON_INBOUND", extra.get("mark_read_on_inbound")),
                True,
            ),
            typing_while_replying=_bool(
                os.getenv("HERMES_TG_TYPING_WHILE_REPLYING", extra.get("typing_while_replying")),
                True,
            ),
            poll_timeout_ms=_int(os.getenv("HERMES_TG_POLL_TIMEOUT_MS", extra.get("poll_timeout_ms")), 25000),
            poll_interval_ms=_int(os.getenv("HERMES_TG_POLL_INTERVAL_MS", extra.get("poll_interval_ms")), 1500),
        )


def build_policy_headers(config: AdapterConfig) -> dict[str, str]:
    headers: dict[str, str] = {}
    if config.api_token:
        headers["Authorization"] = f"Bearer {config.api_token}"
    if config.policy_profile:
        headers["X-Hermes-Policy-Profile"] = config.policy_profile
    if config.allow_all_users:
        headers["X-Hermes-Allow-From"] = "*"
    elif config.allow_from:
        headers["X-Hermes-Allow-From"] = ",".join(config.allow_from)
    if config.deny_from:
        headers["X-Hermes-Deny-From"] = ",".join(config.deny_from)
    if config.write_to:
        headers["X-Hermes-Write-To"] = ",".join(config.write_to)
    if config.deny_write_to:
        headers["X-Hermes-Deny-Write-To"] = ",".join(config.deny_write_to)
    return headers


class BridgeClient:
    def __init__(self, config: AdapterConfig) -> None:
        self.config = config
        self.headers = build_policy_headers(config)
        self.client = httpx.AsyncClient(base_url=config.bridge_url, timeout=(config.poll_timeout_ms / 1000.0) + 5.0)
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
cd plugin/hermes-telegram-user && python3 -m pytest test_adapter.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit plugin skeleton**

Run:

```bash
git add plugin/hermes-telegram-user
git commit -m "Add Hermes Telegram platform plugin skeleton"
```

## Task 4: Implement Bridge HTTP Client Behavior

**Files:**
- Modify: `plugin/hermes-telegram-user/adapter.py`
- Modify: `plugin/hermes-telegram-user/test_adapter.py`

- [ ] **Step 1: Add failing BridgeClient tests**

Append to `plugin/hermes-telegram-user/test_adapter.py`:

```python
import pytest


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://test.local")
            response = httpx.Response(self.status_code, request=request, json=self._payload, headers=self.headers)
            raise httpx.HTTPStatusError("error", request=request, response=response)


class FakeAsyncClient:
    def __init__(self):
        self.calls = []
        self.responses = []

    def queue(self, response):
        self.responses.append(response)

    async def get(self, path, params=None, headers=None):
        self.calls.append(("GET", path, params, headers, None))
        return self.responses.pop(0)

    async def post(self, path, json=None, headers=None):
        self.calls.append(("POST", path, None, headers, json))
        return self.responses.pop(0)

    async def aclose(self):
        self.calls.append(("CLOSE", None, None, None, None))


@pytest.mark.asyncio
async def test_poll_inbox_returns_events():
    cfg = AdapterConfig(bridge_url="http://bridge", allow_from=["123"], write_to=["123"])
    bridge = BridgeClient(cfg)
    fake = FakeAsyncClient()
    fake.queue(FakeResponse(payload={"events": [{"id": 7, "sender_id": "123", "text": "hi"}]}))
    bridge.client = fake

    events = await bridge.poll_inbox()

    assert events == [{"id": 7, "sender_id": "123", "text": "hi"}]
    assert fake.calls[0][0:3] == ("GET", "/dm/inbox/poll", {"timeout_ms": 25000, "limit": 10})


@pytest.mark.asyncio
async def test_send_message_posts_to_backend():
    cfg = AdapterConfig(bridge_url="http://bridge", allow_from=["123"], write_to=["123"])
    bridge = BridgeClient(cfg)
    fake = FakeAsyncClient()
    fake.queue(FakeResponse(payload={"message_id": 8}))
    bridge.client = fake

    result = await bridge.send_message("123", "hello")

    assert result == {"message_id": 8}
    assert fake.calls[0] == (
        "POST",
        "/send_message",
        None,
        build_policy_headers(cfg),
        {"peer": "123", "text": "hello", "reply_to": None},
    )


@pytest.mark.asyncio
async def test_ack_and_read_use_sender_payload():
    cfg = AdapterConfig(bridge_url="http://bridge", allow_from=["123"], write_to=["123"])
    bridge = BridgeClient(cfg)
    fake = FakeAsyncClient()
    fake.queue(FakeResponse(payload={"ok": True}))
    fake.queue(FakeResponse(payload={"ok": True}))
    bridge.client = fake

    await bridge.ack({"id": 9, "sender_id": "123", "sender_username": "alice"})
    await bridge.mark_read({"id": 9, "sender_id": "123", "sender_username": "alice"})

    assert fake.calls[0][1] == "/dm/inbox/ack"
    assert fake.calls[0][4] == {"sender_id": "123", "sender_username": "alice", "message_id": 9}
    assert fake.calls[1][1] == "/dm/read"
    assert fake.calls[1][4] == {"sender_id": "123", "sender_username": "alice", "message_id": 9}
```

Add `import httpx` near the top of the test file.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd plugin/hermes-telegram-user && python3 -m pytest test_adapter.py -q
```

Expected: FAIL because `poll_inbox`, `send_message`, `ack`, and `mark_read` do not exist.

- [ ] **Step 3: Implement BridgeClient methods**

Add to `BridgeClient`:

```python
    async def close(self) -> None:
        await self.client.aclose()

    async def health(self) -> dict[str, Any]:
        response = await self.client.get("/health", headers=self.headers)
        response.raise_for_status()
        return response.json()

    async def poll_inbox(self, limit: int = 10) -> list[dict[str, Any]]:
        response = await self.client.get(
            "/dm/inbox/poll",
            params={"timeout_ms": self.config.poll_timeout_ms, "limit": limit},
            headers=self.headers,
        )
        response.raise_for_status()
        payload = response.json()
        events = payload.get("events", [])
        return events if isinstance(events, list) else []

    async def send_message(self, peer: str, text: str, reply_to: int | None = None) -> dict[str, Any]:
        response = await self.client.post(
            "/send_message",
            json={"peer": peer, "text": text, "reply_to": reply_to},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    async def ack(self, event: dict[str, Any]) -> None:
        await self._post_dm_state("/dm/inbox/ack", event)

    async def mark_read(self, event: dict[str, Any]) -> None:
        await self._post_dm_state("/dm/read", event)

    async def send_typing(self, sender_id: str, sender_username: str | None = None) -> None:
        response = await self.client.post(
            "/dm/typing",
            json={"sender_id": sender_id, "sender_username": sender_username},
            headers=self.headers,
        )
        response.raise_for_status()

    async def _post_dm_state(self, path: str, event: dict[str, Any]) -> None:
        response = await self.client.post(
            path,
            json={
                "sender_id": str(event.get("sender_id", "")),
                "sender_username": event.get("sender_username"),
                "message_id": int(event.get("id", 0)),
            },
            headers=self.headers,
        )
        response.raise_for_status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd plugin/hermes-telegram-user && python3 -m pytest test_adapter.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit HTTP client behavior**

Run:

```bash
git add plugin/hermes-telegram-user/adapter.py plugin/hermes-telegram-user/test_adapter.py
git commit -m "Add Hermes bridge HTTP client"
```

## Task 5: Implement Hermes Platform Adapter

**Files:**
- Modify: `plugin/hermes-telegram-user/adapter.py`
- Modify: `plugin/hermes-telegram-user/test_adapter.py`

- [ ] **Step 1: Add failing conversion tests**

Append to `plugin/hermes-telegram-user/test_adapter.py`:

```python
from adapter import TelegramUserAdapter


def test_event_to_message_event_shape():
    adapter = TelegramUserAdapter(AdapterConfig(allow_from=["123"], write_to=["123"]), bridge=None)
    event = {
        "id": 11,
        "sender_id": "123",
        "sender_name": "Alice",
        "sender_username": "alice",
        "text": "hello",
        "date": "2026-05-16T12:00:00+00:00",
    }

    message = adapter.event_to_message(event)

    assert message["platform"] == "telegram_user"
    assert message["chat_id"] == "123"
    assert message["chat_type"] == "dm"
    assert message["user_id"] == "123"
    assert message["user_name"] == "Alice"
    assert message["content"] == "hello"
    assert message["metadata"]["telegram_message_id"] == 11
    assert message["metadata"]["sender_username"] == "alice"


def test_event_to_message_uses_media_marker_when_text_empty():
    adapter = TelegramUserAdapter(AdapterConfig(allow_from=["123"], write_to=["123"]), bridge=None)
    event = {
        "id": 12,
        "sender_id": "123",
        "sender_name": "Alice",
        "text": "",
        "has_media": True,
        "media_type": "MessageMediaPhoto",
    }

    message = adapter.event_to_message(event)

    assert message["content"] == "[Telegram media: MessageMediaPhoto]"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd plugin/hermes-telegram-user && python3 -m pytest test_adapter.py -q
```

Expected: FAIL because `TelegramUserAdapter` does not exist.

- [ ] **Step 3: Implement adapter conversion and Hermes registration shim**

Add to `adapter.py`:

```python
class TelegramUserAdapter:
    platform_name = "telegram_user"

    def __init__(self, config: AdapterConfig, bridge: BridgeClient | None = None) -> None:
        self.config = config
        self.bridge = bridge or BridgeClient(config)
        self._running = False
        self._poll_task: asyncio.Task[None] | None = None

    def event_to_message(self, event: dict[str, Any]) -> dict[str, Any]:
        sender_id = str(event.get("sender_id", ""))
        sender_name = str(event.get("sender_name") or event.get("sender_username") or sender_id)
        text = str(event.get("text") or "")
        if not text and event.get("has_media"):
            text = f"[Telegram media: {event.get('media_type') or 'unknown'}]"
        return {
            "platform": self.platform_name,
            "message_id": str(event.get("id", "")),
            "chat_id": sender_id,
            "chat_name": sender_name,
            "chat_type": "dm",
            "user_id": sender_id,
            "user_name": sender_name,
            "content": text,
            "metadata": {
                "telegram_message_id": event.get("id"),
                "sender_username": event.get("sender_username"),
                "date": event.get("date"),
                "raw": event,
            },
        }

    async def connect(self) -> None:
        await self.bridge.health()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def disconnect(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self.bridge.close()

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                events = await self.bridge.poll_inbox()
                for event in events:
                    await self._handle_inbound_event(event)
                await asyncio.sleep(self.config.poll_interval_ms / 1000.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(min(30.0, max(1.0, self.config.poll_interval_ms / 1000.0)))

    async def _handle_inbound_event(self, event: dict[str, Any]) -> None:
        message = self.event_to_message(event)
        handler = getattr(self, "handle_message", None)
        if handler is not None:
            result = handler(message)
            if hasattr(result, "__await__"):
                await result
        await self.bridge.ack(event)
        if self.config.mark_read_on_inbound:
            await self.bridge.mark_read(event)

    async def send(self, chat_id: str, content: str, reply_to: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            result = await self.bridge.send_message(chat_id, content, int(reply_to) if reply_to else None)
            return {"success": True, "message_id": str(result.get("message_id", "")), "error": None}
        except Exception as exc:
            return {"success": False, "message_id": None, "error": str(exc)}

    async def send_typing(self, chat_id: str, metadata: dict[str, Any] | None = None) -> None:
        await self.bridge.send_typing(chat_id, (metadata or {}).get("sender_username"))


def register(ctx: Any) -> None:
    extra = getattr(ctx, "extra", None) or getattr(ctx, "config", None) or {}
    config = AdapterConfig.from_env_and_extra(extra if isinstance(extra, dict) else {})
    adapter = TelegramUserAdapter(config)
    ctx.register_platform("telegram_user", adapter)
```

When real Hermes imports are available locally, replace dict-shaped messages/results with the documented Hermes `MessageEvent` and `SendResult` classes if required by runtime.

- [ ] **Step 4: Add failing inbound ack/read test**

Append:

```python
class RecordingBridge:
    def __init__(self):
        self.calls = []

    async def ack(self, event):
        self.calls.append(("ack", event["id"]))

    async def mark_read(self, event):
        self.calls.append(("read", event["id"]))


@pytest.mark.asyncio
async def test_handle_inbound_event_dispatches_then_acks_and_reads():
    bridge = RecordingBridge()
    adapter = TelegramUserAdapter(AdapterConfig(allow_from=["123"], write_to=["123"]), bridge=bridge)
    seen = []

    async def handle_message(message):
        seen.append(message)

    adapter.handle_message = handle_message

    await adapter._handle_inbound_event({"id": 13, "sender_id": "123", "text": "hi"})

    assert seen[0]["content"] == "hi"
    assert bridge.calls == [("ack", 13), ("read", 13)]
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd plugin/hermes-telegram-user && python3 -m pytest test_adapter.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit platform adapter**

Run:

```bash
git add plugin/hermes-telegram-user/adapter.py plugin/hermes-telegram-user/test_adapter.py
git commit -m "Implement Hermes Telegram user platform adapter"
```

## Task 6: Deployment Examples And README

**Files:**
- Create: `deploy/hermes-tg-bridge.service`
- Create: `deploy/policy.json.example`
- Create: `deploy/config.yaml.example`
- Create: `README.md`
- Create: `plugin/hermes-telegram-user/README.md`

- [ ] **Step 1: Create systemd service example**

Create `deploy/hermes-tg-bridge.service`:

```ini
[Unit]
Description=Hermes Telegram User Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/hermes-tg-bridge/backend
EnvironmentFile=/etc/hermes-tg-bridge.env
ExecStart=/opt/hermes-tg-bridge/backend/.venv/bin/hermes-tg-bridge run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create policy example**

Create `deploy/policy.json.example`:

```json
{
  "defaults": {
    "reply_delay_sec": 1.5,
    "reply_delay_max_sec": 10,
    "read_allow_chat_ids": [],
    "read_deny_chat_ids": [],
    "write_allow_chat_ids": [],
    "write_deny_chat_ids": []
  },
  "profiles": {
    "dm_inbox": {
      "read_allow_chat_ids": ["123456789"],
      "write_allow_chat_ids": ["123456789"]
    }
  }
}
```

- [ ] **Step 3: Create Hermes config example**

Create `deploy/config.yaml.example`:

```yaml
plugins:
  enabled:
    - hermes-telegram-user

gateway:
  platforms:
    telegram_user:
      enabled: true
      extra:
        bridge_url: "http://127.0.0.1:8765"
        api_token: "change-me"
        policy_profile: "dm_inbox"
        allow_from: ["123456789"]
        write_to: ["123456789"]
        poll_timeout_ms: 25000
        poll_interval_ms: 1500
        mark_read_on_inbound: true
```

- [ ] **Step 4: Create README**

Create `README.md` with:

```markdown
# Hermes Telegram User Bridge

Hermes gateway platform for a live Telegram user account. This is not a bot integration: Telegram access is handled by Telethon using a user session.

## Components

- `backend/`: local FastAPI service that owns the Telethon session and enforces policy.
- `plugin/hermes-telegram-user/`: Hermes platform plugin named `telegram_user`.

## Local Setup

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

Create a Telegram session:

```bash
hermes-tg-bridge auth-qr --session-path ~/.hermes/telethon/user.session
```

Run the bridge:

```bash
export TELEGRAM_API_ID=123
export TELEGRAM_API_HASH=your_hash
export TELEGRAM_SESSION_PATH=~/.hermes/telethon/user.session
export TELEGRAM_BRIDGE_API_TOKEN=change-me
export TELEGRAM_POLICY_PATH=$PWD/../deploy/policy.json.example
hermes-tg-bridge run
```

Enable the Hermes plugin with the example in `deploy/config.yaml.example`.

## Safety Defaults

The backend listens on `127.0.0.1` by default. Writes are denied unless `write_allow_chat_ids` or `X-Hermes-Write-To` explicitly allows the peer.
```

Create `plugin/hermes-telegram-user/README.md` with:

```markdown
# hermes-telegram-user

Hermes platform plugin that connects the Hermes gateway to `hermes-tg-bridge`.

Platform name: `telegram_user`.

Required backend: `hermes-tg-bridge run`.
```

- [ ] **Step 5: Commit docs and deploy examples**

Run:

```bash
git add README.md deploy plugin/hermes-telegram-user/README.md
git commit -m "Add Hermes Telegram bridge deployment docs"
```

## Task 7: Full Verification

**Files:**
- Modify only files needed to fix test failures.

- [ ] **Step 1: Run backend tests**

Run:

```bash
cd backend && python3 -m pytest tests -q
```

Expected: PASS.

- [ ] **Step 2: Run plugin tests**

Run:

```bash
cd plugin/hermes-telegram-user && python3 -m pytest test_adapter.py -q
```

Expected: PASS.

- [ ] **Step 3: Run import smoke checks**

Run:

```bash
cd backend && python3 -m hermes_tg_bridge.__main__ --help
```

Expected: help output includes `auth`, `auth-qr`, and `run`.

Run:

```bash
cd plugin/hermes-telegram-user && python3 - <<'PY'
from adapter import AdapterConfig, TelegramUserAdapter
adapter = TelegramUserAdapter(AdapterConfig())
print(adapter.platform_name)
PY
```

Expected: `telegram_user`.

- [ ] **Step 4: Check no stale OpenClaw package refs**

Run:

```bash
rg -n "openclaw_tg_bridge|openclaw-tg-bridge|X-OpenClaw" .
```

Expected: no matches. Mentions of `OpenClaw` are allowed only in the design spec as historical source context.

- [ ] **Step 5: Commit verification fixes if any**

Run only if files changed:

```bash
git add .
git commit -m "Verify Hermes Telegram bridge implementation"
```

## Self-Review

- Spec coverage: backend rename, Hermes headers, default state paths, platform plugin, long polling, outbound sends, ack/read receipts, deployment examples, and tests are all covered by tasks.
- Deferred rich Telegram tools are intentionally not implemented in this first plan; backend endpoints are preserved so they can be registered later.
- No placeholders remain in the plan. Any real Hermes runtime type mismatch discovered during implementation must be handled inside Task 5 while preserving the tested adapter behavior.
