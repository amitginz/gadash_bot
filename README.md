# Gadash Bot — Field-Crop Work Management

[![Live Demo](https://img.shields.io/badge/Live%20Demo-gadash--bot.fly.dev-2563eb?style=for-the-badge&logo=fly.io&logoColor=white)](https://gadash-bot.fly.dev/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)](LICENSE)

> **This README describes the earlier version (Google Sheets, single tenant).** The current version, with a multi-tenant Postgres backend and a WhatsApp channel, is on the [`feature/postgres-multitenant`](https://github.com/amitginz/gadash_bot/tree/feature/postgres-multitenant) branch.

A system for an agricultural contractor to manage field work (Hebrew: *gadash*, field crops). Field workers enter jobs through a Hebrew **Telegram bot**; managers view, edit and export all the data from a **web dashboard**. Everything syncs automatically to **Google Sheets**. The UI and bot are in Hebrew.

---

## Submission details

**Course:** Advanced Topics in Software Development, Semester B, 2026

**Project name:** Gadash work management system — website and Telegram bot

**Authors:** Amit Ginzberg, Hadar Keller

**Git repository:** https://github.com/amitginz/gadash_bot

**Final document (full project report):** `gadash_report.docx` (attached to the submission)

---

## Live demo

**[gadash-bot.fly.dev](https://gadash-bot.fly.dev/)**

---

## Architecture

```
┌─────────────────────┐     HTTP/JSON      ┌──────────────────────┐
│   Web Browser       │ ◄────────────────► │   Flask App          │
│   HTML5 / Jinja2    │                    │   app.py             │
│   Bootstrap 5 RTL   │                    │                      │
└─────────────────────┘                    │   gadash/ package    │
                                           │   ├── models.py      │
┌─────────────────────┐     async calls    │   ├── sheets.py      │
│   Telegram App      │ ◄────────────────► │   ├── service.py     │
│   ConversationBot   │                    │   ├── bot.py         │
│   16 states         │                    │   ├── audit.py       │
└─────────────────────┘                    │   └── workers.py     │
                                           └──────────┬───────────┘
                                                      │ gspread API
                                           ┌──────────▼───────────┐
                                           │   Google Sheets       │
                                           │   "Gadash Data"       │
                                           │   6 worksheets        │
                                           └──────────────────────┘
```

---

## Features

### Telegram bot
- Guided conversation in Hebrew with 16 conversation states (`ConversationHandler`)
- Job entry in 12 steps: client, date, task type, field, crop, amount, hours, tool, operator, notes
- A photo can be attached as a note
- `/undo` removes the last entry
- Search by client name
- Automatic broadcasts: a daily summary at 08:00, a reminder at 18:00, and a weekly report on Mondays

### Manager dashboard
- Entries table with global search, filtering and inline editing
- Quick-Add modal for adding an entry without a page reload
- Bulk delete and duplicate entries
- Excel / CSV export
- Excel import with automatic deduplication
- Monthly report with 3 charts (Chart.js)
- Hours report by field / crop
- Client report
- Field map (Leaflet.js, with GPS coordinates from Google Sheets)
- AI summary (Gemini 2.5 Flash), with a Hebrew template as the fallback

### Worker portal (`/worker`)
- Quick entry form
- Personal history only

### Security and operations
- CSRF protection on every form and AJAX request
- Rate limiting: 5 requests per minute
- Session timeout: 8 hours
- Manager and worker passwords hashed with Werkzeug PBKDF2-SHA256
- Audit log, written both to a file and to the AuditLog sheet
- In-memory cache with a 5-minute TTL, guarded by a `threading.Lock`

---

## File layout

```
gadash_bot/
├── app.py                  # Flask app — routes, auth, CSRF, rate limiting
├── gadash/
│   ├── models.py           # WorkEntry dataclass, COLUMNS, VALID_TASKS
│   ├── sheets.py           # Google Sheets I/O + in-memory cache
│   ├── service.py          # create_entry() — writes + audit
│   ├── bot.py              # Telegram ConversationHandler (16 states)
│   ├── audit.py            # Dual audit log (file + Sheets)
│   ├── workers.py          # Worker CRUD + password hashing
│   └── subscribers.py      # Telegram broadcast subscriber list
├── templates/              # Jinja2 HTML templates
├── static/                 # CSS, JS, Chart.js
├── tests/
│   └── test_app.py         # Flask test client tests
├── requirements.txt
├── Dockerfile
├── fly.toml
└── Procfile
```

---

## Google Sheets structure

| Worksheet | Contents |
|---|---|
| `Sheet1` | Main work data (11 Hebrew-named columns) |
| `Settings` | Manager and worker passwords (key-value) |
| `Workers` | Worker registry + password hash + telegram_id |
| `FieldCoords` | GPS coordinates by field name |
| `AuditLog` | Action log (ts, action, user, detail) |
| `Subscribers` | chat_ids for the bot's broadcasts |

`Sheet1` columns (client, date, task, field, crop, amount, hours, tool, operator, notes, entered by):
```
שם לקוח | תאריך | עבודה | שם חלקה | גידול | כמות | שעות | כלי | מפעיל | הערות | מזין
```

---

## Local setup

### Prerequisites

- Python 3.11+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A Google Cloud service account with the Sheets and Drive APIs enabled
- A Google Sheet named `Gadash Data`, shared with the service account

### Running

```bash
git clone https://github.com/amitginz/gadash_bot.git
cd gadash_bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file:

```env
BOT_TOKEN=your_telegram_token
GOOGLE_CREDS={"type":"service_account",...}
SECRET_KEY=your_secret_key
WEB_PASSWORD=choose_an_admin_password
WORKER_PASSWORD=choose_a_worker_password
GEMINI_API_KEY=optional_for_ai_summaries
```

```bash
python app.py
```

The dashboard is available at `http://localhost:8080`.

### Running the tests

```bash
pytest tests/ -v
```

---

## Deployment — Fly.io

```bash
flyctl launch
flyctl secrets set BOT_TOKEN="..." GOOGLE_CREDS='{"type":"service_account",...}' SECRET_KEY="..."
flyctl deploy
```

> Important: run gunicorn with `--workers 1`. The Telegram bot and the in-memory cache are not multi-process safe.

---

## Environment variables

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token |
| `GOOGLE_CREDS` | Service account JSON (takes precedence over `credentials.json`) |
| `SECRET_KEY` | Flask session secret |
| `WEB_PASSWORD` | Initial manager password (overridden by the Settings sheet) |
| `WORKER_PASSWORD` | Initial worker password |
| `WEB_APP_URL` | Public URL; enables Telegram webhook mode |
| `GEMINI_API_KEY` | Gemini key for the AI summaries |
| `PORT` | HTTP port (default 8080) |

---

## Credits

Built with the assistance of [Claude](https://claude.ai) (Anthropic).

## License

MIT
