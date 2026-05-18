from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass, field
from enum import Enum
from types import SimpleNamespace
from typing import Any

import httpx

try:
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import (
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        SendResult,
    )
except ImportError:
    class Platform(str):
        def __new__(cls, value: str) -> "Platform":
            return str.__new__(cls, value)

    @dataclass
    class PlatformConfig:
        extra: dict[str, Any] = field(default_factory=dict)

    class MessageType(Enum):
        TEXT = "text"

    @dataclass
    class MessageEvent:
        text: str
        message_type: MessageType = MessageType.TEXT
        source: Any = None
        raw_message: Any = None
        message_id: str | None = None
        media_urls: list[str] = field(default_factory=list)
        media_types: list[str] = field(default_factory=list)

    @dataclass
    class SendResult:
        success: bool
        message_id: str | None = None
        error: str | None = None
        raw_response: Any = None
        retryable: bool = False

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
            guild_id: str | None = None,
            parent_chat_id: str | None = None,
            message_id: str | None = None,
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
        write_to_raw = pick("WRITE_TO", "write_to")
        write_to = _list(write_to_raw) if write_to_raw is not None else allow_from

        return cls(
            bridge_url=str(pick("BRIDGE_URL", "bridge_url", cls.bridge_url)).rstrip("/"),
            api_token=str(pick_token(cls.api_token)),
            policy_profile=str(pick("POLICY_PROFILE", "policy_profile", cls.policy_profile)),
            allow_from=allow_from,
            deny_from=_list(pick("DENY_FROM", "deny_from")),
            write_to=write_to if write_to else allow_from,
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
        self.client = httpx.AsyncClient(
            base_url=config.bridge_url,
            timeout=(config.poll_timeout_ms / 1000.0) + 5.0,
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
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            events = payload.get("events", [])
            return events if isinstance(events, list) else []
        return []

    async def send_message(self, peer: Any, text: str, reply_to: Any | None = None) -> Any:
        response = await self.client.post(
            "/send_message",
            json={"peer": peer, "text": text, "reply_to": reply_to},
            headers=self.headers,
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

    @property
    def name(self) -> str:
        return "Telegram User"

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
        return MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=event,
            message_id=message_id,
        )

    async def connect(self) -> bool:
        try:
            await self.bridge.health()
        except Exception as exc:
            self._set_fatal_error("bridge_unavailable", str(exc), retryable=True)
            return False
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._mark_connected()
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
        bounded_delay = min(delay, 5.0)
        while self._running:
            try:
                events = await self.bridge.poll_inbox()
                for event in events:
                    await self._handle_inbound_event(event)
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    self._set_fatal_error("bridge_unauthorized", str(exc), retryable=False)
                    self._mark_disconnected()
                    return
                elif exc.response.status_code == 429:
                    retry_after = exc.response.headers.get("Retry-After")
                    sleep_time = _parse_retry_after(retry_after, bounded_delay)
                    await asyncio.sleep(sleep_time)
                else:
                    await asyncio.sleep(bounded_delay)
            except Exception:
                await asyncio.sleep(bounded_delay)

    async def _handle_inbound_event(self, event: dict[str, Any]) -> None:
        if not self.adapter_config.allow_all_users and self.adapter_config.allow_from:
            sender_id = str(event.get("sender_id", ""))
            sender_username = event.get("sender_username")
            if sender_username and sender_username.startswith("@"):
                sender_username = sender_username[1:]

            allow_from_lower = [item.lower() for item in self.adapter_config.allow_from]
            sender_id_match = sender_id in self.adapter_config.allow_from
            username_match = (
                sender_username
                and sender_username.lower() in allow_from_lower
            )

            if not (sender_id_match or username_match):
                return

        message = self.event_to_message(event)
        await self.handle_message(message)
        await self.bridge.ack(event)
        if self.adapter_config.mark_read_on_inbound:
            await self.bridge.mark_read(event)

    async def send(
        self,
        chat_id: Any,
        content: str,
        reply_to: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        try:
            payload = await self.bridge.send_message(
                chat_id,
                content,
                int(reply_to) if reply_to is not None and reply_to != "" else None,
            )
            message_id = None
            if isinstance(payload, dict):
                message_id = payload.get("message_id")
                if message_id is None:
                    message_id = payload.get("id")
            return SendResult(
                success=True,
                message_id=str(message_id) if message_id is not None else None,
                raw_response=payload,
            )
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def send_typing(self, chat_id: Any, metadata: dict[str, Any] | None = None) -> Any:
        sender_username = metadata.get("sender_username") if metadata else None
        return await self.bridge.send_typing(chat_id, sender_username)

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": str(chat_id), "type": "dm"}


def check_requirements() -> bool:
    return bool(os.getenv("HERMES_TG_BRIDGE_API_TOKEN", "").strip())


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
        max_message_length=4096,
        emoji="",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting through a live Telegram user-account DM bridge. "
            "Each Telegram sender id is a separate Hermes chat session."
        ),
    )
