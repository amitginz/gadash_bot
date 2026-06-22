from flask import Flask, render_template, request, redirect, url_for, send_file, flash, session, jsonify
from functools import wraps
from urllib.parse import urlencode
import pandas as pd
from datetime import date, datetime, timedelta
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
from dataclasses import dataclass
import os, json, time, math, re, secrets, csv

WEB_APP_URL      = os.environ.get("WEB_APP_URL", "https://gadash-bot.fly.dev")
PAGE_SIZE        = 50
SUBSCRIBERS_FILE = "subscribers.json"
AUDIT_LOG_FILE   = "audit.log"

MENU, CLIENT, DATE, TASK, FIELD, AMOUNT, TOOL, OPERATOR, NOTE, CONFIRM, SEARCH, EDIT_SELECT = range(12)

TASK_CHOICES    = [["חריש", "ריסוס"], ["קציר", "דיסוק"], ["אחר"]]
CONFIRM_KEYBOARD = [["כן", "לא"]]
NOTES_KEYBOARD  = [["ללא הערות"]]
MENU_KEYBOARD   = [
    ["הזן עבודה חדשה"],
    ["5 עבודות אחרונות", "חפש לפי לקוח"],
    ["סטטיסטיקות", "ערוך רשומה"],
    ["סיים"],
]

COLUMNS     = ["שם לקוח", "תאריך", "עבודה", "שם חלקה", "כמות", "כלי", "מפעיל", "הערות", "מזין"]
VALID_TASKS = {"חריש", "ריסוס", "קציר", "דיסוק", "אחר"}


@dataclass
class WorkEntry:
    client:     str
    date:       str
    task:       str
    field_name: str = ""
    amount:     str = ""
    tool:       str = ""
    operator:   str = ""
    notes:      str = ""
    entered_by: str = ""

    def __post_init__(self):
        self.client = self.client.strip()
        self.date   = self.date.strip()
        self.task   = self.task.strip()
        if not self.client:
            raise ValueError("שם לקוח חובה")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.date):
            raise ValueError(f"תאריך לא תקין: '{self.date}' — נדרש YYYY-MM-DD")
        if self.task not in VALID_TASKS:
            raise ValueError(f"סוג עבודה לא תקין: '{self.task}'")

    def to_sheet_row(self) -> list:
        return [
            self.client, self.date, self.task, self.field_name,
            self.amount, self.tool, self.operator, self.notes, self.entered_by,
        ]

    def to_dict(self) -> dict:
        return dict(zip(COLUMNS, self.to_sheet_row()))

    @classmethod
    def from_dict(cls, d: dict) -> "WorkEntry":
        return cls(
            client=str(d.get("שם לקוח", "")),
            date=str(d.get("תאריך", "")),
            task=str(d.get("עבודה", "")),
            field_name=str(d.get("שם חלקה", "")),
            amount=str(d.get("כמות", "")),
            tool=str(d.get("כלי", "")),
            operator=str(d.get("מפעיל", "")),
            notes=str(d.get("הערות", "")),
            entered_by=str(d.get("מזין", "")),
        )

    @classmethod
    def from_form(cls, form, entered_by: str = "Web") -> "WorkEntry":
        return cls(
            client=form.get("שם לקוח", ""),
            date=form.get("תאריך", ""),
            task=form.get("עבודה", ""),
            field_name=form.get("שם חלקה", ""),
            amount=form.get("כמות", ""),
            tool=form.get("כלי", ""),
            operator=form.get("מפעיל", ""),
            notes=form.get("הערות", ""),
            entered_by=entered_by,
        )

    @classmethod
    def from_bot(cls, user_data: dict, full_name: str) -> "WorkEntry":
        return cls(
            client=user_data.get("שם לקוח", ""),
            date=user_data.get("תאריך", ""),
            task=user_data.get("עבודה", ""),
            field_name=user_data.get("שם חלקה", ""),
            amount=user_data.get("כמות", ""),
            tool=user_data.get("כלי", ""),
            operator=user_data.get("מפעיל", ""),
            notes=user_data.get("הערות", ""),
            entered_by=full_name,
        )


# ── Google Sheets ──────────────────────────────────────────────────────────────

_gs_client  = None
_gs_lock    = threading.Lock()
_cache_data = None
_cache_time = 0.0
_CACHE_TTL  = 30


def _invalidate_cache():
    global _cache_data, _cache_time
    _cache_data = None
    _cache_time = 0.0


def _get_sheet():
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
                raise RuntimeError("No Google credentials found.")
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


def append_row_to_gsheet(entry: WorkEntry):
    sheet = _get_sheet()
    sheet.append_row(entry.to_sheet_row(), value_input_option="USER_ENTERED")
    _invalidate_cache()


def save_data_to_gsheet(df: pd.DataFrame):
    sheet = _get_sheet()
    sheet.clear()
    sheet.append_row(COLUMNS)
    if not df.empty:
        rows = df[COLUMNS].fillna("").astype(str).values.tolist()
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
    _invalidate_cache()


# ── Audit log ──────────────────────────────────────────────────────────────────

_audit_lock = threading.Lock()


def _log_audit(action: str, user: str, detail: str):
    entry = json.dumps({
        "ts":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "user":   user,
        "detail": detail,
    }, ensure_ascii=False)
    with _audit_lock:
        try:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            pass


def _read_audit_log(limit: int = 200) -> list:
    if not os.path.exists(AUDIT_LOG_FILE):
        return []
    try:
        with _audit_lock:
            with open(AUDIT_LOG_FILE, encoding="utf-8") as f:
                lines = f.readlines()
        entries = [json.loads(l) for l in lines if l.strip()]
        return list(reversed(entries[-limit:]))
    except Exception:
        return []


# ── Subscriber management ──────────────────────────────────────────────────────

def _get_subscribers() -> set:
    try:
        if os.path.exists(SUBSCRIBERS_FILE):
            with open(SUBSCRIBERS_FILE) as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def _add_subscriber(chat_id: int):
    subs = _get_subscribers()
    subs.add(chat_id)
    try:
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(list(subs), f)
    except Exception:
        pass


# ── Telegram Bot ───────────────────────────────────────────────────────────────

def _menu_markup():
    return ReplyKeyboardMarkup(MENU_KEYBOARD, resize_keyboard=True)


def _recent_clients_markup():
    try:
        df = load_data_from_gsheet()
        seen, recent = set(), []
        for name in reversed(df["שם לקוח"].dropna().tolist()):
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                recent.append(name)
            if len(recent) == 5:
                break
        if recent:
            return ReplyKeyboardMarkup([[c] for c in recent], one_time_keyboard=True, resize_keyboard=True)
    except Exception:
        pass
    return ReplyKeyboardRemove()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _add_subscriber(update.message.chat_id)
    await update.message.reply_text(
        'שלום! אני בוט ניהול העבודות של גד"ש 🌾\nמה תרצה לעשות?',
        reply_markup=_menu_markup(),
    )
    return MENU


async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _add_subscriber(update.message.chat_id)
    return await _handle_menu(update, context, update.message.text.strip())


async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _handle_menu(update, context, update.message.text.strip())


async def _handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    if text == "הזן עבודה חדשה":
        await update.message.reply_text("מעולה! מה שם הלקוח?", reply_markup=_recent_clients_markup())
        return CLIENT
    elif text == "5 עבודות אחרונות":
        await bot_recent(update, context)
        return MENU
    elif text == "חפש לפי לקוח":
        await update.message.reply_text("הכנס שם לקוח לחיפוש:", reply_markup=ReplyKeyboardRemove())
        return SEARCH
    elif text == "סטטיסטיקות":
        await bot_stats(update, context)
        return MENU
    elif text == "ערוך רשומה":
        return await bot_edit_last(update, context)
    elif text == "סיים":
        await update.message.reply_text("נתראה בקרוב! 👋", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    await update.message.reply_text(
        'שלום! אני בוט ניהול העבודות של גד"ש 🌾\nמה תרצה לעשות?',
        reply_markup=_menu_markup(),
    )
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


async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        df = load_data_from_gsheet()
        if df.empty:
            await update.message.reply_text("אין נתונים עדיין.", reply_markup=_menu_markup())
            return
        month_prefix = datetime.now().strftime("%Y-%m")
        month_count  = int(df["תאריך"].str.startswith(month_prefix).sum())
        top_client   = df["שם לקוח"].mode()[0]
        top_task     = df["עבודה"].mode()[0]
        task_lines   = "\n".join(f"  • {t}: {c}" for t, c in df["עבודה"].value_counts().items())
        msg = (
            f"📊 סטטיסטיקות:\n\n"
            f"📋 סה\"כ עבודות: {len(df)}\n"
            f"📅 החודש: {month_count}\n"
            f"👤 לקוח מוביל: {top_client}\n"
            f"🚜 עבודה נפוצה: {top_task}\n\n"
            f"עבודות לפי סוג:\n{task_lines}"
        )
        await update.message.reply_text(msg, reply_markup=_menu_markup())
    except Exception as e:
        await update.message.reply_text(f"שגיאה: {e}", reply_markup=_menu_markup())


async def bot_edit_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        df = load_data_from_gsheet()
        if df.empty:
            await update.message.reply_text("אין עבודות לעריכה.", reply_markup=_menu_markup())
            return MENU
        tail = df.tail(5)
        lines = ["✏️ בחר רשומה לעריכה:\n"]
        choices = []
        indices = []
        for i, (idx, row) in enumerate(tail.iterrows(), 1):
            lines.append(f"{i}. {row.get('תאריך','')} | {row.get('שם לקוח','')} | {row.get('עבודה','')}")
            choices.append([str(i)])
            indices.append(idx)
        context.user_data["_edit_indices"] = indices
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=ReplyKeyboardMarkup(choices, one_time_keyboard=True, resize_keyboard=True),
        )
        return EDIT_SELECT
    except Exception as e:
        await update.message.reply_text(f"שגיאה: {e}", reply_markup=_menu_markup())
        return MENU


async def bot_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        n = int(text)
        indices = context.user_data.get("_edit_indices", [])
        if 1 <= n <= len(indices):
            row_id = indices[n - 1]
            df = load_data_from_gsheet()
            row = df.iloc[row_id]
            details = "\n".join(f"• {col}: {row.get(col, '')}" for col in COLUMNS)
            await update.message.reply_text(
                f"✏️ פרטי הרשומה:\n\n{details}\n\n🔗 לעריכה באתר:\n{WEB_APP_URL}/edit/{row_id}",
                reply_markup=_menu_markup(),
            )
        else:
            await update.message.reply_text("בחר מספר מהרשימה.", reply_markup=_menu_markup())
    except ValueError:
        await update.message.reply_text("בחר מספר מהרשימה.", reply_markup=_menu_markup())
    context.user_data.pop("_edit_indices", None)
    return MENU


async def bot_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    try:
        df = load_data_from_gsheet()
        results = df[df["שם לקוח"].str.contains(query, case=False, na=False)]
        if results.empty:
            await update.message.reply_text(f"לא נמצאו תוצאות עבור '{query}'.", reply_markup=_menu_markup())
        else:
            lines = [f"🔍 נמצאו {len(results)} עבודות עבור '{query}':\n"]
            for _, row in results.tail(10).iterrows():
                lines.append(
                    f"• {row.get('תאריך','')} | {row.get('עבודה','')} | "
                    f"{row.get('שם חלקה','')} | {row.get('כמות','')}"
                )
            if len(results) > 10:
                lines.append(f"\n...ועוד {len(results)-10} תוצאות נוספות")
            await update.message.reply_text("\n".join(lines), reply_markup=_menu_markup())
    except Exception as e:
        await update.message.reply_text(f"שגיאה בחיפוש: {e}", reply_markup=_menu_markup())
    return MENU


async def client_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["שם לקוח"] = update.message.text.strip()
    await update.message.reply_text("מה התאריך? (YYYY-MM-DD או 'היום')", reply_markup=ReplyKeyboardRemove())
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
    await update.message.reply_text(
        "הערות (אם אין, לחץ על הכפתור):",
        reply_markup=ReplyKeyboardMarkup(NOTES_KEYBOARD, one_time_keyboard=True, resize_keyboard=True),
    )
    return NOTE


async def note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["הערות"] = "" if text == "ללא הערות" else text
    summary = "\n".join(f"• {k}: {v}" for k, v in context.user_data.items())
    await update.message.reply_text(
        f"סיכום לפני שמירה:\n\n{summary}\n\nלחץ כן לשמירה או לא לביטול.",
        reply_markup=ReplyKeyboardMarkup(CONFIRM_KEYBOARD, resize_keyboard=True),
    )
    return CONFIRM


async def note_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    context.user_data["הערות"] = f"[תמונה: {photo.file_id}]"
    summary = "\n".join(f"• {k}: {v}" for k, v in context.user_data.items() if not k.startswith("_"))
    await update.message.reply_text(
        f"📷 תמונה התקבלה.\n\nסיכום:\n\n{summary}\n\nלחץ כן לשמירה או לא לביטול.",
        reply_markup=ReplyKeyboardMarkup(CONFIRM_KEYBOARD, resize_keyboard=True),
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "כן":
        try:
            entry = WorkEntry.from_bot(context.user_data, update.message.from_user.full_name)
            append_row_to_gsheet(entry)
            _log_audit("add", entry.entered_by, f"{entry.client} | {entry.date} | {entry.task}")
            await update.message.reply_text("✅ נשמר בהצלחה!")
        except ValueError as e:
            await update.message.reply_text(f"❌ שגיאת אימות: {e}")
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה בשמירה: {e}")
    else:
        await update.message.reply_text("❌ בוטל.")
    context.user_data.clear()
    await update.message.reply_text("מה תרצה לעשות?", reply_markup=_menu_markup())
    return MENU


async def bot_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        df = load_data_from_gsheet()
        if df.empty:
            await update.message.reply_text("אין עבודות למחיקה.", reply_markup=_menu_markup())
            return MENU
        last = df.iloc[-1]
        detail = f"{last.get('שם לקוח','')} | {last.get('תאריך','')} | {last.get('עבודה','')}"
        save_data_to_gsheet(df.iloc[:-1].reset_index(drop=True))
        _log_audit("undo", update.message.from_user.full_name, detail)
        await update.message.reply_text(f"✅ הרשומה האחרונה נמחקה:\n{detail}", reply_markup=_menu_markup())
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}", reply_markup=_menu_markup())
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
            NOTE:     [
                MessageHandler(filters.TEXT & ~filters.COMMAND, note),
                MessageHandler(filters.PHOTO, note_photo),
            ],
            CONFIRM:  [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
            SEARCH:   [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_search_results)],
            EDIT_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_edit_select)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("undo", bot_undo),
            CommandHandler("start", start),
        ],
    )
    telegram_app.add_handler(conv)

    async def _scheduled_reports():
        while True:
            now = datetime.now()
            next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= next_8am:
                next_8am += timedelta(days=1)
            await asyncio.sleep((next_8am - now).total_seconds())
            try:
                df = load_data_from_gsheet()
                subs = _get_subscribers()
                if not subs:
                    continue

                # Daily summary
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                y_df = df[df["תאריך"] == yesterday]
                if not y_df.empty:
                    lines = [f"☀️ סיכום יום {yesterday} — {len(y_df)} עבודות:\n"]
                    for _, row in y_df.iterrows():
                        lines.append(
                            f"• {row.get('שם לקוח','')} | {row.get('עבודה','')} | "
                            f"{row.get('שם חלקה','')} | {row.get('כמות','')}"
                        )
                    daily_msg = "\n".join(lines)
                    for chat_id in subs:
                        try:
                            await telegram_app.bot.send_message(chat_id=chat_id, text=daily_msg)
                        except Exception:
                            pass

                # Weekly summary every Monday
                if datetime.now().weekday() == 0:
                    week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                    week_df = df[df["תאריך"] >= week_start]
                    if not week_df.empty:
                        top_clients = week_df["שם לקוח"].value_counts().head(3)
                        lines = [f"📊 סיכום שבועי (7 ימים אחרונים) — {len(week_df)} עבודות:\n"]
                        lines.append("לקוחות מובילים:")
                        for client, cnt in top_clients.items():
                            lines.append(f"  • {client}: {cnt}")
                        weekly_msg = "\n".join(lines)
                        for chat_id in subs:
                            try:
                                await telegram_app.bot.send_message(chat_id=chat_id, text=weekly_msg)
                            except Exception:
                                pass
                # Client reminders — clients with no entry in 14 days
                if not df.empty:
                    threshold = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
                    last_per_client = df.groupby("שם לקוח")["תאריך"].max()
                    inactive = last_per_client[last_per_client < threshold]
                    if not inactive.empty:
                        lines = ["⏰ תזכורת — לקוחות ללא עבודה ב-14 ימים האחרונים:\n"]
                        for client_name, last_dt in inactive.items():
                            lines.append(f"• {client_name} (אחרון: {last_dt})")
                        reminder_msg = "\n".join(lines)
                        for chat_id in subs:
                            try:
                                await telegram_app.bot.send_message(chat_id=chat_id, text=reminder_msg)
                            except Exception:
                                pass
            except Exception as e:
                print(f"[BOT] Scheduled report error: {e}")

    async def _run():
        asyncio.create_task(_scheduled_reports())
        async with telegram_app:
            await telegram_app.updater.start_polling()
            await telegram_app.start()
            await asyncio.Event().wait()

    loop.run_until_complete(_run())


# ── Flask App ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gadash-dev-secret-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

_current_password = os.environ.get("WEB_PASSWORD", "gadash2025")

# ── CSRF (manual, no extra dependency needed) ──────────────────────────────────

def _get_csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(32)
    return session["_csrf"]

app.jinja_env.globals["csrf_token"] = _get_csrf_token


@app.before_request
def _csrf_protect():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.endpoint in ("login", "static"):
        return
    if not session.get("logged_in"):
        return
    token = (request.form.get("csrf_token")
             or request.headers.get("X-CSRFToken"))
    if not token or token != session.get("_csrf"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "CSRF token invalid"}), 403
        flash("בקשה לא תקינה (CSRF) ❌", "danger")
        return redirect(request.referrer or url_for("index"))


# ── Rate limiter on login ──────────────────────────────────────────────────────

_login_attempts: dict = {}
_LOGIN_MAX  = 5
_LOGIN_WINDOW = 60  # seconds


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= _LOGIN_MAX


def _record_attempt(ip: str):
    _login_attempts.setdefault(ip, []).append(time.time())


# ── Auth ───────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    ip = request.remote_addr
    if request.method == "POST":
        if _check_rate_limit(ip):
            flash("יותר מדי ניסיונות — המתן דקה ❌", "danger")
            return render_template("login.html")
        if request.form.get("password", "") == _current_password:
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("index"))
        _record_attempt(ip)
        remaining = _LOGIN_MAX - len(_login_attempts.get(ip, []))
        flash(f"סיסמה שגויה ❌ ({remaining} ניסיונות נותרו)", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    global _current_password
    if request.method == "POST":
        old  = request.form.get("old_password", "")
        new1 = request.form.get("new_password", "")
        new2 = request.form.get("confirm_password", "")
        if old != _current_password:
            flash("הסיסמה הנוכחית שגויה ❌", "danger")
        elif new1 != new2:
            flash("הסיסמאות החדשות אינן תואמות ❌", "danger")
        elif len(new1) < 4:
            flash("הסיסמה חייבת לכלול לפחות 4 תווים ❌", "danger")
        else:
            _current_password = new1
            flash("הסיסמה שונתה ✅ (שינוי זמני — לקבוע עדכן WEB_PASSWORD)", "success")
    return render_template("change_password.html")


# ── Filters helper ─────────────────────────────────────────────────────────────

def _apply_filters(df):
    client_filter = request.args.get("client", "").strip()
    date_from     = request.args.get("date_from", "").strip()
    date_to       = request.args.get("date_to", "").strip()
    task_filter   = request.args.get("task", "").strip()
    if client_filter:
        df = df[df["שם לקוח"].str.contains(client_filter, case=False, na=False)]
    if date_from:
        df = df[df["תאריך"] >= date_from]
    if date_to:
        df = df[df["תאריך"] <= date_to]
    if task_filter:
        df = df[df["עבודה"] == task_filter]
    return df


def _autocomplete_lists(df: pd.DataFrame) -> dict:
    return {
        "client_list":   sorted(df["שם לקוח"].dropna().unique().tolist()),
        "field_list":    sorted(df["שם חלקה"].dropna().unique().tolist()),
        "operator_list": sorted(df["מפעיל"].dropna().unique().tolist()),
        "tool_list":     sorted(df["כלי"].dropna().unique().tolist()),
    }


# ── Web routes ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    try:
        df = load_data_from_gsheet()
        total_count  = len(df)
        month_prefix = date.today().strftime("%Y-%m")
        month_count  = int(df["תאריך"].str.startswith(month_prefix).sum()) if total_count else 0
        top_client   = df["שם לקוח"].mode()[0] if total_count else "—"
        top_task     = df["עבודה"].mode()[0] if total_count else "—"

        df = df.reset_index().rename(columns={"index": "_row_id"})
        df = df.sort_values(by="תאריך", ascending=False)
        df = _apply_filters(df)

        filtered_count = len(df)
        page           = request.args.get("page", 1, type=int)
        total_pages    = max(1, math.ceil(filtered_count / PAGE_SIZE))
        page           = max(1, min(page, total_pages))
        df             = df.iloc[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

        full_df       = load_data_from_gsheet()
        task_counts   = full_df["עבודה"].value_counts().to_dict()
        client_counts = full_df["שם לקוח"].value_counts().head(6).to_dict()
        auto          = _autocomplete_lists(full_df)

        return render_template(
            "index.html",
            records=df.to_dict(orient="records"),
            total_count=total_count,
            filtered_count=filtered_count,
            month_count=month_count,
            top_client=top_client,
            top_task=top_task,
            client_filter=request.args.get("client", "").strip(),
            date_from=request.args.get("date_from", "").strip(),
            date_to=request.args.get("date_to", "").strip(),
            task_filter=request.args.get("task", "").strip(),
            task_options=sorted(VALID_TASKS),
            task_counts=task_counts,
            client_counts=client_counts,
            page=page,
            total_pages=total_pages,
            today=date.today().strftime("%Y-%m-%d"),
            **auto,
        )
    except Exception as e:
        return render_template(
            "index.html",
            records=[], total_count=0, filtered_count=0,
            month_count=0, top_client="—", top_task="—",
            client_filter="", date_from="", date_to="", task_filter="",
            task_options=[], task_counts={}, client_counts={},
            page=1, total_pages=1, error=str(e),
        )


@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    today = date.today().strftime("%Y-%m-%d")
    if request.method == "POST":
        try:
            entry = WorkEntry.from_form(request.form, entered_by="Web")
            append_row_to_gsheet(entry)
            _log_audit("add", "Web", f"{entry.client} | {entry.date} | {entry.task}")
            flash("הרשומה נוספה בהצלחה ✅", "success")
            return redirect(url_for("index"))
        except ValueError as e:
            flash(f"שגיאת אימות: {e} ❌", "danger")
        except Exception as e:
            flash(f"שגיאה בשמירה: {e} ❌", "danger")
    prefill = {col: request.args.get(col, "") for col in COLUMNS if col != "מזין"}
    if not prefill.get("תאריך"):
        prefill["תאריך"] = today
    lists = {}
    try:
        lists = _autocomplete_lists(load_data_from_gsheet())
    except Exception:
        pass
    return render_template("add.html", today=today, prefill=prefill, **lists)


@app.route("/duplicate/<int:row_id>")
@login_required
def duplicate(row_id):
    try:
        df  = load_data_from_gsheet()
        row = df.iloc[row_id].to_dict()
        row["תאריך"] = date.today().strftime("%Y-%m-%d")
        qs = urlencode({k: v for k, v in row.items() if k != "מזין"})
        return redirect(f"/add?{qs}")
    except Exception:
        return redirect(url_for("add"))


@app.route("/edit/<int:row_id>", methods=["GET", "POST"])
@login_required
def edit(row_id):
    df = load_data_from_gsheet()
    if request.method == "POST":
        try:
            original_entered_by = df.at[row_id, "מזין"] if row_id < len(df) else "Web"
            entry = WorkEntry.from_form(request.form, entered_by=original_entered_by)
            for key, value in entry.to_dict().items():
                df.at[row_id, key] = value
            save_data_to_gsheet(df)
            _log_audit("edit", "Web", f"row {row_id}: {entry.client} | {entry.date}")
            flash("הרשומה עודכנה בהצלחה ✅", "success")
            return redirect(url_for("index"))
        except ValueError as e:
            flash(f"שגיאת אימות: {e} ❌", "danger")
            try:
                lists = _autocomplete_lists(df)
                return render_template("edit.html", row=df.iloc[row_id].to_dict(), row_id=row_id, **lists)
            except Exception:
                pass
        except Exception as e:
            flash(f"שגיאה בעדכון: {e} ❌", "danger")
    try:
        lists = _autocomplete_lists(df)
        return render_template("edit.html", row=df.iloc[row_id].to_dict(), row_id=row_id, **lists)
    except Exception as e:
        return f"שגיאה בטעינת שורה: {e}"


@app.route("/delete/<int:row_id>", methods=["POST"])
@login_required
def delete(row_id):
    df = load_data_from_gsheet()
    detail = df.iloc[row_id].get("שם לקוח", str(row_id)) if row_id < len(df) else str(row_id)
    df = df.drop(index=row_id).reset_index(drop=True)
    save_data_to_gsheet(df)
    _log_audit("delete", "Web", f"row {row_id}: {detail}")
    flash("הרשומה נמחקה ✅", "success")
    return redirect(url_for("index"))


@app.route("/bulk-delete", methods=["POST"])
@login_required
def bulk_delete():
    row_ids = [int(r) for r in request.form.getlist("row_ids")]
    if not row_ids:
        flash("לא נבחרו רשומות ⚠️", "warning")
        return redirect(url_for("index"))
    df = load_data_from_gsheet()
    df = df.drop(index=row_ids).reset_index(drop=True)
    save_data_to_gsheet(df)
    _log_audit("bulk-delete", "Web", f"{len(row_ids)} rows: {row_ids}")
    flash(f"{len(row_ids)} רשומות נמחקו ✅", "success")
    return redirect(url_for("index"))


@app.route("/summary")
@login_required
def summary():
    try:
        df = load_data_from_gsheet()
        if df.empty:
            return render_template("summary.html", monthly=[], client_totals=[], task_types=[])
        df["חודש"] = pd.to_datetime(df["תאריך"], errors="coerce").dt.strftime("%Y-%m")
        df = df.dropna(subset=["חודש"])
        pivot = df.groupby(["חודש", "עבודה"]).size().unstack(fill_value=0)
        task_types = list(pivot.columns)
        pivot["סה\"כ"] = pivot.sum(axis=1)
        monthly = pivot.reset_index().sort_values("חודש", ascending=False).to_dict(orient="records")
        client_totals = (
            df.groupby("שם לקוח").size()
            .sort_values(ascending=False).head(15)
            .reset_index().rename(columns={0: "סה\"כ"})
            .to_dict(orient="records")
        )
        return render_template("summary.html", monthly=monthly, client_totals=client_totals, task_types=task_types)
    except Exception as e:
        return render_template("summary.html", monthly=[], client_totals=[], task_types=[], error=str(e))


@app.route("/audit")
@login_required
def audit():
    entries = _read_audit_log(200)
    return render_template("audit.html", entries=entries)


@app.route("/print")
@login_required
def print_report():
    df = load_data_from_gsheet()
    df = _apply_filters(df)
    return render_template(
        "print_report.html",
        records=df.to_dict(orient="records"),
        client_filter=request.args.get("client", "").strip(),
        date_from=request.args.get("date_from", "").strip(),
        date_to=request.args.get("date_to", "").strip(),
        task_filter=request.args.get("task", "").strip(),
        generated=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


@app.route("/import", methods=["GET", "POST"])
@login_required
def import_data():
    if request.method == "POST":
        file = request.files.get("file")
        if file and file.filename.endswith(".xlsx"):
            try:
                new_df = pd.read_excel(file)
                existing_df = load_data_from_gsheet()
                key_cols = ["שם לקוח", "תאריך", "עבודה", "שם חלקה"]
                skipped = 0
                if not existing_df.empty:
                    existing_keys = set(
                        tuple(str(v) for v in row)
                        for row in existing_df[key_cols].values.tolist()
                    )
                    unique_rows, skip_rows = [], []
                    for _, row in new_df.iterrows():
                        key = tuple(str(row.get(c, "")) for c in key_cols)
                        (skip_rows if key in existing_keys else unique_rows).append(row)
                    skipped = len(skip_rows)
                    new_df = pd.DataFrame(unique_rows, columns=new_df.columns) if unique_rows else pd.DataFrame()
                if skipped:
                    flash(f"⚠️ {skipped} שורות כפולות דולגו", "warning")
                if not new_df.empty:
                    combined = pd.concat([existing_df, new_df], ignore_index=True) if not existing_df.empty else new_df
                    save_data_to_gsheet(combined)
                    _log_audit("import", "Web", f"{len(new_df)} rows imported, {skipped} skipped")
                    flash(f"{len(new_df)} שורות יובאו בהצלחה ✅", "success")
                    return redirect(url_for("index"))
                else:
                    flash("כל השורות בקובץ כבר קיימות ⚠️", "warning")
            except Exception as e:
                flash(f"שגיאה בייבוא: {e} ❌", "danger")
        else:
            flash("יש לבחור קובץ Excel תקני (.xlsx) ❌", "danger")
    return render_template("import.html")


@app.route("/export")
@login_required
def export():
    df = load_data_from_gsheet()
    if df.empty:
        flash("אין נתונים לייצוא ❌", "danger")
        return redirect(url_for("index"))
    df = _apply_filters(df)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Data")
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="gadash_data.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/export/csv")
@login_required
def export_csv():
    df = load_data_from_gsheet()
    if df.empty:
        flash("אין נתונים לייצוא ❌", "danger")
        return redirect(url_for("index"))
    df = _apply_filters(df)
    output = BytesIO()
    df.to_csv(output, index=False, encoding="utf-8-sig")
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="gadash_data.csv",
                     mimetype="text/csv; charset=utf-8-sig")


# ── REST API ───────────────────────────────────────────────────────────────────

@app.route("/api/docs")
@login_required
def api_docs():
    return render_template("api_docs.html")


@app.route("/api/entries")
@login_required
def api_entries():
    df = load_data_from_gsheet()
    df = _apply_filters(df)
    return jsonify(df.fillna("").to_dict(orient="records"))


@app.route("/api/entries/<int:row_id>", methods=["PATCH"])
@login_required
def api_patch_entry(row_id):
    data  = request.get_json(force=True, silent=True) or {}
    field = data.get("field", "")
    value = str(data.get("value", ""))
    editable = [c for c in COLUMNS if c != "מזין"]
    if field not in editable:
        return jsonify({"error": f"שדה לא תקין: {field}"}), 400
    df = load_data_from_gsheet()
    if row_id >= len(df):
        return jsonify({"error": "שורה לא קיימת"}), 404
    df.at[row_id, field] = value
    save_data_to_gsheet(df)
    _log_audit("edit-inline", "Web", f"row {row_id}: {field}={value}")
    return jsonify({"ok": True, "row_id": row_id, "field": field, "value": value})


# ── Start bot thread ───────────────────────────────────────────────────────────

if os.environ.get("BOT_TOKEN"):
    _bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    _bot_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
