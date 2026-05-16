# Hermes Telegram User Bridge Design

## Goal

Build a Hermes Agent gateway integration that lets Hermes communicate through a live Telegram user account, not a Telegram bot. The behavior should match the existing OpenClaw Telegram user bridge where practical: one long-running Telethon session, backend-enforced access policy, inbound DM polling, read receipts, typing status, and outbound replies from the same user account.

The first implementation targets direct-message gateway parity. Rich Telegram tools such as source polling, topic reads, invite management, file sending, and contact management remain available in the backend design but are a second layer after the gateway path is working.

## Source Baseline

The OpenClaw bridge has two separable parts:

- A mostly agent-agnostic Python backend using FastAPI and Telethon.
- An OpenClaw-specific TypeScript plugin that registers OpenClaw tools, channel runtime, bindings, and OpenClaw config schema.

For Hermes, the backend is retained and renamed, while the OpenClaw plugin is replaced by a Hermes Python platform plugin following Hermes' documented gateway adapter model.

## Architecture

The system has two runtime processes:

1. `hermes-tg-bridge` backend:
   - owns the Telethon client and Telegram user session;
   - exposes a local HTTP API;
   - enforces read/write policies;
   - tracks inbound DM cursors;
   - handles flood-wait behavior and Telegram session re-authentication.

2. Hermes platform plugin `telegram_user`:
   - registers a Hermes gateway platform via `ctx.register_platform()`;
   - starts a long-poll loop against the backend `/dm/inbox/poll` endpoint;
   - converts inbound Telegram DM events into Hermes `MessageEvent` values and calls `self.handle_message(event)`;
   - implements `send()` by calling backend `/send_message`;
   - implements `send_typing()` by calling backend `/dm/typing`;
   - acknowledges accepted inbound messages through `/dm/inbox/ack` and optionally marks them read through `/dm/read`.

The platform name should be `telegram_user` to avoid collision with Hermes' built-in Telegram bot adapter.

## Repository Layout

```text
backend/
  hermes_tg_bridge/
    __main__.py
    auth.py
    auth_qr.py
    client.py
    config.py
    lock.py
    server.py
    state.py
  tests/
  pyproject.toml
  requirements.txt

plugin/hermes-telegram-user/
  PLUGIN.yaml
  adapter.py
  README.md

deploy/
  hermes-tg-bridge.service
  policy.json.example
  config.yaml.example

create_telethon_session.sh
README.md
```

## Backend Adaptation

Rename the Python package and command:

- package: `openclaw_tg_bridge` -> `hermes_tg_bridge`;
- distribution: `openclaw-tg-bridge` -> `hermes-tg-bridge`;
- console command: `hermes-tg-bridge`.

Rename OpenClaw-specific headers to Hermes-neutral headers:

- `X-OpenClaw-Policy-Profile` -> `X-Hermes-Policy-Profile`;
- `X-OpenClaw-Reply-Delay-Sec` -> `X-Hermes-Reply-Delay-Sec`;
- `X-OpenClaw-Reply-Delay-Max-Sec` -> `X-Hermes-Reply-Delay-Max-Sec`;
- `X-OpenClaw-Allow-From` -> `X-Hermes-Allow-From`;
- `X-OpenClaw-Deny-From` -> `X-Hermes-Deny-From`;
- `X-OpenClaw-Write-To` -> `X-Hermes-Write-To`;
- `X-OpenClaw-Deny-Write-To` -> `X-Hermes-Deny-Write-To`.

Keep the existing policy semantics: reads and writes are separately scoped, writes are denied by default unless explicitly allowed, and a policy profile can be resolved from the request headers.

Default state paths move from `~/.openclaw/telethon` to `~/.hermes/telethon`.

## Hermes Plugin Configuration

The plugin should support environment variables first, with `config.yaml` `extra` values as a secondary path:

- `HERMES_TG_BRIDGE_URL`, default `http://127.0.0.1:8765`;
- `HERMES_TG_BRIDGE_API_TOKEN`;
- `HERMES_TG_POLICY_PROFILE`, default `dm_inbox`;
- `HERMES_TG_ALLOWED_USERS`, comma-separated Telegram user ids or usernames;
- `HERMES_TG_ALLOW_ALL_USERS`, default false;
- `HERMES_TG_MARK_READ_ON_INBOUND`, default true;
- `HERMES_TG_TYPING_WHILE_REPLYING`, default true;
- `HERMES_TG_POLL_TIMEOUT_MS`, default 25000;
- `HERMES_TG_POLL_INTERVAL_MS`, default 1500.

Example Hermes config:

```yaml
plugins:
  enabled:
    - hermes-telegram-user

gateway:
  platforms:
    telegram_user:
      enabled: true
      extra:
        bridge_url: "http://127.0.0.1:8765"
        policy_profile: "dm_inbox"
        allow_from: ["123456789"]
        write_to: ["123456789"]
```

## Inbound Flow

1. Hermes gateway starts the `telegram_user` adapter.
2. The adapter validates bridge connectivity through `/health`.
3. The adapter starts a background polling task:
   - calls `/dm/inbox/poll?timeout_ms=...`;
   - receives zero or more events;
   - filters by configured allowlist if needed;
   - builds a Hermes message source with:
     - `chat_id`: Telegram sender id;
     - `chat_name`: sender display name or id;
     - `chat_type`: `dm`;
     - `user_id`: Telegram sender id;
     - `user_name`: Telegram sender name or username.
   - calls `handle_message(event)`.
4. After dispatching an accepted event, the adapter calls `/dm/inbox/ack`.
5. If configured, it calls `/dm/read` to send Telegram read receipts.

## Outbound Flow

When Hermes replies in the gateway session:

1. Hermes calls adapter `send(chat_id, content, reply_to=None, metadata=None)`.
2. The adapter calls backend `/send_message` with `peer=chat_id` and `text=content`.
3. The backend policy must allow writing to that peer.
4. The adapter returns Hermes `SendResult`.

Long messages remain the backend's responsibility. The OpenClaw backend already splits messages over Telegram's 4096-character limit; retain that behavior.

## Error Handling

The adapter should fail closed:

- if the backend is unreachable, platform connection fails with a clear fatal error;
- if the backend returns `401` or `403`, the adapter surfaces an authorization/configuration error;
- if the backend returns `429`, the adapter backs off using `Retry-After` when available;
- transient poll failures use exponential backoff with a bounded maximum;
- send failures return `SendResult(success=False, error=...)`.

The backend keeps existing conservative flood-wait behavior: short waits may retry once, long waits return `429`.

## Security Model

The trust boundary is the backend policy, not the Hermes prompt. The plugin may filter users for UX, but final read/write enforcement must happen in the backend.

Safe defaults:

- listen on `127.0.0.1`;
- require `TELEGRAM_BRIDGE_API_TOKEN` in deployed setups;
- write allowlist empty by default;
- no `HERMES_TG_ALLOW_ALL_USERS` unless explicitly configured;
- store Telegram session, policy, lock, inventory, and inbox state in a service-owned directory.

## Testing

Backend tests are ported from the OpenClaw bridge and updated for package/header/path renames.

Plugin tests should cover:

- config loading from env and `extra`;
- policy header construction;
- inbound event conversion to `MessageEvent`;
- ack/read calls after accepted inbound messages;
- `send()` success and failure handling;
- poll backoff behavior.

Manual smoke test:

1. Create a Telethon session with `hermes-tg-bridge auth` or `auth-qr`.
2. Run backend locally.
3. Install or symlink plugin into `~/.hermes/plugins/hermes-telegram-user`.
4. Enable plugin and `telegram_user` platform in Hermes config.
5. Start `hermes gateway`.
6. Send a DM to the live Telegram user account from an allowed account.
7. Confirm Hermes receives it and replies from the live user account.

## Deferred Work

After gateway parity works, register optional Hermes tools for richer Telegram operations:

- `telegram_user_get_messages`;
- `telegram_user_list_sources`;
- `telegram_user_sync_sources`;
- `telegram_user_list_topics`;
- `telegram_user_send_file`;
- privileged account/contact/group/channel management tools.

These should reuse the backend HTTP API and remain gated by backend policy profiles.
