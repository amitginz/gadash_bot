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
