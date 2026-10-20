"""WhatsApp Business Cloud API channel.

Unlike the Telegram bot's 12-step guided ConversationHandler flow, a worker
here just sends one message — typed or a voice note — describing the job,
which goes through the same Gemini extraction as Telegram's voice feature
(gadash/ai_extract.py). Only registration (company code → name → password)
and the confirm-before-save step are their own small states. See the
"Key design decision" section of the WhatsApp integration plan for why.

One shared WhatsApp Business number serves every tenant — a worker's
whatsapp_number resolves their tenant on its own (gadash/workers.py), the
same pattern already used for the Telegram bot's telegram_id.

send_text_message/download_media degrade to a logged no-op if the three
WHATSAPP_* env vars aren't set, same as the rest of this app does when
GOOGLE_CREDS/BOT_TOKEN are missing.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gadash import ai_extract
from gadash.auth import get_tenant_id_by_slug
from gadash.db import delete_work_entry_by, last_work_entry_by, log_audit
from gadash.models import WorkEntry
from gadash.models_db import WhatsAppProcessedMessage, WhatsAppSession, db
from gadash.service import create_entry
from gadash.workers import (
    _get_worker_by_whatsapp_number, _link_worker_whatsapp,
    _load_workers, _verify_worker,
)

_logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v20.0"

# ── Webhook verification & delivery ─────────────────────────────────────────

def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    """Meta's one-time webhook subscription handshake (GET /webhook/whatsapp).
    Returns the challenge string to echo back on success, None on failure —
    the route turns that into a 200/403."""
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if mode == "subscribe" and expected and token == expected:
        return challenge
    return None


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Meta signs every webhook POST with HMAC-SHA256 of the raw body, keyed
    by the app secret, in the X-Hub-Signature-256 header ("sha256=<hex>").
    Without this check anyone who finds the webhook URL can forge a message
    from any worker's phone number. Fails closed if the secret isn't set."""
    secret = os.environ.get("WHATSAPP_APP_SECRET")
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


def is_configured() -> bool:
    return bool(os.environ.get("WHATSAPP_PHONE_NUMBER_ID") and os.environ.get("WHATSAPP_ACCESS_TOKEN"))


def send_text_message(to: str, body: str):
    if not is_configured():
        _logger.warning("[WhatsApp] not configured — would have sent to %s: %s", to, body)
        return
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
    headers = {"Authorization": f"Bearer {os.environ['WHATSAPP_ACCESS_TOKEN']}"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        _logger.error("[WhatsApp] send to %s failed: %s", to, e)


def download_media(media_id: str) -> bytes:
    """Meta's media API is two calls: resolve the media id to a short-lived
    URL, then download from it — both need the access token."""
    headers = {"Authorization": f"Bearer {os.environ['WHATSAPP_ACCESS_TOKEN']}"}
    meta = requests.get(f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}",
                         headers=headers, timeout=10)
    meta.raise_for_status()
    media = requests.get(meta.json()["url"], headers=headers, timeout=20)
    media.raise_for_status()
    return media.content


def parse_incoming(payload: dict) -> list:
    """Pulls {"phone", "text", "audio_id", "id"} out of a Meta webhook POST body —
    one entry per text/audio message found. Silently ignores delivery/read
    status-update payloads (no "messages" key) and other message types
    (images, locations, ...) this flow doesn't handle."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                phone = msg.get("from")
                if not phone:
                    continue
                if msg.get("type") == "text":
                    out.append({"phone": phone, "text": msg.get("text", {}).get("body", ""),
                                "audio_id": None, "id": msg.get("id")})
                elif msg.get("type") == "audio":
                    out.append({"phone": phone, "text": None,
                                "audio_id": msg.get("audio", {}).get("id"), "id": msg.get("id")})
    return out


# WhatsApp Cloud API retries webhook delivery when it doesn't get a fast
# enough response (our handler calls out to Gemini and the Send API before
# returning) — the same message can arrive more than once, even at the same
# instant. Message ids (wamid) are recorded in Postgres rather than process
# memory so a retry landing after a restart or deploy is still caught, and the
# INSERT ... ON CONFLICT is atomic: two deliveries racing each other can't
# both conclude they were first.
DEDUP_RETENTION = timedelta(days=7)


def is_duplicate_message(message_id: str | None) -> bool:
    """Checks-and-marks: the first call for a given message id returns False
    and records it; every subsequent call (a Meta webhook retry) returns
    True. Callers should skip all processing — audio download included —
    for a duplicate, not just the save step."""
    if not message_id:
        return False
    inserted = db.session.execute(
        pg_insert(WhatsAppProcessedMessage).values(message_id=message_id).on_conflict_do_nothing()
    )
    db.session.execute(delete(WhatsAppProcessedMessage).where(
        WhatsAppProcessedMessage.created_at < datetime.now(timezone.utc) - DEDUP_RETENTION))
    db.session.commit()
    return inserted.rowcount == 0


# ── Conversation ─────────────────────────────────────────────────────────────

WELCOME_MESSAGE = (
    "שלום! 👋 כאן הבוט של מערכת גד\"ש לדיווח עבודות שדה.\n\n"
    "כדי להתחיל, שלח לי את קוד החברה שקיבלת מהמנהל שלך."
)

# (emoji, friendly label) per ai_extract field, for the pre-save summary —
# keeps the raw internal Hebrew keys off the screen and makes each line easy
# to scan on a phone. Falls back to a bullet + the raw key for anything new.
FIELD_DISPLAY = {
    "שם לקוח": ("👤", "לקוח"),
    "תאריך":   ("📅", "תאריך"),
    "עבודה":   ("🔧", "עבודה"),
    "שם חלקה": ("📍", "חלקה"),
    "גידול":   ("🌾", "גידול"),
    "כמות":    ("📏", "כמות"),
    "שעות":    ("⏱️", "שעות"),
    "כלי":     ("🚜", "כלי"),
    "מפעיל":   ("👷", "מפעיל"),
    "הערות":   ("📝", "הערות"),
}


# A conversation left half-finished is dropped after this long — otherwise a
# stray "כן" days later would save a report the worker had forgotten about.
SESSION_TTL = timedelta(minutes=30)


def _load_session(phone: str) -> dict | None:
    """The stored in-progress conversation for `phone`, or None."""
    row = db.session.get(WhatsAppSession, phone)
    if row is None:
        return None
    if datetime.now(timezone.utc) - row.updated_at > SESSION_TTL:
        db.session.delete(row)
        db.session.commit()
        return None
    return {"state": row.state, **row.data}


def _save_session(phone: str, session: dict) -> None:
    """Persist a conversation in progress; a finished one (IDLE) has no row —
    a registered worker with nothing pending is re-derived from the workers
    table on every message, so removing or changing a worker takes effect
    immediately instead of whenever the process next restarts."""
    row = db.session.get(WhatsAppSession, phone)
    if session["state"] == "IDLE":
        if row is not None:
            db.session.delete(row)
    else:
        data = {k: v for k, v in session.items() if k != "state"}
        if row is None:
            row = WhatsAppSession(phone=phone, state=session["state"], data=data)
            db.session.add(row)
        else:
            row.state, row.data = session["state"], data
        row.updated_at = datetime.now(timezone.utc)
    db.session.commit()


def handle_message(phone: str, text: str | None, audio_bytes: bytes | None) -> str:
    """Returns the reply text for the caller to send back to `phone`."""
    session = _load_session(phone)
    if session is None:
        worker = _get_worker_by_whatsapp_number(phone)
        if not worker:
            _save_session(phone, {"state": "REGISTER_TENANT"})
            return WELCOME_MESSAGE
        session = {"state": "IDLE", "tenant_id": worker["tenant_id"], "worker_name": worker["שם"]}
    reply = _dispatch(phone, session, (text or "").strip(), audio_bytes)
    _save_session(phone, session)
    return reply


def _dispatch(phone: str, session: dict, text: str, audio_bytes: bytes | None) -> str:
    state = session["state"]
    if state == "REGISTER_TENANT":
        return _step_register_tenant(session, text)
    if state == "REGISTER_NAME":
        return _step_register_name(session, text)
    if state == "REGISTER_PASSWORD":
        return _step_register_password(phone, session, text)
    if state == "CONFIRM":
        return _step_confirm(session, text)
    if state == "CONFIRM_DELETE":
        return _step_confirm_delete(session, text)
    if _is_delete_last_command(text):
        return _step_delete_last(session)
    return _step_report(session, text, audio_bytes)


def _step_register_tenant(session: dict, text: str) -> str:
    tenant_id = get_tenant_id_by_slug(text)
    if not tenant_id:
        return "קוד חברה לא נמצא. בדוק עם המנהל ונסה שוב:"
    session["reg_tenant_id"] = tenant_id
    session["state"] = "REGISTER_NAME"
    return "מה שם המשתמש שלך (כפי שמופיע במערכת)?"


def _step_register_name(session: dict, text: str) -> str:
    tenant_id = session.get("reg_tenant_id")
    if not any(w["שם"] == text for w in _load_workers(tenant_id)):
        session.clear()
        session["state"] = "REGISTER_TENANT"
        return "שם לא נמצא במערכת. פנה למנהל, ואז שלח שוב את קוד החברה כדי לנסות מהתחלה."
    session["reg_name"] = text
    session["state"] = "REGISTER_PASSWORD"
    return "הכנס סיסמה:"


def _step_register_password(phone: str, session: dict, text: str) -> str:
    tenant_id = session.get("reg_tenant_id")
    name = session.get("reg_name", "")
    if tenant_id and _verify_worker(tenant_id, name, text):
        _link_worker_whatsapp(tenant_id, name, phone)
        session.clear()
        session.update(state="IDLE", tenant_id=tenant_id, worker_name=name)
        return f"✅ נרשמת בהצלחה כ-{name}!\nשלח לי הודעה או הקלטה עם פרטי עבודה כדי לדווח."
    session.clear()
    session["state"] = "REGISTER_TENANT"
    return "סיסמה שגויה. שלח שוב את קוד החברה כדי לנסות מהתחלה."


def _format_summary(fields: dict) -> str:
    """One icon + label line per non-empty field, in FIELD_DISPLAY order.
    Takes any dict keyed by the Hebrew field names; other keys are ignored."""
    return "\n".join(
        f"{emoji} *{label}:* {fields[key]}"
        for key, (emoji, label) in FIELD_DISPLAY.items() if fields.get(key)
    )


def _step_report(session: dict, text: str, audio_bytes: bytes | None) -> str:
    if not ai_extract.is_available():
        return "🎤 דיווח לא זמין כרגע. נסה שוב מאוחר יותר."
    try:
        if audio_bytes is not None:
            fields = ai_extract.extract_fields_from_audio(audio_bytes)
        elif text:
            fields = ai_extract.extract_fields_from_text(text)
        else:
            return "שלח הודעה כתובה או הקלטה עם פרטי העבודה — לקוח, סוג עבודה, חלקה, שעות."
    except Exception as e:
        _logger.warning("[WhatsApp] extraction failed: %s", e)
        return "❌ לא הצלחתי לנתח את ההודעה. נסה שוב."

    if not fields["שם לקוח"]:
        return "לא זיהיתי שם לקוח בהודעה. נסה שוב ותזכיר את שם הלקוח."

    session["pending_fields"] = fields
    session["state"] = "CONFIRM"
    return (
        f"📋 בדוק שהפרטים נכונים:\n\n{_format_summary(fields)}\n\n"
        "משהו לא נכון (למשל סוג העבודה)? שלח *לא* ונסה שוב עם ניסוח ברור יותר.\n"
        "הכל תקין? שלח *כן* לשמירה."
    )


def _step_confirm(session: dict, text: str) -> str:
    fields = session.get("pending_fields", {})
    if text == "כן":
        tenant_id, worker_name = session["tenant_id"], session["worker_name"]
        session.pop("pending_fields", None)
        session["state"] = "IDLE"
        try:
            entry = WorkEntry.from_bot(fields, worker_name)
            create_entry(tenant_id, entry, worker_name)
            return "✅ נשמר בהצלחה!\nטעית? שלח *מחק אחרון*."
        except ValueError as e:
            return f"❌ שגיאת אימות: {e}"
        except Exception as e:
            return f"❌ שגיאה בשמירה: {e}"
    if text == "לא":
        session.pop("pending_fields", None)
        session["state"] = "IDLE"
        return "בוטל."
    return "שלח *כן* לשמירה או *לא* לביטול."


_DELETE_LAST_COMMANDS = {"מחק אחרון", "מחק את האחרון"}


def _is_delete_last_command(text: str) -> bool:
    return " ".join(text.rstrip(".!?").split()) in _DELETE_LAST_COMMANDS


def _step_delete_last(session: dict) -> str:
    """Offer to delete the worker's own most recent entry — never anyone
    else's, which is what Telegram's /undo does (it removes the tenant's last
    row). Asks first, since this is the one destructive command."""
    last = last_work_entry_by(session["tenant_id"], session["worker_name"])
    if last is None:
        return "אין לך רשומות למחיקה."
    session["delete_row_id"] = last["_row_id"]
    session["delete_detail"] = f"{last['שם לקוח']} | {last['תאריך']} | {last['עבודה']}"
    session["state"] = "CONFIRM_DELETE"
    return (
        f"🗑️ למחוק את הרשומה האחרונה שלך?\n\n{_format_summary(last)}\n\n"
        "שלח *כן* למחיקה או *לא* לביטול."
    )


def _step_confirm_delete(session: dict, text: str) -> str:
    if text == "כן":
        tenant_id, worker_name = session["tenant_id"], session["worker_name"]
        row_id = session.pop("delete_row_id", None)
        detail = session.pop("delete_detail", "")
        session["state"] = "IDLE"
        if row_id is None or not delete_work_entry_by(tenant_id, row_id, worker_name):
            return "הרשומה כבר לא קיימת."
        log_audit(tenant_id, "undo", worker_name, detail)
        return f"✅ הרשומה נמחקה:\n{detail}"
    if text == "לא":
        session.pop("delete_row_id", None)
        session.pop("delete_detail", None)
        session["state"] = "IDLE"
        return "בוטל. הרשומה נשארה."
    return "שלח *כן* למחיקה או *לא* לביטול."
