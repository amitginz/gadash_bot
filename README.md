# Gadash Bot — Field-Crop Work Management

[![Tests](https://github.com/amitginz/gadash_bot/actions/workflows/tests.yml/badge.svg)](https://github.com/amitginz/gadash_bot/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.10+-3776ab?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-2.3-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org)

A **multi-tenant** system for agricultural contractors to manage field work (Hebrew: *gadash*, field crops). Field workers report jobs through Telegram, **WhatsApp** (a typed message or a voice note, parsed by Gemini), or the website. Managers browse, filter and export entries and get reports, including profitability and client billing. The UI and bots are in Hebrew.

## Status

A working prototype, tested end to end against a real Meta WhatsApp test number: worker registration, reporting by text and by voice, corrections, and saving. It ran on a local machine behind an ngrok tunnel. **There are no real users yet, and the code on this branch is not deployed.**

The live site, [gadash-bot.fly.dev](https://gadash-bot.fly.dev/), runs the earlier version of the project: Google Sheets backend, single tenant, no WhatsApp.

---

## Architecture

```
  Web (manager / worker)      Telegram bot        WhatsApp (Meta Cloud API)
            │                       │                         │ HMAC-signed webhook
            └───────────────────────┼─────────────────────────┘
                                    ▼
                         Flask app  (app.py + gadash/)
             auth · CSRF · routes · bot.py · whatsapp.py · ai_extract.py
                                    │
                  gadash/db.py — every query scoped by tenant_id
                                    │
                  PostgreSQL  (Alembic migrations)        Gemini (extraction)
```

**Data model:** `tenants`, `managers`, `workers`, `work_entries`, `fields` (polygons and pins), `rates`, `audit_log`, `whatsapp_sessions`, `whatsapp_processed_messages`. Every table except `tenants` carries a `tenant_id`. `subscribers` is left over from the earlier version and unused.

---

## Key design decisions

- **Tenant isolation.** Every function in `gadash/db.py` takes an explicit `tenant_id`. The one deliberate exception: `telegram_id` and `whatsapp_number` are globally unique, so a single bot and a single WhatsApp number can serve every tenant and identify the tenant from the sender.
- **Real row ids.** The earlier version identified a row by its position in the sheet, which is unsafe under concurrent edits. Here the id is a Postgres primary key.
- **WhatsApp without a guided form.** The worker sends one message, text or voice, and Gemini extracts the fields. After a summary with icons, the worker confirms (`כן`, `אישור`, 👍), corrects a single field (`שנה שעות ל-5`, "change hours to 5"), or cancels. Deletion (`מחק אחרון`, "delete last") only ever offers the **worker's own** most recent entry and asks first.
- **Rule-based corrections, not another model call.** Parsing "change X to Y" is deterministic: no quota spent, no risk of the model rewriting other fields, validated values (hours must be a finite number above 0, the task must be one of the known types, dates are checked), and every correction re-shows the full summary.
- **Webhook security.** Every POST from Meta is checked against `X-Hub-Signature-256` (HMAC-SHA256, constant-time comparison). Without `WHATSAPP_APP_SECRET` every request is rejected (fail closed).
- **Idempotency.** Meta delivers webhooks at least once, and sometimes several times. Message ids are recorded in Postgres with `INSERT … ON CONFLICT DO NOTHING`, so two simultaneous deliveries cannot both decide they were first.
- **Durable conversation state.** Open conversations live in a table and expire after 30 minutes, so a stray "yes" a day later cannot save a forgotten report. A worker removed from the system is blocked immediately.
- **Ownership of entries.** Deleting and listing entries in the bots is limited to the worker's own rows, and the ownership check is part of the `DELETE` statement itself, not just the calling code.

---

## Features

### WhatsApp
- Registration with company code, worker name and password; the number is then linked to the worker
- Reporting by free text or voice note; 13 task types: plowing (חריש), spraying (ריסוס), harvest (קציר), disking (דיסוק), fertilizing (דישון), irrigation (השקיה), planting (שתילה), weeding (עישוב), grape harvest (בציר), olive harvest (מסיק), pruning (גיזום), pest control (הדברה), other (אחר)
- Summary before saving, single-field correction, natural-language confirm and cancel, and delete-last

### Telegram
- Guided conversation in Hebrew, voice notes, and a photo as a note
- Last five entries, search by client, and an edit picker, all limited to the worker's own entries
- `/undo` for the worker's own last entry, with confirmation

### Manager dashboard
- Entries table with search, filtering, inline editing, quick add, duplicate, and bulk delete
- Excel / CSV import and export, and a read API (`/api/docs`)
- Reports: monthly with charts, by field and crop, per client, a **client billing summary**, and **profitability** (revenue and cost rates per task type)
- Field map (Leaflet) with polygons and pins
- Monthly summary written by Gemini, an audit log, and worker management

### Worker portal (`/worker`)
A quick entry form, personal history, undo of the last entry, and password change.

### Security
Per-tenant manager and worker accounts with hashed passwords (Werkzeug), CSRF protection, login rate limiting, an 8-hour session timeout, and an audit log.

---

## Real problems found along the way

These were not caught by the first automated tests. Some surfaced running against real Meta and Gemini, others in code review:

| Problem | Cause | Fix |
|---|---|---|
| The same message was saved several times | Meta redelivers webhooks, even duplicates inside a single request | Deduplication by message id, atomic in Postgres |
| Every extraction failed | `gemini-2.5-flash` was retired for new API keys | Moved to `gemini-3.6-flash` |
| "בצרתי" (I harvested grapes) was saved as "other" | Grape harvest and general harvest come from different Hebrew roots, so there was no match | Separate task types, plus logging of guesses that matched nothing |
| A conversation vanished mid-confirmation | State lived in process memory | A conversations table in Postgres |
| Telegram `/undo` deleted other workers' entries | It deleted the whole tenant's last row | Ownership-scoped delete with confirmation |

---

## Local setup

Requires Python 3.10+ and PostgreSQL.

```bash
git clone https://github.com/amitginz/gadash_bot.git
cd gadash_bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
createdb gadash_dev
cp .env.example .env        # then fill in the values
flask --app app db upgrade
flask --app app create-tenant "Demo Farm" demo1 "choose-a-password" --slug demo
python app.py               # http://localhost:8080
```

Sign in as the manager with `demo1`. Add workers from the Workers page; workers sign in with the company code (`demo`).

### Tests

```bash
pip install -r requirements-dev.txt
createdb gadash_test
DATABASE_URL=postgresql:///gadash_test pytest -q
```

Tests run against a real Postgres (no mocks), including in CI via a service container.

### WhatsApp locally

1. In [Meta for Developers](https://developers.facebook.com): create an app with the WhatsApp use case and a test number.
2. Run `ngrok http 8080`, set the webhook to `https://<ngrok>/webhook/whatsapp` with your `WHATSAPP_VERIFY_TOKEN`, and subscribe to the `messages` event.
3. Fill in `.env`: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`.

Pitfalls we hit:
- The WhatsApp Business Account may be subscribed only to Meta's internal test app and not to yours. Check with `GET /{WABA_ID}/subscribed_apps` and fix with a `POST` to the same path.
- According to Meta, an unpublished app does not receive real webhooks, and publishing requires a privacy policy URL. There is one at `/privacy`.
- The "Try it out" token expires after 24 hours; create a System User token in Business Settings.
- The free Gemini quota was 20 requests per day.

---

## Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection (default `postgresql:///gadash_dev`) |
| `SECRET_KEY` | Flask session key |
| `GEMINI_API_KEY` | Field extraction and the monthly summary |
| `WHATSAPP_VERIFY_TOKEN` | Webhook verification token, of your choosing |
| `WHATSAPP_APP_SECRET` | Webhook signature key; **required**, without it every POST is rejected |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | Sending messages and downloading media |
| `BOT_TOKEN` | Telegram bot (optional) |
| `WEB_APP_URL` | Public URL; enables Telegram webhook mode |
| `GOOGLE_CREDS` | Only for the migration script from the earlier version |
| `PORT` | Default 8080 |

## Deployment

The `Dockerfile` and `fly.toml` are set up for Fly.io. Things worth knowing:

- gunicorn runs with `--workers 1` because the Telegram bot runs as a thread inside the process. WhatsApp state is already in Postgres, so it is not a constraint.
- `auto_stop_machines = false`: Fly's idle detection only watches inbound HTTP, and it stopped the machine in the middle of a Gemini call.
- On every deploy: run `flask db upgrade`, and set secrets for all the variables above.
- Importing data from the earlier version: `python scripts/migrate_sheets_to_db.py --name … --username … --password … --slug …` (requires `GOOGLE_CREDS`)

## Code layout

```
app.py                 Flask: routes, auth, CSRF, webhook, CLI (create-tenant)
gadash/
  db.py                data access, every function scoped by tenant_id
  models_db.py         SQLAlchemy schema
  auth.py, tenancy.py  authentication and tenant resolution
  whatsapp.py          webhook, conversation, signature check, deduplication
  bot.py               Telegram bot
  ai_extract.py        field extraction from text and voice (Gemini)
  service.py, models.py, workers.py
  sheets.py            from the earlier version; used only by the migration script
migrations/            Alembic
scripts/               migrate_sheets_to_db.py
templates/             Jinja2 + Bootstrap RTL
tests/                 283 tests
```

## Known limitations

- WhatsApp is limited to the test number and up to 5 recipients until Meta Business Verification is completed.
- The earlier version's offline mode was not ported to Postgres.
- The Telegram path was tested with mock Update objects, not against real Telegram, on this branch.
- There is no `LICENSE` file in the repository.

---

## Credits

The project began as a coursework project for "Advanced Topics in Software Development" (Semester B, 2026), by **Amit Ginzberg** and **Hadar Keller**. The field map (polygons and pins) is Hadar's.

The multi-tenant Postgres version, the WhatsApp channel, and the work after them were done by Amit with the assistance of [Claude](https://claude.ai) (Anthropic).
