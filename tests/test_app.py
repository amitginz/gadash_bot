"""
Automated tests for gadash_bot.
Run:  pytest tests/ -v
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app, WorkEntry, COLUMNS, VALID_TASKS


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["_csrf"]     = "test-csrf-token"
        yield c

CSRF_HEADER = {"X-CSRFToken": "test-csrf-token"}


# ── WorkEntry validation ───────────────────────────────────────────────────────

class TestWorkEntry:

    def test_valid_entry(self):
        e = WorkEntry(client="לקוח א", date="2025-06-15", task="חריש")
        assert e.client == "לקוח א"
        assert e.date == "2025-06-15"
        assert e.task == "חריש"

    def test_all_valid_tasks(self):
        for t in VALID_TASKS:
            e = WorkEntry(client="א", date="2025-01-01", task=t)
            assert e.task == t

    def test_missing_client_raises(self):
        with pytest.raises(ValueError, match="שם לקוח חובה"):
            WorkEntry(client="", date="2025-06-15", task="חריש")

    def test_whitespace_client_raises(self):
        with pytest.raises(ValueError, match="שם לקוח חובה"):
            WorkEntry(client="   ", date="2025-06-15", task="חריש")

    def test_invalid_date_format_raises(self):
        with pytest.raises(ValueError, match="תאריך לא תקין"):
            WorkEntry(client="א", date="15/06/2025", task="חריש")

    def test_invalid_date_dashes_wrong_order_raises(self):
        with pytest.raises(ValueError, match="תאריך לא תקין"):
            WorkEntry(client="א", date="15-06-2025", task="חריש")

    def test_invalid_task_raises(self):
        with pytest.raises(ValueError, match="סוג עבודה לא תקין"):
            WorkEntry(client="א", date="2025-06-15", task="כריתה")

    def test_client_stripped(self):
        e = WorkEntry(client="  לקוח ב  ", date="2025-01-01", task="קציר")
        assert e.client == "לקוח ב"

    def test_to_sheet_row_length(self):
        e = WorkEntry(client="א", date="2025-01-01", task="ריסוס", entered_by="Web")
        row = e.to_sheet_row()
        assert len(row) == len(COLUMNS)

    def test_to_dict_keys(self):
        e = WorkEntry(client="א", date="2025-01-01", task="דיסוק", entered_by="Web")
        d = e.to_dict()
        assert set(d.keys()) == set(COLUMNS)
        assert d["שם לקוח"] == "א"
        assert d["מזין"] == "Web"

    def test_from_dict_roundtrip(self):
        original = WorkEntry(client="כרמל", date="2025-03-10", task="חריש",
                             field_name="א", amount="20 דונם", tool="טרקטור",
                             operator="יוסי", notes="בוצע", entered_by="Web")
        d = original.to_dict()
        restored = WorkEntry.from_dict(d)
        assert restored.client == original.client
        assert restored.task   == original.task
        assert restored.amount == original.amount

    def test_from_form(self):
        class FakeForm(dict):
            def get(self, k, default=""):
                return super().get(k, default)
        form = FakeForm({"שם לקוח": "א", "תאריך": "2025-01-01", "עבודה": "אחר"})
        e = WorkEntry.from_form(form, entered_by="Web")
        assert e.task == "אחר"
        assert e.entered_by == "Web"


# ── Flask routes ───────────────────────────────────────────────────────────────

class TestFlaskRoutes:

    def test_redirect_to_login_when_not_authenticated(self):
        with app.test_client() as anon:
            res = anon.get("/")
            assert res.status_code == 302
            assert "/login" in res.headers["Location"]

    def test_index_200_when_logged_in(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "גד".encode() in res.data

    def test_login_page_loads(self):
        with app.test_client() as anon:
            res = anon.get("/login")
            assert res.status_code == 200

    def test_add_get_200(self, client):
        res = client.get("/add")
        assert res.status_code == 200

    def test_summary_get_200(self, client):
        res = client.get("/summary")
        assert res.status_code == 200

    def test_audit_get_200(self, client):
        res = client.get("/audit")
        assert res.status_code == 200

    def test_print_get_200(self, client):
        res = client.get("/print")
        assert res.status_code == 200

    def test_import_get_200(self, client):
        res = client.get("/import")
        assert res.status_code == 200

    def test_change_password_get_200(self, client):
        res = client.get("/change-password")
        assert res.status_code == 200

    def test_api_docs_get_200(self, client):
        res = client.get("/api/docs")
        assert res.status_code == 200

    def test_api_entries_returns_json(self, client):
        res = client.get("/api/entries")
        assert res.status_code == 200
        assert res.is_json

    def test_api_entries_filter_by_client(self, client):
        res = client.get("/api/entries?client=__nonexistent__xyz__")
        assert res.status_code == 200
        data = res.get_json()
        assert data == []

    def test_api_patch_invalid_field(self, client):
        res = client.patch(
            "/api/entries/0",
            json={"field": "שדה_לא_קיים", "value": "x"},
            headers=CSRF_HEADER,
            content_type="application/json",
        )
        assert res.status_code == 400

    def test_export_redirect_when_empty(self, client):
        # When no data: flash + redirect; when data: 200 with xlsx
        res = client.get("/export")
        assert res.status_code in (200, 302)

    def test_export_csv_redirect_when_empty(self, client):
        res = client.get("/export/csv")
        assert res.status_code in (200, 302)

    def test_edit_get_nonexistent_row(self, client):
        res = client.get("/edit/99999")
        # Returns 200 with error message or redirect — should not 500
        assert res.status_code in (200, 302, 404)

    def test_delete_redirects(self, client):
        res = client.post("/delete/99999", headers=CSRF_HEADER)
        # Always redirects — error is flashed, not raised
        assert res.status_code == 302

    def test_bulk_delete_empty_list(self, client):
        res = client.post("/bulk-delete", data={}, headers=CSRF_HEADER)
        assert res.status_code == 302

    def test_bulk_delete_requires_csrf(self):
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["logged_in"] = True
                sess["_csrf"] = "real-token"
            res = c.post("/bulk-delete", data={"row_ids": ["0"]},
                         headers={"X-CSRFToken": "wrong-token"})
            assert res.status_code in (302, 403)

    def test_workers_page_200(self, client):
        res = client.get("/workers")
        assert res.status_code == 200

    def test_add_worker_missing_fields(self, client):
        res = client.post("/workers", data={"name": "", "password": ""},
                          headers=CSRF_HEADER)
        assert res.status_code == 302

    def test_add_worker_short_password(self, client):
        res = client.post("/workers", data={"name": "עובד", "password": "ab"},
                          headers=CSRF_HEADER)
        assert res.status_code == 302

    def test_dashboard_page_200(self, client):
        res = client.get("/dashboard")
        assert res.status_code == 200

    def test_api_dashboard_returns_json(self, client):
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        assert res.is_json

    def test_api_dashboard_cached(self, client):
        res1 = client.get("/api/dashboard")
        res2 = client.get("/api/dashboard")
        assert res1.status_code == 200
        assert res2.status_code == 200
        # Second call should return same updated_at (served from cache)
        d1 = res1.get_json()
        d2 = res2.get_json()
        if "updated_at" in d1 and "updated_at" in d2:
            assert d1["updated_at"] == d2["updated_at"]

    def test_import_get_200(self, client):
        res = client.get("/import")
        assert res.status_code == 200

    def test_import_rejects_oversized_file(self, client):
        import io
        big = io.BytesIO(b"x" * (6 * 1024 * 1024))
        big.name = "big.xlsx"
        res = client.post(
            "/import",
            data={"file": (big, "big.xlsx")},
            content_type="multipart/form-data",
            headers=CSRF_HEADER,
        )
        assert res.status_code in (200, 302)

    def test_404_returns_html(self):
        with app.test_client() as c:
            res = c.get("/this-route-does-not-exist-xyz")
            assert res.status_code == 404
            assert b"404" in res.data

    def test_worker_portal_redirects_unauthenticated(self):
        with app.test_client() as anon:
            res = anon.get("/worker")
            assert res.status_code == 302

    def test_worker_index_200(self):
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["worker_logged_in"] = True
                sess["worker_name"]      = "עובד בדיקה"
                sess["_csrf"]            = "test-csrf-token"
            res = c.get("/worker")
            assert res.status_code == 200

    def test_worker_post_invalid_entry(self):
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["worker_logged_in"] = True
                sess["worker_name"]      = "עובד בדיקה"
                sess["_csrf"]            = "test-csrf-token"
            res = c.post("/worker",
                         data={"שם לקוח": "", "תאריך": "2025-01-01", "עבודה": "חריש"},
                         headers=CSRF_HEADER)
            assert res.status_code == 302

    def test_fields_map_200(self, client):
        res = client.get("/fields-map")
        assert res.status_code == 200

    def test_api_fields_returns_json(self, client):
        res = client.get("/api/fields")
        assert res.status_code == 200
        assert res.is_json


# ── WorkEntry date validation ──────────────────────────────────────────────────

class TestWorkEntryDateValidation:

    def test_impossible_date_raises(self):
        with pytest.raises(ValueError, match="תאריך לא קיים"):
            WorkEntry(client="א", date="2025-02-30", task="חריש")

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError, match="תאריך לא קיים"):
            WorkEntry(client="א", date="2025-13-01", task="חריש")

    def test_valid_leap_year_date(self):
        e = WorkEntry(client="א", date="2024-02-29", task="חריש")
        assert e.date == "2024-02-29"

    def test_invalid_leap_year_raises(self):
        with pytest.raises(ValueError):
            WorkEntry(client="א", date="2025-02-29", task="חריש")


# ── Security tests ─────────────────────────────────────────────────────────────

class TestSecurity:

    def test_csrf_required_for_post(self):
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["logged_in"] = True
                sess["_csrf"]     = "valid-token"
            res = c.post("/bulk-delete", data={"row_ids": []},
                         headers={"X-CSRFToken": "wrong-token"})
            assert res.status_code in (302, 403)

    def test_login_rate_limited(self):
        with app.test_client() as c:
            for _ in range(6):
                c.post("/login", data={"role": "manager", "password": "wrong"})
            res = c.post("/login", data={"role": "manager", "password": "wrong"})
            assert res.status_code == 200
            assert "ניסיונות" in res.data.decode("utf-8") or "המתן" in res.data.decode("utf-8")

    def test_manager_password_hashed_in_memory(self):
        from app import _current_password_hash
        assert _is_hashed(_current_password_hash)

    def test_search_regex_injection_safe(self, client):
        # A raw regex string should not crash the server
        res = client.get("/api/entries?q=.*%2B%5B%5D")
        assert res.status_code == 200


def _is_hashed(s: str) -> bool:
    return s.startswith(("pbkdf2:", "scrypt:", "argon2:"))


# ── Bot module smoke tests ─────────────────────────────────────────────────────

class TestBotModule:

    def test_bot_imports_without_error(self):
        import gadash.bot as bot
        assert hasattr(bot, "start_telegram_bot")

    def test_state_constants_defined(self):
        from gadash.bot import (MENU, CLIENT, DATE, TASK, FIELD, CROP,
                                 AMOUNT, HOURS, TOOL, OPERATOR, NOTE,
                                 CONFIRM, SEARCH, EDIT_SELECT,
                                 REGISTER_NAME, REGISTER_PASSWORD)
        states = [MENU, CLIENT, DATE, TASK, FIELD, CROP, AMOUNT, HOURS,
                  TOOL, OPERATOR, NOTE, CONFIRM, SEARCH, EDIT_SELECT,
                  REGISTER_NAME, REGISTER_PASSWORD]
        assert len(states) == 16
        assert len(set(states)) == 16  # all unique

    def test_task_choices_cover_valid_tasks(self):
        from gadash.bot import TASK_CHOICES
        from gadash.models import VALID_TASKS
        flat = [t for row in TASK_CHOICES for t in row]
        assert set(flat) == VALID_TASKS

    def test_menu_keyboard_structure(self):
        from gadash.bot import MENU_KEYBOARD, CONFIRM_KEYBOARD
        assert isinstance(MENU_KEYBOARD, list)
        assert ["כן", "לא"] in CONFIRM_KEYBOARD

    def test_recent_clients_markup_returns_markup_or_remove(self):
        from gadash.bot import _recent_clients_markup
        from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
        result = _recent_clients_markup()
        assert isinstance(result, (ReplyKeyboardMarkup, ReplyKeyboardRemove))
