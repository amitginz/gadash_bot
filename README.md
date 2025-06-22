# 📄 בוט תיעוד עבודות קבלנות לגד״ש

בוט טלגרם להזנת עבודות קבלנות בשטח ושמירתן ל־**Excel** ול־**Google Sheets**, עם יכולות חיפוש ודוחות.

---

## 🚀 תכונות

- הזנת דיווח לפי שדות:
  - שם לקוח, תאריך, סוג עבודה, חלקה, כמות, כלי, מפעיל, הערות
- שמירה ל־`works.xlsx`
- שליחה ל־Google Sheets (`Gadash Data`)
- דוח שבועי מתוך תפריט
- חיפוש עבודות לפי לקוח / תאריכים
- תפריט משתמש נוח בטלגרם

---

## 📦 קבצים

- `bot.py` – קובץ הקוד הראשי
- `credentials.json` – קובץ הרשאות ל־Google Sheets
- `requirements.txt` – תלות ספריות
- `Procfile` – לקונפיגורציית הפעלה בענן

---

## 🛠 התקנה מקומית

1. התקן ספריות:
   ```bash
   pip install -r requirements.txt
   ```

2. הגדר משתנה סביבה:
   ```bash
   export BOT_TOKEN=your_token_here
   ```

3. הרץ את הבוט:
   ```bash
   python bot.py
   ```

---

## ☁️ פריסה ב־Render

1. התחבר ל־[https://render.com](https://render.com)
2. צור שירות חדש (`Web Service`)
3. העלה את הקבצים כולל:
   - `bot.py`
   - `credentials.json`
   - `requirements.txt`
   - `Procfile`

4. הגדר משתנה סביבה:
   ```
   BOT_TOKEN = (הטוקן מה-BotFather)
   ```

5. לחץ Deploy – והבוט רץ! 🟢

---

## 🧪 שימוש בבוט

### התחלה:
- שלח הודעה לבוט או `/start`
- בחר: "הזן עבודה חדשה"

### תפריט ראשי:
- 📥 הזן עבודה חדשה
- 📤 ייצא קובץ Excel
- 📆 שלח דוח שבועי (7 ימים אחרונים)
- 🔍 חפש עבודות (לפי לקוח / תאריכים)
- ❌ סיום

---

## 🔐 הרשאות Google Sheets

- צור Google Sheet בשם `Gadash Data`
- שתף אותו עם האימייל שבקובץ `credentials.json` (נראה כמו: `xyz@project.iam.gserviceaccount.com`)
- ודא הרשאת **עורך**

