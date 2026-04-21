# Gadash Bot

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Visit%20App-2563eb?style=for-the-badge&logo=render)](https://gadash-bot.onrender.com)

A combined Flask web app and Telegram bot for managing contractor work reports for Gadash Sdeh Eliyahu. Field workers log jobs via Telegram; managers view, edit, and export all records from a web dashboard. Everything syncs automatically to a shared Google Sheet.

## Features

- **Telegram Bot** — guided conversation flow for logging a new work entry (client, date, task type, plot, quantity, tool, operator, notes)
- **Web Dashboard** — view all records, add/edit/delete entries manually
- **Excel Import** — bulk-upload work records from `.xlsx` files
- **Excel Export** — download the full dataset as a formatted spreadsheet
- **Google Sheets Sync** — all records are read from and written to a live Google Sheet
- **Deployed on Fly.io** — runs as a single Gunicorn server with the bot on a background thread

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Python + Flask |
| Telegram bot | python-telegram-bot 20.x (async) |
| Data | pandas, Google Sheets via gspread |
| Auth | Google Service Account (google-auth) |
| Server | Gunicorn |
| Deployment | Fly.io / Render (Docker or Procfile) |

## Telegram Bot Flow

The bot uses a 10-step `ConversationHandler`:

```
/start → confirm intent → menu
  ├── הזן עבודה חדשה  →  client → date → task → plot → amount → tool → operator → notes → confirm → save to Sheet
  ├── 5 עבודות אחרונות  →  shows last 5 rows from the Sheet
  └── סיים  →  end conversation
```

## Google Sheet Format

The target sheet must be named **`Gadash Data`** and have these headers in row 1:

```
שם לקוח | תאריך | עבודה | שם חלקה | כמות | כלי | מפעיל | הערות | מזין
```

## Getting Started

### Prerequisites

- Python 3.11+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A Google Cloud service account with Sheets + Drive API enabled
- A Google Sheet named `Gadash Data` shared with the service account

### Local Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/amitginz/gadash_bot.git
   cd gadash_bot
   ```

2. **Create a virtual environment and install dependencies**

   ```bash
   python -m venv venv
   source venv/bin/activate      # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure environment variables**

   ```bash
   cp .env.example .env
   ```

   Fill in your values in `.env`:

   | Variable | Description |
   |---|---|
   | `BOT_TOKEN` | Telegram bot token from BotFather |
   | `GOOGLE_CREDS` | Full Google service account JSON as a single-line string |

   For local development you can alternatively place `credentials.json` in the project root instead of setting `GOOGLE_CREDS`.

4. **Run the app**

   ```bash
   flask run
   ```

   The web dashboard is at `http://localhost:5000` and the Telegram bot starts automatically on a background thread.

## Deployment

### Fly.io

```bash
flyctl launch
flyctl secrets set BOT_TOKEN="your-token" GOOGLE_CREDS='{"type":"service_account",...}'
flyctl deploy
```

### Render / other hosts

Set `BOT_TOKEN` and `GOOGLE_CREDS` as environment variables in your host's dashboard. The `Procfile` starts Gunicorn automatically:

```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 2
```

## License

MIT
