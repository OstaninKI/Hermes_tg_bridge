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
    parse_request_overrides,
    resolve_effective_policy,
    resolve_session_path,
)


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
        self.assertEqual(overrides["read_allow_chat_ids"], ["@User", "-1001"])
        self.assertEqual(overrides["read_deny_chat_ids"], ["spam"])
        self.assertEqual(overrides["write_allow_chat_ids"], ["@allowed"])
        self.assertEqual(overrides["write_deny_chat_ids"], ["-1002"])

    def test_parse_request_overrides_rejects_invalid_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "reply delay"):
            parse_request_overrides({"X-Hermes-Reply-Delay-Sec": "oops"})

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
                {"read_deny_chat_ids": ["blocked"]},
            )

            self.assertEqual(effective["policy_profile"], "owner")
            self.assertEqual(effective["reply_delay_sec"], 2.0)
            self.assertEqual(effective["read_allow_chat_ids"], ["me", "-1001"])
            self.assertEqual(effective["write_allow_chat_ids"], ["me"])
            self.assertEqual(effective["read_deny_chat_ids"], ["blocked"])
            self.assertFalse(effective["sources_auto_discover"])

            shared_effective = resolve_effective_policy(
                {**config, "policy_default_profile": "shared"},
                PolicyStore(str(policy_file)),
            )

            self.assertTrue(shared_effective["sources_auto_discover"])
            self.assertEqual(shared_effective["sources_include_types"], ["channel", "supergroup"])
            self.assertEqual(shared_effective["sources_exclude_usernames"], ["private_feed"])

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
