from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass, field
from typing import Any

import httpx


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


def build_policy_headers(config: AdapterConfig) -> dict[str, str]:
    allow_from = "*" if config.allow_all_users else ",".join(config.allow_from)
    return {
        "Authorization": f"Bearer {config.api_token}",
        "X-Hermes-Policy-Profile": config.policy_profile,
        "X-Hermes-Allow-From": allow_from,
        "X-Hermes-Deny-From": ",".join(config.deny_from),
        "X-Hermes-Write-To": ",".join(config.write_to or config.allow_from),
        "X-Hermes-Deny-Write-To": ",".join(config.deny_write_to),
    }


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


class TelegramUserAdapter:
    platform_name = "telegram_user"

    def __init__(self, config: AdapterConfig, bridge: Any | None = None) -> None:
        self.config = config
        self.bridge = bridge or BridgeClient(config)
        self._running = False
        self._poll_task: asyncio.Task[Any] | None = None

    def event_to_message(self, event: dict[str, Any]) -> dict[str, Any]:
        text = str(event.get("text") or "")
        if not text and event.get("has_media"):
            media_type = event.get("media_type") or "unknown"
            text = f"[Telegram media: {media_type}]"

        sender_id = str(event.get("sender_id", ""))
        sender_name = event.get("sender_name")
        sender_username = event.get("sender_username")

        return {
            "platform": self.platform_name,
            "chat_id": sender_id,
            "chat_type": "dm",
            "user_id": sender_id,
            "user_name": sender_name,
            "content": text,
            "metadata": {
                "telegram_message_id": int(event["id"]),
                "sender_username": sender_username,
            },
        }

    async def connect(self) -> None:
        await self.bridge.health()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def disconnect(self) -> None:
        self._running = False
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
        delay = max(self.config.poll_interval_ms / 1000.0, 0.1)
        bounded_delay = min(delay, 5.0)
        while self._running:
            try:
                events = await self.bridge.poll_inbox()
                for event in events:
                    await self._handle_inbound_event(event)
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(bounded_delay)

    async def _handle_inbound_event(self, event: dict[str, Any]) -> None:
        message = self.event_to_message(event)
        handler = getattr(self, "handle_message", None)
        if handler is not None:
            result = handler(message)
            if inspect.isawaitable(result):
                await result
        await self.bridge.ack(event)
        if self.config.mark_read_on_inbound:
            await self.bridge.mark_read(event)

    async def send(
        self,
        chat_id: Any,
        content: str,
        reply_to: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
            return {"success": True, "message_id": message_id, "error": None}
        except Exception as exc:
            return {"success": False, "message_id": None, "error": str(exc)}

    async def send_typing(self, chat_id: Any, metadata: dict[str, Any] | None = None) -> Any:
        sender_username = metadata.get("sender_username") if metadata else None
        return await self.bridge.send_typing(chat_id, sender_username)

    @classmethod
    def register(cls, ctx: Any) -> "TelegramUserAdapter":
        extra: dict[str, Any] = {}
        if isinstance(getattr(ctx, "extra", None), dict):
            extra = ctx.extra
        elif isinstance(getattr(ctx, "config", None), dict):
            extra = ctx.config
        config = AdapterConfig.from_env_and_extra(extra)
        adapter = cls(config)
        ctx.register_platform(cls.platform_name, adapter)
        return adapter


def register(ctx: Any) -> TelegramUserAdapter:
    return TelegramUserAdapter.register(ctx)
