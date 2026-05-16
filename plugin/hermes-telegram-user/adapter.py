from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

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
    allow_from: tuple[str, ...] = ()
    deny_from: tuple[str, ...] = ()
    write_to: tuple[str, ...] = ()
    deny_write_to: tuple[str, ...] = ()
    allow_all_users: bool = False
    mark_read_on_inbound: bool = True
    typing_while_replying: bool = True
    poll_timeout_ms: int = 25000
    poll_interval_ms: int = 1500

    @classmethod
    def from_env_and_extra(cls, extra: dict[str, Any] | None = None) -> "AdapterConfig":
        extra = extra or {}

        def pick(name: str, fallback: Any = None) -> Any:
            env_name = f"HERMES_TG_{name}"
            if env_name in os.environ:
                return os.environ[env_name]
            return extra.get(name.lower(), extra.get(name, fallback))

        allow_from = _list(pick("ALLOWED_USERS", extra.get("allow_from", extra.get("allowed_users"))))
        write_to_raw = pick("WRITE_TO", extra.get("write_to"))
        write_to = _list(write_to_raw) if write_to_raw is not None else list(allow_from)

        return cls(
            bridge_url=str(pick("BRIDGE_URL", cls.bridge_url)),
            api_token=str(pick("API_TOKEN", cls.api_token)),
            policy_profile=str(pick("POLICY_PROFILE", cls.policy_profile)),
            allow_from=tuple(allow_from),
            deny_from=tuple(_list(pick("DENY_FROM", extra.get("deny_from")))),
            write_to=tuple(write_to if write_to else allow_from),
            deny_write_to=tuple(_list(pick("DENY_WRITE_TO", extra.get("deny_write_to")))),
            allow_all_users=_bool(pick("ALLOW_ALL_USERS", extra.get("allow_all_users")), cls.allow_all_users),
            mark_read_on_inbound=_bool(
                pick("MARK_READ_ON_INBOUND", extra.get("mark_read_on_inbound")),
                cls.mark_read_on_inbound,
            ),
            typing_while_replying=_bool(
                pick("TYPING_WHILE_REPLYING", extra.get("typing_while_replying")),
                cls.typing_while_replying,
            ),
            poll_timeout_ms=_int(pick("POLL_TIMEOUT_MS", extra.get("poll_timeout_ms")), cls.poll_timeout_ms),
            poll_interval_ms=_int(pick("POLL_INTERVAL_MS", extra.get("poll_interval_ms")), cls.poll_interval_ms),
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
        self.client = httpx.AsyncClient(
            base_url=config.bridge_url,
            timeout=(config.poll_timeout_ms / 1000.0) + 5.0,
            headers=build_policy_headers(config),
        )

