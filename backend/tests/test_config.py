"""Tests for configuration helpers and policy profiles."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_tg_bridge.auth import resolve_local_session_file, write_session_string
from hermes_tg_bridge.config import (
    PolicyStore,
    load_config,
    normalize_peer_key,
    parse_request_overrides,
    resolve_effective_policy,
    resolve_session_path,
)


def _base_config() -> dict:
    return {
        "reply_delay_sec": 1.0,
        "reply_delay_max_sec": None,
        "allow_chat_ids": [],
        "deny_chat_ids": [],
        "write_allow_chat_ids": [],
        "write_deny_chat_ids": [],
        "policy_default_profile": None,
    }


class TestConfigHelpers(unittest.TestCase):
    def test_resolve_session_path_adds_suffix(self) -> None:
        resolved = resolve_session_path("~/telegram/hermes_user")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.suffix, ".session")

    def test_parse_request_overrides_accepts_hermes_headers(self) -> None:
        overrides = parse_request_overrides(
            {
                "X-Hermes-Policy-Profile": "owner",
                "X-Hermes-Reply-Delay-Sec": "2.5",
                "x-hermes-reply-delay-max-sec": "4",
                "X-Hermes-Allow-From": "@User,-1001",
                "X-Hermes-Deny-From": "spam",
                "X-Hermes-Write-To": "@allowed",
                "X-Hermes-Deny-Write-To": "-1002",
            }
        )

        self.assertEqual(overrides["policy_profile"], "owner")
        self.assertEqual(overrides["reply_delay_sec"], 2.5)
        self.assertEqual(overrides["reply_delay_max_sec"], 4.0)
        self.assertEqual(overrides["read_allow_from"], ["@User", "-1001"])
        self.assertEqual(overrides["read_deny_from"], ["spam"])
        self.assertEqual(overrides["write_allow_to"], ["@allowed"])
        self.assertEqual(overrides["write_deny_to"], ["-1002"])

    def test_parse_request_overrides_rejects_invalid_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "reply delay"):
            parse_request_overrides({"X-Hermes-Reply-Delay-Sec": "oops"})

    def test_parse_request_overrides_handles_empty_id_lists(self) -> None:
        overrides = parse_request_overrides(
            {
                "X-Hermes-Allow-From": "",
                "X-Hermes-Deny-From": "  ,  ,  ",
                "X-Hermes-Write-To": "id1, , id2",
            }
        )

        self.assertEqual(overrides["read_allow_from"], [])
        self.assertEqual(overrides["read_deny_from"], [])
        self.assertEqual(overrides["write_allow_to"], ["id1", "id2"])

    def test_resolve_local_session_file_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = resolve_local_session_file(f"{temp_dir}/nested/hermes_user")
            self.assertTrue(session_file.parent.exists())
            self.assertEqual(session_file.suffix, ".session")

    def test_load_config_defaults_to_hermes_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {}, clear=True), patch(
                "hermes_tg_bridge.config.Path.home", return_value=Path(temp_dir)
            ):
                config = load_config()

        expected_root = Path(temp_dir) / ".hermes" / "telethon"
        self.assertEqual(config["sources_inventory_path"], str(expected_root / "sources_inventory.json"))
        self.assertEqual(config["inbox_state_path"], str(expected_root / "dm_inbox_state.json"))
        self.assertEqual(config["dm_media_path"], str(expected_root / "dm_inbox_media"))
        self.assertEqual(config["lock_path"], str(expected_root / "bridge.lock"))

    def test_write_session_string_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "session.txt"
            write_session_string(str(output), "secret")
            self.assertEqual(output.read_text(encoding="utf-8"), "secret\n")

    def test_policy_store_and_effective_policy_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_file = Path(temp_dir) / "policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "replyDelaySec": 2,
                            "read": {"allow": ["*"], "deny": []},
                            "write": {"allow": [], "deny": []},
                        },
                        "profiles": {
                            "owner": {
                                "read": {"allow": ["me", "-1001"]},
                                "write": {"allow": ["me"]},
                            },
                            "shared": {
                                "read": {"allow": ["-1001", "@news"]},
                                "write": {"allow": []},
                                "sources": {
                                    "autoDiscover": True,
                                    "includeTypes": ["channel", "supergroup"],
                                    "excludeUsernames": ["private_feed"],
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "reply_delay_sec": 1.0,
                "reply_delay_max_sec": None,
                "allow_chat_ids": ["*"],
                "deny_chat_ids": [],
                "write_allow_chat_ids": [],
                "write_deny_chat_ids": [],
                "policy_default_profile": "owner",
            }

            effective = resolve_effective_policy(
                config,
                PolicyStore(str(policy_file)),
                {
                    "reply_delay_sec": 0.25,
                    "read_allow_chat_ids": ["attacker"],
                    "read_deny_chat_ids": ["blocked"],
                    "write_allow_chat_ids": ["attacker"],
                    "write_deny_chat_ids": ["me"],
                },
            )

            self.assertEqual(effective["policy_profile"], "owner")
            self.assertEqual(effective["reply_delay_sec"], 0.25)
            self.assertEqual(effective["read_allow_chat_ids"], ["me", "-1001"])
            self.assertEqual(effective["write_allow_chat_ids"], ["me"])
            self.assertEqual(effective["read_deny_chat_ids"], [])
            self.assertEqual(effective["write_deny_chat_ids"], [])
            self.assertFalse(effective["sources_auto_discover"])

            shared_effective = resolve_effective_policy(
                {**config, "policy_default_profile": "shared"},
                PolicyStore(str(policy_file)),
            )

            self.assertTrue(shared_effective["sources_auto_discover"])
            self.assertEqual(shared_effective["sources_include_types"], ["channel", "supergroup"])
            self.assertEqual(shared_effective["sources_exclude_usernames"], ["private_feed"])

    def test_header_allow_overrides_can_only_narrow_not_expand(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_file = Path(temp_dir) / "policy.json"
            policy_file.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "read": {"allow": ["me", "-1001"]},
                            "write": {"allow": ["me"]},
                        }
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "reply_delay_sec": 1.0,
                "reply_delay_max_sec": None,
                "allow_chat_ids": [],
                "deny_chat_ids": [],
                "write_allow_chat_ids": [],
                "write_deny_chat_ids": [],
                "policy_default_profile": None,
            }

            # X-Hermes-Allow-From / X-Hermes-Write-To must only narrow the
            # policy.json allowlist (intersection), never add chats beyond it.
            effective = resolve_effective_policy(
                config,
                PolicyStore(str(policy_file)),
                {
                    "read_allow_from": ["-1001", "attacker_chat"],
                    "write_allow_to": ["attacker_chat"],
                },
            )

            self.assertEqual(effective["read_allow_chat_ids"], ["-1001"])
            self.assertEqual(effective["write_allow_chat_ids"], [])

    def test_deploy_policy_example_matches_policy_store_schema(self) -> None:
        policy_file = Path(__file__).resolve().parents[2] / "deploy" / "policy.json.example"
        policy_data = json.loads(policy_file.read_text(encoding="utf-8"))

        self.assertIn("defaults", policy_data)
        self.assertIn("profiles", policy_data)
        self.assertNotIn("policy_profiles", policy_data)

        effective = PolicyStore(str(policy_file)).resolve("dm_inbox")

        self.assertIn("read", effective)
        self.assertIn("write", effective)
        self.assertIn("allow", effective["read"])
        self.assertIn("allow", effective["write"])
        self.assertIn("replyDelaySec", effective)
        self.assertNotIn("read_allow_chat_ids", effective)
        self.assertNotIn("write_allow_chat_ids", effective)

    def test_systemd_template_uses_backend_env_names_and_run_subcommand(self) -> None:
        service_file = Path(__file__).resolve().parents[2] / "deploy" / "hermes-tg-bridge.service"
        service = service_file.read_text(encoding="utf-8")

        self.assertIn("EnvironmentFile=/etc/hermes-tg-bridge/secret.env", service)
        self.assertNotIn("Environment=TELEGRAM_BRIDGE_API_TOKEN=", service)
        self.assertIn("Environment=TELEGRAM_POLICY_PATH=", service)
        self.assertNotIn("HERMES_TG_BRIDGE_API_TOKEN", service)
        self.assertNotIn("TELEGRAM_BRIDGE_POLICY_PATH", service)
        self.assertIn("ExecStart=/opt/hermes-tg-bridge/backend/.venv/bin/hermes-tg-bridge run", service)

    def test_unknown_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_file = Path(temp_dir) / "policy.json"
            policy_file.write_text(json.dumps({"profiles": {}}), encoding="utf-8")
            config = {
                "reply_delay_sec": 1.0,
                "reply_delay_max_sec": None,
                "allow_chat_ids": ["*"],
                "deny_chat_ids": [],
                "write_allow_chat_ids": [],
                "write_deny_chat_ids": [],
                "policy_default_profile": None,
            }

            with self.assertRaisesRegex(ValueError, "Unknown policy profile"):
                resolve_effective_policy(
                    config,
                    PolicyStore(str(policy_file)),
                    {"policy_profile": "missing"},
                )

    def test_invalid_policy_json_reports_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_file = Path(temp_dir) / "policy.json"
            policy_file.write_text("{ invalid json", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, str(policy_file)):
                PolicyStore(str(policy_file)).resolve(None)


if __name__ == "__main__":
    unittest.main()


class TestHeaderNarrowingNormalization(unittest.TestCase):
    """Headers may only narrow the policy — and must agree with it on peer identity."""

    def _policy(self, *, allow: list[str], header: str, key: str = "x-hermes-allow-from") -> dict:
        store = PolicyStore(None)
        config = _base_config()
        config["allow_chat_ids"] = allow
        overrides = parse_request_overrides({key: header})
        return resolve_effective_policy(config, store, overrides)

    def test_username_spelling_differences_still_intersect(self) -> None:
        # Policy says "@Alice", the plugin sends "alice": same person.
        policy = self._policy(allow=["@Alice"], header="alice")
        self.assertEqual(policy["read_allow_chat_ids"], ["@Alice"])

    def test_numeric_id_with_padding_still_intersects(self) -> None:
        policy = self._policy(allow=["123456789"], header=" 123456789 ")
        self.assertEqual(policy["read_allow_chat_ids"], ["123456789"])

    def test_header_cannot_add_a_peer_the_policy_never_allowed(self) -> None:
        policy = self._policy(allow=["123"], header="999")
        self.assertEqual(policy["read_allow_chat_ids"], [])

    def test_header_becomes_the_allowlist_when_policy_is_wide_open(self) -> None:
        policy = self._policy(allow=["*"], header="123")
        self.assertEqual(policy["read_allow_chat_ids"], ["123"])

    def test_write_allowlist_narrows_on_normalized_keys_too(self) -> None:
        store = PolicyStore(None)
        config = _base_config()
        config["write_allow_chat_ids"] = ["@Bob"]
        overrides = parse_request_overrides({"x-hermes-write-to": "BOB"})
        policy = resolve_effective_policy(config, store, overrides)
        self.assertEqual(policy["write_allow_chat_ids"], ["@Bob"])

    def test_denylists_merge_without_duplicating_the_same_peer(self) -> None:
        store = PolicyStore(None)
        config = _base_config()
        config["deny_chat_ids"] = ["@Mallory"]
        overrides = parse_request_overrides({"x-hermes-deny-from": "mallory,eve"})
        policy = resolve_effective_policy(config, store, overrides)
        self.assertEqual(len(policy["read_deny_chat_ids"]), 2)
        normalized = {normalize_peer_key(item) for item in policy["read_deny_chat_ids"]}
        self.assertEqual(normalized, {"mallory", "eve"})


class TestNumericEnvDefaults(unittest.TestCase):
    """An explicit 0 must survive; `x or default` used to rewrite it."""

    def test_zero_reply_delay_is_honored(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_REPLY_DELAY_SEC": "0"}, clear=False):
            self.assertEqual(load_config()["reply_delay_sec"], 0.0)

    def test_zero_flood_wait_sleep_is_honored(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_FLOOD_WAIT_MAX_SLEEP_SEC": "0"}, clear=False):
            self.assertEqual(load_config()["flood_wait_max_sleep_sec"], 0.0)

    def test_missing_value_still_falls_back_to_the_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_config()["reply_delay_sec"], 2.0)

    def test_backfill_mode_defaults_to_unread_and_rejects_garbage(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_config()["dm_inbox_backfill"], "unread")
        with patch.dict(os.environ, {"TELEGRAM_DM_INBOX_BACKFILL": "nonsense"}, clear=True):
            self.assertEqual(load_config()["dm_inbox_backfill"], "unread")
        with patch.dict(os.environ, {"TELEGRAM_DM_INBOX_BACKFILL": "none"}, clear=True):
            self.assertEqual(load_config()["dm_inbox_backfill"], "none")
