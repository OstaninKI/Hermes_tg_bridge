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
- `HERMES_TG_ALLOWED_USERS` - список Telegram id/username через запятую;
- `HERMES_TG_ALLOW_ALL_USERS` - `true`/`false`, default `false`;
- `HERMES_TG_MARK_READ_ON_INBOUND` - `true`/`false`, default `true`;
- `HERMES_TG_TYPING_WHILE_REPLYING` - `true`/`false`, default `true`;
- `HERMES_TG_POLL_TIMEOUT_MS` - default `25000`;
- `HERMES_TG_POLL_INTERVAL_MS` - default `1500`.

`gateway.platforms.telegram_user.extra` can provide the same settings as fallback values, but env vars win.

`HERMES_TG_ALLOWED_USERS` / `HERMES_TG_ALLOW_ALL_USERS` дополнительно зарегистрированы
как `allowed_users_env` / `allow_all_env`, поэтому Hermes применяет к ним свою штатную
проверку авторизации пользователя, а не только фильтр внутри адаптера.

## Installation / discovery

Hermes находит directory-based плагин по манифесту `plugin.yaml` и импортирует директорию
как пакет `hermes_plugins.<slug>`, вызывая `register(ctx)` из `__init__.py`. Разместите
директорию плагина в `~/.hermes/plugins/hermes-telegram-user/` (или `./.hermes/plugins/`),
сохранив `plugin.yaml`, `__init__.py` и `adapter.py`.

Импорт внутри `__init__.py` обязан быть относительным (`from .adapter import register`) —
абсолютный `from adapter import ...` не резолвится из пакетного контекста, которым Hermes
загружает плагин.

## Behaviour notes

- **Медиа.** Backend скачивает вложения; адаптер перекладывает их байты в media-кэш Hermes
  через `cache_media_bytes` и проставляет `media_urls`/`media_types`, чтобы файл был виден
  агенту (в т.ч. vision-инструментам). Файлы больше 100 МБ пропускаются с записью в лог.
- **Сбойные сообщения.** Одно сообщение повторяется до 10 раз; после этого оно ack-ается
  принудительно, чтобы очередь не встала. Отфильтрованные по `allow_from` события тоже
  ack-аются — иначе backend отдавал бы их на каждом опросе бесконечно.
- **Дубликаты при отправке.** Каждый `send()` уходит с `Idempotency-Key`, стабильным для
  повторов одного и того же сообщения в пределах 120 секунд, поэтому ретрай после
  клиентского таймаута не приводит ко второй доставке в Telegram.
- **Ошибки отправки.** `SendResult` возвращает `retryable`, `retry_after` и `error_kind`,
  так что база Hermes сама решает, повторять ли попытку и как долго ждать.

## Notes

The plugin is intentionally small. It implements Hermes' `BasePlatformAdapter` plugin contract, polls the backend for inbound DMs, forwards accepted messages into Hermes as `MessageEvent` values, and sends replies back through the backend as `SendResult` values.

Read/write enforcement stays in the backend policy layer; peer allowlists must be configured there, not through plugin request headers. Each inbound DM uses the Telegram sender id as both Hermes `chat_id` and `user_id`, so separate Telegram accounts map to separate Hermes chat sessions.

## Tests

```bash
python3 -m pytest plugin/tests -q
```

Тесты лежат вне директории плагина и загружают его ровно так же, как Hermes
(`spec_from_file_location` + `submodule_search_locations`), поэтому ошибки импорта
пакета ловятся тестами, а не только на живом запуске.
