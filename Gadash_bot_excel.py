import pandas as pd
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
from datetime import date
import os
import openpyxl
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


app = TOKEN
conv_handler = ConversationHandler(
    entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_start)],
    states={
        MENU: [MessageHandler(filters.Regex("^(כן, רוצה להתחיל|לא, תודה|הזן עבודה חדשה|ייצא קובץ|סיים)$"), menu_choice)],
        CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, client)],
        DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, date_input)],
        TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, task)],
        FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, field)],
        AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount)],
        TOOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, tool)],
        OPERATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, operator)],
        NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, note)],
        CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

app.add_handler(conv_handler)
app.add_handler(CommandHandler("report", report))
app.add_handler(CommandHandler("export", export))

app.run_polling()
