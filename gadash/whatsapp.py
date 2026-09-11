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

import logging
import os
from collections import deque

import requests

from gadash import ai_extract
from gadash.auth import get_tenant_id_by_slug
from gadash.models import WorkEntry
from gadash.service import create_entry
from gadash.workers import (
    _get_worker_by_whatsapp_number, _link_worker_whatsapp,
    _load_workers, _verify_worker,
)

_logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v20.0"

# Per-phone-number conversation state — the WhatsApp equivalent of Telegram's
# context.user_data. In-memory only (lost on restart, same tradeoff the
# Telegram bot already has); a worker mid-registration or mid-confirm just
# has to send their last message again.
_sessions: dict = {}


# ── Webhook verification & delivery ─────────────────────────────────────────

def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    """Meta's one-time webhook subscription handshake (GET /webhook/whatsapp).
    Returns the challenge string to echo back on success, None on failure —
    the route turns that into a 200/403."""
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if mode == "subscribe" and expected and token == expected:
        return challenge
    return None


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
# returning) — the same message can arrive more than once. Track recently
# seen message ids (wamid) so a retry is a no-op instead of a duplicate save.
# Bounded so long-running processes don't leak memory; in-memory only, same
# restart tradeoff as _sessions.
_seen_message_ids: deque = deque(maxlen=2000)
_seen_message_ids_set: set = set()


def is_duplicate_message(message_id: str | None) -> bool:
    """Checks-and-marks: the first call for a given message id returns False
    and remembers it; every subsequent call (a Meta webhook retry) returns
    True. Callers should skip all processing — audio download included —
    for a duplicate, not just the save step."""
    if not message_id:
        return False
    if message_id in _seen_message_ids_set:
        return True
    if len(_seen_message_ids) >= _seen_message_ids.maxlen:
        oldest = _seen_message_ids.popleft()
        _seen_message_ids_set.discard(oldest)
    _seen_message_ids.append(message_id)
    _seen_message_ids_set.add(message_id)
    return False


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


def handle_message(phone: str, text: str | None, audio_bytes: bytes | None) -> str:
    """Returns the reply text for the caller to send back to `phone`."""
    if phone not in _sessions:
        worker = _get_worker_by_whatsapp_number(phone)
        if worker:
            _sessions[phone] = {"state": "IDLE", "tenant_id": worker["tenant_id"], "worker_name": worker["שם"]}
        else:
            _sessions[phone] = {"state": "REGISTER_TENANT"}
            return WELCOME_MESSAGE
    session = _sessions[phone]
    state = session["state"]
    text = (text or "").strip()

    if state == "REGISTER_TENANT":
        return _step_register_tenant(session, text)
    if state == "REGISTER_NAME":
        return _step_register_name(session, text)
    if state == "REGISTER_PASSWORD":
        return _step_register_password(phone, session, text)
    if state == "CONFIRM":
        return _step_confirm(session, text)
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
        _sessions[phone] = {"state": "IDLE", "tenant_id": tenant_id, "worker_name": name}
        return f"✅ נרשמת בהצלחה כ-{name}!\nשלח לי הודעה או הקלטה עם פרטי עבודה כדי לדווח."
    _sessions[phone] = {"state": "REGISTER_TENANT"}
    return "סיסמה שגויה. שלח שוב את קוד החברה כדי לנסות מהתחלה."


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
    summary = "\n".join(
        f"{FIELD_DISPLAY.get(k, ('•', k))[0]} *{FIELD_DISPLAY.get(k, ('•', k))[1]}:* {v}"
        for k, v in fields.items() if v
    )
    return (
        f"📋 בדוק שהפרטים נכונים:\n\n{summary}\n\n"
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
            return "✅ נשמר בהצלחה!"
        except ValueError as e:
            return f"❌ שגיאת אימות: {e}"
        except Exception as e:
            return f"❌ שגיאה בשמירה: {e}"
    if text == "לא":
        session.pop("pending_fields", None)
        session["state"] = "IDLE"
        return "בוטל."
    return "שלח *כן* לשמירה או *לא* לביטול."
