"""Pure-logic tests for gadash/ai_extract.py — the Gemini extraction shared
by the Telegram voice feature and the WhatsApp channel. No live Gemini call
here; these test the JSON-parsing/normalization logic against canned model
output, which is what actually determines correctness regardless of which
channel (voice audio or typed text) produced the input.
"""
import json

import pytest

from gadash.ai_extract import _fields_from_json, _normalize_task, _strip_json_fences


def test_strip_json_fences_removes_markdown_wrapper():
    assert _strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_json_fences('{"a": 1}') == '{"a": 1}'


def test_normalize_task_exact_match():
    assert _normalize_task("קציר") == "קציר"


def test_normalize_task_fuzzy_match():
    assert _normalize_task("עשינו חריש היום") == "חריש"


def test_normalize_task_unrecognized_falls_back_to_other():
    assert _normalize_task("משהו מוזר לגמרי") == "אחר"
    assert _normalize_task("") == "אחר"


def test_fields_from_json_happy_path():
    raw = json.dumps({
        "שם לקוח": "איתמר", "תאריך": "2026-06-01", "עבודה": "ריסוס",
        "שם חלקה": "חלקה ב", "גידול": "חיטה", "כמות": "30 דונם",
        "שעות": "3.5", "כלי": "מרסס", "מפעיל": "דני", "הערות": "",
    })
    fields = _fields_from_json(raw)
    assert fields["שם לקוח"] == "איתמר"
    assert fields["עבודה"] == "ריסוס"
    assert fields["תאריך"] == "2026-06-01"


def test_fields_from_json_defaults_missing_date_to_today():
    from datetime import date
    raw = json.dumps({"שם לקוח": "רוזה", "עבודה": "קציר"})
    fields = _fields_from_json(raw)
    assert fields["תאריך"] == date.today().strftime("%Y-%m-%d")


def test_fields_from_json_strips_code_fences():
    raw = '```json\n{"שם לקוח": "מאי", "עבודה": "דיסוק"}\n```'
    fields = _fields_from_json(raw)
    assert fields["שם לקוח"] == "מאי"
    assert fields["עבודה"] == "דיסוק"


def test_fields_from_json_malformed_raises():
    with pytest.raises(json.JSONDecodeError):
        _fields_from_json("not json at all")


def test_is_available_false_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from gadash import ai_extract
    assert ai_extract.is_available() is False


def test_is_available_true_with_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    from gadash import ai_extract
    assert ai_extract.is_available() is True
