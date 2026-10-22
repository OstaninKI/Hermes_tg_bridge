---
name: telegram-user-bridge-setup
description: "Install hermes-tg-bridge so Hermes can talk through the user's own Telegram account: backend service, QR login, plugin, restart. Walks the user through the steps only they can do."
version: 1.4.4
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
- **Keep a state file and resume from it.** This install spans several restarts, and you lose your context and shell variables on each one. See [State file](#state-file).
- **Never guess which Hermes profile answers whom.** Ask in step 1. A contact without a route lands in the default profile, the one that usually runs the user's bot.

## State file

Path: `$HOME/.hermes/tg-bridge-setup.md`, mode 0600. It is the same for every Hermes profile. It holds decisions and progress, **never secrets**.

- **At the very start**, before step 0: if the file exists, read it. Tell the user what is already done and which decisions are recorded, then continue from the first unfinished step. Do not ask again for anything already recorded unless the user wants to change it.
- **Before trusting a recorded step**, re-run its check (for example `/health`, `hermes profile list`, the `.env` keys). If the check fails, redo the step.
- **Update the file** right after each user answer and each finished step, before you reply.
- **Shell variables do not survive.** Recreate `H` (step 5) in every command block that uses it. If `/tmp/tg-bridge-token` is gone (reboot, `PrivateTmp`), recreate it from `secret.env`: `sudo sed -n 's/^TELEGRAM_BRIDGE_API_TOKEN=//p' /etc/hermes-tg-bridge/secret.env > /tmp/tg-bridge-token` under `umask 077`.

Format:

```markdown
# hermes-tg-bridge setup state
skill_version: 1.4.4
updated: <ISO date>
last_step_done: <0..8>
hermes_user: <id -un>
current_profile: <profile this chat runs in, "default" for ~/.hermes>
sudo: yes | no
linked_account: <name/@username after step 6>

## Profiles
| contact (numeric id) | label | Hermes profile | new profile? | status |
|---|---|---|---|---|

home_channel: <id or none>

## Decisions
- <date>: <question> -> <user's answer, in their words>
```

Status per contact: `planned`, `profile_ready`, `routed`, `tested`.

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
hermes profile list
sudo -n true && echo SUDO_OK || echo SUDO_NO
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/health
```

- **Not Linux, no systemd, or `hermes` not found in the terminal** (a sandboxed terminal backend): stop. Tell the user this skill needs the local terminal on a Linux/systemd host and point them to the manual install in the repository README.
- **Python older than 3.11:** stop and say so.
- **Port 8765 already answers:** a bridge may already be installed. If the state file covers it, resume. Otherwise ask the user whether to reuse it (do step 1, then the policy part of step 4, then 7 and 8) or stop.
- **Write to the state file:** `hermes_user`, `current_profile` (the profile whose home is `$HERMES_HOME`), `sudo`, and the output of `hermes profile list` as a decision line. Also record any existing `gateway.profile_routes` in `~/.hermes/config.yaml`.

**If `SUDO_NO`:** every block marked *(root)* below goes into one file, `/tmp/hermes-tg-bridge-root.sh`, instead of being run directly. Build that file progressively, with any secrets read from files, never inlined. Show the user its full contents and ask them to run `sudo sh /tmp/hermes-tg-bridge-root.sh` over SSH and reply "done". Then verify the result yourself before continuing.

Tell the user what you are about to do (an install in about 9 steps, roughly 10 minutes of their time). In the same message, give them these warnings (details: the README section "Legal and Telegram terms"):

> "This is an unofficial project. I will write on your behalf, from your account, so Telegram treats everything I send as yours and can limit or ban the account if people report it as spam. Telegram's terms also require each person whose messages reach an AI to explicitly agree to that, so tell everyone you allow that an AI answers them, and get a clear yes first."

Ask whether to proceed. **Wait.**

## Step 1. Contacts and profiles (ask)

How it works: one bridge listens to the user's account. The plugin runs once, in the default profile (`~/.hermes`), which hosts the multiplexed gateway. `gateway.profile_routes` sends each allowed contact to a Hermes profile: each profile has its own memory, persona and sessions. Senders outside the allowlist are dropped by the backend and the plugin and reach no profile. Senders on the allowlist without a route go to the **default** profile.

Ask:

> "Who should be able to talk to me through your Telegram account? List only people who have agreed that an AI answers them. (You cannot test by writing to yourself: Saved Messages are not DMs.)
>
> For each person, which agent (Hermes profile) should answer them? Your profiles now: `<hermes profile list>`. You can pick an existing profile, ask for a new one (it gets its own memory), or use `default`. Note that `default` is the agent that runs your bot, so they would share memory with it."

**Wait.** Then:
- **Numeric ids only.** Routes match the numeric Telegram user id, not the @username. If the user only knows a username, ask them to get the id (for example, forward one of that person's messages to @userinfobot) or to have the person DM the account once, then read `sender_id` from `journalctl -u hermes-tg-bridge` after step 6.
- One person, one profile. Several people may share a profile.
- **Someone else's own Telegram account** (e.g. the wife wants an AI on her account rather than to talk to the user's): that needs a second bridge instance with its own login, which this skill does not cover. Say so and leave it out.
- **A new profile starts clean.** It gets the model `default` uses and that provider's key, but **not** your memory, persona (`SOUL.md`), past sessions or other secrets: those hold the user's private context, and the contact could get it out of the agent by asking. For each new profile ask only:
  > "How should the agent behave with `<contact>` (tone, role, anything it must know or must not do)? I'll write that as the new profile's persona."

  Copy the user's memory into a contact's profile only if the user asks for it on their own. Then first warn them: "`<contact>` will be able to ask the agent about anything in your memory." Record their answer as a decision.

Optionally ask:

> "Should scheduled (cron) results go to one of them, or to someone else? Send a numeric id or 'no'."

Ask which language these contacts speak in voice messages, offering the language of this chat as the default. Hermes' speech-to-text assumes English unless told otherwise; for contacts who mix languages, use auto-detect (`""`), which is less reliable on short clips.

Show the final table back and ask the user to confirm it. **Wait.** Write it and the answers to the state file.

## Step 2. Telegram API credentials (user action)

> "Open https://my.telegram.org, sign in with your phone number, go to **API development tools** and create an app (platform 'Other'; any name and short name, but no word 'Telegram' in them, e.g. 'Hermes bridge'). You'll get an **api_id** (a number) and an **api_hash** (32 hex characters).
>
> Don't send them here: the `api_hash` would stay in my chat history. Put them on this server yourself over SSH:
> `sudo install -m 600 /dev/null /tmp/tg-api && sudo nano /tmp/tg-api` with two lines, `TELEGRAM_API_ID=...` and `TELEGRAM_API_HASH=...`, then reply 'file'."

**Wait.** Validate the file without printing it:

```bash
sudo python3 -c 'import re; d=dict(l.strip().split("=",1) for l in open("/tmp/tg-api") if "=" in l); print("API_OK" if re.fullmatch(r"\d+", d.get("TELEGRAM_API_ID","")) and re.fullmatch(r"[0-9a-f]{32}", d.get("TELEGRAM_API_HASH","")) else "API_BAD")'
```

On `API_BAD`, ask the user to fix the file. If the user sends the values in chat anyway, do not use them: ask them to delete that message and to put the values in the file. They can also revoke the hash by creating a new app on my.telegram.org.

## Step 3. Install the code (root)

```bash
id hermes-tg >/dev/null 2>&1 || sudo useradd --system --home-dir /var/lib/hermes-tg-bridge --shell /usr/sbin/nologin hermes-tg
[ -d /opt/hermes-tg-bridge/.git ] || sudo git clone https://github.com/OstaninKI/Hermes_tg_bridge.git /opt/hermes-tg-bridge
sudo python3 -m venv /opt/hermes-tg-bridge/backend/.venv
sudo /opt/hermes-tg-bridge/backend/.venv/bin/pip install -q '/opt/hermes-tg-bridge/backend[fast]'
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
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`, from `/tmp/tg-api`;
- `TELEGRAM_BRIDGE_API_TOKEN=` followed by the contents of `/tmp/tg-bridge-token`;
- `HERMES_QR_PASSWORD_TIMEOUT=900`, which gives the user 15 minutes for the 2FA step;
- `TELEGRAM_DISABLE_EXTENDED_API=1`: Hermes only needs the core endpoints, and this switches the rest off.

Build it with shell redirection from those files (for example `{ sudo cat /tmp/tg-api; printf 'TELEGRAM_BRIDGE_API_TOKEN=%s\n' "$(cat /tmp/tg-bridge-token)"; echo HERMES_QR_PASSWORD_TIMEOUT=900; echo TELEGRAM_DISABLE_EXTENDED_API=1; } | sudo tee /etc/hermes-tg-bridge/secret.env >/dev/null`), then `sudo chown root:hermes-tg` and `sudo chmod 0640` it. Remove it afterwards with `sudo rm -f /tmp/tg-api`.

Write `/etc/hermes-tg-bridge/policy.json` (`root:hermes-tg`, 0640) with **all** contacts from step 1 in both lists, plus the home channel in `write.allow` if set:

```json
{
  "profiles": {
    "dm_inbox": {
      "read":  { "allow": ["<all contacts...>"], "deny": [] },
      "write": { "allow": ["<all contacts...>", "<home channel>"], "deny": [] }
    }
  }
}
```

Validate it with `python3 -m json.tool`. If the backend already runs (adding contacts later), apply it with `sudo systemctl restart hermes-tg-bridge`.

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
- **`awaiting_password`:** the account has 2FA. **Never ask for the 2FA password in chat**: it would stay in the chat history on this server and in Telegram. The user types it into a hidden prompt over SSH. Send them this command and ask them to reply 'done':
  > "Your account has a cloud password (2FA). Don't send it here. Run this on the server over SSH; it asks for the password without showing it, and it reads the bridge token itself:"

  ```bash
  sudo python3 -c 'import getpass, json, urllib.request as u
  t = next(l.split("=", 1)[1].strip() for l in open("/etc/hermes-tg-bridge/secret.env") if l.startswith("TELEGRAM_BRIDGE_API_TOKEN="))
  body = json.dumps({"password": getpass.getpass("Telegram 2FA password: ")}).encode()
  r = u.Request("http://127.0.0.1:8765/auth/qr/2fa", body, {"Authorization": "Bearer " + t, "Content-Type": "application/json"})
  try: print(u.urlopen(r).read().decode())
  except u.HTTPError as e: print(e.code, e.read().decode())'
  ```

  **Wait.** Then poll the status for up to 20 s. If the user sends the password in chat anyway, do not use it: ask them to delete that message, to consider changing the password, and to run the command above.
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

## Step 7. Plugin, profiles and routes (no user action)

**Plugin, in the default profile only** (`HH=$HOME/.hermes`, whichever profile you run in):

```bash
HH="$HOME/.hermes"
mkdir -p "$HH/plugins" "$HH/backups"
[ -e "$HH/plugins/hermes-telegram-user" ] && mv "$HH/plugins/hermes-telegram-user" "$HH/backups/plugin-$(date +%Y%m%d-%H%M%S)"
cp -r /opt/hermes-tg-bridge/plugin/hermes-telegram-user "$HH/plugins/"
hermes -p default plugins enable hermes-telegram-user
sudo usermod -aG hermes-tg "$(id -un)"        # (root) read inbound media
```

Add these to `$HH/.env`, replacing existing keys rather than duplicating them, and keep the file mode 0600:
- `HERMES_TG_BRIDGE_API_TOKEN`: from `/tmp/tg-bridge-token`, written by redirection, never echoed;
- `HERMES_TG_ALLOWED_USERS`: all contacts, comma-separated;
- `HERMES_TG_HOME_CHANNEL`: only if set.

Then clean up with `rm -f /tmp/tg-bridge-token "$H"`.

**New profiles**, from the table: `hermes profile create <name>` **without `--clone`**. A clone copies the user's memory, persona and the whole `.env` (other channels' tokens too); none of that may reach a contact's profile. Then give the empty profile only what it needs to answer:
- **Model:** use Hermes' own command for a profile's model if it has one (`hermes -p <name> ...`). Otherwise copy only the model/provider section of `~/.hermes/config.yaml` into `~/.hermes/profiles/<name>/config.yaml`.
- **Provider key:** only the key that model needs. Copy that one line from `~/.hermes/.env` into `~/.hermes/profiles/<name>/.env` (mode 0600) by redirection, never echoed. No bot tokens and no `HERMES_TG_*` keys.
- **Persona:** write `~/.hermes/profiles/<name>/SOUL.md` from the answer in step 1.

Copy memory files only for a recorded decision to share them. Check `hermes profile list`, and `hermes -p <name> doctor` if it exists. Do not install a gateway for it: the multiplexed gateway picks new profiles up.

**Routes**, in `$HH/config.yaml`: make sure `gateway.multiplex_profiles` is `true` and add one entry per contact routed to a non-default profile, keeping any existing routes:

```yaml
gateway:
  multiplex_profiles: true
  profile_routes:
    - name: tg-user-<label>
      platform: telegram_user
      user_id: "<numeric id>"
      profile: <profile>
```

Re-read the file to check it is valid YAML and that each `profile` exists. Mark each contact `routed` in the state file.

**Speech-to-text.** Voice messages and video circles are transcribed by Telegram when the account has Premium. Without Premium, or when Telegram fails, the plugin hands the audio to Hermes, so Hermes needs its own speech-to-text: local faster-whisper, offline, no API key, no audio sent anywhere. In the default profile and every routed one (`<lang>` from step 1, e.g. `ru`):

```bash
for p in default <routed profiles>; do
  hermes -p "$p" config set stt.enabled true
  hermes -p "$p" config set stt.provider local        # explicit: Hermes never switches to a cloud STT on its own
  hermes -p "$p" config set stt.local.model base      # ~150 MB, fine on CPU; small/medium for better accuracy
  hermes -p "$p" config set stt.language "<lang>"     # Hermes defaults to "en"
  hermes -p "$p" config set stt.echo_transcripts false
done
```

`stt.echo_transcripts false` matters: Hermes otherwise posts each transcript back into the chat as 🎙️ "...", which here means from the user's own account to the contact.

Then install faster-whisper ahead of time through Hermes' package manager, so the first voice message does not wait for it:

```bash
hermes pm install --extra stt-whisper && hermes pm status | python3 -c '
import json, sys
try: features = json.load(sys.stdin).get("feature_list") or []
except ValueError: features = []  # no receipt: pm status prints plain text
ok = "stt-whisper" in features; print("STT_OK" if ok else "STT_MISSING"); sys.exit(not ok)'
```

`feature_list` in the `hermes pm status` JSON can be long, so parse it rather than `grep` it. Do not check with `python -c "import faster_whisper"`: a bare interpreter does not see PM's environment and reports the package missing when it is installed. The restart in step 8 starts the gateway on the new environment, and its voice test is the real check.

Install failed: tell the user and go on. With `stt.provider local` Hermes installs faster-whisper itself on the first voice message (`security.allow_lazy_installs`, on by default), which can need one more `/restart`; or the user runs `hermes tools` → Speech-to-Text → Local Whisper as the Hermes owner. The model itself (~150 MB for `base`) downloads from huggingface.co on first use. Record the language and `STT_OK` (or not) in the state file.

If Hermes runs as a **user** systemd service (`systemctl --user status hermes-gateway` works), the new group only applies after `sudo systemctl restart "user@$(id -u)"` or a reboot. Tell the user, because otherwise inbound photos and files fail to load.

## Step 8. Restart and test (user action)

> "Everything is installed. Send **/restart** here. I'll go offline for a moment and confirm when I'm back."

**Wait.** After the restart, when the user writes again, read the state file first. Then:

```bash
hermes gateway status
grep -i "telegram_user\|Telegram User\|profile_route" "$HOME/.hermes/logs/"*.log | tail -10
```

Look for the platform being connected and for no `bridge_unauthorized` or `bridge_unavailable` errors. Then:

> "Ask one contact per profile to send a DM to your account. Each should get a reply from your account within a few seconds, from the agent of their profile. Ask one of them to also send a short voice message: the reply should answer what they said."

**Wait** for confirmation. On failure:
- a voice message answered as if not understood: `grep -i "transcri\|whisper" "$HOME/.hermes/logs/"*.log | tail`, then `stt.provider`, `stt.language` and faster-whisper from step 7;
- `journalctl -u hermes-tg-bridge -n 50`;
- the Hermes logs;
- whether the sender's id is in both `policy.json` and `HERMES_TG_ALLOWED_USERS`;
- for a reply from the wrong agent: the sender's `user_id` in `profile_routes`, and the gateway log line for that route.

## Done

Set `last_step_done: 8` in the state file and keep the file: it records the layout for later changes.

Summarize for the user:
- the linked account;
- each contact with its profile;
- the cron home channel, if any.

Also tell them how to change and how to remove things:
- **Adding contacts or moving them between profiles:** ask me to run this skill again; I'll read the state file and only redo steps 1, 4 (policy), 7 and 8.
- **By hand:** a new contact goes into `/etc/hermes-tg-bridge/policy.json`, `HERMES_TG_ALLOWED_USERS` in `~/.hermes/.env` and, for a non-default profile, `gateway.profile_routes` in `~/.hermes/config.yaml`. Then `sudo systemctl restart hermes-tg-bridge` and `/restart`.
- **Unlinking:** terminate the session in Telegram → Settings → Devices, and/or run `sudo systemctl disable --now hermes-tg-bridge`.
- **Hermes updates:** before and after each one, run the `telegram-user-bridge-maintenance` skill (same repository, `skills/telegram-user-bridge-maintenance/SKILL.md`). It backs up state and checks that every contact still reaches its profile. Offer to install it now.
- **Uninstalling:** also remove `/opt/hermes-tg-bridge`, `/etc/hermes-tg-bridge`, `/var/lib/hermes-tg-bridge*`, `~/.hermes/plugins/hermes-telegram-user`, the `tg-user-*` entries in `profile_routes`, and `~/.hermes/tg-bridge-setup.md`.
