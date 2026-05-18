# Hermes Telegram Bridge

Это новый Hermes bridge для Telegram user account. Он не выполняет автоматическую миграцию из старой OpenClaw-сессии или state в `~/.openclaw`. Если вы переносите существующую сессию или state, задайте пути к ним явно через env `TELEGRAM_SESSION_PATH`, `TELEGRAM_POLICY_PATH`, `TELEGRAM_SOURCES_INVENTORY_PATH`, `TELEGRAM_INBOX_STATE_PATH`, `TELEGRAM_DM_MEDIA_PATH` и `TELEGRAM_LOCK_PATH`, а не рассчитывайте на автообнаружение.

## Что входит

- `backend/` - HTTP backend на FastAPI и Telethon;
- `plugin/hermes-telegram-user/` - Hermes platform plugin `telegram_user`;
- `deploy/` - примеры systemd, policy и Hermes config;
- `create_telethon_session.sh` - локальная авторизация Telegram user session.

## Setup

1. Создайте virtualenv для backend и установите зависимости из `backend/`.
2. Сгенерируйте Telegram session локально через `python -m hermes_tg_bridge.auth` или `python -m hermes_tg_bridge.auth_qr`.
3. Сохраните backend policy и config на сервисной машине, например по образцу из `deploy/`.
4. Запустите backend как отдельный сервис.
5. Подключите plugin `hermes-telegram-user` в Hermes и включите platform `telegram_user`.

## Auth

Авторизация Telegram user account выполняется локально один раз и сохраняет Telethon session в сервисный state directory.

Безопасный вариант:

```bash
cd backend
python3 -m hermes_tg_bridge.auth
```

QR-вариант:

```bash
cd backend
python3 -m hermes_tg_bridge.auth_qr
```

Если вы переносите старую OpenClaw session/state, укажите используемые paths явно через backend policy/config и systemd environment. Не полагайтесь на старые значения по умолчанию.

## Run backend

Пример systemd-сервиса: [deploy/hermes-tg-bridge.service](./deploy/hermes-tg-bridge.service)

Проверьте, что backend слушает только `127.0.0.1` и требует API token. Для deployment используйте отдельного service user, отдельный state directory и явно заданный policy file.

## Plugin config

Платформа Hermes: `telegram_user`

Поддерживаемые env vars:

- `HERMES_TG_BRIDGE_URL`
- `HERMES_TG_BRIDGE_API_TOKEN`
- `HERMES_TG_POLICY_PROFILE`
- `HERMES_TG_MARK_READ_ON_INBOUND`
- `HERMES_TG_TYPING_WHILE_REPLYING`
- `HERMES_TG_POLL_TIMEOUT_MS`
- `HERMES_TG_POLL_INTERVAL_MS`

Приоритет конфигурации:

1. env vars;
2. `gateway.platforms.telegram_user.extra` в `config.yaml`.

Пример: [deploy/config.yaml.example](./deploy/config.yaml.example)

## Safety defaults

- backend binds only to `127.0.0.1`;
- API token обязателен для deployed setup;
- read/write allowlists задаются только в backend policy, а не в plugin config;
- `mark_read_on_inbound` включен только если это ожидаемое поведение;
- Telegram session/state хранится в выделенной service-owned директории;
- policy должен явно ограничивать read/write peers.

## Extended API

Backend реализует дополнительные операции Telegram, выходящие за пределы scope gateway (отправка файлов/голоса/стикеров/локации, редактирование/удаление/пересылка сообщений, управление контактами и источниками). Все эти endpoints защищены Bearer auth и backend policy. Это реализованные, но не входящие в core gateway scope операции, указанные в spec как "Deferred Work". Их можно отключить, установив env `TELEGRAM_DISABLE_EXTENDED_API=1`.
