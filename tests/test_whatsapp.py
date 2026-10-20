"""Tests for gadash/whatsapp.py.

Nothing here hits the real WhatsApp Cloud API or Gemini — requests.post/get
and gadash.ai_extract's Gemini client are mocked, same approach already used
for the Telegram voice feature's tests. The conversation logic itself
(registration, free-text/voice job reporting, confirm) runs for real against
a local Postgres, same as every other test this session.
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from gadash import whatsapp
from gadash.models_db import WhatsAppProcessedMessage, WhatsAppSession, db


def _state(phone: str) -> str:
    """Conversation state as stored in Postgres. A worker with nothing pending
    has no row at all, which reads as IDLE."""
    session = whatsapp._load_session(phone)
    return session["state"] if session else "IDLE"


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
        assert _state("972509999999") == "REGISTER_TENANT"

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
        assert _state(phone) == "REGISTER_TENANT"

    def test_wrong_name_resets_to_tenant_step(self, mock_gsheet):
        from gadash import auth
        slug = auth.get_tenant_slug(mock_gsheet.tenant_id)
        phone = "972500000002"
        whatsapp.handle_message(phone, "היי", None)  # consumes the welcome step
        whatsapp.handle_message(phone, slug, None)
        reply = whatsapp.handle_message(phone, "לא קיים בכלל", None)
        assert "לא נמצא" in reply
        assert _state(phone) == "REGISTER_TENANT"

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
        assert _state(phone) == "REGISTER_TENANT"

    def test_already_linked_worker_skips_registration_after_restart(self, mock_gsheet, monkeypatch):
        """A worker already linked in the DB has no conversation row and no
        process memory to rely on — the very next message should resolve
        straight to the report flow, not ask them to register again."""
        from gadash.workers import _add_worker, _link_worker_whatsapp
        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000004")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        assert whatsapp._load_session("972500000004") is None
        reply = whatsapp.handle_message("972500000004", "עבדתי היום", None)

        assert "לא זמין" in reply  # the report-flow's fallback, not a registration prompt
        assert _state("972500000004") == "IDLE"


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
        assert "👤 *לקוח:* איתמר" in r1
        assert "🔧 *עבודה:* ריסוס" in r1
        assert _state("972500000010") == "CONFIRM"

        r2 = whatsapp.handle_message("972500000010", "כן", None)
        assert "נשמר" in r2
        assert _state("972500000010") == "IDLE"

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
        assert _state("972500000013") == "IDLE"

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

    def test_first_sighting_is_recorded_in_the_database(self):
        assert whatsapp.is_duplicate_message("wamid.persisted") is False
        assert db.session.get(WhatsAppProcessedMessage, "wamid.persisted") is not None

    def test_records_past_retention_are_purged(self):
        old = datetime.now(timezone.utc) - whatsapp.DEDUP_RETENTION - timedelta(hours=1)
        db.session.add(WhatsAppProcessedMessage(message_id="wamid.ancient", created_at=old))
        db.session.commit()

        assert whatsapp.is_duplicate_message("wamid.fresh") is False  # triggers the purge
        assert db.session.get(WhatsAppProcessedMessage, "wamid.ancient") is None
        assert whatsapp.is_duplicate_message("wamid.ancient") is False  # forgotten, so treated as new

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


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:

    def test_valid_signature_accepted(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
        body = b'{"entry": []}'
        assert whatsapp.verify_signature(body, _sign(body, "s3cret")) is True

    def test_tampered_body_rejected(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
        sig = _sign(b'{"entry": []}', "s3cret")
        assert whatsapp.verify_signature(b'{"entry": [1]}', sig) is False

    def test_wrong_secret_rejected(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
        body = b"{}"
        assert whatsapp.verify_signature(body, _sign(body, "other")) is False

    def test_missing_or_malformed_header_rejected(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
        body = b"{}"
        digest = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        assert whatsapp.verify_signature(body, None) is False
        assert whatsapp.verify_signature(body, "") is False
        assert whatsapp.verify_signature(body, digest) is False  # no "sha256=" prefix

    def test_fails_closed_when_secret_not_configured(self, monkeypatch):
        monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
        body = b"{}"
        assert whatsapp.verify_signature(body, _sign(body, "")) is False
        assert whatsapp.verify_signature(body, _sign(body, "anything")) is False


class TestWebhookRouteSignature:
    """The POST route must not process (or reply to) anything unsigned —
    otherwise anyone who finds the URL can forge a worker's message."""

    def _post(self, body: bytes, signature: str | None):
        import app as app_module
        headers = {"X-Hub-Signature-256": signature} if signature else {}
        return app_module.app.test_client().post(
            "/webhook/whatsapp", data=body, content_type="application/json", headers=headers)

    def _payload(self) -> bytes:
        return json.dumps({"entry": [{"changes": [{"value": {"messages": [
            {"from": "972500000099", "type": "text", "text": {"body": "היי"}, "id": "wamid.route-1"}
        ]}}]}]}).encode()

    @pytest.fixture
    def spies(self, monkeypatch):
        handle, send = MagicMock(return_value="reply"), MagicMock()
        monkeypatch.setattr(whatsapp, "handle_message", handle)
        monkeypatch.setattr(whatsapp, "send_text_message", send)
        return handle, send

    def test_unsigned_request_rejected_and_not_processed(self, mock_gsheet, monkeypatch, spies):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
        resp = self._post(self._payload(), None)
        assert resp.status_code == 403
        spies[0].assert_not_called()
        spies[1].assert_not_called()

    def test_forged_signature_rejected_and_not_processed(self, mock_gsheet, monkeypatch, spies):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
        body = self._payload()
        resp = self._post(body, _sign(body, "attacker-guess"))
        assert resp.status_code == 403
        spies[0].assert_not_called()

    def test_rejected_when_secret_not_configured(self, mock_gsheet, monkeypatch, spies):
        monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
        body = self._payload()
        resp = self._post(body, _sign(body, ""))
        assert resp.status_code == 403
        spies[0].assert_not_called()

    def test_signed_request_is_processed_and_replied_to(self, mock_gsheet, monkeypatch, spies):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
        body = self._payload()
        resp = self._post(body, _sign(body, "s3cret"))
        assert resp.status_code == 200
        spies[0].assert_called_once_with("972500000099", "היי", None)
        spies[1].assert_called_once_with("972500000099", "reply")


class TestPersistentConversations:
    """Conversation state lives in Postgres, not process memory: it survives
    restarts, and every message re-derives a registered worker from the
    workers table instead of trusting a cached copy."""

    def _fake_gemini(self, monkeypatch, fields: dict):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        response = MagicMock()
        response.text = json.dumps(fields)
        model = MagicMock()
        model.generate_content.return_value = response
        genai = MagicMock()
        genai.GenerativeModel.return_value = model
        monkeypatch.setattr("gadash.ai_extract._genai", genai)

    def test_registration_progress_is_stored_then_cleared(self, mock_gsheet):
        from gadash import auth
        from gadash.workers import _add_worker
        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        slug = auth.get_tenant_slug(mock_gsheet.tenant_id)
        phone = "972500000030"

        whatsapp.handle_message(phone, "היי", None)
        whatsapp.handle_message(phone, slug, None)
        row = db.session.get(WhatsAppSession, phone)
        assert row.state == "REGISTER_NAME"
        assert row.data == {"reg_tenant_id": mock_gsheet.tenant_id}

        whatsapp.handle_message(phone, "דני", None)
        whatsapp.handle_message(phone, "pw12345", None)
        assert db.session.get(WhatsAppSession, phone) is None  # registered: nothing pending

    def test_pending_report_is_stored_and_saved_on_confirm(self, mock_gsheet, monkeypatch):
        from gadash.db import load_work_entries
        from gadash.workers import _add_worker, _link_worker_whatsapp
        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000031")
        self._fake_gemini(monkeypatch, {"שם לקוח": "גל", "עבודה": "גיזום", "שעות": "2"})

        whatsapp.handle_message("972500000031", "גזמתי אצל גל שעתיים", None)
        row = db.session.get(WhatsAppSession, "972500000031")
        assert row.state == "CONFIRM"
        assert row.data["pending_fields"]["שם לקוח"] == "גל"
        assert row.data["worker_name"] == "דני"

        assert "נשמר" in whatsapp.handle_message("972500000031", "כן", None)
        assert db.session.get(WhatsAppSession, "972500000031") is None
        assert len(load_work_entries(mock_gsheet.tenant_id)) == 1

    def test_stale_confirmation_expires_instead_of_saving(self, mock_gsheet, monkeypatch):
        """A forgotten pending report must not be saved by a stray "כן" later."""
        from gadash.db import load_work_entries
        from gadash.workers import _add_worker, _link_worker_whatsapp
        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000032")
        self._fake_gemini(monkeypatch, {"שם לקוח": "גל", "עבודה": "גיזום"})
        whatsapp.handle_message("972500000032", "גזמתי אצל גל", None)

        row = db.session.get(WhatsAppSession, "972500000032")
        row.updated_at = datetime.now(timezone.utc) - whatsapp.SESSION_TTL - timedelta(minutes=1)
        db.session.commit()
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        reply = whatsapp.handle_message("972500000032", "כן", None)
        assert "נשמר" not in reply
        assert load_work_entries(mock_gsheet.tenant_id).empty
        assert db.session.get(WhatsAppSession, "972500000032") is None

    def test_removed_worker_cannot_keep_reporting(self, mock_gsheet, monkeypatch):
        """Used to keep working until the process restarted, via a cached
        in-memory session."""
        from gadash.workers import _add_worker, _delete_worker, _link_worker_whatsapp
        _add_worker(mock_gsheet.tenant_id, "דני", "pw12345")
        _link_worker_whatsapp(mock_gsheet.tenant_id, "דני", "972500000033")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert "לא זמין" in whatsapp.handle_message("972500000033", "עבדתי", None)  # recognized

        _delete_worker(mock_gsheet.tenant_id, "דני")
        assert whatsapp.handle_message("972500000033", "עבדתי", None) == whatsapp.WELCOME_MESSAGE
