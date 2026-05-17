# Hermes Telegram User Plugin

Platform name: `telegram_user`

Этот plugin подключает Hermes к живому Telegram user account через backend `hermes-tg-bridge`.

## Requirements

- запущенный backend `hermes-tg-bridge`;
- доступ к backend URL, по умолчанию `http://127.0.0.1:8765`;
- API token, совпадающий с backend token;
- Telegram user session уже создан локально на стороне backend.

## Environment variables

- `HERMES_TG_BRIDGE_URL` - URL backend, default `http://127.0.0.1:8765`;
- `HERMES_TG_BRIDGE_API_TOKEN` - bearer token для backend;
- `HERMES_TG_POLICY_PROFILE` - policy profile, default `dm_inbox`;
- `HERMES_TG_ALLOWED_USERS` - comma-separated user ids or usernames;
- `HERMES_TG_ALLOW_ALL_USERS` - `true`/`false`, default `false`;
- `HERMES_TG_MARK_READ_ON_INBOUND` - `true`/`false`, default `true`;
- `HERMES_TG_TYPING_WHILE_REPLYING` - `true`/`false`, default `true`;
- `HERMES_TG_POLL_TIMEOUT_MS` - default `25000`;
- `HERMES_TG_POLL_INTERVAL_MS` - default `1500`.

`gateway.platforms.telegram_user.extra` can provide the same settings as fallback values, but env vars win.

## Notes

The plugin is intentionally small. It polls the backend for inbound DMs, forwards accepted messages into Hermes, and sends replies back through the backend. Final read/write enforcement stays in the backend policy layer.
