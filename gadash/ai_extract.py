"""Shared Gemini extraction: turns a voice note or a typed free-text message
into WorkEntry-shaped fields. Originally built for the Telegram bot's voice
feature (gadash/bot.py); factored out here so the WhatsApp channel — where
free text/voice *is* the whole reporting flow, not an alternative to a
guided 12-step form — can reuse the exact same tested extraction instead of
a second copy that could drift out of sync.
"""
import json
import os
import re
from datetime import date

from gadash.models import VALID_TASKS

try:
    import google.generativeai as _genai
except ImportError:
    _genai = None

FIELDS = [
    "שם לקוח", "תאריך", "עבודה", "שם חלקה", "גידול",
    "כמות", "שעות", "כלי", "מפעיל", "הערות",
]

EXTRACTION_PROMPT = """אתה עוזר להזין נתוני עבודות שדה חקלאיות ממערכת ניהול עבודות גד"ש.
נתח את ההודעה הבאה (קולית או כתובה, בעברית מדוברת) וחלץ ממנה את הפרטים הבאים כאובייקט JSON יחיד, בלי טקסט נוסף ובלי markdown fences:

{
  "שם לקוח": "",
  "תאריך": "בפורמט YYYY-MM-DD אם הוזכר תאריך מפורש (למשל 'אתמול', 'ה-3 ליוני') — אחרת השאר ריק",
  "עבודה": "אחד בדיוק מהערכים: חריש, ריסוס, קציר, דיסוק — או 'אחר' אם לא מתאים",
  "שם חלקה": "",
  "גידול": "",
  "כמות": "לדוגמה '30 דונם'",
  "שעות": "מספר שעות עבודה, ספרות בלבד אם אפשר",
  "כלי": "",
  "מפעיל": "",
  "הערות": ""
}

אם פרט מסוים לא הוזכר בהודעה כלל — השאר את הערך שלו כמחרוזת ריקה. אל תמציא מידע שלא נאמר בפירוש."""


def is_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY")) and _genai is not None


def _strip_json_fences(raw: str) -> str:
    """Gemini sometimes wraps JSON in ```json ... ``` despite instructions not to."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE).strip()


def _normalize_task(text: str) -> str:
    """Map free-text task guesses onto the fixed VALID_TASKS enum, defaulting to 'אחר'."""
    text = (text or "").strip()
    if text in VALID_TASKS:
        return text
    return next((t for t in VALID_TASKS if t in text), "אחר")


def _fields_from_json(raw: str) -> dict:
    """Parse a Gemini extraction response into WorkEntry-shaped field values.

    Raises ValueError/json.JSONDecodeError on malformed model output — callers
    decide how to degrade (e.g. ask the worker to try again).
    """
    data = json.loads(_strip_json_fences(raw))
    fields = {k: str(data.get(k, "") or "").strip() for k in FIELDS}
    fields["עבודה"] = _normalize_task(fields["עבודה"])
    fields["תאריך"] = fields["תאריך"] or date.today().strftime("%Y-%m-%d")
    return fields


def _call_gemini(parts: list) -> str:
    if not is_available():
        raise RuntimeError("GEMINI_API_KEY not configured — check is_available() before calling")
    _genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = _genai.GenerativeModel("gemini-2.5-flash")
    return model.generate_content(parts).text


def extract_fields_from_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> dict:
    """Synchronous/blocking — async callers (e.g. the Telegram bot) should run
    this via asyncio.to_thread rather than await it directly."""
    raw = _call_gemini([{"mime_type": mime_type, "data": audio_bytes}, EXTRACTION_PROMPT])
    return _fields_from_json(raw)


def extract_fields_from_text(text: str) -> dict:
    raw = _call_gemini([f"{EXTRACTION_PROMPT}\n\nההודעה שהתקבלה:\n{text}"])
    return _fields_from_json(raw)
