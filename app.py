from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
from datetime import date
import gspread
import asyncio
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
import os, json

TOKEN = os.getenv("BOT_TOKEN")  # קבלת הטוקן ממשתני סביבה

MENU, CLIENT, DATE, TASK, FIELD, AMOUNT, TOOL, OPERATOR, NOTE, CONFIRM = range(10)

START_KEYBOARD = [["כן, רוצה להתחיל"], ["לא, תודה"]]

MENU_KEYBOARD = [
    ["הזן עבודה חדשה"],
    ["ייצא קובץ"],
    ["סיים"]
]

# --- פונקציות גוגל שיטס ---

def init_gsheet():
    try:
        creds_json = os.environ["GOOGLE_CREDS"]
    except KeyError:
        raise RuntimeError("⚠️ GOOGLE_CREDS לא מוגדר במשתני הסביבה.")

    try:
        creds_dict = json.loads(creds_json)
    except json.JSONDecodeError:
        raise RuntimeError("⚠️ GOOGLE_CREDS אינו JSON תקף.")

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    try:
        sheet = client.open("Gadash Data").sheet1
    except Exception as e:
        raise RuntimeError(f"שגיאה בפתיחת הגיליון: {e}")

    return sheet


def load_data_from_gsheet():
    try:
        sheet = init_gsheet()
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        print("עמודות ב-DataFrame:", df.columns.tolist())  # בדיקה
        df.columns = df.columns.str.strip()  # מסיר רווחים
        if "תאריך" not in df.columns:
            print("העמודה 'תאריך' לא נמצאה!")
            return pd.DataFrame()
        return df
    except Exception as e:
        print(f"שגיאה בטעינת גוגל שיטס: {e}")
        return pd.DataFrame()


# --- בוט טלגרם ---

async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "שלום! רוצה להתחיל להזין עבודה חדשה?",
        reply_markup=ReplyKeyboardMarkup(START_KEYBOARD, one_time_keyboard=True, resize_keyboard=True)
    )
    return MENU

async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "כן, רוצה להתחיל" or text == "הזן עבודה חדשה":
        await update.message.reply_text("מעולה! מה שם הלקוח?", reply_markup=ReplyKeyboardRemove())
        return CLIENT
    elif text == "ייצא קובץ":
        await export(update, context)
        await update.message.reply_text(
            "מה תרצה לעשות עכשיו?",
            reply_markup=ReplyKeyboardMarkup(MENU_KEYBOARD, one_time_keyboard=True, resize_keyboard=True)
        )
        return MENU
    elif text == "סיים" or text == "לא, תודה":
        await update.message.reply_text("אין בעיה, נתראה בקרוב! 👋", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    else:
        await update.message.reply_text("בחר אפשרות תקינה בבקשה.")
        return MENU

async def client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["שם לקוח"] = update.message.text
    await update.message.reply_text("מה התאריך? (YYYY-MM-DD או 'היום')")
    return DATE

async def date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    if user_input.lower() == "היום":
        context.user_data["תאריך"] = date.today().strftime("%Y-%m-%d")
    else:
        context.user_data["תאריך"] = user_input
    keyboard = [["חריש", "ריסוס"], ["קציר", "דיסוק"]]
    await update.message.reply_text(
        "בחר את סוג העבודה:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return TASK

async def task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["עבודה"] = update.message.text
    await update.message.reply_text("מה שם החלקה?", reply_markup=ReplyKeyboardRemove())
    return FIELD

async def field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["שם חלקה"] = update.message.text
    await update.message.reply_text("מה הכמות (למשל 30 דונם)?")
    return AMOUNT

async def amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["כמות"] = update.message.text
    await update.message.reply_text("מה הכלי שבו השתמשת?")
    return TOOL

async def tool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["כלי"] = update.message.text
    await update.message.reply_text("מי המפעיל?")
    return OPERATOR

async def operator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["מפעיל"] = update.message.text
    await update.message.reply_text("הערות? (אם אין, כתוב - )")
    return NOTE

async def note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["הערות"] = update.message.text
    summary = "\n".join(f"{k}: {v}" for k, v in context.user_data.items())
    await update.message.reply_text(f"לאישור שמירה:\n\n{summary}\n\nשלח 'כן' לשמירה או 'לא' לביטול.")
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "כן":
        row = context.user_data.copy()
        row["מזין"] = update.message.from_user.full_name
        df = load_data_from_gsheet()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        save_data_to_gsheet(df)
        await update.message.reply_text("✅ נשמר בהצלחה!")
    else:
        await update.message.reply_text("❌ בוטל.")
    context.user_data.clear()
    await update.message.reply_text(
        "מה תרצה לעשות עכשיו?",
        reply_markup=ReplyKeyboardMarkup(MENU_KEYBOARD, one_time_keyboard=True, resize_keyboard=True)
    )
    return MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ביטלת את הפעולה.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        df = load_data_from_gsheet()
        if df.empty:
            await update.message.reply_text("הקובץ ריק כרגע.")
            return
        last_entries = df.tail(5)
        message = "📄 5 העבודות האחרונות:\n"
        for idx, row in last_entries.iterrows():
            message += f"\n— {row['תאריך']} | {row['עבודה']} | {row['שם חלקה']} | {row['כמות']} | {row['מזין']}"
        await update.message.reply_text(message)
    except Exception as e:
        await update.message.reply_text(f"שגיאה: {e}")

async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("הייצוא לקובץ Excel עדיין לא נתמך עם Google Sheets. אפשר להוסיף בעתיד.")

def start_telegram_bot():
    token = os.getenv("BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_start)],
        states={
            MENU: [MessageHandler(filters.TEXT, menu_choice)],
            CLIENT: [MessageHandler(filters.TEXT, client)],
            DATE: [MessageHandler(filters.TEXT, date_input)],
            TASK: [MessageHandler(filters.TEXT, task)],
            FIELD: [MessageHandler(filters.TEXT, field)],
            AMOUNT: [MessageHandler(filters.TEXT, amount)],
            TOOL: [MessageHandler(filters.TEXT, tool)],
            OPERATOR: [MessageHandler(filters.TEXT, operator)],
            NOTE: [MessageHandler(filters.TEXT, note)],
            CONFIRM: [MessageHandler(filters.TEXT, confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.add_handler(conv_handler)
    app.run_polling()

# --- Flask ---

app = Flask(__name__)

@app.route('/')
def index():
    try:
        df = load_data_from_gsheet()
        df = df.sort_values(by="תאריך", ascending=False)
        total_count = len(df)

        client = request.args.get("client", "").strip()
        date_filter = request.args.get("date", "").strip()

        if client:
            df = df[df["שם לקוח"].str.contains(client, case=False, na=False)]
        if date_filter:
            df = df[df["תאריך"] == date_filter]

        return render_template("index.html", records=df.to_dict(orient='records'), total_count=total_count)
    except Exception as e:
        return f"שגיאה בטעינת הדף: {e}"

@app.route('/add', methods=["GET", "POST"])
def add():
    if request.method == "POST":
        df = load_data_from_gsheet()
        new_row = {col: request.form.get(col, "") for col in df.columns} if not df.empty else request.form.to_dict()
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_data_to_gsheet(df)

        return redirect(url_for("index"))

    today = date.today().strftime("%Y-%m-%d")
    return render_template("add.html", today=today, success=True)

@app.route('/edit/<int:row_id>', methods=["GET", "POST"])
def edit(row_id):
    df = load_data_from_gsheet()

    if request.method == "POST":
        try:
            for key in df.columns:
                df.at[row_id, key] = request.form.get(key)
            save_data_to_gsheet(df)
            return redirect(url_for("index"))
        except Exception as e:
            return f"שגיאה בעדכון: {e}"

    try:
        row_data = df.iloc[row_id].to_dict()
        return render_template("edit.html", row=row_data, row_id=row_id)
    except Exception as e:
        return f"שגיאה בטעינת שורה: {e}"

@app.route('/delete/<int:row_id>')
def delete(row_id):
    df = load_data_from_gsheet()
    df = df.drop(index=row_id).reset_index(drop=True)
    save_data_to_gsheet(df)
    return redirect(url_for("index"))

@app.route("/import", methods=["GET", "POST"])
def import_data():
    if request.method == "POST":
        file = request.files.get("file")
        if file and file.filename.endswith(".xlsx"):
            new_df = pd.read_excel(file)
            df = load_data_from_gsheet()
            if df.empty:
                df = new_df
            else:
                df = pd.concat([df, new_df], ignore_index=True)
            save_data_to_gsheet(df)
            return redirect("/")
        else:
            return "יש לבחור קובץ Excel תקני (.xlsx)"
    return render_template("import.html")

# --- הפעלה ---

if __name__ == "__main__":
    import threading

    def run_flask():
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    token = os.getenv("BOT_TOKEN")
    if not token:
        print("❌ BOT_TOKEN לא הוגדר, הבוט לא יפעל")
    else:
        start_telegram_bot()
