# Gadash Bot — מערכת ניהול עבודות גד"ש

[![Live Demo](https://img.shields.io/badge/Live%20Demo-gadash--bot.fly.dev-2563eb?style=for-the-badge&logo=fly.io&logoColor=white)](https://gadash-bot.fly.dev/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)](LICENSE)

מערכת לניהול עבודות שדה עבור קבלן חקלאי. עובדי שדה מזינים עבודות דרך **בוט טלגרם** בעברית; מנהלים צופים, עורכים ומייצאים את כל הנתונים מ**לוח בקרה ויבי**. הכול מסתנכרן אוטומטית ל-**Google Sheets**.

---

## תצוגה חיה

**[gadash-bot.fly.dev](https://gadash-bot.fly.dev/)** — סיסמת מנהל: `gadash2025`

---

## ארכיטקטורה

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

## יכולות

### בוט טלגרם
- שיחה מודרכת בעברית — 16 מצבי שיחה (ConversationHandler)
- הזנת עבודה ב-12 שלבים: לקוח, תאריך, סוג עבודה, חלקה, גידול, כמות, שעות, כלי, מפעיל, הערות
- תמיכה בתמונה כהערה
- `/undo` — ביטול הרשומה האחרונה
- חיפוש לפי שם לקוח
- שידורים אוטומטיים: סיכום יומי 08:00, תזכורת 18:00, דוח שבועי בימי שני

### לוח בקרה למנהל
- טבלת רשומות עם חיפוש גלובלי, סינון ועריכה מוטבעת
- Quick-Add modal — הוספה מהירה ללא טעינת דף
- מחיקה מרובה ושכפול רשומות
- ייצוא Excel / CSV
- ייבוא Excel עם deduplication אוטומטי
- דוח חודשי — 3 גרפים (Chart.js)
- דוח שעות לפי חלקה / גידול
- דוח לקוח
- מפת חלקות (Leaflet.js + GPS מ-Google Sheets)
- סיכום AI (Gemini 2.5 Flash) — fallback לתבנית עברית

### פורטל עובד (`/worker`)
- טופס הזנה מהיר
- היסטוריה אישית בלבד

### אבטחה ותפעול
- CSRF protection על כל טופס ובקשת AJAX
- Rate limiting — 5 בקשות/דקה
- Session timeout — 8 שעות
- סיסמאות מנהל ועובד — Werkzeug PBKDF2-SHA256
- לוג ביקורת (audit) — כתיבה כפולה לקובץ + AuditLog sheet
- cache בזיכרון עם TTL של 5 דקות + threading.Lock

---

## מבנה הקבצים

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

## Google Sheets — מבנה

| Worksheet | תוכן |
|---|---|
| `Sheet1` | נתוני עבודה ראשיים (11 עמודות עבריות) |
| `Settings` | סיסמאות מנהל ועובד (key-value) |
| `Workers` | רישום עובדים + password hash + telegram_id |
| `FieldCoords` | קואורדינטות GPS לפי שם חלקה |
| `AuditLog` | לוג פעולות (ts, action, user, detail) |
| `Subscribers` | chat_ids לשידורי הבוט |

עמודות Sheet1:
```
שם לקוח | תאריך | עבודה | שם חלקה | גידול | כמות | שעות | כלי | מפעיל | הערות | מזין
```

---

## התקנה מקומית

### דרישות מקדימות

- Python 3.11+
- Telegram bot token (מ-[@BotFather](https://t.me/BotFather))
- Google Cloud service account עם Sheets + Drive API
- Google Sheet בשם `Gadash Data` משותף עם ה-service account

### הרצה

```bash
git clone https://github.com/amitginz/gadash_bot.git
cd gadash_bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

צור קובץ `.env`:

```env
BOT_TOKEN=your_telegram_token
GOOGLE_CREDS={"type":"service_account",...}
SECRET_KEY=your_secret_key
WEB_PASSWORD=gadash2025
WORKER_PASSWORD=worker2025
GEMINI_API_KEY=optional_for_ai_summaries
```

```bash
python app.py
```

הדשבורד זמין ב-`http://localhost:8080`.

### הרצת בדיקות

```bash
pytest tests/ -v
```

---

## פריסה — Fly.io

```bash
flyctl launch
flyctl secrets set BOT_TOKEN="..." GOOGLE_CREDS='{"type":"service_account",...}' SECRET_KEY="..."
flyctl deploy
```

> חשוב: השתמש ב-`--workers 1` ב-gunicorn — בוט הטלגרם וה-cache בזיכרון אינם multi-process safe.

---

## משתני סביבה

| משתנה | תיאור |
|---|---|
| `BOT_TOKEN` | טוקן בוט טלגרם |
| `GOOGLE_CREDS` | JSON של service account (עדיפות על `credentials.json`) |
| `SECRET_KEY` | Flask session secret |
| `WEB_PASSWORD` | סיסמת מנהל ראשונית (מוחלף על ידי Settings sheet) |
| `WORKER_PASSWORD` | סיסמת עובד ראשונית |
| `WEB_APP_URL` | URL ציבורי — מפעיל Webhook mode בטלגרם |
| `GEMINI_API_KEY` | מפתח Gemini לסיכומי AI |
| `PORT` | פורט HTTP (ברירת מחדל 8080) |

---

## Credits

Built with the assistance of [Claude](https://claude.ai) (Anthropic).

## License

MIT
