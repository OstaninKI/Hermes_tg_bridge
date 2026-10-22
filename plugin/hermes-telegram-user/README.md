# Hermes Telegram User Plugin

Platform name: `telegram_user`

Connects Hermes to a live Telegram user account through the `hermes-tg-bridge` backend. Install and deployment are covered in the [main README](../../README.md).

## Requirements

- a running `hermes-tg-bridge` backend with an authorized Telegram session;
- network access to the backend URL, by default `http://127.0.0.1:8765`;
- the backend's API token;
- read access to the backend's inbound media directory (in the reference layout, the Hermes user joins the `hermes-tg` group).

## Environment variables

- `HERMES_TG_BRIDGE_URL`: backend URL, default `http://127.0.0.1:8765`;
- `HERMES_TG_BRIDGE_API_TOKEN`: bearer token for the backend (required);
- `HERMES_TG_POLICY_PROFILE`: backend policy profile, default `dm_inbox`;
- `HERMES_TG_ALLOWED_USERS`: comma-separated Telegram ids/usernames allowed to reach the agent;
- `HERMES_TG_ALLOW_ALL_USERS`: `true`/`false`, default `false`;
- `HERMES_TG_HOME_CHANNEL`: Telegram id/username for cron `deliver=telegram_user`;
- `HERMES_TG_MARK_READ_ON_INBOUND`: `true`/`false`, default `true`;
- `HERMES_TG_TYPING_WHILE_REPLYING`: `true`/`false`, default `true`;
- `HERMES_TG_POLL_TIMEOUT_MS`: default `25000`;
- `HERMES_TG_POLL_INTERVAL_MS`: default `1500`.

`gateway.platforms.telegram_user.extra` in `config.yaml` can provide the same settings as fallbacks; env vars win.

`HERMES_TG_ALLOWED_USERS` and `HERMES_TG_ALLOW_ALL_USERS` are also registered as the platform's `allowed_users_env` / `allow_all_env`. Hermes applies its own user authorization to them, on top of the adapter's filter. The platform is registered with `allowlist_suppresses_pairing=True`, so when an allowlist is set, Hermes never sends a pairing code to a stranger from the owner's personal account.

## Cron delivery

Set `HERMES_TG_HOME_CHANNEL`, and cron jobs with `deliver=telegram_user` go to that chat.

When no gateway runs in the same process (`hermes cron`), the plugin sends directly through the backend (`standalone_sender_fn`), with the same token and policy headers. Only text is supported for now: attachments in standalone mode return an error instead of being dropped silently. The recipient must be allowed by the backend's `write.allow`.

## Installation / discovery

Hermes discovers a directory plugin by its `plugin.yaml` manifest. It imports the directory as the package `hermes_plugins.<slug>` and calls `register(ctx)` from `__init__.py`.

1. Copy this directory to `~/.hermes/plugins/hermes-telegram-user/` (or `./.hermes/plugins/`), keeping `plugin.yaml`, `__init__.py` and `adapter.py`.
2. Run `hermes plugins enable hermes-telegram-user`.

The import in `__init__.py` must stay relative (`from .adapter import register`). An absolute `from adapter import ...` does not resolve from the package context Hermes loads the plugin in.

## Behaviour notes

- **Media.** The backend downloads attachments. The adapter copies their bytes into Hermes' media cache through `cache_media_bytes` and sets `media_urls`/`media_types`, so Hermes routes them as usual: images to vision, voice to its speech-to-text, video and files as paths the agent can open. Files over 100 MB are skipped and logged.
- **Voice and video circles.** When the event has `can_transcribe` (the account has Premium), the adapter asks the backend's `/transcribe_voice` first and passes the transcript as a bare quoted line, Hermes' own STT format; a voice note then goes as text without its audio. Without a transcript, voice goes to Hermes' STT, and a circle is listed twice, as video and as `audio/mp4`, so Hermes transcribes its speech too. Set `stt.echo_transcripts: false` in Hermes, or every transcript is posted back to the contact from the owner's account.
- **Location, contacts, links.** A location uses Hermes' native Telegram text (coordinates, venue, map link). A shared contact and each hidden link (`text_url`) are appended as `[Telegram contact | …]` and `[Telegram link | …]`. A sender typing `[Telegram …` or `[The user …` sees it rewritten to `[TG …`, so they cannot forge these lines.
- **Failing messages.** If Hermes does not accept a message (`handle_message` raises), it is retried up to 10 times, then acknowledged anyway so the queue keeps moving. Events filtered out by `allow_from` are acknowledged too; otherwise the backend would return them on every poll forever.
- **Duplicate sends.** Every `send()` carries an `Idempotency-Key` that is stable across retries of the same message within 120 seconds, so a retry after a client timeout never delivers twice.
- **Send errors.** `SendResult` carries `retryable`, `retry_after` and `error_kind`, so Hermes decides whether to retry and how long to wait.

## Development

Run the tests from the `plugin/` directory:

- `python -m pytest`: runs against a small shim of the Hermes gateway API.
- `PYTHONPATH=<hermes-agent checkout> python -m pytest`: runs the same suite against the real gateway, including a check that media lands where Hermes' own classifier routes it. Use Python 3.14 with the checkout's `pyproject.toml` dependencies installed; set `HOME` to a scratch directory, since Hermes writes its media cache there.
