---
name: telegram-user-bridge-setup
description: "Install hermes-tg-bridge so Hermes can talk through the user's own Telegram account: backend service, QR login, plugin, restart. Walks the user through the steps only they can do."
version: 1.0.0
author: OstaninKI
license: MIT
platforms: [linux]
prerequisites:
  commands: [git, python3, curl, systemctl]
metadata:
  hermes:
    tags: [Telegram, Setup, Gateway, Plugin, Install]
---

# Telegram user bridge setup

You are installing [hermes-tg-bridge](https://github.com/OstaninKI/Hermes_tg_bridge) on the host where you (Hermes) run. It lets you receive and answer DMs sent to the **user's personal Telegram account**. You do the work; the user only does what needs them: credentials, QR scan, 2FA password, approvals and the final restart.

## Rules for the whole flow

- **One step at a time.** Before each step that needs the user, tell them exactly what to do and **end your turn**. Continue only after they reply. Never assume they did it.
- **Never echo secrets.** That covers the bridge token, `api_hash`, the 2FA password and anything in `secret.env`. Do not print them in replies, do not pass them on the command line where `ps` can see them, and do not write them anywhere except the files named below.
- **The session file is full access to the user's account.** Never read it, copy it or change its permissions beyond what is written here.
- **Check after every command.** Run each step, check the result, and on failure show the user the relevant error line and stop. Do not improvise around security settings.
- **Never restart the gateway yourself.** `hermes gateway restart` waits for your own turn to finish and hangs. The user sends `/restart` at the end.
- **Tell the user up front:** "Some commands may need your approval in this chat (`/approve`)."
- **Talk to the user in their language.** The quoted prompts below are templates; translate them.

Fixed layout, used in every command below:

| Path | Owner / mode |
|---|---|
| `/opt/hermes-tg-bridge` (repo + `backend/.venv`) | `root`, 0755 |
| `/etc/hermes-tg-bridge/secret.env`, `policy.json` | `root:hermes-tg`, dir 0750, files 0640 |
| `/var/lib/hermes-tg-bridge` (session, state) | `hermes-tg`, 0700 |
| `/var/lib/hermes-tg-bridge-media` (inbound media) | `hermes-tg:hermes-tg`, 2750 |

## Step 0. Preflight (no user action)

Run and check:

```bash
uname -s; systemctl --version | head -1; python3 --version; git --version; hermes --version
id -un; echo "${HERMES_HOME:-$HOME/.hermes}"
sudo -n true && echo SUDO_OK || echo SUDO_NO
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/health
```

- **Not Linux, no systemd, or `hermes` not found in the terminal** (a sandboxed terminal backend): stop. Tell the user this skill needs the local terminal on a Linux/systemd host and point them to the manual install in the repository README.
- **Python older than 3.10:** stop and say so.
- **Port 8765 already answers:** a bridge may already be installed. Ask the user whether to reuse it (skip to step 5 or 7) or stop.
- **Remember:** `HERMES_USER=$(id -un)`, the Hermes home, and whether you have `SUDO_OK`.

**If `SUDO_NO`:** every block marked *(root)* below goes into one file, `/tmp/hermes-tg-bridge-root.sh`, instead of being run directly. Build that file progressively, with any secrets read from files, never inlined. Show the user its full contents and ask them to run `sudo sh /tmp/hermes-tg-bridge-root.sh` over SSH and reply "done". Then verify the result yourself before continuing.

Tell the user what you are about to do (an install in about 8 steps, roughly 10 minutes of their time), and ask whether to proceed. **Wait.**

## Step 1. Allowed contacts (ask)

> "Who should be able to talk to me through your Telegram account? Send their Telegram @usernames or numeric ids, comma-separated. Only these people will reach me; everyone else is ignored. (You cannot test by writing to yourself: Saved Messages are not DMs.)"

**Wait.** Normalize the answer:
- Numeric ids stay as they are.
- Usernames keep their leading `@`.
- Reject anything else.

Remember the result as `ALLOWED`, a list. Ask once more if it is empty.

Optionally ask:

> "Should scheduled (cron) results go to one of them, or to someone else? Send an id/@username or 'no'."

Remember the answer as `HOME_CHANNEL`.

## Step 2. Telegram API credentials (user action)

> "Open https://my.telegram.org, sign in with your phone number, go to **API development tools** and create an app (any name and short name; platform 'Other'). You'll get an **api_id** (a number) and an **api_hash** (32 hex characters).
>
> You can send them to me here, but they will stay in my chat history on this server. If you'd rather not, put them on this server yourself:
> `sudo install -m 600 /dev/null /tmp/tg-api && sudo nano /tmp/tg-api` with two lines, `TELEGRAM_API_ID=...` and `TELEGRAM_API_HASH=...`, then reply 'file'."

**Wait.** Validate: `api_id` is all digits, `api_hash` matches `^[0-9a-f]{32}$`. If they sent the values in chat, write them in step 4 without echoing them back.

## Step 3. Install the code (root)

```bash
id hermes-tg >/dev/null 2>&1 || sudo useradd --system --home-dir /var/lib/hermes-tg-bridge --shell /usr/sbin/nologin hermes-tg
[ -d /opt/hermes-tg-bridge/.git ] || sudo git clone https://github.com/OstaninKI/Hermes_tg_bridge.git /opt/hermes-tg-bridge
sudo python3 -m venv /opt/hermes-tg-bridge/backend/.venv
sudo /opt/hermes-tg-bridge/backend/.venv/bin/pip install -q /opt/hermes-tg-bridge/backend
sudo install -d -o hermes-tg -g hermes-tg -m 0700 /var/lib/hermes-tg-bridge
sudo install -d -o hermes-tg -g hermes-tg -m 2750 /var/lib/hermes-tg-bridge-media
sudo install -d -o root -g hermes-tg -m 0750 /etc/hermes-tg-bridge
/opt/hermes-tg-bridge/backend/.venv/bin/hermes-tg-bridge --help >/dev/null && echo INSTALL_OK
```

## Step 4. Secrets and policy (root, no user action)

Generate the token into a private file, never into chat output:

```bash
umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > /tmp/tg-bridge-token
```

Write `/etc/hermes-tg-bridge/secret.env` (`root:hermes-tg`, 0640) with these keys:
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`, from step 2 or `/tmp/tg-api`;
- `TELEGRAM_BRIDGE_API_TOKEN=` followed by the contents of `/tmp/tg-bridge-token`;
- `HERMES_QR_PASSWORD_TIMEOUT=900`, which gives the user 15 minutes for the 2FA step;
- `TELEGRAM_DISABLE_EXTENDED_API=1`: Hermes only needs the core endpoints, and this switches the rest off.

Build it with shell redirection from those files (for example `{ sudo cat /tmp/tg-api; printf 'TELEGRAM_BRIDGE_API_TOKEN=%s\n' "$(cat /tmp/tg-bridge-token)"; echo HERMES_QR_PASSWORD_TIMEOUT=900; echo TELEGRAM_DISABLE_EXTENDED_API=1; } | sudo tee /etc/hermes-tg-bridge/secret.env >/dev/null`), then `sudo chown root:hermes-tg` and `sudo chmod 0640` it. If the API values came through chat, first write them to a 0600 `/tmp/tg-api` the same way. Remove it afterwards with `sudo rm -f /tmp/tg-api`.

Write `/etc/hermes-tg-bridge/policy.json` (`root:hermes-tg`, 0640), with `ALLOWED` in both lists, plus `HOME_CHANNEL` in `write.allow` if set:

```json
{
  "profiles": {
    "dm_inbox": {
      "read":  { "allow": ["<ALLOWED...>"], "deny": [] },
      "write": { "allow": ["<ALLOWED...>", "<HOME_CHANNEL>"], "deny": [] }
    }
  }
}
```

Validate it with `python3 -m json.tool`.

## Step 5. Start the backend (root)

```bash
sudo cp /opt/hermes-tg-bridge/deploy/hermes-tg-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now hermes-tg-bridge
sleep 5; curl -s http://127.0.0.1:8765/health
```

Expect `{"status":"needs_reauth",...}`. Anything else: show `sudo journalctl -u hermes-tg-bridge -n 30 --no-pager` (strip secrets) and stop.

For all calls below, read the token from the file into a header file, so it never shows up in `ps`:

```bash
H=$(mktemp); chmod 600 "$H"; printf 'Authorization: Bearer %s\n' "$(cat /tmp/tg-bridge-token)" > "$H"
```

## Step 6. Telegram login by QR (user action, repeatable)

Prepare the user first:

> "Next I'll send you a QR code to link this server as a new device on your Telegram account. You need to **see the QR on one screen and scan it with Telegram on your phone**: for example, open this chat on a computer (Telegram Desktop or web.telegram.org). On the phone, go to **Settings → Devices → Link Desktop Device**. Each code is valid for about 30 seconds. Reply 'ready' when you're set."

**Wait for "ready".** Then:

```bash
curl -s -X POST -H @"$H" http://127.0.0.1:8765/auth/qr/start   # only the first time, or after state "error"
sleep 2
Q="${HERMES_HOME:-$HOME/.hermes}/cache/images/tg-bridge-login-qr.png"; mkdir -p "$(dirname "$Q")"
curl -s -H @"$H" http://127.0.0.1:8765/auth/qr \
  | python3 -c 'import sys,json,base64; d=json.load(sys.stdin); open(sys.argv[1],"wb").write(base64.b64decode(d["qr_png_b64"])); print(d["state"], d["expires_at"])' "$Q"
```

Reply with the image as `MEDIA:<Q>` and:

> "Scan this now in Settings → Devices → Link Desktop Device. Then reply 'done'. If it says the code expired, reply 'again'."

**Wait.** Then check `curl -s -H @"$H" http://127.0.0.1:8765/auth/qr/status`:

- **`done`:** go to step 7.
- **`awaiting_scan`:** the code rotated or wasn't scanned. Fetch `/auth/qr` again (without `start`), send the fresh image, and wait again. After 3 tries, offer the fallback below.
- **`awaiting_password`:** the account has 2FA. Ask:
  > "Your account has a cloud password (2FA). Send it here and I'll submit it. It will stay in my chat history on this server, so delete the message afterwards and consider changing the password later. Or submit it yourself over SSH:
  > `curl -s -X POST -H 'Authorization: Bearer <token from /etc/hermes-tg-bridge/secret.env>' -H 'Content-Type: application/json' -d '{"password":"..."}' http://127.0.0.1:8765/auth/qr/2fa`
  > then reply 'done'."

  **Wait.** If they sent it, submit it with a JSON body read from stdin, never as a command-line argument:
  `python3 -c 'import json,sys; print(json.dumps({"password": sys.stdin.read().rstrip("\n")}))' <<'EOF' | curl -s -X POST -H @"$H" -H 'Content-Type: application/json' --data-binary @- http://127.0.0.1:8765/auth/qr/2fa` with the password on the heredoc line. Do not repeat it in your reply. Then poll the status for up to 20 s.
- **`error`:** show the error.
  - `api_id/api_hash ... invalid`: go back to step 2.
  - A wrong password: ask again.
  - Otherwise, restart with `POST /auth/qr/start`.

**Fallback (no second screen, or QR keeps failing):** ask the user to log in by phone and code over SSH:
`sudo systemctl stop hermes-tg-bridge && sudo -u hermes-tg sh -c 'set -a; . /etc/hermes-tg-bridge/secret.env; /opt/hermes-tg-bridge/backend/.venv/bin/hermes-tg-bridge auth --session-path /var/lib/hermes-tg-bridge/hermes_user.session' && sudo systemctl start hermes-tg-bridge`, then reply "done". Do not ask for the login code in chat: Telegram blocks login codes that get shared in messages.

When done, run `rm -f "$Q"` and verify:

```bash
curl -s http://127.0.0.1:8765/health          # {"status":"ok","connected":true}
curl -s -H @"$H" http://127.0.0.1:8765/me     # the user's account
```

Tell the user which account is linked (name/@username from `/me`) and that a new linked device now appears in Telegram → Settings → Devices; terminating it there cuts the bridge off.

## Step 7. Install the plugin (no user action)

```bash
HH="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HH/plugins" && rm -rf "$HH/plugins/hermes-telegram-user"
cp -r /opt/hermes-tg-bridge/plugin/hermes-telegram-user "$HH/plugins/"
hermes plugins enable hermes-telegram-user
sudo usermod -aG hermes-tg "$(id -un)"        # (root) read inbound media
```

Add these to `$HH/.env`, replacing existing keys rather than duplicating them, and keep the file mode 0600:
- `HERMES_TG_BRIDGE_API_TOKEN`: from `/tmp/tg-bridge-token`, written by redirection, never echoed;
- `HERMES_TG_ALLOWED_USERS`: `ALLOWED`, comma-separated;
- `HERMES_TG_HOME_CHANNEL`: only if set.

Then clean up with `rm -f /tmp/tg-bridge-token "$H"`.

If Hermes runs as a **user** systemd service (`systemctl --user status hermes-gateway` works), the new group only applies after `sudo systemctl restart "user@$(id -u)"` or a reboot. Tell the user, because otherwise inbound photos and files fail to load.

## Step 8. Restart and test (user action)

> "Everything is installed. Send **/restart** here. I'll go offline for a moment and confirm when I'm back."

**Wait.** After the restart, when the user writes again:

```bash
hermes gateway status
grep -i "telegram_user\|Telegram User" "${HERMES_HOME:-$HOME/.hermes}/logs/"*.log | tail -5
```

Look for the platform being connected and for no `bridge_unauthorized` or `bridge_unavailable` errors. Then:

> "Ask one of the allowed contacts to send a DM to your account. I should reply from your account within a few seconds."

**Wait** for confirmation. On failure:
- `journalctl -u hermes-tg-bridge -n 50`;
- the Hermes logs;
- whether the sender's id/username is in both `policy.json` and `HERMES_TG_ALLOWED_USERS`.

## Done

Summarize for the user:
- the linked account;
- the allowed contacts;
- the cron home channel, if any.

Also tell them how to change and how to remove things:
- **Allowed contacts:** edit `/etc/hermes-tg-bridge/policy.json` and `HERMES_TG_ALLOWED_USERS` in `~/.hermes/.env`, then `sudo systemctl restart hermes-tg-bridge` and `/restart`.
- **Unlinking:** terminate the session in Telegram → Settings → Devices, and/or run `sudo systemctl disable --now hermes-tg-bridge`.
- **Uninstalling:** also remove `/opt/hermes-tg-bridge`, `/etc/hermes-tg-bridge`, `/var/lib/hermes-tg-bridge*` and `~/.hermes/plugins/hermes-telegram-user`.
