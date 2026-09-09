"""Tests for gadash/whatsapp.py.

Nothing here hits the real WhatsApp Cloud API or Gemini — requests.post/get
and gadash.ai_extract's Gemini client are mocked, same approach already used
for the Telegram voice feature's tests. The conversation logic itself
(registration, free-text/voice job reporting, confirm) runs for real against
a local Postgres, same as every other test this session.
"""
import json
from unittest.mock import MagicMock

import pytest

from gadash import whatsapp


@pytest.fixture(autouse=True)
def _reset_sessions():
    """_sessions and the dedup tracker are module-level state — without this,
    one test's in-progress registration or seen-message-ids would leak into
    the next test's fresh phone number / message id checks."""
    whatsapp._sessions.clear()
    whatsapp._seen_message_ids.clear()
    whatsapp._seen_message_ids_set.clear()
    yield
    whatsapp._sessions.clear()
    whatsapp._seen_message_ids.clear()
    whatsapp._seen_message_ids_set.clear()


class TestVerifyWebhook:

    def test_correct_mode_and_token_returns_challenge(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secret123")
        assert whatsapp.verify_webhook("subscribe", "secret123", "chal-abc") == "chal-abc"

    def test_wrong_token_returns_none(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secret123")
        assert whatsapp.verify_webhook("subscribe", "wrong", "chal-abc") is None

    def test_wrong_mode_returns_none(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secret123")
        assert whatsapp.verify_webhook("unsubscribe", "secret123", "chal-abc") is None

    def test_no_verify_token_configured_returns_none(self, monkeypatch):
        monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)
        assert whatsapp.verify_webhook("subscribe", "anything", "chal-abc") is None


class TestParseIncoming:

    def test_text_message(self):
        payload = {"entry": [{"changes": [{"value": {"messages": [
            {"from": "972501234567", "type": "text", "text": {"body": "שלום"}, "id": "wamid.1"}
        ]}}]}]}
        assert whatsapp.parse_incoming(payload) == [
            {"phone": "972501234567", "text": "שלום", "audio_id": None, "id": "wamid.1"}
        ]

    def test_audio_message(self):
        payload = {"entry": [{"changes": [{"value": {"messages": [
            {"from": "972501234567", "type": "audio", "audio": {"id": "media-42"}, "id": "wamid.2"}
        ]}}]}]}
        assert whatsapp.parse_incoming(payload) == [
            {"phone": "972501234567", "text": None, "audio_id": "media-42", "id": "wamid.2"}
        ]

    def test_status_update_payload_yields_nothing(self):
        payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.abc", "status": "delivered"}]}}]}]}
        assert whatsapp.parse_incoming(payload) == []

    def test_unsupported_message_type_skipped(self):
        payload = {"entry": [{"changes": [{"value": {"messages": [
            {"from": "972501234567", "type": "image", "image": {"id": "img-1"}}
        ]}}]}]}
        assert whatsapp.parse_incoming(payload) == []

    def test_empty_payload(self):
        assert whatsapp.parse_incoming({}) == []


class TestSendTextMessage:

    def test_not_configured_skips_send(self, monkeypatch):
        monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
        monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
        called = MagicMock()
        monkeypatch.setattr("gadash.whatsapp.requests.post", called)
        whatsapp.send_text_message("972501234567", "hi")
        called.assert_not_called()

    def test_configured_posts_to_graph_api(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pnid-1")
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok-1")
        fake_resp = MagicMock()
        fake_post = MagicMock(return_value=fake_resp)
        monkeypatch.setattr("gadash.whatsapp.requests.post", fake_post)

        whatsapp.send_text_message("972501234567", "שלום")

        fake_post.assert_called_once()
        url, kwargs = fake_post.call_args[0][0], fake_post.call_args[1]
        assert "pnid-1/messages" in url
        assert kwargs["json"]["to"] == "972501234567"
        assert kwargs["json"]["text"]["body"] == "שלום"
        assert kwargs["headers"]["Authorization"] == "Bearer tok-1"
        fake_resp.raise_for_status.assert_called_once()


class TestDownloadMedia:

    def test_resolves_url_then_downloads(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok-1")
        meta_resp = MagicMock()
        meta_resp.json.return_value = {"url": "https://graph.example/media-file"}
        file_resp = MagicMock()
        file_resp.content = b"fake-audio-bytes"

        fake_get = MagicMock(side_effect=[meta_resp, file_resp])
        monkeypatch.setattr("gadash.whatsapp.requests.get", fake_get)

        result = whatsapp.download_media("media-42")

        assert result == b"fake-audio-bytes"
        assert fake_get.call_count == 2
        assert "media-42" in fake_get.call_args_list[0][0][0]
        assert fake_get.call_args_list[1][0][0] == "https://graph.example/media-file"


class TestRegistrationFlow:

    def test_first_ever_message_gets_welcome_and_is_not_treated_as_slug(self, mock_gsheet):
        """A brand-new phone number's first message — whatever it says —
        should get the welcome/instructions, not be silently interpreted
        as a (probably wrong) company-code guess."""
        reply = whatsapp.handle_message("972509999999", "Hi", None)
        assert reply == whatsapp.WELCOME_MESSAGE
        assert whatsapp._sessions["972509999999"]["state"] == "REGISTER_TENANT"

    def test_registers_and_links_worker(self, mock_gsheet):
        from gadash import auth
        from gadash.workers import _add_worker, _get_worker_by_whatsapp_number

        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        slug = auth.get_tenant_slug(mock_gsheet.tenant_id)
        phone = "972501234567"

        r0 = whatsapp.handle_message(phone, "שלום", None)
        assert r0 == whatsapp.WELCOME_MESSAGE

        r1 = whatsapp.handle_message(phone, slug, None)
        assert "שם המשתמש" in r1

        r2 = whatsapp.handle_message(phone, "דני", None)
        assert "סיסמה" in r2

        r3 = whatsapp.handle_message(phone, "pw12345", None)
        assert "נרשמת בהצלחה" in r3
        assert _get_worker_by_whatsapp_number(phone) == {"שם": "דני", "tenant_id": mock_gsheet.tenant_id}

    def test_wrong_slug_stays_on_step(self, mock_gsheet):
        phone = "972500000001"
        whatsapp.handle_message(phone, "היי", None)  # consumes the welcome step
        reply = whatsapp.handle_message(phone, "no-such-slug", None)
        assert "לא נמצא" in reply
        assert whatsapp._sessions[phone]["state"] == "REGISTER_TENANT"

    def test_wrong_name_resets_to_tenant_step(self, mock_gsheet):
        from gadash import auth
        slug = auth.get_tenant_slug(mock_gsheet.tenant_id)
        phone = "972500000002"
        whatsapp.handle_message(phone, "היי", None)  # consumes the welcome step
        whatsapp.handle_message(phone, slug, None)
        reply = whatsapp.handle_message(phone, "לא קיים בכלל", None)
        assert "לא נמצא" in reply
        assert whatsapp._sessions[phone]["state"] == "REGISTER_TENANT"

    def test_wrong_password_resets_to_tenant_step(self, mock_gsheet):
        from gadash import auth
        from gadash.workers import _add_worker
        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        slug = auth.get_tenant_slug(mock_gsheet.tenant_id)
        phone = "972500000003"
        whatsapp.handle_message(phone, "היי", None)  # consumes the welcome step
        whatsapp.handle_message(phone, slug, None)
        whatsapp.handle_message(phone, "דני", None)
        reply = whatsapp.handle_message(phone, "wrong-password", None)
        assert "שגויה" in reply
        assert whatsapp._sessions[phone]["state"] == "REGISTER_TENANT"

    def test_already_linked_worker_skips_registration_after_restart(self, mock_gsheet, monkeypatch):
        """Simulates the bot process restarting: in-memory _sessions is gone,
        but the worker is already linked in the DB — the very next message
        should resolve straight to the report flow, not ask them to register
        again."""
        from gadash.workers import _add_worker, _link_worker_whatsapp
        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000004")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        assert "972500000004" not in whatsapp._sessions  # confirms the "restart" premise
        reply = whatsapp.handle_message("972500000004", "עבדתי היום", None)

        assert "לא זמין" in reply  # the report-flow's fallback, not a registration prompt
        assert whatsapp._sessions["972500000004"]["state"] == "IDLE"


class TestJobReportFlow:

    def test_text_report_flows_to_confirm_and_saves(self, mock_gsheet, monkeypatch):
        from gadash.db import load_work_entries
        from gadash.workers import _add_worker, _link_worker_whatsapp

        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000010")

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        fake_response = MagicMock()
        fake_response.text = json.dumps({
            "שם לקוח": "איתמר", "עבודה": "ריסוס", "שעות": "3",
            "תאריך": "", "שם חלקה": "", "גידול": "", "כמות": "", "כלי": "", "מפעיל": "", "הערות": "",
        })
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        monkeypatch.setattr("gadash.ai_extract._genai", fake_genai)

        r1 = whatsapp.handle_message("972500000010", "עבדתי אצל איתמר, ריססתי 3 שעות", None)
        assert "איתמר" in r1
        assert whatsapp._sessions["972500000010"]["state"] == "CONFIRM"

        r2 = whatsapp.handle_message("972500000010", "כן", None)
        assert "נשמר" in r2
        assert whatsapp._sessions["972500000010"]["state"] == "IDLE"

        df = load_work_entries(mock_gsheet.tenant_id)
        assert len(df) == 1
        assert df.iloc[0]["שם לקוח"] == "איתמר"
        assert df.iloc[0]["מזין"] == "דני"

    def test_confirm_no_cancels_without_saving(self, mock_gsheet, monkeypatch):
        from gadash.db import load_work_entries
        from gadash.workers import _add_worker, _link_worker_whatsapp

        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000011")

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        fake_response = MagicMock()
        fake_response.text = json.dumps({"שם לקוח": "מאי", "עבודה": "חריש"})
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        monkeypatch.setattr("gadash.ai_extract._genai", fake_genai)

        whatsapp.handle_message("972500000011", "עבדתי אצל מאי חריש", None)
        reply = whatsapp.handle_message("972500000011", "לא", None)

        assert "בוטל" in reply
        assert load_work_entries(mock_gsheet.tenant_id).empty

    def test_voice_report_uses_audio_extraction(self, mock_gsheet, monkeypatch):
        from gadash.workers import _add_worker, _link_worker_whatsapp

        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000012")

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        fake_response = MagicMock()
        fake_response.text = json.dumps({"שם לקוח": "יורי", "עבודה": "קציר"})
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        monkeypatch.setattr("gadash.ai_extract._genai", fake_genai)

        reply = whatsapp.handle_message("972500000012", None, b"fake-audio-bytes")
        assert "יורי" in reply
        blob = fake_model.generate_content.call_args[0][0][0]
        assert blob["data"] == b"fake-audio-bytes"

    def test_no_client_name_stays_idle(self, mock_gsheet, monkeypatch):
        from gadash.workers import _add_worker, _link_worker_whatsapp

        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000013")

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        fake_response = MagicMock()
        fake_response.text = json.dumps({"שם לקוח": "", "עבודה": "חריש"})
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        monkeypatch.setattr("gadash.ai_extract._genai", fake_genai)

        reply = whatsapp.handle_message("972500000013", "עבדתי היום", None)
        assert "לקוח" in reply
        assert whatsapp._sessions["972500000013"]["state"] == "IDLE"

    def test_gemini_unavailable_gives_clear_message(self, mock_gsheet, monkeypatch):
        from gadash.workers import _add_worker, _link_worker_whatsapp

        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000014")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        reply = whatsapp.handle_message("972500000014", "עבדתי אצל מישהו", None)
        assert "לא זמין" in reply


class TestDuplicateMessages:
    """Meta retries webhook delivery when it doesn't get a fast response —
    the same message id can arrive more than once. Found in production
    testing: a slow reply (Gemini + Send API round trip) caused the same
    'כן' confirm to be redelivered and saved 3 times before this existed."""

    def test_first_call_is_not_a_duplicate(self):
        assert whatsapp.is_duplicate_message("wamid.abc") is False

    def test_second_call_with_same_id_is_a_duplicate(self):
        assert whatsapp.is_duplicate_message("wamid.abc") is False
        assert whatsapp.is_duplicate_message("wamid.abc") is True
        assert whatsapp.is_duplicate_message("wamid.abc") is True

    def test_different_ids_are_independent(self):
        assert whatsapp.is_duplicate_message("wamid.1") is False
        assert whatsapp.is_duplicate_message("wamid.2") is False
        assert whatsapp.is_duplicate_message("wamid.1") is True
        assert whatsapp.is_duplicate_message("wamid.2") is True

    def test_missing_id_is_never_flagged_as_duplicate(self):
        assert whatsapp.is_duplicate_message(None) is False
        assert whatsapp.is_duplicate_message(None) is False

    def test_tracker_is_bounded(self):
        maxlen = whatsapp._seen_message_ids.maxlen
        for i in range(maxlen + 10):
            assert whatsapp.is_duplicate_message(f"wamid.{i}") is False
        assert len(whatsapp._seen_message_ids) == maxlen
        assert len(whatsapp._seen_message_ids_set) == maxlen
        # the earliest ids were evicted to make room — no longer flagged as seen
        assert whatsapp.is_duplicate_message("wamid.0") is False

    def test_retried_confirm_does_not_double_save(self, mock_gsheet, monkeypatch):
        """End-to-end shape of the bug: the same 'כן' webhook delivered twice
        must save the work entry once, not twice."""
        from gadash.db import load_work_entries
        from gadash.workers import _add_worker, _link_worker_whatsapp

        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000020")

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        fake_response = MagicMock()
        fake_response.text = json.dumps({"שם לקוח": "רותם", "עבודה": "דיסוק"})
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        monkeypatch.setattr("gadash.ai_extract._genai", fake_genai)

        whatsapp.handle_message("972500000020", "עבדתי אצל רותם דיסוק", None)

        # Simulate app.py's route: check is_duplicate_message before calling
        # handle_message at all, exactly like the real webhook loop does.
        confirm_id = "wamid.confirm-1"
        assert whatsapp.is_duplicate_message(confirm_id) is False
        whatsapp.handle_message("972500000020", "כן", None)

        # Meta retries delivery of the same confirm event.
        assert whatsapp.is_duplicate_message(confirm_id) is True
        # A real caller would skip calling handle_message entirely here.

        df = load_work_entries(mock_gsheet.tenant_id)
        assert len(df) == 1
