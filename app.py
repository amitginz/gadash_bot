from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
from datetime import date
import gspread
import asyncio
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
from datetime import date
import os

TOKEN = os.getenv("BOT_TOKEN")  # קבלת הטוקן ממשתני סביבה
DATA_FILE = "works.xlsx"

MENU, CLIENT, DATE, TASK, FIELD, AMOUNT, TOOL, OPERATOR, NOTE, CONFIRM = range(10)

START_KEYBOARD = [["כן, רוצה להתחיל"], ["לא, תודה"]]

MENU_KEYBOARD = [
    ["הזן עבודה חדשה"],
    ["ייצא קובץ"],
    ["סיים"]
]


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
        df = pd.DataFrame([row])
        try:
            existing = pd.read_excel(DATA_FILE)
            df = pd.concat([existing, df], ignore_index=True)
        except FileNotFoundError:
            pass
        df.to_excel(DATA_FILE, index=False)
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
        df = pd.read_excel(DATA_FILE)
        if df.empty:
            await update.message.reply_text("הקובץ ריק כרגע.")
            return
        last_entries = df.tail(5)
        message = "📄 5 העבודות האחרונות:\n"
        for idx, row in last_entries.iterrows():
            message += f"\n— {row['תאריך']} | {row['עבודה']} | {row['שם חלקה']} | {row['כמות']} | {row['מזין']}"
        await update.message.reply_text(message)
    except FileNotFoundError:
        await update.message.reply_text("⚠️ הקובץ עדיין לא קיים.")
    except Exception as e:
        await update.message.reply_text(f"שגיאה: {e}")


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not os.path.exists(DATA_FILE):
            await update.message.reply_text("⚠️ הקובץ עדיין לא קיים.")
            return
        await update.message.reply_document(document=open(DATA_FILE, "rb"), filename="עבודות_גדש.xlsx")
    except Exception as e:
        await update.message.reply_text(f"שגיאה בשליחת הקובץ: {e}")


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
    # צור event loop חדש והגדר אותו ל-thread הנוכחי
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.add_handler(conv_handler)
    app.run_polling()


app = Flask(__name__)


@app.route('/')
def index():
    try:
        df = pd.read_excel("works.xlsx")
        df = df.sort_values(by="תאריך", ascending=False)

        total_count = len(df)  # ⬅️ ספירה מלאה לפני סינון

        client = request.args.get("client", "").strip()
        date_filter = request.args.get("date", "").strip()

        if client:
            df = df[df["שם לקוח"].str.contains(client, case=False, na=False)]
        if date_filter:
            df = df[df["תאריך"] == date_filter]

        return render_template("index.html", records=df.to_dict(orient='records'), total_count=total_count)
    except Exception as e:
        return f"שגיאה בטעינת הקובץ: {e}"


def init_gsheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open("Gadash Data").sheet1
    return sheet


@app.route('/add', methods=["GET", "POST"])
def add():
    if request.method == "POST":
        df = pd.read_excel("works.xlsx")
        new_row = {col: request.form.get(col, "") for col in df.columns}
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_excel("works.xlsx", index=False)

        # ניסיון לשמור גם ל-Google Sheets
        try:
            sheet = init_gsheet()
            sheet.append_row(list(new_row.values()))
        except Exception as e:
            print(f"שגיאה ב-Google Sheets: {e}")

        return redirect(url_for("index"))

    today = date.today().strftime("%Y-%m-%d")
    return render_template("add.html", today=today, success=True)


@app.route('/edit/<int:row_id>', methods=["GET", "POST"])
def edit(row_id):
    df = pd.read_excel("works.xlsx")

    # POST: עדכון ושמירה
    if request.method == "POST":
        try:
            for key in df.columns:
                df.at[row_id, key] = request.form.get(key)
            df.to_excel("works.xlsx", index=False)
            return redirect(url_for("index"))
        except Exception as e:
            return f"שגיאה בעדכון: {e}"

    # GET: שליחה לתבנית edit.html
    try:
        row_data = df.iloc[row_id].to_dict()
        return render_template("edit.html", row=row_data, row_id=row_id)
    except Exception as e:
        return f"שגיאה בטעינת שורה: {e}"


@app.route('/delete/<int:row_id>')
def delete(row_id):
    df = pd.read_excel("works.xlsx")
    df = df.drop(index=row_id).reset_index(drop=True)
    df.to_excel("works.xlsx", index=False)
    return redirect(url_for("index"))


@app.route("/export")
def export():
    return send_file("works.xlsx", as_attachment=True, download_name="דוח_גדש.xlsx")


@app.route("/import", methods=["GET", "POST"])
def import_data():
    if request.method == "POST":
        file = request.files.get("file")
        if file and file.filename.endswith(".xlsx"):
            new_df = pd.read_excel(file)
            try:
                existing_df = pd.read_excel("works.xlsx")
                df = pd.concat([existing_df, new_df], ignore_index=True)
            except FileNotFoundError:
                df = new_df
            df.to_excel("works.xlsx", index=False)
            return redirect("/")
        else:
            return "יש לבחור קובץ Excel תקני (.xlsx)"
    return render_template("import.html")


if __name__ == '__main__':
    import threading

    # הרץ את הבוט בת׳רד נפרד
    bot_thread = threading.Thread(target=start_telegram_bot)
    bot_thread.start()

    # הרץ את השרת Flask
    app.run(debug=True)
