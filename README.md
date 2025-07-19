# גד"ש Data Management

מערכת ניהול דיווחי עבודות קבלן עבור גד"ש שדה אליהו, עם ממשק אינטרנטי, תמיכה בבוט טלגרם, ושמירה אוטומטית לגיליון Google Sheets.

## 🚀 תכונות

- העלאת קובצי Excel עם עבודות מהשדה
- חיפוש וסינון לפי לקוח ותאריך
- יצוא לאקסל
- ממשק בוט טלגרם לדיווח עבודות
- שמירה אוטומטית ל-Google Sheets
- פריסה על Fly.io

---

## 📁 מבנה הקבצים

- `app.py` — קוד שרת Flask
- `templates/` — קבצי HTML
- `static/` — קבצי CSS/JS במידת הצורך
- `credentials.json` — קובץ ההרשאות של Google (לא מועלה ל-GitHub)
- `.env` — קובץ משתני סביבה (כולל GOOGLE_CREDS)
- `requirements.txt` — ספריות Python

---

## ⚙️ התקנה מקומית

1. שכפל את הריפוזיטורי:

```bash
git clone https://github.com/your-username/gadash-data.git
cd gadash-data
```

2. צור וירטואלית והתקן חבילות:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. הכן משתני סביבה:

צור קובץ `.env` והוסף בו:

```
GOOGLE_CREDS=<תוכן ה-json כ-string>
```

> מומלץ להשתמש ב־1-liner JSON עם `json.dumps()` מראש או לקרוא ישירות מתוך הקובץ אם לא בפריסה ל־Fly.io.

4. הרץ את האפליקציה:

```bash
flask run
```

---

## ☁️ פריסה על Fly.io

1. התקן את CLI של Fly.io:

```bash
curl -L https://fly.io/install.sh | sh
```

2. התחבר ופרוס:

```bash
flyctl launch
flyctl deploy
```

3. הגדר משתני סביבה:

```bash
flyctl secrets set GOOGLE_CREDS='{"type": "service_account", ...}'
```

---

## 📄 פורמט גיליון Google Sheets

יש לוודא שהגיליון כולל את העמודות הבאות (שורה ראשונה היא כותרות):

```
שם לקוח | תאריך | עבודה | שם חלקה | כמות | כלי | מפעיל | הערות | מזין
```

---

## 🧠 הערות

- אין להעלות את `credentials.json` ל־GitHub — השתמשו ב־`.gitignore`.
- אם יש בעיה עם `GOOGLE_CREDS`, ודאו שהתוכן בפורמט JSON תקני.

---

## 🧑‍💻 תרומה

תרומות, הצעות ושיפורים תמיד מתקבלים בברכה.

---

## רישיון

[MIT License](LICENSE)