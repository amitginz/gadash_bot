# 🌾 מערכת ניהול עבודות קבלנות - גד"ש שדה אליהו

פרויקט מבוסס Flask ובוט טלגרם לתיעוד, תצוגה וחיפוש עבודות חקלאיות, עם שילוב Google Sheets כבסיס נתונים.

## 🚀 תכונות עיקריות

- שליחת דיווחי עבודות דרך בוט טלגרם
- תצוגת כל העבודות באתר אינטרנט
- סינון לפי לקוח ותאריך
- ייצוא לקובץ Excel
- שמירה ושליפה מ־Google Sheets

---

## 🛠️ טכנולוגיות

- Python + Flask
- Telegram Bot API (באמצעות python-telegram-bot)
- Google Sheets API (gspread)
- Bootstrap RTL לגרסה עברית

---

## ⚙️ התקנה והרצה מקומית

1. **שכפול הריפוזיטורי:**

```bash
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name
```

2. **התקנת חבילות:**

```bash
pip install -r requirements.txt
```

3. **הוספת אישורים של Google Sheets:**

- צור פרויקט בגוגל קונסול, הפעל Sheets API ו־Drive API.
- צור מפתח שירות (Service Account) והורד את קובץ `credentials.json`.
- המרת הקובץ למשתנה סביבה:

```bash
export GOOGLE_CREDS="$(cat credentials.json)"
```

או ב־`.env`:

```env
GOOGLE_CREDS={"type": "...", "project_id": "...", ...}
```

4. **הרצת Flask:**

```bash
flask run
```

---

## 🤖 שימוש בבוט

- שלח `/start` לבוט
- שלח דיווח בפורמט:
```
שם לקוח, תאריך, עבודה, חלקה, כמות, כלי, מפעיל, הערות
```

---

## 📁 מבנה קבצים עיקריים

```
├── app.py                  # שרת Flask
├── bot.py                  # קוד בוט הטלגרם
├── templates/
│   └── index.html          # תצוגת הנתונים
├── static/                 # קבצי עיצוב בעתיד
├── requirements.txt
└── README.md
```

---

## 📝 הערות

- ודא ששם הגיליון ב־Google Sheets הוא **Gadash Data**
- השורה הראשונה בגיליון חייבת להיות:

  ```text
  שם לקוח | תאריך | עבודה | שם חלקה | כמות | כלי | מפעיל | הערות | מזין
  ```

---

## 📸 תמונה לדוגמה (אופציונלי)

![screenshot](static/screenshot.png)

---

## 📤 פריסה בענן

ניתן להפעיל גם ב־Render, Railway או Heroku. ודא שאתה מגדיר משתנה סביבה `GOOGLE_CREDS` במערכת.

---

## 🧑‍💻 קרדיטים

פותח ע"י עמית גינזבורג, 2025  
בשיתוף פעולה עם קיבוץ שדה אליהו 🌿