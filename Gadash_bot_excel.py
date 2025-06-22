
import os
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
from datetime import date

TOKEN = os.getenv("BOT_TOKEN")
EXCEL_FILE = "works.xlsx"
SHEET_NAME = "Gadash Data"

MENU, CLIENT, DATE, TASK, FIELD, AMOUNT, TOOL, OPERATOR, NOTE, CONFIRM = range(10)
START_KEYBOARD = [["כן, רוצה להתחיל"], ["לא, תודה"]]
MENU_KEYBOARD = [["הזן עבודה חדשה"], ["ייצא קובץ"], ["סיים"]]

def init_gsheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
    return sheet

async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("שלום! רוצה להתחיל להזין עבודה חדשה?",
        reply_markup=ReplyKeyboardMarkup(START_KEYBOARD, one_time_keyboard=True, resize_keyboard=True))
    return MENU

async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "כן, רוצה להתחיל" or text == "הזן עבודה חדשה":
        await update.message.reply_text("מעולה! מה שם הלקוח?", reply_markup=ReplyKeyboardRemove())
        return CLIENT
    elif text == "ייצא קובץ":
        if os.path.exists(EXCEL_FILE):
            await update.message.reply_document(open(EXCEL_FILE, "rb"))
        else:
            await update.message.reply_text("⚠️ אין קובץ נתונים לשיתוף.")
        return MENU
    elif text == "סיים" or text == "לא, תודה":
        await update.message.reply_text("להתראות 👋", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    else:
        await update.message.reply_text("בחר בבקשה מהתפריט.")
        return MENU

async def client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["שם לקוח"] = update.message.text
    await update.message.reply_text("מה התאריך? (YYYY-MM-DD או 'היום')")
    return DATE

async def date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    context.user_data["תאריך"] = date.today().strftime("%Y-%m-%d") if user_input.lower() == "היום" else user_input
    await update.message.reply_text("איזו עבודה בוצעה?")
    return TASK

async def task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["עבודה"] = update.message.text
    await update.message.reply_text("שם חלקה?")
    return FIELD

async def field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["שם חלקה"] = update.message.text
    await update.message.reply_text("כמות?")
    return AMOUNT

async def amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["כמות"] = update.message.text
    await update.message.reply_text("כלי?")
    return TOOL

async def tool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["כלי"] = update.message.text
    await update.message.reply_text("מפעיל?")
    return OPERATOR

async def operator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["מפעיל"] = update.message.text
    await update.message.reply_text("הערות? (אם אין, כתוב - )")
    return NOTE

async def note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["הערות"] = update.message.text
    summary = "\n".join(f"{k}: {v}" for k, v in context.user_data.items())
    await update.message.reply_text(f"אישור:
{summary}

לשלוח 'כן' לשמירה או 'לא' לביטול")
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "כן":
        row = context.user_data.copy()
        row["מזין"] = update.message.from_user.full_name
        df = pd.DataFrame([row])
        try:
            existing = pd.read_excel(EXCEL_FILE)
            df = pd.concat([existing, df], ignore_index=True)
        except FileNotFoundError:
            pass
        df.to_excel(EXCEL_FILE, index=False)

        try:
            sheet = init_gsheet()
            sheet.append_row(list(row.values()))
        except Exception as e:
            await update.message.reply_text(f"שגיאה בשמירה ל-Google Sheets: {e}")
        await update.message.reply_text("✅ נשמר בהצלחה!")
    else:
        await update.message.reply_text("❌ בוטל.")
    context.user_data.clear()
    await update.message.reply_text("מה תרצה לעשות עכשיו?", reply_markup=ReplyKeyboardMarkup(MENU_KEYBOARD, one_time_keyboard=True, resize_keyboard=True))
    return MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ביטול התהליך.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

app = ApplicationBuilder().token(TOKEN).build()
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
app.add_handler(conv_handler)
app.run_polling()
