# Gadash Bot — מערכת ניהול עבודות גד"ש

[![Tests](https://github.com/amitginz/gadash_bot/actions/workflows/tests.yml/badge.svg)](https://github.com/amitginz/gadash_bot/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.10+-3776ab?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-2.3-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org)

מערכת **multi-tenant** לניהול עבודות שדה חקלאיות (גד"ש) עבור קבלנים. עובדי שדה מדווחים על עבודה בטלגרם, ב**וואטסאפ** (הודעת טקסט או הקלטה קולית שמפוענחת ב-Gemini) או באתר. מנהלים צופים, מסננים, מייצאים ומפיקים דוחות — כולל רווחיות וחיוב ללקוחות.

## סטטוס

אב-טיפוס עובד שנבדק מקצה לקצה מול מספר בדיקה אמיתי של Meta (רישום עובד, דיווח בטקסט ובקול, תיקון, שמירה), על סביבה מקומית עם מנהרת ngrok. **אין עדיין משתמשים אמיתיים והקוד בענף הזה לא נפרס.**

האתר החי, [gadash-bot.fly.dev](https://gadash-bot.fly.dev/), מריץ את הגרסה הקודמת של הפרויקט: מבוססת Google Sheets, חד-לקוחית ובלי וואטסאפ.

---

## ארכיטקטורה

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

**מודל הנתונים:** `tenants`, `managers`, `workers`, `work_entries`, `fields` (פוליגונים ונעצים), `rates`, `audit_log`, `whatsapp_sessions`, `whatsapp_processed_messages`. כל טבלה פרט ל-`tenants` נושאת `tenant_id`. `subscribers` נשארה מהגרסה הקודמת ואינה בשימוש.

---

## החלטות תכנון עיקריות

- **בידוד בין לקוחות.** כל פונקציה ב-`gadash/db.py` מקבלת `tenant_id` מפורש. היוצא מן הכלל הבא נעשה בכוונה: `telegram_id` ו-`whatsapp_number` ייחודיים גלובלית, כדי שבוט אחד ומספר וואטסאפ אחד ישרתו את כל הלקוחות ויזהו את הלקוח לפי השולח.
- **מזהי שורה אמיתיים.** הגרסה הקודמת זיהתה רשומה לפי מיקומה בגיליון, מה שמסוכן תחת עריכות מקבילות. כאן המזהה הוא מפתח ראשי של Postgres.
- **ערוץ וואטסאפ ללא טופס מודרך.** העובד שולח הודעה אחת, בטקסט או בקול, ו-Gemini מחלץ ממנה את השדות. אחרי סיכום עם אייקונים הוא מאשר (`כן`, `אישור`, 👍), מתקן שדה בודד (`שנה שעות ל-5`) או מבטל. המחיקה (`מחק אחרון`) מוגבלת לרשומה האחרונה **של העובד עצמו** ודורשת אישור.
- **תיקונים לפי כללים ולא עוד קריאה למודל.** פענוח של "שנה X ל-Y" דטרמיניסטי: בלי צריכת מכסה, בלי סיכון שהמודל ישכתב שדות אחרים, עם אימות ערכים (שעות מספר סופי וחיובי, סוג עבודה מתוך הרשימה, תאריכים), וכל תיקון מציג מחדש את הסיכום המלא.
- **אבטחת ה-webhook.** כל POST מ-Meta נבדק מול `X-Hub-Signature-256` (HMAC-SHA256, השוואה בזמן קבוע); בלי `WHATSAPP_APP_SECRET` כל בקשה נדחית (fail-closed).
- **אידמפוטנטיות.** Meta מוסרת הודעות לפחות פעם אחת, ולפעמים כמה פעמים. מזהה ההודעה נרשם ב-Postgres עם `INSERT … ON CONFLICT DO NOTHING`, כך ששתי מסירות בו-זמניות לא יכולות שתיהן להחליט שהן הראשונות.
- **מצב שיחה עמיד.** שיחות פתוחות נשמרות בטבלה ופוקעות אחרי 30 דקות (כדי ש"כן" אקראי יום אחר כך לא ישמור דיווח שנשכח). עובד שהוסר מהמערכת נחסם מיד.
- **בעלות על רשומות.** מחיקה ותצוגה בבוטים מוגבלות לרשומות של העובד, והבדיקה נעשית בתוך ה-`DELETE` עצמו ולא רק בקוד הקורא.

---

## יכולות

### וואטסאפ
- הרשמה: קוד חברה, שם עובד וסיסמה; המספר מקושר לעובד
- דיווח בטקסט חופשי או בהקלטה קולית; 13 סוגי עבודה (חריש, ריסוס, קציר, דיסוק, דישון, השקיה, שתילה, עישוב, בציר, מסיק, גיזום, הדברה, אחר)
- סיכום לפני שמירה, תיקון שדה, אישור וביטול בניסוח חופשי, ו-`מחק אחרון`

### טלגרם
- שיחה מודרכת בעברית, הקלטות קוליות, ותמונה כהערה
- חמש העבודות האחרונות, חיפוש לפי לקוח ובחירת רשומה לעריכה — רק של העובד עצמו
- `/undo` של הרשומה האחרונה של העובד, עם אישור

### לוח בקרה למנהל
- טבלת רשומות עם חיפוש, סינון, עריכה מוטבעת, הוספה מהירה, שכפול ומחיקה מרובה
- ייבוא וייצוא Excel / CSV, ו-API לקריאת הרשומות (`/api/docs`)
- דוחות: חודשי עם גרפים, לפי חלקה וגידול, לקוח, **סיכום חיוב ללקוח** ו**רווחיות** (תעריפי הכנסה ועלות לכל סוג עבודה)
- מפת חלקות (Leaflet) עם פוליגונים ונעצים
- סיכום חודשי ב-Gemini, לוג ביקורת, וניהול עובדים

### פורטל עובד (`/worker`)
טופס הזנה מהיר, היסטוריה אישית, ביטול הרשומה האחרונה ושינוי סיסמה.

### אבטחה
הרשאות ו-hash סיסמאות (Werkzeug) לכל לקוח בנפרד, הגנת CSRF, הגבלת קצב על ההתחברות, פקיעת session אחרי 8 שעות, ולוג ביקורת.

---

## בעיות אמיתיות שנמצאו בדרך

אלה לא נתפסו בבדיקות האוטומטיות הראשונות. חלקן עלו בהרצה מול Meta ו-Gemini אמיתיים, וחלקן בסקירת הקוד:

| בעיה | סיבה | תיקון |
|---|---|---|
| אותה הודעה נשמרה כמה פעמים | Meta מוסרת מחדש הודעות, גם כפולות בתוך אותה בקשה | דה-דופליקציה לפי מזהה הודעה, אטומית ב-Postgres |
| כל חילוץ נכשל | `gemini-2.5-flash` הוצא משימוש למפתחות חדשים | מעבר ל-`gemini-3.6-flash` |
| "בצרתי" נשמר כ"אחר" | בציר ענבים וקציר משורשים שונים, ולכן אין התאמה | קטגוריות נפרדות, ולוג של ניחושים שלא התאימו |
| שיחה נעלמה באמצע אישור | מצב שיחה בזיכרון התהליך | טבלת שיחות ב-Postgres |
| `/undo` בטלגרם מחק רשומות של עובדים אחרים | נמחקה השורה האחרונה של הלקוח כולו | מחיקה מוגבלת בעלות ובקשת אישור |

---

## התקנה מקומית

דרישות: Python 3.10+ ו-PostgreSQL.

```bash
git clone https://github.com/amitginz/gadash_bot.git
cd gadash_bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
createdb gadash_dev
cp .env.example .env        # ומלא את הערכים
flask --app app db upgrade
flask --app app create-tenant "חוות הדגמה" demo1 "בחר-סיסמה" --slug demo
python app.py               # http://localhost:8080
```

הכניסה: מנהל עם `demo1`; עובדים נוספים מוסיפים מעמוד העובדים, ונכנסים עם קוד החברה (`demo`).

### בדיקות

```bash
pip install -r requirements-dev.txt
createdb gadash_test
DATABASE_URL=postgresql:///gadash_test pytest -q
```

הבדיקות רצות מול Postgres אמיתי (לא mocks), וגם ב-CI עם service container.

### וואטסאפ מקומית

1. ב-[Meta for Developers](https://developers.facebook.com): אפליקציה עם מקרה השימוש WhatsApp, ומספר בדיקה.
2. `ngrok http 8080` והגדרת webhook ל-`https://<ngrok>/webhook/whatsapp` עם `WHATSAPP_VERIFY_TOKEN`, וקבלת האירוע `messages`.
3. מלא ב-`.env`: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`.

מלכודות שנתקלנו בהן:
- ה-WhatsApp Business Account עשוי להיות מנוי רק לאפליקציית הבדיקה הפנימית של Meta ולא לשלך. בדיקה: `GET /{WABA_ID}/subscribed_apps`, ותיקון: `POST` לאותו נתיב.
- לפי Meta, אפליקציה לא מפורסמת לא מקבלת webhooks אמיתיים, ופרסום דורש כתובת מדיניות פרטיות. יש כזו ב-`/privacy`.
- טוקן ה-"Try it out" פג אחרי 24 שעות; יוצרים System User token מ-Business Settings.
- המכסה החינמית של Gemini הייתה 20 בקשות ליום.

---

## משתני סביבה

| משתנה | תיאור |
|---|---|
| `DATABASE_URL` | חיבור ל-Postgres (ברירת מחדל `postgresql:///gadash_dev`) |
| `SECRET_KEY` | מפתח ה-session של Flask |
| `GEMINI_API_KEY` | חילוץ שדות והסיכום החודשי |
| `WHATSAPP_VERIFY_TOKEN` | טוקן לאימות ה-webhook, בבחירתך |
| `WHATSAPP_APP_SECRET` | חתימת ה-webhook; **חובה**, בלעדיו כל POST נדחה |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | שליחה והורדת מדיה |
| `BOT_TOKEN` | בוט טלגרם (אופציונלי) |
| `WEB_APP_URL` | כתובת ציבורית; מפעילה webhook בטלגרם |
| `GOOGLE_CREDS` | רק לסקריפט ההגירה מהגרסה הקודמת |
| `PORT` | ברירת מחדל 8080 |

## פריסה

`Dockerfile` ו-`fly.toml` מוכנים ל-Fly.io. נקודות שחשוב להכיר:

- gunicorn עם `--workers 1`, כי בוט הטלגרם רץ כ-thread בתוך התהליך. מצב הוואטסאפ כבר ב-Postgres ולכן אינו מגביל.
- `auto_stop_machines = false`: זיהוי חוסר הפעילות של Fly מסתכל רק על HTTP נכנס, והוא עצר את המכונה באמצע קריאה ל-Gemini.
- בכל פריסה: `flask db upgrade`, ו-secrets לכל המשתנים שלמעלה.
- ייבוא נתונים מהגרסה הקודמת: `python scripts/migrate_sheets_to_db.py --name … --username … --password … --slug …` (דורש `GOOGLE_CREDS`)

## מבנה הקוד

```
app.py                 Flask: routes, auth, CSRF, webhook, CLI (create-tenant)
gadash/
  db.py                גישה לנתונים, כל פונקציה ממוקדת tenant_id
  models_db.py         סכמת SQLAlchemy
  auth.py, tenancy.py  הרשאות וזיהוי לקוח
  whatsapp.py          webhook, שיחה, אימות חתימה, דה-דופליקציה
  bot.py               בוט טלגרם
  ai_extract.py        חילוץ שדות מטקסט וקול (Gemini)
  service.py, models.py, workers.py
  sheets.py            מהגרסה הקודמת; בשימוש רק בסקריפט ההגירה
migrations/            Alembic
scripts/               migrate_sheets_to_db.py
templates/             Jinja2 + Bootstrap RTL
tests/                 283 בדיקות
```

## מגבלות ידועות

- וואטסאפ מוגבל למספר הבדיקה ועד 5 נמענים עד שמסיימים Business Verification מול Meta.
- מצב offline של הגרסה הקודמת לא הועבר ל-Postgres.
- נתיב הטלגרם נבדק עם אובייקטי Update מדומים, לא מול טלגרם אמיתי בענף הזה.
- אין קובץ `LICENSE` בריפו.

---

## קרדיטים

הפרויקט התחיל כעבודת קורס ב"נושאים מתקדמים בפיתוח תוכנה" (סמסטר ב׳ תשפ״ו, ד״ר איאד סולימאן), בשיתוף **עמית גינזבורג** ו**הדר קלר**. מפת החלקות (פוליגונים ונעצים) היא של הדר.

הגרסה הרב-לקוחית על Postgres, ערוץ הוואטסאפ והעבודה שאחריהם נעשו על ידי עמית, בסיוע [Claude](https://claude.ai) (Anthropic).
