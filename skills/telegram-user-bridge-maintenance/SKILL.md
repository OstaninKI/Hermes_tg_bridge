---
name: telegram-user-bridge-maintenance
description: "Check and debug hermes-tg-bridge around a Hermes update, or when the Telegram user bridge misbehaves: no replies, a reply from the wrong profile, lost settings, cron results not delivered. Backup before, checklist after, where to look in the logs."
version: 1.0.0
author: OstaninKI
license: MIT
platforms: [linux]
prerequisites:
  commands: [python3, curl, systemctl, journalctl]
metadata:
  hermes:
    tags: [Telegram, Gateway, Plugin, Update, Debug, Maintenance]
---

# Telegram user bridge maintenance

Use this skill **before and after updating Hermes**, and whenever the `telegram_user` channel misbehaves. The install is described by `telegram-user-bridge-setup`; its state file `$HOME/.hermes/tg-bridge-setup.md` lists the contacts, their profiles and the home channel. Read it first.

## What can break, and why

```
Telegram ↔ backend (/opt/hermes-tg-bridge, own venv, systemd) ↔ plugin (in Hermes) → profile_routes → profiles
```

A Hermes update does not touch the backend. It can break the **plugin**: it imports Hermes classes (`gateway.platforms.base`), calls `register_platform()`, and relies on Hermes for routing, allowlists, config and cron delivery. The dangerous failures are quiet ones: the bridge answers, but from the wrong profile, or cron results stop while chats still work.

## Rules

- **Never echo secrets** (bridge token, `api_hash`, anything in `secret.env` or `.env`). Never read the Telegram session file.
- **Never restart the gateway yourself.** The user sends `/restart`.
- **Show, don't fix blindly.** On a failure, show the user the matching log line and what it means (table below). Do not edit plugin code to work around a Hermes API change: that needs a plugin update from the repository.
- Talk to the user in their language.

## Before a Hermes update

1. **Baseline.** Save the last startup line of the plugin and the Hermes version:

   ```bash
   B="$HOME/.hermes/backups/tg-bridge-$(date +%Y%m%d-%H%M)"; mkdir -p "$B"; chmod 700 "$B"
   hermes --version > "$B/hermes-version.txt"
   grep -h "\[telegram_user\] startup" "$HOME/.hermes/logs/"*.log | tail -1 > "$B/startup-line.txt"
   cat "$B/startup-line.txt"
   ```

   No startup line means the plugin is older than this skill: update the plugin first (see [Updating the bridge](#updating-the-bridge)).

2. **Backup state, consistently.** Every profile has its own `state.db`. Copy them with SQLite's backup API (safe while the gateway runs), plus configs:

   ```bash
   cd "$HOME/.hermes" && find . -name state.db -not -path './backups/*' | while read -r db; do
     mkdir -p "$B/$(dirname "$db")"
     python3 -c 'import sqlite3,sys; sqlite3.connect(sys.argv[1]).backup(sqlite3.connect(sys.argv[2]))' "$db" "$B/$db"
   done
   find . \( -name config.yaml -o -name .env \) -not -path './backups/*' -exec cp --parents {} "$B" \;
   chmod -R go-rwx "$B"; ls -R "$B" | head -40
   ```

   Code and state go back **together**: rolling Hermes back after it migrated a database is only safe with these copies.

3. Tell the user the backup path and that they can update now.

## After a Hermes update (or a `/restart`)

Go through all steps, in order. Record each result in a short report for the user.

### 1. Backend alive

```bash
curl -s http://127.0.0.1:8765/health               # {"status":"ok","connected":true}
systemctl is-active hermes-tg-bridge
sudo journalctl -u hermes-tg-bridge -n 30 --no-pager
```

`needs_reauth`: the Telegram session was revoked; run step 6 of the setup skill. The backend does not depend on Hermes, so a failure here is not caused by the update.

### 2. Plugin loaded, same settings

```bash
grep -h "telegram_user" "$HOME/.hermes/logs/"*.log | grep -i "startup\|API changed\|error\|fatal" | tail -10
```

- `API changed, plugin needs an update`: the update renamed or removed something the plugin uses. The plugin refuses to load on purpose. Tell the user; the fix is a plugin update or a Hermes rollback.
- No `startup` line since the restart: the plugin did not start. Check `hermes plugins list` and the gateway log around the restart.
- Compare the new `startup` line with `$B/startup-line.txt` field by field. Any change in `hermes_home`, `token_set`, `allowed_users`, `allow_all`, `home_channel_set`, `policy`, `mark_read` or `typing`, other than `hermes=`, means Hermes now loads config or `.env` differently. Show the user the diff.
- Also diff the configs: `diff "$B/config.yaml" "$HOME/.hermes/config.yaml"`, especially `gateway.multiplex_profiles`, `gateway.profile_routes` and the settings that control quiet mode and intermediate messages.

### 3. Routing: each contact lands in its profile

"It answered" is not enough. Check **which profile** owns the session.

For each profile in the state file, count lines mentioning the contact's numeric id in that profile's `state.db` (read-only, prints only a number):

```bash
count() { python3 -c 'import sqlite3,sys; c=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro",uri=True); print(sum(sys.argv[2] in l for l in c.iterdump()))' "$1" "$2"; }
count "$HOME/.hermes/state.db" <id>                         # default
count "$HOME/.hermes/profiles/<profile>/state.db" <id>       # routed profile
```

Then ask one contact per profile to send a DM. Watch:

```bash
grep -h "\[telegram_user\] inbound\|\[telegram_user\] sent to\|profile_route" "$HOME/.hermes/logs/"*.log | tail -20
```

Expected: `inbound ('<id>', '<msg>') handed to Hermes`, then `sent to <id>`. Re-run `count`: it must grow **only** in the contact's own profile. If it grew in `default` instead, routing broke: check that the route still matches (`platform: telegram_user`, `user_id` as a quoted numeric string) and whether Hermes changed the `profile_routes` format in its changelog.

### 4. Access control

```bash
grep -h "\[telegram_user\]" "$HOME/.hermes/logs/"*.log | grep -i "not in allow_from\|pairing" | tail -10
```

- An allowed contact shows `dropping message ... not in allow_from`: the allowlist did not load (compare `allowed_users=` in the startup line).
- Any line about a **pairing code being sent** to a Telegram user: stop and tell the user immediately. Pairing codes must never go out from their personal account (`allowlist_suppresses_pairing`).

### 5. Cron delivery

Cron uses a different path than chat. With the user's approval, schedule a one-off test job delivered to `telegram_user` (home channel from the state file) and wait for it. Then:

```bash
grep -h "\[telegram_user\] sent to\|standalone (cron) send" "$HOME/.hermes/logs/"*.log | tail -5
sudo journalctl -u hermes-tg-bridge --since "-15min" --no-pager | grep -i send
```

Nothing at the expected time means cron delivery broke while chats may still work. No home channel configured: skip and say so.

### 6. Databases

```bash
cd "$HOME/.hermes" && find . -name state.db -not -path './backups/*' | while read -r db; do
  printf '%s ' "$db"
  python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("pragma integrity_check").fetchone()[0])' "$db"
done
```

Every profile must print `ok`, and each profile from the state file must still have its own `state.db`.

## Log lines

Hermes logs: `$HOME/.hermes/logs/`. Backend logs: `journalctl -u hermes-tg-bridge`. All plugin lines start with `[telegram_user]`.

| Line | Meaning |
|---|---|
| `startup plugin=… hermes=… hermes_home=… …` | Plugin started; settings it sees, no secrets. Compare across updates. |
| `Hermes gateway API changed, plugin needs an update` | Hermes classes the plugin imports are gone or renamed. Plugin not loaded. |
| `Hermes register_platform() API changed` | Hermes no longer accepts an argument the plugin passes. Plugin not loaded. |
| `bridge health check failed` | Backend down or wrong `HERMES_TG_BRIDGE_URL`. |
| `bridge rejected our token` | Token in `.env` differs from `secret.env`. |
| `inbound (…) handed to Hermes` | Message passed to Hermes. Routing is Hermes' job from here. |
| `sent to <id>` / `send to <id> failed` | Reply or cron result left through the live adapter. |
| `standalone (cron) send …` | Cron delivery when no gateway runs in-process. |
| `dropping message … not in allow_from` | Sender not on the plugin allowlist. |
| `message … failed (attempt n/3)` | Hermes raised while handling it; the traceback follows in the log. |

## Rollback

If the update broke the plugin and no plugin update exists yet: roll Hermes back to the version in `$B/hermes-version.txt` the way it was installed, then restore **all** `state.db` files and configs from `$B` with the gateway stopped (the user stops it, or you prepare the commands for them). Never restore code without the matching state, or state without the matching code.

## Updating the bridge

```bash
sudo git -C /opt/hermes-tg-bridge pull --ff-only
sudo /opt/hermes-tg-bridge/backend/.venv/bin/pip install -q '/opt/hermes-tg-bridge/backend[fast]'
sudo systemctl restart hermes-tg-bridge
rm -rf "$HOME/.hermes/plugins/hermes-telegram-user" && cp -r /opt/hermes-tg-bridge/plugin/hermes-telegram-user "$HOME/.hermes/plugins/"
```

Then the user sends `/restart`, and you run [After a Hermes update](#after-a-hermes-update-or-a-restart).
