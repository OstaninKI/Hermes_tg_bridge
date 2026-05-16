import os

from adapter import AdapterConfig, build_policy_headers


def test_env_config_loads_defaults_and_env(monkeypatch):
    monkeypatch.setenv("HERMES_TG_BRIDGE_URL", "http://localhost:9000")
    monkeypatch.setenv("HERMES_TG_API_TOKEN", "secret")
    monkeypatch.setenv("HERMES_TG_ALLOWED_USERS", "alice,bob")
    monkeypatch.setenv("HERMES_TG_POLL_TIMEOUT_MS", "12345")

    config = AdapterConfig.from_env_and_extra({})

    assert config.bridge_url == "http://localhost:9000"
    assert config.api_token == "secret"
    assert config.allow_from == ("alice", "bob")
    assert config.write_to == ("alice", "bob")
    assert config.poll_timeout_ms == 12345
    assert config.policy_profile == "dm_inbox"
    assert config.mark_read_on_inbound is True
    assert config.typing_while_replying is True
    assert config.poll_interval_ms == 1500


def test_extra_config_used_when_env_missing(monkeypatch):
    monkeypatch.delenv("HERMES_TG_BRIDGE_URL", raising=False)
    monkeypatch.delenv("HERMES_TG_API_TOKEN", raising=False)
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
    assert config.allow_from == ("carol",)
    assert config.write_to == ("dave",)
    assert config.poll_interval_ms == 2000


def test_build_policy_headers():
    config = AdapterConfig(
        api_token="token",
        allow_from=("alice", "bob"),
        deny_from=("mallory",),
        write_to=("alice",),
        deny_write_to=("eve",),
    )

    headers = build_policy_headers(config)

    assert headers["Authorization"] == "Bearer token"
    assert headers["X-Hermes-Policy-Profile"] == "dm_inbox"
    assert headers["X-Hermes-Allow-From"] == "alice,bob"
    assert headers["X-Hermes-Deny-From"] == "mallory"
    assert headers["X-Hermes-Write-To"] == "alice"
    assert headers["X-Hermes-Deny-Write-To"] == "eve"
