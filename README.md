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
- request-заголовки `X-Hermes-Allow-From` / `X-Hermes-Write-To` могут только **сузить** backend allowlist (пересечение), но не расширить его сверх policy;
- исходящие отправки ограничены app-level rate-limit (`TELEGRAM_MAX_SENDS_PER_MIN`, default 25) поверх реактивной FloodWait-защиты;
- `mark_read_on_inbound` включен только если это ожидаемое поведение;
- Telegram session/state хранится в выделенной service-owned директории;
- policy должен явно ограничивать read/write peers.

## Backend tuning env

Все опциональны, с безопасными дефолтами. Задаются в systemd `EnvironmentFile`/`Environment`.

- `TELEGRAM_MAX_SENDS_PER_MIN` - жёсткий предел исходящих отправок в минуту на процесс, default `25` (предохранитель от runaway-агента). Длинное сообщение, которое режется на N частей, списывает N единиц лимита;
- `TELEGRAM_FLOOD_WAIT_MAX_SLEEP_SEC` - до скольких секунд FloodWait backend ждёт и ретраит сам, прежде чем вернуть `429 Retry-After`, default `30`;
- `TELEGRAM_REPLY_DELAY_SEC` - пауза перед исходящей отправкой, default `2`. Явный `0` теперь уважается;
- `TELEGRAM_IDEMPOTENCY_TTL_SEC` - TTL кэша идемпотентности отправок, default `600`;
- `TELEGRAM_IDEMPOTENCY_MAX_KEYS` - размер кэша идемпотентности, default `1000`;
- `TELEGRAM_DM_INBOX_BACKFILL` - что делать с сообщениями, пришедшими до первого старта: `unread` (default) - обработать непрочитанные, `none` - только новые, `all` - проиграть недавнюю историю;
- `TELEGRAM_DM_MEDIA_RETENTION_DAYS` - через сколько дней удалять скачанные входящие вложения, default `7`, `0` отключает уборку;
- `HERMES_QR_PASSWORD_TIMEOUT` - таймаут ожидания 2FA-пароля в QR-авторизации, секунды, default `180`;
- `TELEGRAM_DISABLE_EXTENDED_API=1` - оставить только endpoints, нужные Hermes gateway (`/health`, `/me`, `/send_message`, `/dm/*`, `/auth/qr/*`); все остальные отдают `404`.

### Первый запуск и старые сообщения

При первом обращении к `/dm/inbox/poll` для каждого разрешённого отправителя backend
берёт стартовую позицию курсора из состояния прочтения самого Telegram. По умолчанию
(`TELEGRAM_DM_INBOX_BACKFILL=unread`) агент получит ровно то, что осталось непрочитанным,
а не всю недавнюю переписку. Позиция сохраняется в `dm_inbox_state.json`, поэтому
подбор выполняется один раз на отправителя.

### Idempotency

Send-эндпоинты (`send_message`, `send_file`, `send_voice`, `send_sticker`, `send_location`) принимают опциональный заголовок `Idempotency-Key`. Повторный запрос с тем же ключом в пределах TTL возвращает закэшированный ответ и не шлёт сообщение в Telegram повторно. Кэш in-memory per-process (теряется при рестарте; при мультиинстансе нужен shared store).

## Extended API

Backend реализует дополнительные операции Telegram, выходящие за пределы scope gateway (отправка файлов/голоса/стикеров/локации, редактирование/удаление/пересылка сообщений, чтение истории и диалогов, управление контактами, группами, каналами и правами). Все эти endpoints защищены Bearer auth и backend policy; привилегированные операции дополнительно требуют, чтобы write-scope включал `me` или `*`, поэтому при обычной DM-policy они отдают `403`.

Это реализованные, но не входящие в core gateway scope операции, указанные в spec как "Deferred Work". `TELEGRAM_DISABLE_EXTENDED_API=1` убирает их все разом: список разрешённого задан явным перечнем core-путей, поэтому любой новый endpoint по умолчанию попадает под запрет, а не мимо него.
