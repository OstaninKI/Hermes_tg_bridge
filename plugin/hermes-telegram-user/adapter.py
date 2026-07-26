from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

logger = logging.getLogger(__name__)

try:
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import (
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        SendResult,
        cache_media_bytes,
        classify_send_error,
    )
except ImportError:  # pragma: no cover - exercised only outside a Hermes install
    # Test/standalone shim. Mirrors the real gateway API (NousResearch/hermes-agent,
    # gateway/platforms/base.py) closely enough to exercise adapter logic; anything
    # added here must match the upstream signature it stands in for.
    cache_media_bytes = None  # type: ignore[assignment]

    def classify_send_error(exc: BaseException | None, error_text: str = "") -> str:
        blob = f"{exc or ''} {error_text}".lower()
        if "429" in blob or "too many requests" in blob or "rate limit" in blob:
            return "rate_limited"
        if "403" in blob or "forbidden" in blob:
            return "forbidden"
        if "404" in blob or "not found" in blob:
            return "not_found"
        if "timeout" in blob or "connect" in blob:
            return "transient"
        return "unknown"

    class Platform(str):
        def __new__(cls, value: str) -> "Platform":
            return str.__new__(cls, value)

    @dataclass
    class PlatformConfig:
        extra: dict[str, Any] = field(default_factory=dict)

    class MessageType(Enum):
        TEXT = "text"
        LOCATION = "location"
        PHOTO = "photo"
        VIDEO = "video"
        AUDIO = "audio"
        VOICE = "voice"
        DOCUMENT = "document"
        STICKER = "sticker"
        COMMAND = "command"

    @dataclass
    class MessageEvent:
        text: str
        message_type: MessageType = MessageType.TEXT
        source: Any = None
        raw_message: Any = None
        message_id: str | None = None
        media_urls: list[str] = field(default_factory=list)
        media_types: list[str] = field(default_factory=list)
        reply_to_message_id: str | None = None
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class SendResult:
        success: bool
        message_id: str | None = None
        error: str | None = None
        raw_response: Any = None
        retryable: bool = False
        retry_after: float | None = None
        continuation_message_ids: tuple = ()
        error_kind: str | None = None

    class BasePlatformAdapter:
        def __init__(self, config: Any, platform: Any) -> None:
            self.config = config
            self.platform = platform
            self._running = False
            self._message_handler = None

        @property
        def name(self) -> str:
            return str(self.platform)

        @property
        def is_connected(self) -> bool:
            return self._running

        def _mark_connected(self) -> None:
            self._running = True

        def _mark_disconnected(self) -> None:
            self._running = False

        def _set_fatal_error(self, code: str, message: str, *, retryable: bool) -> None:
            self._running = False
            self._fatal_error_code = code
            self._fatal_error_message = message
            self._fatal_error_retryable = retryable

        def build_source(
            self,
            chat_id: str,
            chat_name: str | None = None,
            chat_type: str = "dm",
            user_id: str | None = None,
            user_name: str | None = None,
            thread_id: str | None = None,
            chat_topic: str | None = None,
            user_id_alt: str | None = None,
            chat_id_alt: str | None = None,
            is_bot: bool = False,
            scope_id: str | None = None,
            guild_id: str | None = None,
            parent_chat_id: str | None = None,
            message_id: str | None = None,
            role_authorized: bool = False,
            auto_thread_created: bool = False,
            auto_thread_initial_name: str | None = None,
        ) -> Any:
            return SimpleNamespace(
                platform=self.platform,
                chat_id=str(chat_id),
                chat_name=chat_name,
                chat_type=chat_type,
                user_id=str(user_id) if user_id else None,
                user_name=user_name,
                thread_id=str(thread_id) if thread_id else None,
                chat_topic=chat_topic,
                user_id_alt=user_id_alt,
                chat_id_alt=chat_id_alt,
                is_bot=is_bot,
                scope_id=str(scope_id) if scope_id else None,
                guild_id=str(guild_id) if guild_id else None,
                parent_chat_id=str(parent_chat_id) if parent_chat_id else None,
                message_id=str(message_id) if message_id else None,
            )

        async def handle_message(self, event: MessageEvent) -> None:
            if self._message_handler is None:
                return
            result = self._message_handler(event)
            if inspect.isawaitable(result):
                await result


# Give up on an inbound event after this many failed dispatch attempts and ack it
# anyway, so one poison message cannot wedge the whole inbox behind it.
MAX_EVENT_ATTEMPTS = 10

# Bound the failure bookkeeping so a long-lived gateway cannot grow it without limit.
MAX_TRACKED_FAILURES = 256

# Reuse one Idempotency-Key for retries of the same logical send within this window.
SEND_KEY_TTL_SEC = 120.0
MAX_TRACKED_SEND_KEYS = 256

# Skip inline media larger than this; the agent still gets the text and a marker.
MAX_INLINE_MEDIA_BYTES = 100 * 1024 * 1024

MAX_POLL_BACKOFF_SEC = 60.0

# Telegram media class name -> Hermes message type, for media whose kind is not
# derivable from the MIME type alone.
_GEO_MEDIA_TYPES = frozenset({"MessageMediaGeo", "MessageMediaGeoLive", "MessageMediaVenue"})


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    for separator in (",", ";"):
        if separator in text:
            return [item.strip() for item in text.split(separator) if item.strip()]
    return [text]


def _normalize_peer_key(value: Any) -> str:
    """Match the backend's peer normalization so plugin and backend agree.

    Mirrors ``hermes_tg_bridge.client._normalize_peer``: numeric ids stay canonical
    integers, usernames lose a leading ``@`` and fold to lowercase.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if text == "*":
        return "*"
    if text.lower() == "me":
        return "me"
    if text.startswith("@"):
        text = text[1:]
    if text.lstrip("-").isdigit():
        try:
            return str(int(text))
        except ValueError:
            return text
    return text.lower()


@dataclass(frozen=True)
class AdapterConfig:
    bridge_url: str = "http://127.0.0.1:8765"
    api_token: str = ""
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
    def from_env_and_extra(cls, extra: dict[str, Any] | None = None) -> "AdapterConfig":
        extra = extra or {}

        def pick(env_suffix: str, extra_key: str, fallback: Any = None) -> Any:
            env_name = f"HERMES_TG_{env_suffix}"
            if env_name in os.environ:
                return os.environ[env_name]
            if extra_key in extra:
                return extra[extra_key]
            return fallback

        def pick_any(env_suffix: str, extra_keys: list[str], fallback: Any = None) -> Any:
            env_name = f"HERMES_TG_{env_suffix}"
            if env_name in os.environ:
                return os.environ[env_name]
            for key in extra_keys:
                if key in extra:
                    return extra[key]
            return fallback

        def pick_token(fallback: Any = None) -> Any:
            if "HERMES_TG_BRIDGE_API_TOKEN" in os.environ:
                return os.environ["HERMES_TG_BRIDGE_API_TOKEN"]
            if "api_token" in extra:
                return extra["api_token"]
            return fallback

        allow_from = _list(pick_any("ALLOWED_USERS", ["allow_from", "allowed_users"]))
        # An explicitly configured (even empty) write_to means "these peers and no
        # others" — only an absent one falls back to mirroring the read allowlist.
        write_to_raw = pick("WRITE_TO", "write_to")
        write_to = _list(write_to_raw) if write_to_raw is not None else allow_from

        return cls(
            bridge_url=str(pick("BRIDGE_URL", "bridge_url", cls.bridge_url)).rstrip("/"),
            api_token=str(pick_token(cls.api_token)),
            policy_profile=str(pick("POLICY_PROFILE", "policy_profile", cls.policy_profile)),
            allow_from=allow_from,
            deny_from=_list(pick("DENY_FROM", "deny_from")),
            write_to=write_to,
            deny_write_to=_list(pick("DENY_WRITE_TO", "deny_write_to")),
            allow_all_users=_bool(pick("ALLOW_ALL_USERS", "allow_all_users"), cls.allow_all_users),
            mark_read_on_inbound=_bool(
                pick("MARK_READ_ON_INBOUND", "mark_read_on_inbound"),
                cls.mark_read_on_inbound,
            ),
            typing_while_replying=_bool(
                pick("TYPING_WHILE_REPLYING", "typing_while_replying"),
                cls.typing_while_replying,
            ),
            poll_timeout_ms=_int(pick("POLL_TIMEOUT_MS", "poll_timeout_ms"), cls.poll_timeout_ms),
            poll_interval_ms=_int(pick("POLL_INTERVAL_MS", "poll_interval_ms"), cls.poll_interval_ms),
        )


def _parse_retry_after(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        seconds = float(value.strip())
        return min(max(seconds, 0), 60)
    except (ValueError, AttributeError):
        return default


def build_policy_headers(config: AdapterConfig) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.api_token}",
        "X-Hermes-Policy-Profile": config.policy_profile,
    }
    # The backend intersects these with its own policy, so they can only narrow it.
    if config.allow_from:
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
        self.poll_timeout = (config.poll_timeout_ms / 1000.0) + 10.0
        # Writes get their own budget: the backend may sit on a reply delay, a
        # 30s RPC timeout and a FloodWait retry before it answers, and a client
        # timeout there produces a duplicate message on the next attempt.
        self.write_timeout = 90.0
        self.client = httpx.AsyncClient(
            base_url=config.bridge_url,
            timeout=30.0,
            headers=self.headers,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def health(self) -> Any:
        response = await self.client.get("/health", headers=self.headers)
        response.raise_for_status()
        return response.json()

    async def poll_inbox(self, limit: int = 10) -> list[Any]:
        response = await self.client.get(
            "/dm/inbox/poll",
            params={"timeout_ms": self.config.poll_timeout_ms, "limit": limit},
            headers=self.headers,
            timeout=self.poll_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            events = payload.get("events", [])
            return events if isinstance(events, list) else []
        return []

    async def send_message(
        self,
        peer: Any,
        text: str,
        reply_to: Any | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = dict(self.headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = await self.client.post(
            "/send_message",
            json={"peer": peer, "text": text, "reply_to": reply_to},
            headers=headers,
            timeout=self.write_timeout,
        )
        response.raise_for_status()
        return response.json()

    async def ack(self, event: dict[str, Any]) -> Any:
        return await self._post_dm_state("/dm/inbox/ack", event)

    async def mark_read(self, event: dict[str, Any]) -> Any:
        return await self._post_dm_state("/dm/read", event)

    async def send_typing(self, sender_id: Any, sender_username: Any | None = None) -> Any:
        response = await self.client.post(
            "/dm/typing",
            json={"sender_id": sender_id, "sender_username": sender_username},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    async def _post_dm_state(self, path: str, event: dict[str, Any]) -> Any:
        response = await self.client.post(
            path,
            json={
                "sender_id": str(event["sender_id"]),
                "sender_username": event.get("sender_username"),
                "message_id": int(event["id"]),
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()


class TelegramUserAdapter(BasePlatformAdapter):
    platform_name = "telegram_user"

    def __init__(self, config: AdapterConfig | PlatformConfig, bridge: Any | None = None) -> None:
        super().__init__(config=config, platform=Platform(self.platform_name))
        extra = getattr(config, "extra", {}) if not isinstance(config, AdapterConfig) else {}
        self.adapter_config = config if isinstance(config, AdapterConfig) else AdapterConfig.from_env_and_extra(extra)
        self.bridge = bridge or BridgeClient(self.adapter_config)
        self._poll_task: asyncio.Task[Any] | None = None
        self._failed_events: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._send_keys: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._allow_keys = frozenset(
            key for key in (_normalize_peer_key(item) for item in self.adapter_config.allow_from) if key
        )

    @property
    def name(self) -> str:
        return "Telegram User"

    @property
    def enforces_own_access_policy(self) -> bool:
        """The bridge backend gates inbound DMs against its own read allowlist.

        The backend refuses to serve ``/dm/inbox/poll`` without an explicit list of
        allowed senders, so every event that reaches this adapter already passed a
        real allowlist check — not an "open" default. Reported as ``False`` under
        ``allow_all_users`` so the gateway keeps requiring its own explicit opt-in
        rather than treating open access as pre-authorized.
        """
        return not self.adapter_config.allow_all_users

    def _sender_allowed(self, event: dict[str, Any]) -> bool:
        """UX-level filter. The backend policy is the actual trust boundary."""
        if self.adapter_config.allow_all_users or not self._allow_keys:
            return True
        candidates = {
            _normalize_peer_key(event.get("sender_id")),
            _normalize_peer_key(event.get("sender_username")),
        }
        return bool(candidates & self._allow_keys)

    @staticmethod
    def _event_key(event: dict[str, Any]) -> tuple[str, str]:
        return (str(event.get("sender_id", "")), str(event.get("id", "")))

    def _record_failure(self, key: tuple[str, str]) -> int:
        attempts = self._failed_events.pop(key, 0) + 1
        self._failed_events[key] = attempts
        while len(self._failed_events) > MAX_TRACKED_FAILURES:
            self._failed_events.popitem(last=False)
        return attempts

    def _message_type_for(self, event: dict[str, Any]) -> MessageType:
        if not event.get("has_media"):
            return MessageType.TEXT
        media_type = str(event.get("media_type") or "")
        if media_type in _GEO_MEDIA_TYPES:
            return MessageType.LOCATION
        if event.get("is_voice_note"):
            return MessageType.VOICE
        mime = str(event.get("mime_type") or "").lower()
        if media_type == "MessageMediaPhoto" or mime.startswith("image/"):
            return MessageType.PHOTO
        if mime.startswith("video/"):
            return MessageType.VIDEO
        if mime.startswith("audio/"):
            return MessageType.AUDIO
        return MessageType.DOCUMENT

    def event_to_message(self, event: dict[str, Any]) -> MessageEvent:
        text = str(event.get("text") or "")
        if not text and event.get("has_media"):
            media_type = event.get("media_type") or "unknown"
            text = f"[Telegram media: {media_type}]"

        sender_id = str(event.get("sender_id", ""))
        sender_name = event.get("sender_name")
        sender_username = event.get("sender_username")
        display_name = sender_name or sender_username or sender_id
        message_id = str(event["id"])

        source = self.build_source(
            chat_id=sender_id,
            chat_name=display_name,
            chat_type="dm",
            user_id=sender_id,
            user_name=display_name,
            message_id=message_id,
        )
        reply_to = event.get("reply_to_message_id")
        return MessageEvent(
            text=text,
            message_type=self._message_type_for(event),
            source=source,
            raw_message=event,
            message_id=message_id,
            reply_to_message_id=str(reply_to) if reply_to else None,
        )

    async def _attach_media(self, event: dict[str, Any], message: MessageEvent) -> None:
        """Hand the backend's downloaded attachments to Hermes as agent-visible files.

        The backend writes inbound media into its own state directory; Hermes expects
        local paths inside its media cache (sandbox-translated). Copying the bytes
        through ``cache_media_bytes`` is what makes them reachable by the vision and
        file tools.
        """
        paths = event.get("media_paths")
        if not isinstance(paths, list) or not paths:
            single = event.get("media_path")
            paths = [single] if isinstance(single, str) and single.strip() else []
        if not paths:
            return

        mime_type = event.get("mime_type") if isinstance(event.get("mime_type"), str) else ""
        file_name = event.get("file_name") if isinstance(event.get("file_name"), str) else ""

        for raw_path in paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            path = Path(raw_path)
            try:
                stat = await asyncio.to_thread(path.stat)
            except OSError as exc:
                logger.warning("[telegram_user] inbound media unreadable (%s): %s", raw_path, exc)
                continue
            if stat.st_size > MAX_INLINE_MEDIA_BYTES:
                logger.warning(
                    "[telegram_user] skipping %s (%d bytes) — over the %d byte inline limit",
                    raw_path, stat.st_size, MAX_INLINE_MEDIA_BYTES,
                )
                continue
            if cache_media_bytes is None:
                # No Hermes media cache available (standalone/test run): pass the
                # backend path through unchanged rather than dropping the file.
                message.media_urls.append(str(path))
                message.media_types.append(mime_type or "application/octet-stream")
                continue
            try:
                data = await asyncio.to_thread(path.read_bytes)
                cached = await asyncio.to_thread(
                    lambda: cache_media_bytes(data, filename=file_name or path.name, mime_type=mime_type)
                )
            except Exception:
                logger.warning("[telegram_user] failed to cache inbound media %s", raw_path, exc_info=True)
                continue
            if cached is None:
                continue
            message.media_urls.append(cached.path)
            message.media_types.append(cached.media_type)

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        try:
            await self.bridge.health()
        except Exception as exc:
            logger.error("[telegram_user] bridge health check failed: %s", exc)
            self._set_fatal_error("bridge_unavailable", str(exc), retryable=True)
            return False
        self._mark_connected()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "[telegram_user] connected to bridge at %s (reconnect=%s)",
            self.adapter_config.bridge_url, is_reconnect,
        )
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()
        poll_task = self._poll_task
        self._poll_task = None
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        close = getattr(self.bridge, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    async def _poll_loop(self) -> None:
        delay = max(self.adapter_config.poll_interval_ms / 1000.0, 0.1)
        base_delay = min(delay, 5.0)
        backoff = base_delay
        while self._running:
            try:
                events = await self.bridge.poll_inbox()
                for event in events:
                    await self._handle_inbound_event(event)
                backoff = base_delay
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    logger.error("[telegram_user] bridge rejected our token (%s); stopping poll loop", status)
                    self._set_fatal_error("bridge_unauthorized", str(exc), retryable=False)
                    self._mark_disconnected()
                    return
                if status == 429:
                    sleep_time = _parse_retry_after(exc.response.headers.get("Retry-After"), base_delay)
                    logger.warning("[telegram_user] bridge rate-limited the poll; retrying in %ss", sleep_time)
                    await asyncio.sleep(sleep_time)
                    continue
                logger.warning(
                    "[telegram_user] poll failed with HTTP %s; retrying in %ss: %s",
                    status, backoff, _response_detail(exc.response),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_POLL_BACKOFF_SEC)
            except Exception as exc:
                logger.warning("[telegram_user] poll failed (%s); retrying in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_POLL_BACKOFF_SEC)

    async def _force_ack(self, event: dict[str, Any], reason: str) -> None:
        """Ack an event we will not dispatch, so it stops being re-polled forever."""
        try:
            await self.bridge.ack(event)
        except Exception:
            logger.warning(
                "[telegram_user] could not ack skipped message %s (%s)",
                self._event_key(event), reason, exc_info=True,
            )

    async def _handle_inbound_event(self, event: dict[str, Any]) -> None:
        key = self._event_key(event)
        if not self._sender_allowed(event):
            logger.info("[telegram_user] dropping message %s: sender not in allow_from", key)
            await self._force_ack(event, "sender not in allow_from")
            return

        try:
            message = self.event_to_message(event)
            await self._attach_media(event, message)
            await self.handle_message(message)
            await self.bridge.ack(event)
        except Exception as exc:
            attempts = self._record_failure(key)
            if attempts < MAX_EVENT_ATTEMPTS:
                logger.warning(
                    "[telegram_user] message %s failed (attempt %d/%d): %s",
                    key, attempts, MAX_EVENT_ATTEMPTS, exc,
                )
                return
            logger.error(
                "[telegram_user] message %s failed %d times; acking it to unblock the inbox",
                key, attempts, exc_info=True,
            )
            await self._force_ack(event, "attempt limit reached")
            self._failed_events.pop(key, None)
            return

        self._failed_events.pop(key, None)
        if self.adapter_config.mark_read_on_inbound:
            try:
                await self.bridge.mark_read(event)
            except Exception:
                logger.warning("[telegram_user] read receipt failed for %s", key, exc_info=True)

    def _idempotency_key(self, chat_id: Any, content: str, reply_to: Any) -> str:
        """Stable key across retries of one logical send, fresh for a genuine repeat.

        Hermes retries a failed ``send()`` by calling it again with the same
        arguments, so the key has to be derived from those arguments rather than
        generated per call — otherwise a send that timed out client-side but landed
        in Telegram gets delivered twice. The window expires so that deliberately
        repeating the same text later is not swallowed as a duplicate.
        """
        loop_time = asyncio.get_event_loop().time()
        signature = hashlib.sha256(
            f"{chat_id}\x00{reply_to}\x00{content}".encode("utf-8", "replace")
        ).hexdigest()[:32]
        cached = self._send_keys.get(signature)
        if cached is not None and cached[1] > loop_time:
            return cached[0]
        key = f"{signature}-{uuid.uuid4().hex[:8]}"
        self._send_keys[signature] = (key, loop_time + SEND_KEY_TTL_SEC)
        self._send_keys.move_to_end(signature)
        while len(self._send_keys) > MAX_TRACKED_SEND_KEYS:
            self._send_keys.popitem(last=False)
        return key

    async def send(
        self,
        chat_id: Any,
        content: str,
        reply_to: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        try:
            reply_to_id = int(reply_to) if reply_to is not None and reply_to != "" else None
        except (TypeError, ValueError):
            reply_to_id = None

        try:
            payload = await self.bridge.send_message(
                chat_id,
                content,
                reply_to_id,
                idempotency_key=self._idempotency_key(chat_id, content, reply_to_id),
            )
        except Exception as exc:
            return _send_failure(exc)

        message_id = None
        continuation: tuple = ()
        if isinstance(payload, dict):
            message_id = payload.get("message_id")
            if message_id is None:
                message_id = payload.get("id")
            extra_ids = payload.get("message_ids")
            if isinstance(extra_ids, list) and len(extra_ids) > 1:
                continuation = tuple(str(item) for item in extra_ids[1:] if item is not None)
        return SendResult(
            success=True,
            message_id=str(message_id) if message_id is not None else None,
            raw_response=payload,
            continuation_message_ids=continuation,
        )

    async def send_typing(self, chat_id: Any, metadata: dict[str, Any] | None = None) -> None:
        if not self.adapter_config.typing_while_replying:
            return
        sender_username = metadata.get("sender_username") if metadata else None
        try:
            await self.bridge.send_typing(chat_id, sender_username)
        except Exception as exc:
            # Typing is cosmetic — never let it break the reply path.
            logger.debug("[telegram_user] typing indicator failed for %s: %s", chat_id, exc)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": str(chat_id), "type": "dm"}


def _response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return response.text[:200]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])[:200]
    return str(body)[:200]


def _send_failure(exc: Exception) -> SendResult:
    """Map a bridge failure onto the gateway's retry/classification contract."""
    retry_after: float | None = None
    detail = str(exc)
    retryable = isinstance(exc, (httpx.TimeoutException, httpx.TransportError))

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        detail = f"HTTP {status}: {_response_detail(exc.response)}"
        if status == 429:
            retry_after = _parse_retry_after(exc.response.headers.get("Retry-After"), 5.0)
            retryable = True
        elif status in (502, 503, 504):
            retryable = True

    return SendResult(
        success=False,
        error=detail,
        retryable=retryable,
        retry_after=retry_after,
        error_kind=classify_send_error(exc, detail),
    )


def check_requirements() -> bool:
    """Dependency check. Configuration is validated separately by validate_config."""
    return httpx is not None


def validate_config(config: Any) -> bool:
    extra = getattr(config, "extra", {}) or {}
    adapter_config = AdapterConfig.from_env_and_extra(extra)
    return bool(adapter_config.bridge_url and adapter_config.api_token)


def _env_enablement() -> dict[str, Any] | None:
    api_token = os.getenv("HERMES_TG_BRIDGE_API_TOKEN", "").strip()
    if not api_token:
        return None
    seed: dict[str, Any] = {"api_token": api_token}
    for env_name, extra_key in (
        ("HERMES_TG_BRIDGE_URL", "bridge_url"),
        ("HERMES_TG_POLICY_PROFILE", "policy_profile"),
        ("HERMES_TG_MARK_READ_ON_INBOUND", "mark_read_on_inbound"),
        ("HERMES_TG_TYPING_WHILE_REPLYING", "typing_while_replying"),
        ("HERMES_TG_POLL_TIMEOUT_MS", "poll_timeout_ms"),
        ("HERMES_TG_POLL_INTERVAL_MS", "poll_interval_ms"),
    ):
        value = os.getenv(env_name)
        if value is not None and value != "":
            seed[extra_key] = value
    return seed


def register(ctx: Any) -> Any:
    return ctx.register_platform(
        name=TelegramUserAdapter.platform_name,
        label="Telegram User",
        adapter_factory=lambda cfg: TelegramUserAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["HERMES_TG_BRIDGE_API_TOKEN"],
        install_hint="Run hermes-tg-bridge backend and configure HERMES_TG_BRIDGE_API_TOKEN.",
        env_enablement_fn=_env_enablement,
        allowed_users_env="HERMES_TG_ALLOWED_USERS",
        allow_all_env="HERMES_TG_ALLOW_ALL_USERS",
        max_message_length=4096,
        emoji="📲",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting through a live Telegram user-account DM bridge. "
            "Each Telegram sender id is a separate Hermes chat session."
        ),
    )
