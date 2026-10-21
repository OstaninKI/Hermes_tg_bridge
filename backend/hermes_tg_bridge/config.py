"""Configuration from environment, policy files, and request overrides."""

import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Mapping

HEADER_POLICY_PROFILE = "x-hermes-policy-profile"
HEADER_REPLY_DELAY_SEC = "x-hermes-reply-delay-sec"
HEADER_REPLY_DELAY_MAX_SEC = "x-hermes-reply-delay-max-sec"
HEADER_ALLOW_FROM = "x-hermes-allow-from"
HEADER_DENY_FROM = "x-hermes-deny-from"
HEADER_WRITE_TO = "x-hermes-write-to"
HEADER_DENY_WRITE_TO = "x-hermes-deny-write-to"


def _get_int(key: str, default: int | None = None) -> int | None:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(key: str, default: float | None = None) -> float | None:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _float_or(key: str, default: float) -> float:
    """Env float with a default that survives an explicit zero.

    ``_get_float(key) or default`` silently rewrites a deliberate ``0`` into the
    default, which made e.g. ``TELEGRAM_REPLY_DELAY_SEC=0`` unsettable.
    """
    value = _get_float(key)
    return default if value is None else value


def _int_or(key: str, default: int) -> int:
    value = _get_int(key)
    return default if value is None else value


def _get_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _list_from_env(key: str) -> list[str]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _get_header(headers: Mapping[str, str], key: str) -> str | None:
    for header_name, value in headers.items():
        if header_name.lower() == key:
            return value
    return None


def _parse_float(raw: str, key: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid {key}: {raw!r}") from exc


def _parse_id_list(raw: str) -> list[str]:
    """Parse comma-separated list of IDs/usernames, strip whitespace, drop empties."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def normalize_peer_key(peer: str | int) -> str:
    """Canonical comparison key for a peer: bare lowercase username or integer id.

    Shared by the policy layer and the Telegram client so a policy file entry
    (``"123456789"``), a request header (``"@Alice"``) and a Telegram entity all
    reduce to the same key. Without this, narrowing a policy through headers
    intersects unequal spellings of the same peer and silently yields nothing.
    """
    value = str(peer).strip()
    if not value:
        return ""
    if value == "*":
        return "*"
    if value.lower() == "me":
        return "me"
    if value.startswith("@"):
        value = value[1:]
    if value.lstrip("-").isdigit():
        try:
            return str(int(value))
        except ValueError:
            return value
    return value.lower()


def _narrow_allowlist(base_allow: list[str] | None, header_allow: list[str]) -> list[str]:
    """Intersect a policy allowlist with a request header allowlist.

    Headers may only *narrow* what the backend policy already permits. When the
    policy places no restriction (``None``) or allows everything (``*``), the
    header list becomes the effective allowlist. Comparison happens on normalized
    keys; the values kept are the policy's own spellings so downstream resolution
    still sees what the operator configured.
    """
    header_keys = {key for key in (normalize_peer_key(item) for item in header_allow) if key}
    if base_allow is None or any(normalize_peer_key(item) == "*" for item in base_allow):
        return list(header_allow)
    return [item for item in base_allow if normalize_peer_key(item) in header_keys]


def _merge_denylist(base_deny: list[str] | None, header_deny: list[str]) -> list[str]:
    """Union of both denylists, deduplicated on normalized keys."""
    merged: list[str] = []
    seen: set[str] = set()
    for item in list(base_deny or []) + list(header_deny):
        key = normalize_peer_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _deep_merge_dicts(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> dict[str, Any]:
    """Load bridge config from environment."""
    api_id = _get_int("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session_path = os.environ.get("TELEGRAM_SESSION_PATH", "").strip()
    session_string = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    listen = os.environ.get("TELEGRAM_BRIDGE_LISTEN", "127.0.0.1:8765").strip()
    reply_delay_sec = _float_or("TELEGRAM_REPLY_DELAY_SEC", 2.0)
    reply_delay_max_sec = _get_float("TELEGRAM_REPLY_DELAY_MAX_SEC")
    allow_chat_ids = _list_from_env("TELEGRAM_ALLOW_CHAT_IDS")
    deny_chat_ids = _list_from_env("TELEGRAM_DENY_CHAT_IDS")
    write_allow_chat_ids = _list_from_env("TELEGRAM_WRITE_ALLOW_CHAT_IDS")
    write_deny_chat_ids = _list_from_env("TELEGRAM_WRITE_DENY_CHAT_IDS")
    api_token = os.environ.get("TELEGRAM_BRIDGE_API_TOKEN", "").strip()
    rpc_timeout_sec = _float_or("TELEGRAM_RPC_TIMEOUT_SEC", 30.0)
    flood_wait_max_sleep_sec = _float_or("TELEGRAM_FLOOD_WAIT_MAX_SLEEP_SEC", 30.0)
    policy_path = os.environ.get("TELEGRAM_POLICY_PATH", "").strip()
    policy_default_profile = os.environ.get("TELEGRAM_POLICY_DEFAULT_PROFILE", "").strip()
    default_state_dir = (
        Path(policy_path).expanduser().resolve().parent
        if policy_path
        else (Path.home() / ".hermes" / "telethon")
    )
    sources_inventory_path = os.environ.get("TELEGRAM_SOURCES_INVENTORY_PATH", "").strip()
    inbox_state_path = os.environ.get("TELEGRAM_INBOX_STATE_PATH", "").strip()
    dm_media_path = os.environ.get("TELEGRAM_DM_MEDIA_PATH", "").strip()
    file_upload_roots = _list_from_env("TELEGRAM_FILE_UPLOAD_ROOTS")
    dm_auto_download_media = _get_bool("TELEGRAM_DM_AUTO_DOWNLOAD_MEDIA", True)
    lock_path = os.environ.get("TELEGRAM_LOCK_PATH", "").strip()
    sources_refresh_sec = _float_or("TELEGRAM_SOURCES_REFRESH_SEC", 300.0)
    sources_dialog_limit = _int_or("TELEGRAM_SOURCES_DIALOG_LIMIT", 500)
    dm_media_retention_days = _float_or("TELEGRAM_DM_MEDIA_RETENTION_DAYS", 7.0)
    dm_inbox_backfill = os.environ.get("TELEGRAM_DM_INBOX_BACKFILL", "unread").strip().lower()
    if dm_inbox_backfill not in {"unread", "none", "all"}:
        dm_inbox_backfill = "unread"

    # A misconfigured proxy fails startup instead of silently connecting directly.
    proxy = None
    proxy_type = os.environ.get("TELEGRAM_PROXY_TYPE", "").strip().lower()
    if proxy_type:
        if proxy_type not in {"socks5", "mtproxy"}:
            raise ValueError(f"Invalid TELEGRAM_PROXY_TYPE: {proxy_type!r} (use socks5 or mtproxy)")
        host = os.environ.get("TELEGRAM_PROXY_HOST", "").strip()
        port = _get_int("TELEGRAM_PROXY_PORT")
        if not host or not port:
            raise ValueError("TELEGRAM_PROXY_TYPE is set but TELEGRAM_PROXY_HOST or TELEGRAM_PROXY_PORT is missing")
        if proxy_type == "socks5":
            if importlib.util.find_spec("python_socks") is None and importlib.util.find_spec("socks") is None:
                raise ValueError("TELEGRAM_PROXY_TYPE=socks5 needs python-socks: pip install './backend[proxy]'")
            proxy = ("socks5", host, port)
        else:
            secret = os.environ.get("TELEGRAM_PROXY_SECRET", "00000000000000000000000000000000").strip()
            proxy = ("mtproxy", host, port, secret)

    resolved_dm_media_path = dm_media_path or str(default_state_dir / "dm_inbox_media")
    if not file_upload_roots:
        file_upload_roots = [resolved_dm_media_path]

    return {
        "api_id": api_id,
        "api_hash": api_hash,
        "session_path": session_path or None,
        "session_string": session_string or None,
        "listen": listen,
        "reply_delay_sec": max(0.0, reply_delay_sec),
        "reply_delay_max_sec": reply_delay_max_sec if reply_delay_max_sec is not None else None,
        "allow_chat_ids": allow_chat_ids,
        "deny_chat_ids": deny_chat_ids,
        "write_allow_chat_ids": write_allow_chat_ids,
        "write_deny_chat_ids": write_deny_chat_ids,
        "api_token": api_token or None,
        "rpc_timeout_sec": max(1.0, rpc_timeout_sec),
        "flood_wait_max_sleep_sec": max(0.0, flood_wait_max_sleep_sec),
        "proxy": proxy,
        "policy_path": policy_path or None,
        "policy_default_profile": policy_default_profile or None,
        "sources_inventory_path": sources_inventory_path or str(default_state_dir / "sources_inventory.json"),
        "inbox_state_path": inbox_state_path or str(default_state_dir / "dm_inbox_state.json"),
        "dm_media_path": resolved_dm_media_path,
        "file_upload_roots": file_upload_roots,
        "dm_auto_download_media": dm_auto_download_media,
        "lock_path": lock_path or str(default_state_dir / "bridge.lock"),
        "sources_refresh_sec": max(0.0, sources_refresh_sec),
        "sources_dialog_limit": min(max(50, sources_dialog_limit), 2000),
        "dm_media_retention_days": max(0.0, dm_media_retention_days),
        "dm_inbox_backfill": dm_inbox_backfill,
    }


def resolve_session_path(session_path: str | None) -> Path | None:
    """Resolve session file path; expand user and make absolute."""
    if not session_path:
        return None
    path = Path(session_path).expanduser().resolve()
    if path.suffix != ".session":
        path = path.with_suffix(path.suffix + ".session" if not path.suffix else ".session")
    return path


class PolicyStore:
    """Reloadable JSON policy store for profile-based access rules."""

    def __init__(self, policy_path: str | None) -> None:
        self._path = Path(policy_path).expanduser().resolve() if policy_path else None
        self._cache: dict[str, Any] = {}
        self._mtime_ns: int | None = None

    def _load(self) -> dict[str, Any]:
        if self._path is None or not self._path.exists():
            self._cache = {}
            self._mtime_ns = None
            return self._cache

        stat = self._path.stat()
        if self._mtime_ns == stat.st_mtime_ns:
            return self._cache

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Policy file {self._path} contains invalid JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ValueError("Policy file must contain a JSON object")
        self._cache = data
        self._mtime_ns = stat.st_mtime_ns
        return self._cache

    def resolve(self, profile_id: str | None) -> dict[str, Any]:
        data = self._load()
        defaults = data.get("defaults", {})
        if not isinstance(defaults, dict):
            raise ValueError("Policy file field 'defaults' must be an object")
        if not profile_id:
            return defaults

        profiles = data.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ValueError("Policy file field 'profiles' must be an object")
        if profile_id not in profiles:
            raise ValueError(f"Unknown policy profile: {profile_id}")

        profile = profiles[profile_id]
        if not isinstance(profile, dict):
            raise ValueError(f"Policy profile {profile_id!r} must be an object")
        return _deep_merge_dicts(defaults, profile)


def parse_request_overrides(headers: Mapping[str, str]) -> dict[str, Any]:
    """Parse optional backend policy overrides from HTTP headers."""
    policy_profile = _get_header(headers, HEADER_POLICY_PROFILE)
    reply_delay_raw = _get_header(headers, HEADER_REPLY_DELAY_SEC)
    reply_delay_max_raw = _get_header(headers, HEADER_REPLY_DELAY_MAX_SEC)
    allow_from_raw = _get_header(headers, HEADER_ALLOW_FROM)
    deny_from_raw = _get_header(headers, HEADER_DENY_FROM)
    write_to_raw = _get_header(headers, HEADER_WRITE_TO)
    deny_write_to_raw = _get_header(headers, HEADER_DENY_WRITE_TO)

    overrides: dict[str, Any] = {}
    if policy_profile is not None:
        overrides["policy_profile"] = policy_profile.strip() or None
    if reply_delay_raw is not None:
        overrides["reply_delay_sec"] = max(0.0, _parse_float(reply_delay_raw, "reply delay"))
    if reply_delay_max_raw is not None:
        overrides["reply_delay_max_sec"] = max(0.0, _parse_float(reply_delay_max_raw, "reply delay max"))
    if allow_from_raw is not None:
        overrides["read_allow_from"] = _parse_id_list(allow_from_raw)
    if deny_from_raw is not None:
        overrides["read_deny_from"] = _parse_id_list(deny_from_raw)
    if write_to_raw is not None:
        overrides["write_allow_to"] = _parse_id_list(write_to_raw)
    if deny_write_to_raw is not None:
        overrides["write_deny_to"] = _parse_id_list(deny_write_to_raw)
    return overrides


def resolve_effective_policy(
    config: dict[str, Any],
    policy_store: PolicyStore,
    request_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge env config, policy file, and request overrides into one effective policy."""
    request_overrides = dict(request_overrides or {})
    profile_id = request_overrides.get("policy_profile")
    if profile_id is None:
        profile_id = config.get("policy_default_profile")

    merged: dict[str, Any] = {
        "policy_profile": profile_id,
        "reply_delay_sec": config["reply_delay_sec"],
        "reply_delay_max_sec": config["reply_delay_max_sec"],
        "read_allow_chat_ids": list(config["allow_chat_ids"]) if config["allow_chat_ids"] else None,
        "read_deny_chat_ids": list(config["deny_chat_ids"]) if config["deny_chat_ids"] else None,
        "write_allow_chat_ids": list(config["write_allow_chat_ids"]),
        "write_deny_chat_ids": list(config["write_deny_chat_ids"]) if config["write_deny_chat_ids"] else None,
        "sources_auto_discover": False,
        "sources_include_types": None,
        "sources_exclude_peers": [],
        "sources_exclude_usernames": [],
    }

    policy_data = policy_store.resolve(profile_id)
    if policy_data:
        read_policy = policy_data.get("read", {})
        write_policy = policy_data.get("write", {})
        sources_policy = policy_data.get("sources", {})
        if (
            not isinstance(read_policy, dict)
            or not isinstance(write_policy, dict)
            or not isinstance(sources_policy, dict)
        ):
            raise ValueError("Policy file read/write/sources sections must be objects")

        if "replyDelaySec" in policy_data:
            merged["reply_delay_sec"] = max(0.0, float(policy_data["replyDelaySec"]))
        if "replyDelayMaxSec" in policy_data and policy_data["replyDelayMaxSec"] is not None:
            merged["reply_delay_max_sec"] = max(0.0, float(policy_data["replyDelayMaxSec"]))

        if "allow" in read_policy:
            merged["read_allow_chat_ids"] = list(read_policy["allow"] or [])
        if "deny" in read_policy:
            merged["read_deny_chat_ids"] = list(read_policy["deny"] or [])
        if "allow" in write_policy:
            merged["write_allow_chat_ids"] = list(write_policy["allow"] or [])
        if "deny" in write_policy:
            merged["write_deny_chat_ids"] = list(write_policy["deny"] or [])
        if "autoDiscover" in sources_policy:
            merged["sources_auto_discover"] = bool(sources_policy["autoDiscover"])
        if "includeTypes" in sources_policy:
            merged["sources_include_types"] = list(sources_policy["includeTypes"] or [])
        if "excludePeers" in sources_policy:
            merged["sources_exclude_peers"] = list(sources_policy["excludePeers"] or [])
        if "excludeUsernames" in sources_policy:
            merged["sources_exclude_usernames"] = list(sources_policy["excludeUsernames"] or [])

    for key in (
        "reply_delay_sec",
        "reply_delay_max_sec",
    ):
        if key in request_overrides:
            merged[key] = request_overrides[key]

    if "read_allow_from" in request_overrides:
        merged["read_allow_chat_ids"] = _narrow_allowlist(
            merged.get("read_allow_chat_ids"), request_overrides["read_allow_from"]
        )

    if "read_deny_from" in request_overrides:
        merged["read_deny_chat_ids"] = _merge_denylist(
            merged.get("read_deny_chat_ids"), request_overrides["read_deny_from"]
        )

    if "write_allow_to" in request_overrides:
        merged["write_allow_chat_ids"] = _narrow_allowlist(
            merged.get("write_allow_chat_ids"), request_overrides["write_allow_to"]
        )

    if "write_deny_to" in request_overrides:
        merged["write_deny_chat_ids"] = _merge_denylist(
            merged.get("write_deny_chat_ids"), request_overrides["write_deny_to"]
        )

    return merged
