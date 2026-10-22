# Hermes Telegram Bridge: Hermes Agent acting on your behalf in your personal Telegram account (not a bot)

> **AI agent asked to install this?** Don't improvise from this README. Read and follow the setup skill step by step:
> <https://raw.githubusercontent.com/OstaninKI/Hermes_tg_bridge/main/skills/telegram-user-bridge-setup/SKILL.md>
>
> - **Hermes:** install it with `hermes skills install <that URL>`, then load the skill `telegram-user-bridge-setup`.
> - **Other agents:** fetch the file and follow it as instructions.
>
> It tells you what to run, what to ask the user and when to wait for them.

Let [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research) talk through your **own Telegram account**, not a bot. People you allow can DM you, and Hermes answers from your account, with read receipts and a typing indicator.

Hermes' built-in Telegram channel needs a bot made with @BotFather, so people have to find and start that bot. This project adds a second channel, `telegram_user`, that logs in as **you** over MTProto ([Telethon](https://codeberg.org/Lonami/Telethon)). Contacts write to you as usual, and the AI assistant replies in the same chat.

**Use it for:** a personal AI assistant that answers your DMs, an auto-reply agent for a small set of contacts, or cron reports that arrive from your own account instead of a bot.

> [!WARNING]
> **Unofficial project, use at your own risk.** It is not affiliated with or endorsed by Telegram or Nous Research.
> - The agent writes **on your behalf, from your account**. Everything it sends counts as yours under Telegram's terms. If recipients report it as spam, Telegram can **limit or ban the account**, and it gives no compensation.
> - Under Telegram's terms, passing your contacts' messages to an AI model needs **their explicit consent**. Tell everyone you allowlist that an AI answers them.
>
> Read [Legal and Telegram terms](#legal-and-telegram-terms) before installing.

## Features

- **Native Hermes platform plugin**, not a wrapper script. DMs show up in the Hermes gateway like any other channel, sessions and memory included.
- **Allowlists both ways**: who may write to the agent and who the agent may write to. Enforced in the backend, so the agent cannot widen them.
- **Behaves like a normal client**: marks messages read, shows "typing…", waits a bit before sending. It never hides read receipts, never fakes your online status and never saves self-destructing media.
- **Ban-safety fuses**: a per-minute send cap and FloodWait handling, so a runaway agent cannot spam from your account.
- **Hardened deployment**: the Telegram session lives under a separate system user; the backend listens on `127.0.0.1` only, behind a bearer token.
- **QR login** with 2FA, no phone code to type into a server.
- **Inbound media**: photos, voice and files are downloaded for Hermes to read.
- **Cron delivery**: Hermes scheduled jobs with `deliver=telegram_user` arrive from your account.
- **Self-install**: a Hermes skill lets the agent set all of this up for you over chat.
- **Extended API** (off by default for the gateway): history, dialogs, contacts, groups, channels, edits and forwards over HTTP.

## How it differs from similar projects

| | This project | Hermes built-in Telegram | Telegram MCP servers (e.g. chigwell/telegram-mcp) |
|---|---|---|---|
| Identity | your account | a bot | your account |
| Who starts the conversation | your contacts DM you, the agent answers | users message the bot | the agent, when it calls a tool |
| Inbound DMs reach the agent on their own | yes | yes | no, the agent has to poll |
| Sender/recipient allowlist enforced outside the agent | yes | sender only | usually no |

```
Telegram  ⇄  backend (Telethon, 127.0.0.1:8765)  ⇄  Hermes plugin "telegram_user"  ⇄  Hermes gateway
```

- **`backend/`**: an HTTP service built on FastAPI and Telethon. It owns the Telegram session and enforces the access policy: who may write to the agent and who the agent may write to.
- **`plugin/hermes-telegram-user/`**: the Hermes platform plugin. It polls the backend for DMs and sends replies back.
- **`skills/telegram-user-bridge-setup/`**: a Hermes skill that lets the agent install all of this for you over chat (see [Agent-assisted install](#agent-assisted-install)).
- **`deploy/`**: example systemd unit, backend policy and Hermes config.
- **`create_telethon_session.sh`**: an interactive login helper, if you prefer creating the session by hand.

## Compatibility

| Component | Supported |
|---|---|
| Hermes Agent | v0.22.4 or newer (older v0.22.x lack `allowlist_suppresses_pairing`); Hermes itself needs Python 3.11–3.14 |
| Backend | Python 3.11–3.14 (CI tests both ends, with the oldest and newest allowed dependencies), Telethon 1.45.x, FastAPI ≥ 0.133, Starlette ≥ 1.3.1 |
| OS | Linux with systemd for the documented deployment; the backend itself runs anywhere Python does |

## Agent-assisted install

If Hermes already runs on the target host and you reach it from a messaging app, it can do the setup for you. It walks you through the few steps only you can do: getting API credentials, scanning a QR code, entering your 2FA password, and restarting the gateway.

The simplest way is to send Hermes this message:

> Install https://github.com/OstaninKI/Hermes_tg_bridge so you can talk through my Telegram account.

Hermes reads this README, finds the skill and follows it. To install the skill yourself instead:

```bash
hermes skills install https://raw.githubusercontent.com/OstaninKI/Hermes_tg_bridge/main/skills/telegram-user-bridge-setup/SKILL.md
```

Then send `/telegram-user-bridge-setup` in your chat with Hermes.

The agent needs the **local** terminal backend (commands must run on the Hermes host, not in a sandbox) and passwordless `sudo` for the system-service layout. Without passwordless `sudo` it asks you to run the few root commands yourself.

## Manual install

The steps below set up the hardened layout from `deploy/`:

| Path | Owner / mode | Contents |
|---|---|---|
| `/opt/hermes-tg-bridge` | `root`, 0755 | code and virtualenv |
| `/etc/hermes-tg-bridge` | `root:hermes-tg`, 0750 | `secret.env` (0640), `policy.json` (0640) |
| `/var/lib/hermes-tg-bridge` | `hermes-tg`, 0700 | session, inbox cursors, lock |
| `/var/lib/hermes-tg-bridge-media` | `hermes-tg:hermes-tg`, 2750 | downloaded inbound media (Hermes reads these) |

The Telegram session is full access to your account. Keep it readable only by `hermes-tg`, never by the user that runs Hermes.

### 1. Get Telegram API credentials

Sign in at <https://my.telegram.org> → **API development tools** → create an app, then note the `api_id` and `api_hash`.

Use your own `api_id`; never borrow one. Telegram's API terms forbid the word "Telegram" in the app title unless it is preceded by "Unofficial", so pick a name like `Hermes bridge`.

### 2. Install the backend

```bash
sudo useradd --system --home-dir /var/lib/hermes-tg-bridge --shell /usr/sbin/nologin hermes-tg
sudo git clone https://github.com/OstaninKI/Hermes_tg_bridge.git /opt/hermes-tg-bridge
sudo python3 -m venv /opt/hermes-tg-bridge/backend/.venv
sudo /opt/hermes-tg-bridge/backend/.venv/bin/pip install '/opt/hermes-tg-bridge/backend[fast]'
sudo install -d -o hermes-tg -g hermes-tg -m 0700 /var/lib/hermes-tg-bridge
sudo install -d -o hermes-tg -g hermes-tg -m 2750 /var/lib/hermes-tg-bridge-media
sudo install -d -o root -g hermes-tg -m 0750 /etc/hermes-tg-bridge
```

`[fast]` adds [cryptg](https://pypi.org/project/cryptg/), native encryption for Telethon that makes media transfers much faster. Drop it if it does not install on your platform. To connect through a SOCKS5 proxy, install `[fast,proxy]` instead (see `TELEGRAM_PROXY_TYPE` below).

### 3. Configure secrets and policy

Generate the bridge token, which is shared with the plugin:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Create `/etc/hermes-tg-bridge/secret.env` (`root:hermes-tg`, mode 0640):

```ini
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_BRIDGE_API_TOKEN=<generated token>
```

Copy [deploy/policy.json.example](deploy/policy.json.example) to `/etc/hermes-tg-bridge/policy.json` (`root:hermes-tg`, mode 0640). Under `profiles.dm_inbox`, list the Telegram ids or `@usernames` that may DM the agent (`read.allow`) and that the agent may reply to (`write.allow`). The backend rejects everyone else.

### 4. Start the backend

```bash
sudo cp /opt/hermes-tg-bridge/deploy/hermes-tg-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-tg-bridge
curl -s http://127.0.0.1:8765/health
```

Without a session yet, `/health` returns `{"status":"needs_reauth", ...}`. That is expected.

### 5. Log in to Telegram (QR)

Log in by QR through the running backend. You need the QR on one screen and the Telegram app on your phone.

```bash
TOKEN=<generated token>
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/auth/qr/start
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/auth/qr   # qr_ascii, qr_png_b64, qr_url
```

1. On your phone, open **Telegram → Settings → Devices → Link Desktop Device** and scan the QR. It rotates about every 30 seconds; fetch `/auth/qr` again if it expired.
2. Check progress with `GET /auth/qr/status`.
3. If it reports `awaiting_password`, send your 2FA password:
   ```bash
   curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"password":"..."}' http://127.0.0.1:8765/auth/qr/2fa
   ```
4. When the status is `done`, `/health` returns `{"status":"ok","connected":true}`.

As an alternative, run `sudo -u hermes-tg sh /opt/hermes-tg-bridge/create_telethon_session.sh /var/lib/hermes-tg-bridge/hermes_user.session` for an interactive phone-and-code login. Then restart the service.

### 6. Install the Hermes plugin

Run these as the user that runs Hermes:

```bash
sudo usermod -aG hermes-tg "$(id -un)"     # read inbound media; takes effect on next login/restart
cp -r /opt/hermes-tg-bridge/plugin/hermes-telegram-user ~/.hermes/plugins/
hermes plugins enable hermes-telegram-user
```

Add to `~/.hermes/.env`:

```ini
HERMES_TG_BRIDGE_API_TOKEN=<generated token>
HERMES_TG_ALLOWED_USERS=123456789,@friend
# optional: where cron jobs with deliver=telegram_user go
HERMES_TG_HOME_CHANNEL=123456789
```

### 7. Restart Hermes and test

```bash
hermes gateway restart
hermes gateway status
```

Have an allowed contact DM your account; Hermes should answer from it. Messages to yourself ("Saved Messages") are not DMs and are ignored.

## Configuration

### Plugin (Hermes side)

Environment variables override `gateway.platforms.telegram_user.extra` in Hermes `config.yaml` (see [deploy/config.yaml.example](deploy/config.yaml.example)).

| Variable | Default | Meaning |
|---|---|---|
| `HERMES_TG_BRIDGE_API_TOKEN` | — (required) | Bearer token, same as the backend's `TELEGRAM_BRIDGE_API_TOKEN` |
| `HERMES_TG_BRIDGE_URL` | `http://127.0.0.1:8765` | Backend URL |
| `HERMES_TG_POLICY_PROFILE` | `dm_inbox` | Backend policy profile |
| `HERMES_TG_ALLOWED_USERS` | — | Comma-separated ids/usernames Hermes accepts (also Hermes' own user allowlist) |
| `HERMES_TG_ALLOW_ALL_USERS` | `false` | Accept every sender the backend lets through (dev only) |
| `HERMES_TG_HOME_CHANNEL` | — | Chat for cron `deliver=telegram_user` |
| `HERMES_TG_MARK_READ_ON_INBOUND` | `true` | Mark DMs read once Hermes accepts them |
| `HERMES_TG_TYPING_WHILE_REPLYING` | `true` | Show "typing…" while Hermes works |
| `HERMES_TG_POLL_TIMEOUT_MS` | `25000` | Long-poll timeout |
| `HERMES_TG_POLL_INTERVAL_MS` | `1500` | Pause between polls |

### Cron delivery

Hermes cron jobs with `deliver=telegram_user` go to `HERMES_TG_HOME_CHANNEL`, or to `gateway.platforms.telegram_user.home_channel` in `config.yaml`. If no gateway runs in the cron process, the plugin sends directly through the backend. Only text is supported, and the recipient must be in the backend `write.allow` list. Details are in the [plugin README](plugin/hermes-telegram-user/README.md#cron-delivery).

### Backend tuning

All optional; set them in the systemd unit or `secret.env`.

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_MAX_SENDS_PER_MIN` | `25` | Hard cap on outgoing sends per process (runaway-agent fuse); a message split into N parts costs N |
| `TELEGRAM_FLOOD_WAIT_MAX_SLEEP_SEC` | `30` | Longest FloodWait the backend sleeps through before returning `429 Retry-After` |
| `TELEGRAM_REPLY_DELAY_SEC` | `2` | Pause before each outgoing send; `0` is honoured |
| `TELEGRAM_IDEMPOTENCY_TTL_SEC` | `600` | Lifetime of the send idempotency cache |
| `TELEGRAM_IDEMPOTENCY_MAX_KEYS` | `1000` | Size of the send idempotency cache |
| `TELEGRAM_DM_INBOX_BACKFILL` | `unread` | Messages from before the first start: `unread`, `none` (only new), `all` (recent history) |
| `TELEGRAM_DM_MEDIA_RETENTION_DAYS` | `7` | Delete downloaded inbound media after N days; `0` disables cleanup |
| `HERMES_QR_PASSWORD_TIMEOUT` | `180` | Seconds the QR login waits for the 2FA password |
| `TELEGRAM_DISABLE_EXTENDED_API` | off | `1` keeps only the endpoints the gateway needs (`/health`, `/me`, `/send_message`, `/dm/*`, `/auth/qr/*`); the rest return `404` |
| `TELEGRAM_PROXY_TYPE` / `_HOST` / `_PORT` / `_SECRET` | — | `socks5` (needs the `[proxy]` extra) or `mtproxy` connection to Telegram. A wrong type or missing host/port stops the backend from starting; it never falls back to a direct connection |

State paths default to the directory of `TELEGRAM_POLICY_PATH`, or `~/.hermes/telethon` if that is unset. Override them with `TELEGRAM_SESSION_PATH`, `TELEGRAM_INBOX_STATE_PATH`, `TELEGRAM_SOURCES_INVENTORY_PATH`, `TELEGRAM_DM_MEDIA_PATH` and `TELEGRAM_LOCK_PATH`, as the example unit does.

## Safety

- **Network:** the backend binds to `127.0.0.1` only and requires the bearer token. Running without one needs an explicit `TELEGRAM_BRIDGE_ALLOW_NO_TOKEN=1`.
- **Access policy:**
  - Read and write allowlists live in the backend policy. The plugin's `X-Hermes-Allow-From` / `X-Hermes-Write-To` headers can only **narrow** them (intersection), never widen them.
  - With `HERMES_TG_ALLOWED_USERS` set, Hermes never sends pairing codes to strangers from your personal account.
- **Send limits:** an app-level rate limit (`TELEGRAM_MAX_SENDS_PER_MIN`) sits on top of FloodWait handling.
- **Session:** the Telegram session is stored in a directory only the backend's service user can read.

## Legal and Telegram terms

This is not legal advice. You run the bridge on your own account, with your own `api_id`, so you are the one bound by Telegram's terms and responsible for what the agent sends. Last checked against Telegram's terms on 2026-10-22. Telegram changes them, so read the originals:
- [Terms of Service](https://telegram.org/tos)
- [API Terms of Service](https://core.telegram.org/api/terms)
- [Content Licensing and AI Scraping](https://telegram.org/tos/content-licensing)

### AI and your contacts' messages

API terms §1.5 and the content-licensing terms forbid using Telegram data to train, fine-tune or **deploy** AI models. The one exception is explicit, informed, affirmative and continued consent from the users concerned, given per chat. An agent that reads DMs and answers them with an LLM comes under this. In practice:

- **Get consent first.** Before you add someone to `read.allow`, tell them an AI answers in this chat and that their messages go to your model provider, and get a clear yes. Remove them when they withdraw it.
- **Stay in consented DMs.** Do not point the bridge at groups or channels, where you cannot get consent from everyone.
- **Do not train on chats.** Never use chat logs to train or fine-tune a model.
- **Check your model provider.** The provider you configure in Hermes has its own terms and data retention. A local model keeps messages on your machine.

The allowlist-only design exists to make this workable: the agent only ever sees chats you chose.

### Acting on your behalf

The bridge is a **third-party Telegram API application**, the same category as unofficial Telegram clients. It is not a bot account, and it does not pretend to be you without your knowledge. It logs in to your account with your own `api_id` and sends replies on your behalf, because you set it up to.

The API terms cover exactly this case. §1.4 forbids actions on behalf of the user *without the user's knowledge and consent*, so the bridge is allowed only because you know about it and agreed. The content-licensing terms let a legitimate third-party client use Telegram content only as far as strictly needed to run the service, and only while it fully complies with the ToS.

What follows from this:

- **You are the user, your contacts are not.** Your consent covers your account. It does not cover the people who write to you, which is why the AI section above asks for their consent.
- **What the agent sends is yours.** The Telegram ToS (no spam, scams or illegal content) applies to every message as if you had typed it.
- **Basic functionality stays intact (§1.3, §1.4).** Some of this is configurable:
  - The bridge marks a message read only once the agent has taken it (`HERMES_TG_MARK_READ_ON_INBOUND`).
  - It shows "typing…" only while the agent works (`HERMES_TG_TYPING_WHILE_REPLYING`).
  - It never hides read receipts or fakes your online status.
  - It never saves self-destructing media or media from chats with an auto-delete timer.
- **Limits and bans still apply.** Telegram's anti-spam judges what an account does, not which app does it. Accounts get limited for flooding, and when recipients report messages as spam. The send cap and FloodWait handling lower that risk; they do not remove it.
  - **No mass messaging or outreach.**
  - **Do not write first** to people who have not written to you.
  - **Keep `write.allow` short**: only people who already talk to you.
- **Prefer a bot if it fits.** If the agent does not need to speak as you, Hermes' built-in bot channel is simpler and carries no risk to your personal account.

### Your contacts' privacy

Messages from your contacts are their personal data. Depending on where you and they live, privacy law such as the GDPR may apply when you store those messages or send them to an AI provider. The bridge deletes downloaded media after `TELEGRAM_DM_MEDIA_RETENTION_DAYS` (7 by default). Message text the agent has seen, however, stays in Hermes' session history and with your model provider. Telegram's API terms (§1.1) also require apps to protect user privacy and follow its [security guidelines](https://core.telegram.org/mtproto/security_guidelines).

### Trademarks and affiliation

Telegram is a trademark of Telegram FZ-LLC. Hermes Agent is a project of Nous Research. This project is independent of both and uses neither logo. "Telegram" in its name only describes what it connects to.

### License and third-party software

This project is [MIT-licensed](LICENSE) and provided "as is", without warranty. Its dependencies are installed from PyPI rather than bundled, and they keep their own licenses:

| Package | License | Source |
|---|---|---|
| Telethon | MIT | [codeberg.org/Lonami/Telethon](https://codeberg.org/Lonami/Telethon) (moved from GitHub, which is archived) |
| FastAPI, Pydantic | MIT | PyPI |
| Starlette, Uvicorn, httpx | BSD-3-Clause | PyPI |
| qrcode | BSD | PyPI |
| Pillow | MIT-CMU | PyPI |
| cryptg (optional, `[fast]`) | CC0-1.0 | PyPI |
| python-socks (optional, `[proxy]`) | Apache-2.0 | PyPI |
| Hermes Agent (not a dependency; the plugin runs inside it) | MIT | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) |

### First start and old messages

On the first `/dm/inbox/poll` for each allowed sender, the backend starts from Telegram's own read state. With the default `TELEGRAM_DM_INBOX_BACKFILL=unread`, the agent sees what is still unread, not the whole recent history. The position is stored in `dm_inbox_state.json`, so this happens once per sender.

### Idempotency

Send endpoints (`send_message`, `send_file`, `send_voice`, `send_sticker`, `send_location`) accept an optional `Idempotency-Key` header. A repeat with the same key within the TTL returns the cached response instead of sending again. The cache is in-memory and per process: it is lost on restart, and multiple instances would need a shared store.

## Extended API

Beyond the gateway's needs, the backend implements more Telegram operations:
- files, voice, stickers and locations;
- editing, deleting and forwarding messages;
- reading history and dialogs;
- managing contacts, groups, channels and permissions.

All of them sit behind bearer auth and the backend policy. Privileged operations also need a write scope that includes `me` or `*`, so a normal DM policy gets `403`. `TELEGRAM_DISABLE_EXTENDED_API=1` turns them all off. The allowed set is an explicit list of core paths, so any new endpoint is blocked by default.
