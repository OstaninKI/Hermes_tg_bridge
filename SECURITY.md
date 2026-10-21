# Security Policy

This bridge holds a Telegram session, which is full access to someone's personal account. Security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through GitHub: **Security → Report a vulnerability** on this repository ([direct link](https://github.com/OstaninKI/Hermes_tg_bridge/security/advisories/new)).

Include what you found, how to reproduce it, and the impact you expect. You should get a reply within a week.

## Supported versions

Only the latest commit on `main` gets fixes.

## In scope

- Bypassing the backend read/write allowlists, or widening them from the plugin side
- Reaching the backend without the bearer token, or from outside `127.0.0.1`
- Leaking the Telegram session, the bridge token or the API hash
- Path traversal or arbitrary file access through media or session paths
- Making the agent send messages beyond the rate limit

## Never post publicly

Session files (`*.session`), `secret.env`, `TELEGRAM_API_HASH`, bridge tokens and real `policy.json` files. If you leaked your own session, end it in **Telegram → Settings → Devices** right away.
