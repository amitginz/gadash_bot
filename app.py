from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
from datetime import date
import gspread
import asyncio
import threading
from google.oauth2.service_account import Credentials
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
from io import BytesIO
import os, json, time, threading

TOKEN = os.getenv("BOT_TOKEN")

MENU, CLIENT, DATE, TASK, FIELD, AMOUNT, TOOL, OPERATOR, NOTE, CONFIRM = range(10)

TASK_CHOICES = [["חריש", "ריסוס"], ["קציר", "דיסוק"], ["אחר"]]
CONFIRM_KEYBOARD = [["כן", "לא"]]
MENU_KEYBOARD = [
    ["הזן עבודה חדשה"],
    ["5 עבודות אחרונות"],
    ["סיים"],
]

# Canonical column order — must match Google Sheet headers
COLUMNS = ["שם לקוח", "תאריך", "עבודה", "שם חלקה", "כמות", "כלי", "מפעיל", "הערות", "מזין"]

# ── Google Sheets ──────────────────────────────────────────────────────────────

_gs_client  = None
_gs_lock    = threading.Lock()
_cache_data = None
_cache_time = 0.0
_CACHE_TTL  = 30  # seconds


def _invalidate_cache():
    global _cache_data, _cache_time
    _cache_data = None
    _cache_time = 0.0


def _get_sheet():
    """Return a gspread Worksheet, reconnecting if the session expired."""
    global _gs_client
    with _gs_lock:
        if _gs_client is None:
            scope = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            raw = os.environ.get("GOOGLE_CREDS")
            if raw:
                creds = Credentials.from_service_account_info(json.loads(raw), scopes=scope)
            elif os.path.exists("credentials.json"):
                creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
            else:
                raise RuntimeError(
                    "No Google credentials found. "
                    "Set GOOGLE_CREDS env var or place credentials.json in the project root."
                )
            _gs_client = gspread.authorize(creds)
        try:
            return _gs_client.open("Gadash Data").sheet1
        except Exception:
            _gs_client = None
            raise


def load_data_from_gsheet() -> pd.DataFrame:
    global _cache_data, _cache_time
    with _gs_lock:
        if _cache_data is not None and (time.time() - _cache_time) < _CACHE_TTL:
            return _cache_data.copy()
    try:
        sheet = _get_sheet()
        all_values = sheet.get_all_values()
        if not all_values or len(all_values) < 2:
            df = pd.DataFrame(columns=COLUMNS)
        else:
            headers = [h.strip() for h in all_values[0]]
            records = [dict(zip(headers, row)) for row in all_values[1:] if any(row)]
            df = pd.DataFrame(records)
            for col in COLUMNS:
                if col not in df.columns:
                    df[col] = ""
            df = df[COLUMNS]
        with _gs_lock:
            _cache_data = df
            _cache_time = time.time()
        return df.copy()
    except Exception as e:
        print(f"[GSheet] load error: {e}")
        return pd.DataFrame(columns=COLUMNS)


def append_row_to_gsheet(row: dict):
    """Append one row — fast and safe (no full rewrite)."""
    sheet = _get_sheet()
    values = [str(row.get(col, "")) for col in COLUMNS]
    sheet.append_row(values, value_input_option="USER_ENTERED")
    _invalidate_cache()


def save_data_to_gsheet(df: pd.DataFrame):
    """Full rewrite — only for edit / delete operations."""
    sheet = _get_sheet()
    sheet.clear()
    sheet.append_row(COLUMNS)
    if not df.empty:
        rows = df[COLUMNS].fillna("").astype(str).values.tolist()
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
    _invalidate_cache()


# ── Telegram Bot ───────────────────────────────────────────────────────────────

def _menu_markup():
    return ReplyKeyboardMarkup(MENU_KEYBOARD, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[BOT] /start received from", update.message.from_user.id, flush=True)
    await update.message.reply_text(
        'שלום! אני בוט ניהול העבודות של גד"ש 🌾\nמה תרצה לעשות?',
        reply_markup=_menu_markup(),
    )
    return MENU


async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "הזן עבודה חדשה":
        await update.message.reply_text("מעולה! מה שם הלקוח?", reply_markup=ReplyKeyboardRemove())
        return CLIENT
    elif text == "5 עבודות אחרונות":
        await bot_recent(update, context)
        return MENU
    elif text == "סיים":
        await update.message.reply_text("נתראה בקרוב! 👋", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    await update.message.reply_text(
        'שלום! אני בוט ניהול העבודות של גד"ש 🌾\nמה תרצה לעשות?',
        reply_markup=_menu_markup(),
    )
    return MENU


async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "הזן עבודה חדשה":
        await update.message.reply_text("מעולה! מה שם הלקוח?", reply_markup=ReplyKeyboardRemove())
        return CLIENT
    elif text == "5 עבודות אחרונות":
        await bot_recent(update, context)
        return MENU
    elif text == "סיים":
        await update.message.reply_text("נתראה בקרוב! 👋", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    else:
        await update.message.reply_text("בחר אפשרות מהתפריט.", reply_markup=_menu_markup())
        return MENU


async def bot_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        df = load_data_from_gsheet()
        if df.empty:
            await update.message.reply_text("אין עבודות רשומות עדיין.", reply_markup=_menu_markup())
            return
        lines = ["📄 5 העבודות האחרונות:\n"]
        for _, row in df.tail(5).iterrows():
            lines.append(
                f"• {row.get('תאריך','')} | {row.get('עבודה','')} | "
                f"{row.get('שם חלקה','')} | {row.get('כמות','')} | {row.get('מזין','')}"
            )
        await update.message.reply_text("\n".join(lines), reply_markup=_menu_markup())
    except Exception as e:
        await update.message.reply_text(f"שגיאה: {e}", reply_markup=_menu_markup())


async def client_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["שם לקוח"] = update.message.text.strip()
    await update.message.reply_text("מה התאריך? (YYYY-MM-DD או 'היום')")
    return DATE


async def date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if user_input.lower() == "היום":
        context.user_data["תאריך"] = date.today().strftime("%Y-%m-%d")
    else:
        try:
            pd.to_datetime(user_input, format="%Y-%m-%d")
            context.user_data["תאריך"] = user_input
        except ValueError:
            await update.message.reply_text(
                "פורמט תאריך שגוי. הכנס YYYY-MM-DD (לדוגמה 2025-06-15) או 'היום':"
            )
            return DATE
    await update.message.reply_text(
        "בחר סוג עבודה:",
        reply_markup=ReplyKeyboardMarkup(TASK_CHOICES, one_time_keyboard=True, resize_keyboard=True),
    )
    return TASK


async def task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["עבודה"] = update.message.text.strip()
    await update.message.reply_text("מה שם החלקה?", reply_markup=ReplyKeyboardRemove())
    return FIELD


async def field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["שם חלקה"] = update.message.text.strip()
    await update.message.reply_text("כמות (למשל 30 דונם):")
    return AMOUNT


async def amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["כמות"] = update.message.text.strip()
    await update.message.reply_text("איזה כלי שימש?")
    return TOOL


async def tool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["כלי"] = update.message.text.strip()
    await update.message.reply_text("מי המפעיל?")
    return OPERATOR


async def operator_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["מפעיל"] = update.message.text.strip()
    await update.message.reply_text("הערות (אם אין, כתוב -):")
    return NOTE


async def note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["הערות"] = update.message.text.strip()
    summary = "\n".join(f"• {k}: {v}" for k, v in context.user_data.items())
    await update.message.reply_text(
        f"סיכום לפני שמירה:\n\n{summary}\n\nלחץ כן לשמירה או לא לביטול.",
        reply_markup=ReplyKeyboardMarkup(CONFIRM_KEYBOARD, resize_keyboard=True),
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "כן":
        row = context.user_data.copy()
        row["מזין"] = update.message.from_user.full_name
        try:
            append_row_to_gsheet(row)
            await update.message.reply_text("✅ נשמר בהצלחה!")
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה בשמירה: {e}")
    else:
        await update.message.reply_text("❌ בוטל.")
    context.user_data.clear()
    await update.message.reply_text("מה תרצה לעשות?", reply_markup=_menu_markup())
    return MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ביטול.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def start_telegram_bot():
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("❌ BOT_TOKEN not set — Telegram bot will not start")
        return

    print("[BOT] Thread starting...", flush=True)
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        print("[BOT] Event loop created", flush=True)
        telegram_app = ApplicationBuilder().token(token).build()
        print("[BOT] App built OK", flush=True)
    except Exception as e:
        print(f"[BOT] ERROR: {e}", flush=True)
        import traceback; traceback.print_exc()
        return

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_start),
        ],
        states={
            MENU:     [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_choice)],
            CLIENT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, client_step)],
            DATE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, date_input)],
            TASK:     [MessageHandler(filters.TEXT & ~filters.COMMAND, task)],
            FIELD:    [MessageHandler(filters.TEXT & ~filters.COMMAND, field)],
            AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, amount)],
            TOOL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, tool)],
            OPERATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, operator_step)],
            NOTE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, note)],
            CONFIRM:  [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
    )
    telegram_app.add_handler(conv)

    async def _run():
        async with telegram_app:
            await telegram_app.updater.start_polling()
            await telegram_app.start()
            await asyncio.Event().wait()

    loop.run_until_complete(_run())


# ── Flask Web App ──────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    try:
        df = load_data_from_gsheet()
        total_count = len(df)

        # Stats for dashboard cards
        today_str = date.today().strftime("%Y-%m-%d")
        month_prefix = date.today().strftime("%Y-%m")
        month_count = int(df["תאריך"].str.startswith(month_prefix).sum()) if total_count else 0
        top_client = df["שם לקוח"].mode()[0] if total_count else "—"
        top_task = df["עבודה"].mode()[0] if total_count else "—"

        # Preserve original GSheet row index before sort/filter
        df = df.reset_index().rename(columns={"index": "_row_id"})
        df = df.sort_values(by="תאריך", ascending=False)

        client_filter = request.args.get("client", "").strip()
        date_filter   = request.args.get("date", "").strip()
        task_filter   = request.args.get("task", "").strip()

        if client_filter:
            df = df[df["שם לקוח"].str.contains(client_filter, case=False, na=False)]
        if date_filter:
            df = df[df["תאריך"] == date_filter]
        if task_filter:
            df = df[df["עבודה"] == task_filter]

        records = df.to_dict(orient="records")
        task_options = ["חריש", "ריסוס", "קציר", "דיסוק", "אחר"]

        # Chart data (full dataset, before filter)
        full_df = load_data_from_gsheet()
        task_counts = full_df["עבודה"].value_counts().to_dict()
        client_counts = full_df["שם לקוח"].value_counts().head(6).to_dict()

        return render_template(
            "index.html",
            records=records,
            total_count=total_count,
            month_count=month_count,
            top_client=top_client,
            top_task=top_task,
            client_filter=client_filter,
            date_filter=date_filter,
            task_filter=task_filter,
            task_options=task_options,
            task_counts=task_counts,
            client_counts=client_counts,
        )
    except Exception as e:
        return render_template(
            "index.html",
            records=[],
            total_count=0,
            month_count=0,
            top_client="—",
            top_task="—",
            client_filter="",
            date_filter="",
            task_filter="",
            task_options=[],
            task_counts={},
            client_counts={},
            error=str(e),
        )


@app.route("/add", methods=["GET", "POST"])
def add():
    today = date.today().strftime("%Y-%m-%d")
    if request.method == "POST":
        row = {col: request.form.get(col, "") for col in COLUMNS if col != "מזין"}
        row["מזין"] = "Web"
        try:
            append_row_to_gsheet(row)
            return render_template("add.html", today=today, success=True)
        except Exception as e:
            return render_template("add.html", today=today, error=str(e))
    return render_template("add.html", today=today)


@app.route("/edit/<int:row_id>", methods=["GET", "POST"])
def edit(row_id):
    df = load_data_from_gsheet()
    if request.method == "POST":
        try:
            for key in COLUMNS:
                df.at[row_id, key] = request.form.get(key, "")
            save_data_to_gsheet(df)
            return redirect(url_for("index"))
        except Exception as e:
            return f"שגיאה בעדכון: {e}"
    try:
        row_data = df.iloc[row_id].to_dict()
        return render_template("edit.html", row=row_data, row_id=row_id)
    except Exception as e:
        return f"שגיאה בטעינת שורה: {e}"


@app.route("/delete/<int:row_id>", methods=["POST"])
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
            combined = pd.concat([df, new_df], ignore_index=True) if not df.empty else new_df
            save_data_to_gsheet(combined)
            return redirect(url_for("index"))
        return "יש לבחור קובץ Excel תקני (.xlsx)"
    return render_template("import.html")


@app.route("/export")
def export():
    df = load_data_from_gsheet()
    if df.empty:
        return "אין נתונים לייצוא."
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Data")
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="gadash_data.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Start bot thread at import time (works with both gunicorn and direct run) ──

if os.environ.get("BOT_TOKEN"):
    _bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    _bot_thread.start()

# ── Entry point (direct run only) ─────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
