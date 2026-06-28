# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Gadash Bot is a Hebrew-language agricultural work-log system for a contracting company (גד"ש). It is a Flask web app with an embedded Telegram bot. **Google Sheets is the sole persistent data store** — there is no local database.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (dev)
python app.py

# Run with gunicorn (matches production)
gunicorn app:app --bind 0.0.0.0:8080 --workers 1 --threads 2

# Run all tests
pytest tests/ -v

# Run a single test
pytest tests/test_app.py::TestWorkEntry::test_valid_entry -v
```

## Architecture

### Entry point and routing

`app.py` is the single Flask application file. It owns all routes, auth decorators, CSRF protection, and rate limiting. It imports from the `gadash/` package.

On startup, `app.py` launches two daemon threads:
1. `gadash.audit._flush_audit_to_sheets` — batches audit log entries to Sheets every 30 s
2. `gadash.bot.start_telegram_bot` — runs the async Telegram bot event loop (only if `BOT_TOKEN` is set)

### The `gadash/` package

| Module | Responsibility |
|---|---|
| `models.py` | `WorkEntry` dataclass + `COLUMNS` (11 Hebrew column names) + `VALID_TASKS` |
| `sheets.py` | All Google Sheets I/O, in-memory cache (5 min TTL), thread-safe with `_gs_lock` |
| `service.py` | Thin `create_entry()` that calls `append_row_to_gsheet` + `_log_audit` |
| `bot.py` | Telegram ConversationHandler (python-telegram-bot 20.x async API), scheduled daily/weekly broadcasts |
| `audit.py` | Dual-write audit log: immediate write to `audit.log` file, async flush to AuditLog sheet |
| `workers.py` | Worker CRUD; passwords stored as SHA-256 hashes in the Workers sheet |
| `subscribers.py` | Telegram broadcast subscriber list persisted in Subscribers sheet |

### Google Sheets structure

One workbook named **"Gadash Data"** with these worksheets:
- `Sheet1` — main data (11 Hebrew columns, see `COLUMNS` in `models.py`)
- `Settings` — web/worker passwords (key-value pairs)
- `FieldCoords` — GPS coordinates per field name (lat/lng)
- `AuditLog` — action audit trail (ts, action, user, detail)
- `Workers` — worker registry (שם, password_hash, telegram_id)
- `Subscribers` — Telegram chat_ids for broadcasts

### Row indexing

`row_id` in the app is a 0-based DataFrame index. Sheet row = `row_id + 2` (row 1 is the header). This offset is applied in every `sheets.py` write function.

### Auth

Two session roles, set independently:
- Manager: `session["logged_in"]` → `@login_required`
- Worker: `session["worker_logged_in"]` → `@worker_required`

Passwords are stored in the Settings sheet and loaded at startup (falling back to `WEB_PASSWORD` / `WORKER_PASSWORD` env vars, defaulting to `gadash2025` / `worker2025`).

CSRF tokens are in session; AJAX calls must send `X-CSRFToken` header.

### Telegram bot

Runs in a dedicated asyncio event loop on a daemon thread. When `WEB_APP_URL` is set, the bot uses webhook mode via the `/webhook/<token>` Flask route. Otherwise it falls back to polling.

Scheduled broadcasts run inside the bot's event loop every 30 minutes and fire at 08:00 (morning summary + weekly report on Mondays + inactive-client reminder) and 18:00 (end-of-day prompt).

### AI summaries

`/api/ai-summary` uses Gemini 2.5 Flash (`google-generativeai`) when `GEMINI_API_KEY` is set. Without it, a template-based Hebrew summary is returned instead.

## Environment variables

| Variable | Purpose |
|---|---|
| `BOT_TOKEN` | Telegram bot token (bot won't start without it) |
| `GOOGLE_CREDS` | Full service-account JSON (takes priority over `credentials.json` file) |
| `SECRET_KEY` | Flask session secret |
| `WEB_APP_URL` | Public app URL; enables Telegram webhook mode |
| `GEMINI_API_KEY` | Enables Gemini AI summaries |
| `WEB_PASSWORD` | Initial manager password (overridden by Settings sheet) |
| `WORKER_PASSWORD` | Initial worker password (overridden by Settings sheet) |
| `PORT` | HTTP port (default 8080) |

## Deployment

Deployed on Fly.io (`fly.toml`, `Dockerfile`). One gunicorn worker with 2 threads — keep at `--workers 1` because the Telegram bot thread and the in-memory cache (`_cache_data`, `_coords_cache`) are not multi-process safe.

## Data model

All column names are Hebrew strings defined in `gadash/models.py`:

```
שם לקוח, תאריך, עבודה, שם חלקה, גידול, כמות, שעות, כלי, מפעיל, הערות, מזין
```

`תאריך` must be `YYYY-MM-DD`. `עבודה` must be one of `VALID_TASKS = {"חריש", "ריסוס", "קציר", "דיסוק", "אחר"}`. Validation is enforced in `WorkEntry.__post_init__`.

## Testing

Tests in `tests/test_app.py` use the Flask test client with `TESTING=True` and a pre-seeded CSRF token in the session. Google Sheets calls are not mocked — tests that hit routes relying on `load_data_from_gsheet` will return empty DataFrames (the function returns an empty DataFrame on any exception) rather than raising.
