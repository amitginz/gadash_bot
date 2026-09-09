import asyncio
import logging
import os
from datetime import date, datetime, timedelta

import pandas as pd
import datetime as _dt

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ConversationHandler,
    ContextTypes, MessageHandler, filters,
)

from gadash import ai_extract
from gadash.auth import get_tenant_id_by_slug
from gadash.db import (
    add_subscriber, delete_work_entry, get_subscribers,
    list_tenant_ids, load_work_entries, log_audit,
)
from gadash.models import COLUMNS, VALID_TASKS, WorkEntry
from gadash.service import create_entry
from gadash.workers import (
    _get_worker_by_telegram_id,
    _link_worker_telegram,
    _load_workers,
    _verify_worker,
)

_logger = logging.getLogger(__name__)

WEB_APP_URL = os.environ.get("WEB_APP_URL", "http://localhost:8080")

(MENU, CLIENT, DATE, TASK, FIELD, CROP, AMOUNT, HOURS, TOOL, OPERATOR,
 NOTE, CONFIRM, SEARCH, EDIT_SELECT,
 REGISTER_TENANT, REGISTER_NAME, REGISTER_PASSWORD) = range(17)

TASK_CHOICES    = [["חריש", "ריסוס"], ["קציר", "דיסוק"], ["דישון", "השקיה"], ["שתילה", "עישוב"], ["אחר"]]
CONFIRM_KEYBOARD = [["כן", "לא"]]
NOTES_KEYBOARD  = [["ללא הערות"]]
MENU_KEYBOARD   = [
    ["הזן עבודה חדשה"],
    ["5 עבודות אחרונות", "חפש לפי לקוח"],
    ["סטטיסטיקות", "ערוך רשומה"],
    ["סיים"],
]

_telegram_app  = None
_telegram_loop = None


def _menu_markup():
    return ReplyKeyboardMarkup(MENU_KEYBOARD, resize_keyboard=True)


def _tenant_id(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Every state handler below is only reachable after start()/register_password()
    resolved and stored this — see the module docstring in gadash/tenancy.py for
    why the bot threads it explicitly instead of using that Flask-session helper."""
    return context.user_data["_tenant_id"]


def _recent_clients_markup(tenant_id: int):
    try:
        df = load_work_entries(tenant_id)
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
    tid = update.message.from_user.id
    worker = _get_worker_by_telegram_id(tid)
    if worker:
        context.user_data["_worker_name"] = worker["שם"]
        context.user_data["_tenant_id"] = worker["tenant_id"]
        add_subscriber(worker["tenant_id"], tid)
        await update.message.reply_text(
            f'שלום {worker["שם"]}! אני בוט ניהול העבודות של גד"ש 🌾\nמה תרצה לעשות?\n\n'
            '🎤 טיפ: אפשר גם פשוט לשלוח לי הודעה קולית עם פרטי העבודה, במקום למלא שלב-שלב.',
            reply_markup=_menu_markup(),
        )
        return MENU
    await update.message.reply_text(
        f"ברוך הבא! עליך להירשם.\nהכנס את קוד החברה שקיבלת מהמנהל:\n\n"
        f"_(מזהה Telegram שלך: `{tid}`)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REGISTER_TENANT


async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.message.from_user.id
    if not _get_worker_by_telegram_id(tid):
        return await start(update, context)
    worker = _get_worker_by_telegram_id(tid)
    context.user_data["_worker_name"] = worker["שם"]
    context.user_data["_tenant_id"] = worker["tenant_id"]
    add_subscriber(worker["tenant_id"], tid)
    return await _handle_menu(update, context, update.message.text.strip())


async def register_tenant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slug = update.message.text.strip()
    tenant_id = get_tenant_id_by_slug(slug)
    if not tenant_id:
        await update.message.reply_text("קוד חברה לא נמצא. בדוק עם המנהל ונסה שוב:")
        return REGISTER_TENANT
    context.user_data["_reg_tenant_id"] = tenant_id
    await update.message.reply_text("מה שם המשתמש שלך (כפי שמופיע במערכת)?")
    return REGISTER_NAME


async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    tenant_id = context.user_data.get("_reg_tenant_id")
    workers = _load_workers(tenant_id)
    if not any(w["שם"] == name for w in workers):
        await update.message.reply_text("שם לא נמצא במערכת. פנה למנהל לרישום.")
        return ConversationHandler.END
    context.user_data["_reg_name"] = name
    await update.message.reply_text("הכנס סיסמה:")
    return REGISTER_PASSWORD


async def register_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd       = update.message.text.strip()
    tenant_id = context.user_data.get("_reg_tenant_id")
    name      = context.user_data.get("_reg_name", "")
    if tenant_id and _verify_worker(tenant_id, name, pwd):
        tid = update.message.from_user.id
        _link_worker_telegram(tenant_id, name, tid)
        context.user_data.pop("_reg_tenant_id", None)
        context.user_data.pop("_reg_name", None)
        context.user_data["_worker_name"] = name
        context.user_data["_tenant_id"]   = tenant_id
        add_subscriber(tenant_id, tid)
        await update.message.reply_text(
            f"✅ נרשמת בהצלחה כ-{name}!\nמה תרצה לעשות?",
            reply_markup=_menu_markup(),
        )
        return MENU
    await update.message.reply_text("סיסמה שגויה. נסה שוב או פנה למנהל.")
    return ConversationHandler.END


async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _handle_menu(update, context, update.message.text.strip())


async def _handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    if text == "הזן עבודה חדשה":
        await update.message.reply_text("מעולה! מה שם הלקוח?", reply_markup=_recent_clients_markup(_tenant_id(context)))
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
        df = load_work_entries(_tenant_id(context))
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
        df = load_work_entries(_tenant_id(context))
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
        df = load_work_entries(_tenant_id(context))
        if df.empty:
            await update.message.reply_text("אין עבודות לעריכה.", reply_markup=_menu_markup())
            return MENU
        tail = df.tail(5)
        lines = ["✏️ בחר רשומה לעריכה:\n"]
        choices = []
        row_ids = []
        for i, (_, row) in enumerate(tail.iterrows(), 1):
            lines.append(f"{i}. {row.get('תאריך','')} | {row.get('שם לקוח','')} | {row.get('עבודה','')}")
            choices.append([str(i)])
            row_ids.append(int(row["_row_id"]))
        context.user_data["_edit_row_ids"] = row_ids
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
        row_ids = context.user_data.get("_edit_row_ids", [])
        if 1 <= n <= len(row_ids):
            row_id = row_ids[n - 1]
            df = load_work_entries(_tenant_id(context))
            row = df[df["_row_id"] == row_id].iloc[0]
            details = "\n".join(f"• {col}: {row.get(col, '')}" for col in COLUMNS)
            await update.message.reply_text(
                f"✏️ פרטי הרשומה:\n\n{details}\n\n🔗 לעריכה באתר:\n{WEB_APP_URL}/edit/{row_id}",
                reply_markup=_menu_markup(),
            )
        else:
            await update.message.reply_text("בחר מספר מהרשימה.", reply_markup=_menu_markup())
    except ValueError:
        await update.message.reply_text("בחר מספר מהרשימה.", reply_markup=_menu_markup())
    context.user_data.pop("_edit_row_ids", None)
    return MENU


async def bot_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    try:
        df = load_work_entries(_tenant_id(context))
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


def _entry_summary(context) -> str:
    # context.user_data also carries bookkeeping keys (_tenant_id, _worker_name,
    # ...) alongside the WorkEntry fields being built — the leading underscore
    # is what keeps them out of the pre-save summary shown to the worker.
    return "\n".join(f"• {k}: {v}" for k, v in context.user_data.items() if not k.startswith("_"))


async def note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["הערות"] = "" if text == "ללא הערות" else text
    summary = _entry_summary(context)
    await update.message.reply_text(
        f"סיכום לפני שמירה:\n\n{summary}\n\nלחץ כן לשמירה או לא לביטול.",
        reply_markup=ReplyKeyboardMarkup(CONFIRM_KEYBOARD, resize_keyboard=True),
    )
    return CONFIRM


async def note_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    context.user_data["הערות"] = f"[תמונה: {photo.file_id}]"
    summary = _entry_summary(context)
    await update.message.reply_text(
        f"📷 תמונה התקבלה.\n\nסיכום:\n\n{summary}\n\nלחץ כן לשמירה או לא לביטול.",
        reply_markup=ReplyKeyboardMarkup(CONFIRM_KEYBOARD, resize_keyboard=True),
    )
    return CONFIRM


async def voice_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🎤 Let a worker report a job by just talking instead of the 12-step flow.

    Gemini transcribes the Hebrew voice note and extracts WorkEntry fields
    directly, which get dropped into context.user_data and handed to the
    existing confirm() step — so saving/validation stays exactly as before.
    """
    tid = update.message.from_user.id
    worker = _get_worker_by_telegram_id(tid)
    if not worker:
        return await start(update, context)
    context.user_data["_worker_name"] = worker["שם"]
    context.user_data["_tenant_id"] = worker["tenant_id"]

    if not ai_extract.is_available():
        await update.message.reply_text(
            "🎤 דיווח קולי לא זמין כרגע. אפשר להזין ידנית — שלח 'הזן עבודה חדשה'.",
            reply_markup=_menu_markup(),
        )
        return MENU

    processing_msg = await update.message.reply_text("🎤 מקשיב ומנתח את ההקלטה...")
    try:
        voice_file = await update.message.voice.get_file()
        audio_bytes = bytes(await voice_file.download_as_bytearray())
        fields = await asyncio.to_thread(ai_extract.extract_fields_from_audio, audio_bytes)
    except Exception as e:
        _logger.warning("[BOT] Voice parsing failed: %s", e)
        await processing_msg.edit_text(
            "❌ לא הצלחתי לנתח את ההקלטה. אפשר לנסות שוב, או להזין ידנית — שלח 'הזן עבודה חדשה'."
        )
        return MENU

    if not fields["שם לקוח"]:
        await processing_msg.edit_text(
            "🎤 שמעתי את ההקלטה, אבל לא זיהיתי בה שם לקוח.\n"
            "נסה הקלטה נוספת ותזכיר את שם הלקוח, או הזן ידנית — שלח 'הזן עבודה חדשה'."
        )
        return MENU

    tenant_id, worker_name = context.user_data["_tenant_id"], context.user_data["_worker_name"]
    context.user_data.clear()
    context.user_data["_tenant_id"], context.user_data["_worker_name"] = tenant_id, worker_name
    context.user_data.update(fields)
    summary = "\n".join(f"• {k}: {v}" for k, v in fields.items() if v)
    await processing_msg.edit_text(f"🎤 זיהיתי מההקלטה:\n\n{summary}")
    await update.message.reply_text(
        "לחץ כן לשמירה או לא לביטול.",
        reply_markup=ReplyKeyboardMarkup(CONFIRM_KEYBOARD, resize_keyboard=True),
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "כן":
        try:
            entry = WorkEntry.from_bot(context.user_data, update.message.from_user.full_name)
            create_entry(_tenant_id(context), entry, entry.entered_by)
            await update.message.reply_text("✅ נשמר בהצלחה!")
        except ValueError as e:
            await update.message.reply_text(f"❌ שגיאת אימות: {e}")
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה בשמירה: {e}")
    else:
        await update.message.reply_text("❌ בוטל.")
    tenant_id, worker_name = context.user_data.get("_tenant_id"), context.user_data.get("_worker_name")
    context.user_data.clear()
    context.user_data["_tenant_id"], context.user_data["_worker_name"] = tenant_id, worker_name
    await update.message.reply_text("מה תרצה לעשות?", reply_markup=_menu_markup())
    return MENU


async def bot_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tenant_id = _tenant_id(context)
        df = load_work_entries(tenant_id)
        if df.empty:
            await update.message.reply_text("אין עבודות למחיקה.", reply_markup=_menu_markup())
            return MENU
        last = df.iloc[-1]
        detail = f"{last.get('שם לקוח','')} | {last.get('תאריך','')} | {last.get('עבודה','')}"
        last_row_id = int(last["_row_id"])
        delete_work_entry(tenant_id, last_row_id)
        log_audit(tenant_id, "undo", update.message.from_user.full_name, detail)
        await update.message.reply_text(f"✅ הרשומה האחרונה נמחקה:\n{detail}", reply_markup=_menu_markup())
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}", reply_markup=_menu_markup())
    return MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ביטול.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def start_telegram_bot(flask_app):
    global _telegram_app, _telegram_loop

    token = os.getenv("BOT_TOKEN")
    if not token:
        _logger.warning("BOT_TOKEN not set — Telegram bot will not start")
        return

    # Every handler above does synchronous Flask-SQLAlchemy queries (gadash/db.py),
    # which need an active Flask application context to resolve db.session. This
    # thread has no request cycle to push/pop one per-call, so push a single
    # context that lives for the thread's whole lifetime instead.
    flask_app.app_context().push()

    _logger.info("[BOT] Thread starting...")
    try:
        _telegram_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_telegram_loop)
        tg = ApplicationBuilder().token(token).build()
        _logger.info("[BOT] App built OK")
    except Exception:
        _logger.exception("[BOT] Failed to build application")
        return

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.VOICE, voice_entry),
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_start),
        ],
        states={
            MENU:              [
                MessageHandler(filters.VOICE, voice_entry),
                MessageHandler(filters.TEXT & ~filters.COMMAND, menu_choice),
            ],
            CLIENT:            [MessageHandler(filters.TEXT & ~filters.COMMAND, client_step)],
            DATE:              [MessageHandler(filters.TEXT & ~filters.COMMAND, date_input)],
            TASK:              [MessageHandler(filters.TEXT & ~filters.COMMAND, task)],
            FIELD:             [MessageHandler(filters.TEXT & ~filters.COMMAND, field)],
            CROP:              [MessageHandler(filters.TEXT & ~filters.COMMAND, crop_step)],
            AMOUNT:            [MessageHandler(filters.TEXT & ~filters.COMMAND, amount)],
            HOURS:             [MessageHandler(filters.TEXT & ~filters.COMMAND, hours_step)],
            TOOL:              [MessageHandler(filters.TEXT & ~filters.COMMAND, tool)],
            OPERATOR:          [MessageHandler(filters.TEXT & ~filters.COMMAND, operator_step)],
            NOTE:              [
                MessageHandler(filters.TEXT & ~filters.COMMAND, note),
                MessageHandler(filters.PHOTO, note_photo),
            ],
            CONFIRM:           [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
            SEARCH:            [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_search_results)],
            EDIT_SELECT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, bot_edit_select)],
            REGISTER_TENANT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, register_tenant)],
            REGISTER_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, register_name)],
            REGISTER_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_password)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("undo", bot_undo),
            CommandHandler("start", start),
        ],
    )
    tg.add_handler(conv)

    async def _broadcast(subs, text):
        for chat_id in subs:
            try:
                await tg.bot.send_message(chat_id=chat_id, text=text)
            except Exception:
                pass

    async def _morning_job(context):
        now = datetime.now()
        for tenant_id in list_tenant_ids():
            try:
                df   = load_work_entries(tenant_id)
                subs = get_subscribers(tenant_id)
                if not subs:
                    continue

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
                        field_h = (
                            w_df[w_df["_שעות"] > 0]
                            .groupby("שם חלקה")["_שעות"].sum()
                            .sort_values(ascending=False).head(5)
                        )
                        if not field_h.empty:
                            lines.append("\n📍 שעות לפי חלקה:")
                            for fn, h in field_h.items():
                                lines.append(f"  • {fn or 'לא צוין'}: {h:.1f}ש׳")
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

                if not df.empty:
                    threshold = (now - timedelta(days=14)).strftime("%Y-%m-%d")
                    last_per_client = df.groupby("שם לקוח")["תאריך"].max()
                    inactive = last_per_client[last_per_client < threshold]
                    if not inactive.empty:
                        lines = ["⏰ תזכורת — לקוחות ללא עבודה ב-14 ימים:\n"]
                        for client_name, last_dt in inactive.items():
                            lines.append(f"• {client_name} (אחרון: {last_dt})")
                        await _broadcast(subs, "\n".join(lines))
            except Exception:
                _logger.exception("[BOT] Morning report error (tenant %s)", tenant_id)

    async def _evening_job(context):
        date_str = datetime.now().strftime("%Y-%m-%d")
        for tenant_id in list_tenant_ids():
            try:
                subs = get_subscribers(tenant_id)
                if subs:
                    msg = (
                        f"📋 תזכורת סוף יום — {date_str}\n\n"
                        "אל תשכח לדווח על שעות העבודה שלך היום!\n"
                        f"🔗 {WEB_APP_URL}/worker\n\n"
                        "לדיווח דרך הבוט — שלח 'הזן עבודה חדשה'"
                    )
                    await _broadcast(subs, msg)
            except Exception:
                _logger.exception("[BOT] Evening reminder error (tenant %s)", tenant_id)

    async def _run():
        global _telegram_app
        _telegram_app = tg
        await tg.initialize()
        await tg.start()

        if tg.job_queue:
            tg.job_queue.run_daily(_morning_job, time=_dt.time(8, 0))
            tg.job_queue.run_daily(_evening_job, time=_dt.time(18, 0))
        else:
            _logger.warning("[BOT] JobQueue unavailable — install python-telegram-bot[job-queue] for scheduled reports")

        explicit_url = os.environ.get("WEB_APP_URL")
        if explicit_url:
            webhook_url = f"{explicit_url}/webhook/{token}"
            try:
                await tg.bot.set_webhook(webhook_url)
                _logger.info("[BOT] Webhook set: %s", webhook_url)
                await asyncio.Event().wait()
            except Exception as e:
                _logger.warning("[BOT] Webhook failed (%s), falling back to polling", e)
                await tg.updater.start_polling()
                await asyncio.Event().wait()
        else:
            await tg.updater.start_polling()
            await asyncio.Event().wait()

    _telegram_loop.run_until_complete(_run())
