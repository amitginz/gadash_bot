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

MENU, CLIENT, DATE, TASK, FIELD, CROP, AMOUNT, HOURS, TOOL, OPERATOR, NOTE, CONFIRM, SEARCH, EDIT_SELECT = range(14)

TASK_CHOICES    = [["חריש", "ריסוס"], ["קציר", "דיסוק"], ["אחר"]]
CONFIRM_KEYBOARD = [["כן", "לא"]]
NOTES_KEYBOARD  = [["ללא הערות"]]
MENU_KEYBOARD   = [
    ["הזן עבודה חדשה"],
    ["5 עבודות אחרונות", "חפש לפי לקוח"],
    ["סטטיסטיקות", "ערוך רשומה"],
    ["סיים"],
]

COLUMNS     = ["שם לקוח", "תאריך", "עבודה", "שם חלקה", "גידול", "כמות", "שעות", "כלי", "מפעיל", "הערות", "מזין"]
VALID_TASKS = {"חריש", "ריסוס", "קציר", "דיסוק", "אחר"}


@dataclass
class WorkEntry:
    client:     str
    date:       str
    task:       str
    field_name: str = ""
    crop:       str = ""
    amount:     str = ""
    hours:      str = ""
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
            self.crop, self.amount, self.hours,
            self.tool, self.operator, self.notes, self.entered_by,
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
            crop=str(d.get("גידול", "")),
            amount=str(d.get("כמות", "")),
            hours=str(d.get("שעות", "")),
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
            crop=form.get("גידול", ""),
            amount=form.get("כמות", ""),
            hours=form.get("שעות", ""),
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
            crop=user_data.get("גידול", ""),
            amount=user_data.get("כמות", ""),
            hours=user_data.get("שעות", ""),
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


_GS_SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _init_gs_client():
    """Initialise _gs_client. Must be called while _gs_lock is held."""
    global _gs_client
    if _gs_client is not None:
        return
    raw = os.environ.get("GOOGLE_CREDS")
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=_GS_SCOPE)
    elif os.path.exists("credentials.json"):
        creds = Credentials.from_service_account_file("credentials.json", scopes=_GS_SCOPE)
    else:
        raise RuntimeError("No Google credentials found.")
    _gs_client = gspread.authorize(creds)


def _get_sheet():
    global _gs_client
    last_exc = None
    for attempt in range(3):
        with _gs_lock:
            try:
                _init_gs_client()
                return _gs_client.open("Gadash Data").sheet1
            except Exception as e:
                last_exc = e
                _gs_client = None
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    raise last_exc


def _get_settings_sheet():
    """Opens (or creates) the Settings worksheet. Returns None on failure."""
    global _gs_client
    with _gs_lock:
        try:
            _init_gs_client()
            wb = _gs_client.open("Gadash Data")
            try:
                return wb.worksheet("Settings")
            except gspread.WorksheetNotFound:
                return wb.add_worksheet("Settings", rows=10, cols=2)
        except Exception:
            _gs_client = None
            return None


def _get_fieldcoords_sheet():
    """Opens (or creates) the FieldCoords worksheet."""
    global _gs_client
    with _gs_lock:
        try:
            _init_gs_client()
            wb = _gs_client.open("Gadash Data")
            try:
                return wb.worksheet("FieldCoords")
            except gspread.WorksheetNotFound:
                ws = wb.add_worksheet("FieldCoords", rows=200, cols=3)
                ws.append_row(["שם חלקה", "lat", "lng"])
                return ws
        except Exception:
            _gs_client = None
            return None


def _load_field_coords() -> dict:
    """Returns {field_name: {lat: float, lng: float}}."""
    try:
        ws = _get_fieldcoords_sheet()
        if not ws:
            return {}
        rows = ws.get_all_values()
        coords = {}
        for row in rows[1:]:
            if len(row) >= 3 and row[0] and row[1] and row[2]:
                try:
                    coords[row[0]] = {"lat": float(row[1]), "lng": float(row[2])}
                except ValueError:
                    pass
        return coords
    except Exception:
        return {}


def _save_field_coord(name: str, lat: float, lng: float):
    """Upsert lat/lng for a field. Overwrites existing row if found."""
    try:
        ws = _get_fieldcoords_sheet()
        if not ws:
            return
        rows = ws.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == name:
                ws.update([[name, lat, lng]], f"A{i}:C{i}")
                return
        ws.append_row([name, lat, lng])
    except Exception as e:
        print(f"[FieldCoords] save error: {e}")


def _load_passwords():
    """Read persisted passwords from the Settings sheet (falls back to env vars)."""
    global _current_password, _worker_password
    try:
        ws = _get_settings_sheet()
        if not ws:
            return
        rows = ws.get_all_values()
        settings = {r[0]: r[1] for r in rows if len(r) >= 2 and r[0] and r[1]}
        if settings.get("web_password"):
            _current_password = settings["web_password"]
        if settings.get("worker_password"):
            _worker_password = settings["worker_password"]
    except Exception:
        pass


def _save_passwords():
    """Persist current passwords to the Settings sheet."""
    try:
        ws = _get_settings_sheet()
        if ws:
            ws.update([["web_password", _current_password],
                       ["worker_password", _worker_password]], "A1")
    except Exception:
        pass


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
    await update.message.reply_text(
        "מה הגידול בחלקה? (לדוגמה: חיטה, תירס, כותנה — או 'דלג')",
        reply_markup=ReplyKeyboardMarkup([["דלג"]], one_time_keyboard=True, resize_keyboard=True),
    )
    return CROP


async def crop_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["גידול"] = "" if text == "דלג" else text
    await update.message.reply_text("כמות (למשל 30 דונם):", reply_markup=ReplyKeyboardRemove())
    return AMOUNT


async def amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["כמות"] = update.message.text.strip()
    await update.message.reply_text(
        "כמה שעות עבודה? (ספרה או 'דלג')",
        reply_markup=ReplyKeyboardMarkup([["דלג"]], one_time_keyboard=True, resize_keyboard=True),
    )
    return HOURS


async def hours_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["שעות"] = "" if text == "דלג" else text
    await update.message.reply_text("איזה כלי שימש?", reply_markup=ReplyKeyboardRemove())
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
            CROP:     [MessageHandler(filters.TEXT & ~filters.COMMAND, crop_step)],
            AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, amount)],
            HOURS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, hours_step)],
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

    async def _broadcast(subs, text):
        for chat_id in subs:
            try:
                await telegram_app.bot.send_message(chat_id=chat_id, text=text)
            except Exception:
                pass

    async def _scheduled_reports():
        sent = set()  # (date_str, event_name)
        while True:
            await asyncio.sleep(1800)  # check every 30 minutes
            now  = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            hour = now.hour

            # ── Morning reports at 08:00 ──────────────────────────────────────
            if hour == 8 and (date_str, "morning") not in sent:
                sent.add((date_str, "morning"))
                sent = {k for k in sent if k[0] == date_str}
                try:
                    df   = load_data_from_gsheet()
                    subs = _get_subscribers()
                    if not subs:
                        continue

                    # Daily summary
                    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                    y_df = df[df["תאריך"] == yesterday]
                    if not y_df.empty:
                        y_df = y_df.copy()
                        y_df["_שעות"] = pd.to_numeric(y_df["שעות"], errors="coerce").fillna(0)
                        total_h = y_df["_שעות"].sum()
                        lines = [f"☀️ סיכום יום {yesterday} — {len(y_df)} עבודות"]
                        if total_h > 0:
                            lines[0] += f" | {total_h:.1f} שעות"
                        lines.append("")
                        for _, row in y_df.iterrows():
                            h = f" | {float(row['_שעות']):.1f}ש׳" if float(row['_שעות']) > 0 else ""
                            lines.append(
                                f"• {row.get('שם לקוח','')} | {row.get('עבודה','')} | "
                                f"{row.get('שם חלקה','')} | {row.get('גידול','')}{h}"
                            )
                        await _broadcast(subs, "\n".join(lines))

                    # Weekly summary every Monday
                    if now.weekday() == 0:
                        week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
                        w_df = df[df["תאריך"] >= week_start].copy()
                        if not w_df.empty:
                            w_df["_שעות"] = pd.to_numeric(w_df["שעות"], errors="coerce").fillna(0)
                            total_h = w_df["_שעות"].sum()
                            top_clients = w_df["שם לקוח"].value_counts().head(3)
                            lines = [f"📊 סיכום שבועי — {len(w_df)} עבודות | {total_h:.1f} שעות\n"]
                            lines.append("👤 לקוחות מובילים:")
                            for client, cnt in top_clients.items():
                                lines.append(f"  • {client}: {cnt}")
                            # Hours by field
                            field_h = (
                                w_df[w_df["_שעות"] > 0]
                                .groupby("שם חלקה")["_שעות"].sum()
                                .sort_values(ascending=False).head(5)
                            )
                            if not field_h.empty:
                                lines.append("\n📍 שעות לפי חלקה:")
                                for fn, h in field_h.items():
                                    lines.append(f"  • {fn or 'לא צוין'}: {h:.1f}ש׳")
                            # Hours by crop
                            crop_h = (
                                w_df[w_df["_שעות"] > 0]
                                .groupby("גידול")["_שעות"].sum()
                                .sort_values(ascending=False).head(5)
                            )
                            if not crop_h.empty:
                                lines.append("\n🌾 שעות לפי גידול:")
                                for cn, h in crop_h.items():
                                    lines.append(f"  • {cn or 'לא צוין'}: {h:.1f}ש׳")
                            await _broadcast(subs, "\n".join(lines))

                    # Inactive client reminders — no entry in 14 days
                    if not df.empty:
                        threshold = (now - timedelta(days=14)).strftime("%Y-%m-%d")
                        last_per_client = df.groupby("שם לקוח")["תאריך"].max()
                        inactive = last_per_client[last_per_client < threshold]
                        if not inactive.empty:
                            lines = ["⏰ תזכורת — לקוחות ללא עבודה ב-14 ימים:\n"]
                            for client_name, last_dt in inactive.items():
                                lines.append(f"• {client_name} (אחרון: {last_dt})")
                            await _broadcast(subs, "\n".join(lines))
                except Exception as e:
                    print(f"[BOT] Morning report error: {e}")

            # ── Evening worker reminder at 18:00 ─────────────────────────────
            if hour == 18 and (date_str, "evening") not in sent:
                sent.add((date_str, "evening"))
                try:
                    subs = _get_subscribers()
                    if subs:
                        msg = (
                            f"📋 תזכורת סוף יום — {date_str}\n\n"
                            "אל תשכח לדווח על שעות העבודה שלך היום!\n"
                            f"🔗 {WEB_APP_URL}/worker\n\n"
                            "לדיווח דרך הבוט — שלח 'הזן עבודה חדשה'"
                        )
                        await _broadcast(subs, msg)
                except Exception as e:
                    print(f"[BOT] Evening reminder error: {e}")

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
_worker_password  = os.environ.get("WORKER_PASSWORD", "worker2025")

# Load persisted passwords from Google Sheets (overrides env vars if saved before)
try:
    _load_passwords()
except Exception:
    pass

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
    if not session.get("logged_in") and not session.get("worker_logged_in"):
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


def worker_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("worker_logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    if session.get("worker_logged_in"):
        return redirect(url_for("worker_index"))
    ip = request.remote_addr
    if request.method == "POST":
        if _check_rate_limit(ip):
            flash("יותר מדי ניסיונות — המתן דקה ❌", "danger")
            role = request.form.get("role", "manager")
            return render_template("login.html", selected_role=role)
        role = request.form.get("role", "manager")
        pwd  = request.form.get("password", "")
        if role == "worker":
            name = request.form.get("name", "").strip() or "עובד"
            if pwd == _worker_password:
                session.permanent = True
                session["worker_logged_in"] = True
                session["worker_name"]      = name
                return redirect(url_for("worker_index"))
            _record_attempt(ip)
            flash("סיסמה שגויה ❌", "danger")
            return render_template("login.html", selected_role="worker", form_name=name)
        else:
            if pwd == _current_password:
                session.permanent = True
                session["logged_in"] = True
                return redirect(url_for("index"))
            _record_attempt(ip)
            remaining = _LOGIN_MAX - len(_login_attempts.get(ip, []))
            flash(f"סיסמה שגויה ❌ ({remaining} ניסיונות נותרו)", "danger")
            return render_template("login.html", selected_role="manager")
    return render_template("login.html", selected_role="manager")


@app.route("/health")
def health():
    try:
        _get_sheet()
        return jsonify({"status": "ok", "sheets": "connected"})
    except Exception as e:
        return jsonify({"status": "degraded", "sheets": str(e)}), 503


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
            _save_passwords()
            flash("הסיסמה שונתה בהצלחה ✅", "success")
    return render_template("change_password.html")


# ── Filters helper ─────────────────────────────────────────────────────────────

def _apply_filters(df):
    q         = request.args.get("q",         "").strip()
    client    = request.args.get("client",    "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to",   "").strip()
    task      = request.args.get("task",      "").strip()

    if q:
        mask = df.apply(
            lambda row: row.astype(str).str.contains(q, case=False, na=False).any(),
            axis=1,
        )
        df = df[mask]
    if client:
        df = df[df["שם לקוח"].str.contains(client, case=False, na=False)]
    if date_from:
        df = df[df["תאריך"] >= date_from]
    if date_to:
        df = df[df["תאריך"] <= date_to]
    if task:
        df = df[df["עבודה"] == task]
    return df


def _autocomplete_lists(df: pd.DataFrame) -> dict:
    return {
        "client_list":   sorted(df["שם לקוח"].dropna().unique().tolist()),
        "field_list":    sorted(df["שם חלקה"].dropna().unique().tolist()),
        "crop_list":     sorted(df["גידול"].dropna().replace("", pd.NA).dropna().unique().tolist()),
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
            q_filter=request.args.get("q", "").strip(),
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
            q_filter="", client_filter="", date_from="", date_to="", task_filter="",
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


# ── Worker portal ──────────────────────────────────────────────────────────────

@app.route("/worker/login")
def worker_login():
    return redirect(url_for("login"))


@app.route("/worker/logout")
def worker_logout():
    session.pop("worker_logged_in", None)
    session.pop("worker_name", None)
    return redirect(url_for("login"))


@app.route("/worker/change-password", methods=["POST"])
@worker_required
def worker_change_password():
    global _worker_password
    old  = request.form.get("old_password", "")
    new1 = request.form.get("new_password", "")
    new2 = request.form.get("confirm_password", "")
    if old != _worker_password:
        flash("הסיסמה הנוכחית שגויה ❌", "danger")
    elif new1 != new2:
        flash("הסיסמאות החדשות אינן תואמות ❌", "danger")
    elif len(new1) < 4:
        flash("הסיסמה חייבת לכלול לפחות 4 תווים ❌", "danger")
    else:
        _worker_password = new1
        _save_passwords()
        flash("הסיסמה שונתה בהצלחה ✅", "success")
    return redirect(url_for("worker_index"))


@app.route("/worker", methods=["GET", "POST"])
@worker_required
def worker_index():
    worker_name = session.get("worker_name", "עובד")
    today = date.today().strftime("%Y-%m-%d")
    lists = {}
    try:
        lists = _autocomplete_lists(load_data_from_gsheet())
    except Exception:
        pass

    if request.method == "POST":
        try:
            entry = WorkEntry.from_form(request.form, entered_by=worker_name)
            append_row_to_gsheet(entry)
            _log_audit("add", worker_name, f"{entry.client} | {entry.date} | {entry.task}")
            flash("הרשומה נוספה בהצלחה ✅", "success")
        except ValueError as e:
            flash(f"שגיאת אימות: {e} ❌", "danger")
        except Exception as e:
            flash(f"שגיאה בשמירה: {e} ❌", "danger")
        return redirect(url_for("worker_index"))

    # Show only this worker's recent entries
    try:
        df = load_data_from_gsheet()
        my_df = df[df["מזין"].str.contains(worker_name, case=False, na=False)]
        recent = my_df.tail(20).sort_values("תאריך", ascending=False).to_dict(orient="records")
        my_count = len(my_df)
    except Exception:
        recent, my_count = [], 0

    return render_template("worker_index.html",
                           worker_name=worker_name, today=today,
                           recent=recent, my_count=my_count, **lists)


@app.route("/client-report")
@login_required
def client_report():
    client_name  = request.args.get("client", "").strip()
    date_from    = request.args.get("date_from", "").strip()
    date_to      = request.args.get("date_to", "").strip()
    try:
        df = load_data_from_gsheet()
        auto = _autocomplete_lists(df)
        if not client_name:
            return render_template("client_report.html", client_name="", records=[],
                                   total_hours=0, total_entries=0, date_range="",
                                   field_hours=[], crop_hours=[], task_counts=[],
                                   monthly_hours=[], date_from=date_from, date_to=date_to,
                                   **auto)
        cdf = df[df["שם לקוח"].str.contains(client_name, case=False, na=False)].copy()
        if date_from:
            cdf = cdf[cdf["תאריך"] >= date_from]
        if date_to:
            cdf = cdf[cdf["תאריך"] <= date_to]
        cdf["_שעות"] = pd.to_numeric(cdf["שעות"], errors="coerce").fillna(0)
        total_hours   = float(cdf["_שעות"].sum())
        total_entries = len(cdf)
        date_range    = f"{cdf['תאריך'].min()} — {cdf['תאריך'].max()}" if total_entries else "—"

        field_hours = (
            cdf.groupby("שם חלקה")["_שעות"].sum()
            .reset_index().rename(columns={"שם חלקה": "label", "_שעות": "hours"})
            .sort_values("hours", ascending=False).to_dict(orient="records")
        )
        crop_hours = (
            cdf.groupby("גידול")["_שעות"].sum()
            .reset_index().rename(columns={"גידול": "label", "_שעות": "hours"})
            .sort_values("hours", ascending=False).to_dict(orient="records")
        )
        task_counts = (
            cdf["עבודה"].value_counts()
            .reset_index().rename(columns={"עבודה": "label", "count": "cnt"})
            .to_dict(orient="records")
        )
        cdf["_month"] = pd.to_datetime(cdf["תאריך"], errors="coerce").dt.strftime("%Y-%m")
        monthly_hours = (
            cdf.groupby("_month").agg(entries=("שם לקוח", "count"), hours=("_שעות", "sum"))
            .reset_index().rename(columns={"_month": "month"})
            .sort_values("month").to_dict(orient="records")
        )
        records = cdf.sort_values("תאריך", ascending=False).to_dict(orient="records")
        return render_template("client_report.html",
                               client_name=client_name, records=records,
                               total_hours=total_hours, total_entries=total_entries,
                               date_range=date_range, field_hours=field_hours,
                               crop_hours=crop_hours, task_counts=task_counts,
                               monthly_hours=monthly_hours,
                               date_from=date_from, date_to=date_to, **auto)
    except Exception as e:
        return render_template("client_report.html", client_name=client_name, records=[],
                               total_hours=0, total_entries=0, date_range="",
                               field_hours=[], crop_hours=[], task_counts=[],
                               monthly_hours=[], date_from=date_from, date_to=date_to,
                               client_list=[], field_list=[], crop_list=[],
                               operator_list=[], tool_list=[], error=str(e))


@app.route("/field-report")
@login_required
def field_report():
    try:
        df = load_data_from_gsheet()
        if df.empty:
            return render_template("field_report.html",
                                   rows=[], crop_pivot=[], crops=[], field_totals=[],
                                   crop_totals=[], total_hours=0,
                                   date_from="", date_to="", client_filter="",
                                   client_list=[], field_list=[], crop_list=[])

        client_filter = request.args.get("client", "").strip()
        date_from     = request.args.get("date_from", "").strip()
        date_to       = request.args.get("date_to", "").strip()

        fdf = df.copy()
        if client_filter:
            fdf = fdf[fdf["שם לקוח"].str.contains(client_filter, case=False, na=False)]
        if date_from:
            fdf = fdf[fdf["תאריך"] >= date_from]
        if date_to:
            fdf = fdf[fdf["תאריך"] <= date_to]

        fdf["_שעות"] = pd.to_numeric(fdf["שעות"], errors="coerce").fillna(0)
        fdf["גידול_label"] = fdf["גידול"].fillna("").replace("", "לא צוין")
        fdf["שם חלקה_label"] = fdf["שם חלקה"].fillna("").replace("", "לא צוין")

        # Totals per field
        field_totals = (
            fdf.groupby("שם חלקה_label")
            .agg(עבודות=("שם לקוח", "count"), שעות=("_שעות", "sum"))
            .reset_index()
            .rename(columns={"שם חלקה_label": "שם חלקה"})
            .sort_values("שעות", ascending=False)
            .to_dict(orient="records")
        )

        # Totals per crop
        crop_totals = (
            fdf.groupby("גידול_label")
            .agg(עבודות=("שם לקוח", "count"), שעות=("_שעות", "sum"))
            .reset_index()
            .rename(columns={"גידול_label": "גידול"})
            .sort_values("שעות", ascending=False)
            .to_dict(orient="records")
        )

        # Pivot: field × crop → hours
        pivot = fdf.pivot_table(
            index="שם חלקה_label", columns="גידול_label",
            values="_שעות", aggfunc="sum", fill_value=0
        )
        crops = list(pivot.columns)
        pivot["סה\"כ"] = pivot.sum(axis=1)
        pivot = pivot.reset_index().rename(columns={"שם חלקה_label": "שם חלקה"})
        crop_pivot = pivot.to_dict(orient="records")

        total_hours = float(fdf["_שעות"].sum())

        auto = _autocomplete_lists(df)
        return render_template(
            "field_report.html",
            crop_pivot=crop_pivot,
            crops=crops,
            field_totals=field_totals,
            crop_totals=crop_totals,
            total_hours=total_hours,
            date_from=date_from,
            date_to=date_to,
            client_filter=client_filter,
            **auto,
        )
    except Exception as e:
        return render_template("field_report.html",
                               rows=[], crop_pivot=[], crops=[], field_totals=[],
                               crop_totals=[], total_hours=0,
                               date_from="", date_to="", client_filter="",
                               client_list=[], field_list=[], crop_list=[],
                               error=str(e))


@app.route("/field-report/print")
@login_required
def field_report_print():
    try:
        df = load_data_from_gsheet()
        client_filter = request.args.get("client", "").strip()
        date_from     = request.args.get("date_from", "").strip()
        date_to       = request.args.get("date_to", "").strip()
        fdf = df.copy()
        if client_filter:
            fdf = fdf[fdf["שם לקוח"].str.contains(client_filter, case=False, na=False)]
        if date_from:
            fdf = fdf[fdf["תאריך"] >= date_from]
        if date_to:
            fdf = fdf[fdf["תאריך"] <= date_to]
        fdf["_שעות"] = pd.to_numeric(fdf["שעות"], errors="coerce").fillna(0)
        fdf["גידול_label"] = fdf["גידול"].fillna("").replace("", "לא צוין")
        fdf["שם חלקה_label"] = fdf["שם חלקה"].fillna("").replace("", "לא צוין")
        field_totals = (
            fdf.groupby("שם חלקה_label")
            .agg(עבודות=("שם לקוח", "count"), שעות=("_שעות", "sum"))
            .reset_index().rename(columns={"שם חלקה_label": "שם חלקה"})
            .sort_values("שעות", ascending=False).to_dict(orient="records")
        )
        crop_totals = (
            fdf.groupby("גידול_label")
            .agg(עבודות=("שם לקוח", "count"), שעות=("_שעות", "sum"))
            .reset_index().rename(columns={"גידול_label": "גידול"})
            .sort_values("שעות", ascending=False).to_dict(orient="records")
        )
        pivot = fdf.pivot_table(
            index="שם חלקה_label", columns="גידול_label",
            values="_שעות", aggfunc="sum", fill_value=0
        )
        crops = list(pivot.columns)
        pivot['סה"כ'] = pivot.sum(axis=1)
        pivot = pivot.reset_index().rename(columns={"שם חלקה_label": "שם חלקה"})
        crop_pivot = pivot.to_dict(orient="records")
        total_hours = float(fdf["_שעות"].sum())
        return render_template("field_report_print.html",
                               crop_pivot=crop_pivot, crops=crops,
                               field_totals=field_totals, crop_totals=crop_totals,
                               total_hours=total_hours, date_from=date_from, date_to=date_to,
                               client_filter=client_filter,
                               generated=datetime.now().strftime("%d/%m/%Y %H:%M"))
    except Exception as e:
        return f"שגיאה: {e}"


# ── AI Summary ────────────────────────────────────────────────────────────────

@app.route("/api/ai-summary", methods=["POST"])
@login_required
def api_ai_summary():
    try:
        df = load_data_from_gsheet()
        if df.empty:
            return jsonify({"summary": "אין נתונים לניתוח."})

        df["_שעות"] = pd.to_numeric(df["שעות"], errors="coerce").fillna(0)
        df["_תאריך"] = pd.to_datetime(df["תאריך"], errors="coerce")

        now = datetime.now()
        cur_m, cur_y = now.month, now.year
        prev_m = cur_m - 1 if cur_m > 1 else 12
        prev_y = cur_y if cur_m > 1 else cur_y - 1

        this_m = df[(df["_תאריך"].dt.month == cur_m) & (df["_תאריך"].dt.year == cur_y)]
        last_m = df[(df["_תאריך"].dt.month == prev_m) & (df["_תאריך"].dt.year == prev_y)]

        def _mode(series):
            m = series.dropna().replace("", None).dropna().mode()
            return m.iloc[0] if not m.empty else "—"

        stats = {
            "month_label":      now.strftime("%m/%Y"),
            "jobs":             int(len(this_m)),
            "hours":            round(float(this_m["_שעות"].sum()), 1),
            "prev_jobs":        int(len(last_m)),
            "prev_hours":       round(float(last_m["_שעות"].sum()), 1),
            "top_client":       _mode(this_m["שם לקוח"]),
            "top_task":         _mode(this_m["עבודה"]),
            "top_operator":     _mode(this_m["מפעיל"]),
            "active_clients":   int(this_m["שם לקוח"].nunique()),
            "active_fields":    int(this_m["שם חלקה"].nunique()),
        }

        gemini_key = os.environ.get("GEMINI_API_KEY")

        if gemini_key:
            prompt = f"""אתה מנהל חקלאי מנוסה. כתוב סיכום חודשי מקצועי בעברית (5-6 משפטים בלבד) בהתבסס על:

חודש {stats['month_label']}:
- עבודות: {stats['jobs']} (חודש קודם: {stats['prev_jobs']})
- שעות: {stats['hours']} (חודש קודם: {stats['prev_hours']})
- לקוח מוביל: {stats['top_client']}
- עבודה שכיחה: {stats['top_task']}
- מפעיל מוביל: {stats['top_operator']}
- לקוחות פעילים: {stats['active_clients']}
- חלקות פעילות: {stats['active_fields']}

כלול: השוואה לחודש הקודם, נקודת חוזק אחת, נקודת חולשה אחת, והמלצה מעשית אחת. כתוב בגוף ראשון רבים ("בחנו", "ראינו")."""
            try:
                from google import genai as _genai
                _client = _genai.Client(api_key=gemini_key)
                r = _client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                summary = r.text
            except Exception as ai_err:
                summary = f"שגיאת AI: {ai_err}"
        else:
            trend     = "עלייה" if stats["jobs"] >= stats["prev_jobs"] else "ירידה"
            diff_jobs = abs(stats["jobs"] - stats["prev_jobs"])
            diff_hrs  = round(abs(stats["hours"] - stats["prev_hours"]), 1)
            rec = ("כדאי לשקול הגדלת כוח אדם לעמידה בקצב הגובר."
                   if stats["jobs"] > stats["prev_jobs"]
                   else "מומלץ לפנות ללקוחות שלא טופלו החודש ולתזמן עבודות נוספות.")
            summary = (
                f"בחודש {stats['month_label']} בוצעו **{stats['jobs']} עבודות** — "
                f"{trend} של {diff_jobs} עבודות לעומת החודש הקודם ({stats['prev_jobs']}). "
                f"סך שעות העבודה עמד על **{stats['hours']:.0f} שעות** "
                f"({'גידול' if stats['hours'] >= stats['prev_hours'] else 'ירידה'} של {diff_hrs} שעות).\n\n"
                f"הלקוח המוביל החודש היה **{stats['top_client']}**, "
                f"עבודת ה**{stats['top_task']}** הייתה הנפוצה ביותר, "
                f"והמפעיל הפעיל ביותר — **{stats['top_operator']}**. "
                f"עסקנו עם **{stats['active_clients']} לקוחות פעילים** ב-**{stats['active_fields']} חלקות**.\n\n"
                f"**המלצה:** {rec}\n\n"
                f"_⚠️ מצב הדגמה — חבר מפתח Gemini לסיכום AI מלא_"
            )

        return jsonify({"summary": summary, "stats": stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Field map ─────────────────────────────────────────────────────────────────

@app.route("/fields-map")
@login_required
def fields_map():
    return render_template("fields_map.html")


@app.route("/api/fields")
@login_required
def api_fields():
    try:
        df = load_data_from_gsheet()
        coords = _load_field_coords()
        if df.empty:
            return jsonify([])

        df["_שעות"] = pd.to_numeric(df["שעות"], errors="coerce").fillna(0)
        df["שם חלקה"] = df["שם חלקה"].fillna("").str.strip()
        df = df[df["שם חלקה"] != ""]

        result = []
        for field_name, grp in df.groupby("שם חלקה"):
            crops = grp["גידול"].dropna().replace("", None).dropna()
            clients = grp["שם לקוח"].dropna()
            dates = grp["תאריך"].dropna()
            result.append({
                "name":      field_name,
                "hours":     round(float(grp["_שעות"].sum()), 1),
                "jobs":      int(len(grp)),
                "crop":      crops.mode().iloc[0] if not crops.empty else "",
                "client":    clients.mode().iloc[0] if not clients.empty else "",
                "last_date": dates.max() if not dates.empty else "",
                "lat":       coords[field_name]["lat"] if field_name in coords else None,
                "lng":       coords[field_name]["lng"] if field_name in coords else None,
            })

        result.sort(key=lambda x: x["hours"], reverse=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fields/coords", methods=["POST"])
@login_required
def api_fields_coords():
    data = request.get_json(silent=True)
    if not data or "name" not in data or "lat" not in data or "lng" not in data:
        return jsonify({"error": "missing fields"}), 400
    try:
        _save_field_coord(str(data["name"]), float(data["lat"]), float(data["lng"]))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Dashboard API ─────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    try:
        df  = load_data_from_gsheet()
        cls = sorted(df["שם לקוח"].dropna().unique().tolist()) if not df.empty else []
    except Exception:
        cls = []
    return render_template("dashboard.html", client_list=cls)


@app.route("/api/dashboard")
@login_required
def api_dashboard():
    try:
        df = load_data_from_gsheet()

        empty_resp = {
            "kpis": {"total": 0, "this_month": 0, "prev_month": 0,
                     "hours_this_month": 0.0, "hours_prev_month": 0.0,
                     "active_clients": 0, "active_clients_prev": 0,
                     "total_hours": 0.0, "avg_hours": 0.0},
            "daily_trend": [], "task_dist": {}, "top_clients": [],
            "top_fields": [], "crop_hours": [], "monthly_trend": [],
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }
        if df.empty:
            return jsonify(empty_resp)

        date_from = request.args.get("from", "")
        date_to   = request.args.get("to",   "")

        # Month-over-month KPIs always computed on full dataset
        now          = datetime.now()
        cur_m, cur_y = now.month, now.year
        prev_m       = cur_m - 1 if cur_m > 1 else 12
        prev_y       = cur_y if cur_m > 1 else cur_y - 1
        month_prefix = now.strftime("%Y-%m")
        prev_prefix  = f"{prev_y:04d}-{prev_m:02d}"

        this_m = df[df["תאריך"].str.startswith(month_prefix, na=False)].copy()
        last_m = df[df["תאריך"].str.startswith(prev_prefix,  na=False)].copy()
        this_m["_h"] = pd.to_numeric(this_m["שעות"], errors="coerce").fillna(0)
        last_m["_h"] = pd.to_numeric(last_m["שעות"], errors="coerce").fillna(0)

        # Apply date range filter for chart data
        cdf = df.copy()
        if date_from:
            cdf = cdf[cdf["תאריך"] >= date_from]
        if date_to:
            cdf = cdf[cdf["תאריך"] <= date_to]

        cdf["_h"] = pd.to_numeric(cdf["שעות"], errors="coerce").fillna(0)
        cdf["_d"] = pd.to_datetime(cdf["תאריך"], errors="coerce")

        kpis = {
            "total":               len(cdf),
            "this_month":          len(this_m),
            "prev_month":          len(last_m),
            "hours_this_month":    round(float(this_m["_h"].sum()), 1),
            "hours_prev_month":    round(float(last_m["_h"].sum()), 1),
            "active_clients":      int(this_m["שם לקוח"].nunique()),
            "active_clients_prev": int(last_m["שם לקוח"].nunique()),
            "total_hours":         round(float(cdf["_h"].sum()), 1),
            "avg_hours": round(float(cdf.loc[cdf["_h"] > 0, "_h"].mean()), 1)
                         if (cdf["_h"] > 0).any() else 0.0,
        }

        # Daily trend (last 60 days of filtered range)
        cdf_v = cdf.dropna(subset=["_d"]).copy()
        daily = (
            cdf_v.groupby(cdf_v["_d"].dt.strftime("%Y-%m-%d"))
            .agg(entries=("שם לקוח", "count"), hours=("_h", "sum"))
            .reset_index().rename(columns={"_d": "date"})
            .sort_values("date").tail(60)
        )
        daily["hours"] = daily["hours"].round(1)

        # Task distribution
        task_dist = cdf["עבודה"].value_counts().to_dict()

        # Top 10 clients by entry count
        top_clients = (
            cdf.groupby("שם לקוח")
            .agg(count=("עבודה", "count"), hours=("_h", "sum"))
            .reset_index()
            .sort_values("count", ascending=False).head(10)
            .rename(columns={"שם לקוח": "name"})
        )
        top_clients["hours"] = top_clients["hours"].round(1)

        # Top 10 fields by hours
        fdf = cdf[cdf["שם חלקה"].fillna("").str.strip() != ""]
        top_fields = (
            fdf.groupby("שם חלקה")
            .agg(hours=("_h", "sum"), entries=("שם לקוח", "count"))
            .reset_index()
            .sort_values("hours", ascending=False).head(10)
            .rename(columns={"שם חלקה": "name"})
        )
        top_fields["hours"] = top_fields["hours"].round(1)

        # Crop hours (top 8)
        crp = cdf[cdf["גידול"].fillna("").str.strip() != ""]
        crop_h = (
            crp.groupby("גידול")["_h"].sum()
            .reset_index()
            .sort_values("_h", ascending=False).head(8)
            .rename(columns={"גידול": "name", "_h": "hours"})
        )
        crop_h["hours"] = crop_h["hours"].round(1)

        # Monthly trend (last 12 months in range)
        cdf_v["_month"] = cdf_v["_d"].dt.strftime("%Y-%m")
        monthly = (
            cdf_v.groupby("_month")
            .agg(entries=("שם לקוח", "count"), hours=("_h", "sum"))
            .reset_index().rename(columns={"_month": "month"})
            .sort_values("month").tail(12)
        )
        monthly["hours"] = monthly["hours"].round(1)

        return jsonify({
            "kpis":          kpis,
            "daily_trend":   daily.to_dict(orient="records"),
            "task_dist":     task_dist,
            "top_clients":   top_clients.to_dict(orient="records"),
            "top_fields":    top_fields.to_dict(orient="records"),
            "crop_hours":    crop_h.to_dict(orient="records"),
            "monthly_trend": monthly.to_dict(orient="records"),
            "updated_at":    datetime.now().strftime("%H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Start bot thread ───────────────────────────────────────────────────────────

if os.environ.get("BOT_TOKEN"):
    _bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    _bot_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
