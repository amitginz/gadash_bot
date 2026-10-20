"""
Automated tests for gadash_bot.
Run:  pytest tests/ -v
"""
import json
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
from datetime import date

from app import app, WorkEntry, COLUMNS, VALID_TASKS


# ── Fixtures ──────────────────────────────────────────────────────────────────
# `client` (logged in as a manager, tenant already resolved) and `mock_gsheet`
# (that tenant's seedable state) come from conftest.py.

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

    def test_worker_index_200(self, mock_gsheet):
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["worker_logged_in"] = True
                sess["worker_name"]      = "עובד בדיקה"
                sess["tenant_id"]        = mock_gsheet.tenant_id
                sess["_csrf"]            = "test-csrf-token"
            res = c.get("/worker")
            assert res.status_code == 200

    def test_worker_post_invalid_entry(self, mock_gsheet):
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["worker_logged_in"] = True
                sess["worker_name"]      = "עובד בדיקה"
                sess["tenant_id"]        = mock_gsheet.tenant_id
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

    def test_api_fields_save_pin(self, client, mock_gsheet):
        from gadash.db import load_pins
        res = client.post("/api/fields", json={
            "uid": "u1", "name": "חלקה א", "type": "pin", "lat": 32.1, "lng": 35.1,
        }, headers=CSRF_HEADER)
        assert res.status_code == 200
        assert res.get_json()["ok"] is True
        pins = load_pins(mock_gsheet.tenant_id)
        assert pins == [{"uid": "u1", "name": "חלקה א", "color": "", "lat": 32.1, "lng": 35.1}]

    def test_api_fields_save_polygon_returns_area(self, client):
        # Roughly a 1km x 1km square near the equator's latitude scale → ~1000 dunam.
        coords = [[0.0, 0.0], [0.009, 0.0], [0.009, 0.009], [0.0, 0.009]]
        res = client.post("/api/fields", json={
            "uid": "u2", "name": "שטח", "type": "polygon", "coordinates": coords,
        }, headers=CSRF_HEADER)
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        assert data["area_dunam"] > 0

    def test_api_fields_save_missing_type_400(self, client):
        res = client.post("/api/fields", json={"uid": "u3", "name": "x"}, headers=CSRF_HEADER)
        assert res.status_code == 400

    def test_api_fields_delete_by_uid(self, client, mock_gsheet):
        from gadash.db import load_pins, save_pin
        save_pin(mock_gsheet.tenant_id, "u1", "חלקה א", "", 32.1, 35.1)
        res = client.delete("/api/fields/u1", headers=CSRF_HEADER)
        assert res.status_code == 200
        assert load_pins(mock_gsheet.tenant_id) == []


# ── Sheet mutation routes (row_id addressing) ──────────────────────────────────
# Regression coverage for two bugs found & fixed while auditing this project:
#   1. /api/dashboard cached "no data" responses inconsistently (app.py).
#   2. edit/delete/patch used a possibly-stale cached df to address rows by
#      position, risking writes to the wrong row under concurrent edits.

class TestSheetMutations:

    def _seed(self, mock_gsheet, rows):
        mock_gsheet["df"] = pd.DataFrame(rows, columns=COLUMNS)

    def test_edit_updates_correct_row(self, client, mock_gsheet):
        self._seed(mock_gsheet, [
            WorkEntry(client="לקוח א", date="2025-06-01", task="חריש", entered_by="Web").to_dict(),
            WorkEntry(client="לקוח ב", date="2025-06-02", task="קציר", entered_by="Web").to_dict(),
        ])
        df = mock_gsheet["df"]
        id_a = int(df[df["שם לקוח"] == "לקוח א"].iloc[0]["_row_id"])
        id_b = int(df[df["שם לקוח"] == "לקוח ב"].iloc[0]["_row_id"])
        res = client.post(f"/edit/{id_b}", data={
            "שם לקוח": "לקוח ב מעודכן", "תאריך": "2025-06-02", "עבודה": "ריסוס",
        }, headers=CSRF_HEADER)
        assert res.status_code == 302
        df = mock_gsheet["df"]
        assert df[df["_row_id"] == id_a].iloc[0]["שם לקוח"] == "לקוח א"           # untouched
        assert df[df["_row_id"] == id_b].iloc[0]["שם לקוח"] == "לקוח ב מעודכן"    # updated
        assert df[df["_row_id"] == id_b].iloc[0]["עבודה"] == "ריסוס"

    def test_edit_out_of_range_row_does_not_crash(self, client, mock_gsheet):
        self._seed(mock_gsheet, [
            WorkEntry(client="לקוח א", date="2025-06-01", task="חריש", entered_by="Web").to_dict(),
        ])
        res = client.post("/edit/999999999", data={
            "שם לקוח": "משהו", "תאריך": "2025-06-02", "עבודה": "ריסוס",
        }, headers=CSRF_HEADER)
        assert res.status_code == 302
        assert len(mock_gsheet["df"]) == 1  # unchanged

    def test_delete_removes_correct_row(self, client, mock_gsheet):
        self._seed(mock_gsheet, [
            WorkEntry(client="לקוח א", date="2025-06-01", task="חריש", entered_by="Web").to_dict(),
            WorkEntry(client="לקוח ב", date="2025-06-02", task="קציר", entered_by="Web").to_dict(),
        ])
        df = mock_gsheet["df"]
        id_a = int(df[df["שם לקוח"] == "לקוח א"].iloc[0]["_row_id"])
        res = client.post(f"/delete/{id_a}", headers=CSRF_HEADER)
        assert res.status_code == 302
        df = mock_gsheet["df"]
        assert len(df) == 1
        assert df.iloc[0]["שם לקוח"] == "לקוח ב"

    def test_patch_cell_updates_correct_field(self, client, mock_gsheet):
        self._seed(mock_gsheet, [
            WorkEntry(client="לקוח א", date="2025-06-01", task="חריש", entered_by="Web").to_dict(),
        ])
        row_id = int(mock_gsheet["df"].iloc[0]["_row_id"])
        res = client.patch(f"/api/entries/{row_id}", json={"field": "שעות", "value": "7.5"},
                            headers=CSRF_HEADER, content_type="application/json")
        assert res.status_code == 200
        assert mock_gsheet["df"].iloc[0]["שעות"] == "7.5"

    def test_dashboard_reflects_seeded_data(self, client, mock_gsheet):
        self._seed(mock_gsheet, [
            WorkEntry(client="לקוח א", date=date.today().strftime("%Y-%m-%d"),
                      task="חריש", hours="4", entered_by="Web").to_dict(),
        ])
        res = client.get("/api/dashboard")
        assert res.status_code == 200
        data = res.get_json()
        assert data["kpis"]["total"] == 1

    def test_dashboard_empty_and_populated_are_both_cached_consistently(self, client, mock_gsheet):
        # Covers the fixed bug: the empty-data branch used to skip caching,
        # so consecutive calls returned different `updated_at` timestamps.
        res1 = client.get("/api/dashboard")  # empty df from fixture default
        res2 = client.get("/api/dashboard")
        assert res1.get_json()["updated_at"] == res2.get_json()["updated_at"]


class TestProfitReport:

    def _seed(self, mock_gsheet, rows):
        mock_gsheet["df"] = pd.DataFrame(rows, columns=COLUMNS)

    def test_profit_empty_shows_zero_state(self, client):
        res = client.get("/profit")
        assert res.status_code == 200
        assert "אין נתונים להצגה".encode() in res.data

    def test_profit_saving_rates_round_trips(self, client, mock_gsheet):
        res = client.post("/profit", data={
            "revenue_חריש": "120", "cost_חריש": "40",
            "revenue_ריסוס": "150", "cost_ריסוס": "60",
            "revenue_קציר": "0", "cost_קציר": "0",
            "revenue_דיסוק": "0", "cost_דיסוק": "0",
            "revenue_אחר": "0", "cost_אחר": "0",
        }, headers=CSRF_HEADER)
        assert res.status_code == 302
        assert mock_gsheet["rates"]["חריש"] == {"revenue": 120.0, "cost": 40.0}
        assert mock_gsheet["rates"]["ריסוס"] == {"revenue": 150.0, "cost": 60.0}

    def test_profit_computes_revenue_cost_and_margin_per_field(self, client, mock_gsheet):
        mock_gsheet["rates"]["חריש"] = {"revenue": 100.0, "cost": 30.0}
        mock_gsheet["rates"]["ריסוס"] = {"revenue": 80.0, "cost": 20.0}
        self._seed(mock_gsheet, [
            WorkEntry(client="איתמר", date="2026-06-01", task="חריש",
                      field_name="חלקה א", hours="10", entered_by="Web").to_dict(),
            WorkEntry(client="מאי", date="2026-06-02", task="ריסוס",
                      field_name="חלקה ב", hours="5", entered_by="Web").to_dict(),
        ])
        res = client.get("/profit")
        assert res.status_code == 200
        html = res.data.decode()
        # חלקה א: 10h * (100-30) = 700 profit, 70% margin
        assert "חלקה א" in html
        assert "700" in html
        # חלקה ב: 5h * (80-20) = 300 profit
        assert "חלקה ב" in html
        assert "300" in html

    def test_profit_date_filter_excludes_out_of_range_rows(self, client, mock_gsheet):
        mock_gsheet["rates"]["חריש"] = {"revenue": 100.0, "cost": 0.0}
        self._seed(mock_gsheet, [
            WorkEntry(client="איתמר", date="2026-01-01", task="חריש",
                      field_name="חלקה ישנה", hours="10", entered_by="Web").to_dict(),
        ])
        res = client.get("/profit?date_from=2026-06-01")
        assert res.status_code == 200
        assert "חלקה ישנה" not in res.data.decode()

    def test_profit_unrated_task_contributes_zero(self, client, mock_gsheet):
        # A task with no configured rate must not crash the report — it just
        # contributes zero revenue/cost, same as a freshly-created sheet.
        self._seed(mock_gsheet, [
            WorkEntry(client="רוזה", date="2026-06-01", task="דיסוק",
                      field_name="חלקה ג", hours="8", entered_by="Web").to_dict(),
        ])
        res = client.get("/profit")
        assert res.status_code == 200
        assert "חלקה ג" in res.data.decode()


class TestBillingSummary:

    def _seed(self, mock_gsheet, rows):
        mock_gsheet["df"] = pd.DataFrame(rows, columns=COLUMNS)

    def test_billing_without_client_redirects(self, client):
        res = client.get("/client-report/billing")
        assert res.status_code == 302
        assert "/client-report" in res.headers["Location"]

    def test_billing_computes_line_totals_and_grand_total(self, client, mock_gsheet):
        mock_gsheet["rates"]["חריש"] = {"revenue": 100.0, "cost": 0.0}
        self._seed(mock_gsheet, [
            WorkEntry(client="איתמר", date="2026-06-01", task="חריש",
                      field_name="חלקה א", hours="4", entered_by="Web").to_dict(),
            WorkEntry(client="איתמר", date="2026-06-03", task="חריש",
                      field_name="חלקה ב", hours="2", entered_by="Web").to_dict(),
        ])
        res = client.get("/client-report/billing?client=איתמר")
        assert res.status_code == 200
        html = res.data.decode()
        assert "400" in html   # 4h * 100
        assert "200" in html   # 2h * 100
        assert "600" in html   # grand total

    def test_billing_unrated_task_shows_warning_not_crash(self, client, mock_gsheet):
        self._seed(mock_gsheet, [
            WorkEntry(client="רוזה", date="2026-06-01", task="דיסוק",
                      field_name="חלקה ג", hours="3", entered_by="Web").to_dict(),
        ])
        res = client.get("/client-report/billing?client=רוזה")
        assert res.status_code == 200
        html = res.data.decode()
        assert "דיסוק" in html
        assert "לא הוגדר תעריף" in html

    def test_billing_date_range_filters_rows(self, client, mock_gsheet):
        mock_gsheet["rates"]["קציר"] = {"revenue": 50.0, "cost": 0.0}
        self._seed(mock_gsheet, [
            WorkEntry(client="יורי", date="2025-01-01", task="קציר",
                      hours="2", entered_by="Web").to_dict(),
            WorkEntry(client="יורי", date="2026-06-01", task="קציר",
                      hours="2", entered_by="Web").to_dict(),
        ])
        res = client.get("/client-report/billing?client=יורי&date_from=2026-01-01")
        assert res.status_code == 200
        html = res.data.decode()
        assert "2025-01-01" not in html
        assert "2026-06-01" in html

    def test_billing_no_matching_client_shows_empty_message(self, client, mock_gsheet):
        res = client.get("/client-report/billing?client=לא-קיים")
        assert res.status_code == 200
        assert "אין עבודות בטווח שנבחר" in res.data.decode()


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

    def test_manager_password_hashed_in_db(self, mock_gsheet):
        from gadash.models_db import Manager
        manager = Manager.query.filter_by(tenant_id=mock_gsheet.tenant_id).first()
        assert _is_hashed(manager.password_hash)

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
                                 REGISTER_TENANT, REGISTER_NAME, REGISTER_PASSWORD,
                                 CONFIRM_UNDO)
        states = [MENU, CLIENT, DATE, TASK, FIELD, CROP, AMOUNT, HOURS,
                  TOOL, OPERATOR, NOTE, CONFIRM, SEARCH, EDIT_SELECT,
                  REGISTER_TENANT, REGISTER_NAME, REGISTER_PASSWORD, CONFIRM_UNDO]
        assert len(states) == 18
        assert len(set(states)) == 18  # all unique

    def test_task_choices_cover_valid_tasks(self):
        from gadash.bot import TASK_CHOICES
        from gadash.models import VALID_TASKS
        flat = [t for row in TASK_CHOICES for t in row]
        assert set(flat) == VALID_TASKS

    def test_menu_keyboard_structure(self):
        from gadash.bot import MENU_KEYBOARD, CONFIRM_KEYBOARD
        assert isinstance(MENU_KEYBOARD, list)
        assert ["כן", "לא"] in CONFIRM_KEYBOARD

    def test_recent_clients_markup_returns_markup_or_remove(self, mock_gsheet):
        from gadash.bot import _recent_clients_markup
        from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
        result = _recent_clients_markup(mock_gsheet.tenant_id)
        assert isinstance(result, (ReplyKeyboardMarkup, ReplyKeyboardRemove))


class TestVoiceEntry:
    """🎤 Voice-note job reporting: Gemini transcribes + extracts fields (see
    tests/test_ai_extract.py for the extraction logic itself), this module
    turns that into the same shape confirm() already knows how to save."""

    def test_voice_entry_registered_in_conversation(self):
        # Guards against the handler existing but never being wired up.
        import gadash.bot as bot_module
        import inspect
        src = inspect.getsource(bot_module.start_telegram_bot)
        assert "voice_entry" in src

    def test_voice_entry_full_handler_with_mocked_telegram_and_gemini(self, monkeypatch, mock_gsheet):
        # Drives the actual async handler (not just the pure helpers) through a
        # fake Telegram Update and a fake Gemini response, since there's no real
        # BOT_TOKEN/GEMINI_API_KEY available to hit the live APIs from here.
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        import gadash.bot as bot_module
        from gadash import ai_extract
        from gadash.workers import _add_worker, _link_worker_telegram

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
        _add_worker(mock_gsheet.tenant_id, "בודק קולי", "pw12345")
        _link_worker_telegram(mock_gsheet.tenant_id, "בודק קולי", 999999)

        fake_response = MagicMock()
        fake_response.text = json.dumps({
            "שם לקוח": "איתמר", "תאריך": "", "עבודה": "עשינו ריסוס בבוקר",
            "שם חלקה": "חלקה ב", "גידול": "חיטה", "כמות": "30 דונם",
            "שעות": "3.5", "כלי": "מרסס", "מפעיל": "דני", "הערות": "",
        })
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        monkeypatch.setattr(ai_extract, "_genai", fake_genai)

        voice_file = MagicMock()
        voice_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"fake-ogg-audio"))
        voice = MagicMock()
        voice.get_file = AsyncMock(return_value=voice_file)

        processing_msg = MagicMock()
        processing_msg.edit_text = AsyncMock()

        message = MagicMock()
        message.voice = voice
        message.from_user.id = 999999
        message.reply_text = AsyncMock(return_value=processing_msg)

        update = MagicMock()
        update.message = message
        context = MagicMock()
        context.user_data = {}

        result_state = asyncio.run(bot_module.voice_entry(update, context))

        assert result_state == bot_module.CONFIRM
        assert context.user_data["שם לקוח"] == "איתמר"
        assert context.user_data["עבודה"] == "ריסוס"       # normalized from free text
        assert context.user_data["תאריך"]                   # defaulted to today
        assert context.user_data["שעות"] == "3.5"

        fake_genai.configure.assert_called_once_with(api_key="fake-key-for-test")
        blob = fake_model.generate_content.call_args[0][0][0]
        assert blob == {"mime_type": "audio/ogg", "data": b"fake-ogg-audio"}

        processing_msg.edit_text.assert_awaited_once()
        assert "איתמר" in processing_msg.edit_text.call_args[0][0]
        assert message.reply_text.await_count == 2  # "מקשיב..." + confirm-keyboard prompt

    def test_voice_entry_no_client_name_stays_on_menu(self, monkeypatch, mock_gsheet):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        import gadash.bot as bot_module
        from gadash import ai_extract
        from gadash.workers import _add_worker, _link_worker_telegram

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
        _add_worker(mock_gsheet.tenant_id, "בודק קולי", "pw12345")
        _link_worker_telegram(mock_gsheet.tenant_id, "בודק קולי", 999999)

        fake_response = MagicMock()
        fake_response.text = json.dumps({"שם לקוח": "", "עבודה": "חריש"})
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        monkeypatch.setattr(ai_extract, "_genai", fake_genai)

        voice_file = MagicMock()
        voice_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"audio"))
        voice = MagicMock()
        voice.get_file = AsyncMock(return_value=voice_file)

        processing_msg = MagicMock()
        processing_msg.edit_text = AsyncMock()
        message = MagicMock()
        message.voice = voice
        message.from_user.id = 999999
        message.reply_text = AsyncMock(return_value=processing_msg)
        update = MagicMock()
        update.message = message
        context = MagicMock()
        context.user_data = {}

        result_state = asyncio.run(bot_module.voice_entry(update, context))

        assert result_state == bot_module.MENU
        # Only the tenant/worker bookkeeping is set — no WorkEntry field made
        # it in, since there was nothing to confirm/save.
        assert set(context.user_data.keys()) == {"_tenant_id", "_worker_name"}
        assert "לקוח" in processing_msg.edit_text.call_args[0][0]

    def test_voice_entry_gemini_error_falls_back_gracefully(self, monkeypatch, mock_gsheet):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        import gadash.bot as bot_module
        from gadash import ai_extract
        from gadash.workers import _add_worker, _link_worker_telegram

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
        _add_worker(mock_gsheet.tenant_id, "בודק קולי", "pw12345")
        _link_worker_telegram(mock_gsheet.tenant_id, "בודק קולי", 999999)

        fake_model = MagicMock()
        fake_model.generate_content.side_effect = RuntimeError("network blip")
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model
        monkeypatch.setattr(ai_extract, "_genai", fake_genai)

        voice_file = MagicMock()
        voice_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"audio"))
        voice = MagicMock()
        voice.get_file = AsyncMock(return_value=voice_file)

        processing_msg = MagicMock()
        processing_msg.edit_text = AsyncMock()
        message = MagicMock()
        message.voice = voice
        message.from_user.id = 999999
        message.reply_text = AsyncMock(return_value=processing_msg)
        update = MagicMock()
        update.message = message
        context = MagicMock()
        context.user_data = {}

        result_state = asyncio.run(bot_module.voice_entry(update, context))

        assert result_state == bot_module.MENU
        processing_msg.edit_text.assert_awaited_once()
        assert "לא הצלחתי" in processing_msg.edit_text.call_args[0][0]



class _TelegramHelpers:
    """Fake Telegram Update/context plus tiny DB seeding helpers (not collected
    as tests — see the Test* classes below)."""

    def _update(self, text="", display_name="Telegram Display Name"):
        from unittest.mock import AsyncMock, MagicMock
        update = MagicMock()
        update.message.text = text
        update.message.from_user.full_name = display_name
        update.message.reply_text = AsyncMock()
        return update

    def _context(self, tenant_id, worker_name):
        from unittest.mock import MagicMock
        context = MagicMock()
        context.user_data = {"_tenant_id": tenant_id, "_worker_name": worker_name}
        return context

    def _replies(self, update):
        return [c.args[0] for c in update.message.reply_text.call_args_list]

    def _entry(self, tenant_id, client, by):
        from gadash.db import append_work_entry
        append_work_entry(tenant_id, WorkEntry(client=client, date="2026-10-20", task="חריש", entered_by=by))

    def _clients(self, tenant_id):
        from gadash.db import load_work_entries
        return list(load_work_entries(tenant_id)["שם לקוח"])


class TestTelegramUndo(_TelegramHelpers):
    """/undo used to delete the tenant's last row — whoever entered it — with
    no confirmation. It now offers only the caller's own last entry (matched
    on the verified worker name) and asks first."""

    def test_offers_only_the_callers_own_last_entry_then_deletes_it_on_yes(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        from gadash.db import read_audit_log
        t = mock_gsheet.tenant_id
        self._entry(t, "ראשון", "דני")
        self._entry(t, "אחרון-של-דני", "דני")
        self._entry(t, "של-אורי", "אורי")  # newest in the tenant, not דני's
        context = self._context(t, "דני")

        ask = self._update()
        assert asyncio.run(bot.bot_undo(ask, context)) == bot.CONFIRM_UNDO
        assert "אחרון-של-דני" in self._replies(ask)[0] and "של-אורי" not in self._replies(ask)[0]
        assert len(self._clients(t)) == 3  # asked, not deleted

        yes = self._update("כן")
        assert asyncio.run(bot.confirm_undo(yes, context)) == bot.MENU
        assert self._clients(t) == ["ראשון", "של-אורי"]
        assert any(e["action"] == "undo" and e["user"] == "דני" for e in read_audit_log(t))
        assert "_undo_row_id" not in context.user_data

    def test_declining_keeps_the_entry(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        t = mock_gsheet.tenant_id
        self._entry(t, "x", "דני")
        context = self._context(t, "דני")
        asyncio.run(bot.bot_undo(self._update(), context))
        assert asyncio.run(bot.confirm_undo(self._update("לא"), context)) == bot.MENU
        assert self._clients(t) == ["x"]

    def test_a_worker_with_no_entries_of_their_own_is_told_so(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        t = mock_gsheet.tenant_id
        self._entry(t, "של-אורי", "אורי")
        update = self._update()
        assert asyncio.run(bot.bot_undo(update, self._context(t, "דני"))) == bot.MENU
        assert "אין לך רשומות" in self._replies(update)[0]
        assert self._clients(t) == ["של-אורי"]

    def test_telegram_display_name_cannot_be_used_to_reach_another_workers_entry(self, mock_gsheet):
        """A worker who sets their Telegram name to "דני" gains nothing: the
        entry is matched on the registered worker name, not the display name."""
        import asyncio
        import gadash.bot as bot
        t = mock_gsheet.tenant_id
        self._entry(t, "של-דני", "דני")
        update = self._update(display_name="דני")
        assert asyncio.run(bot.bot_undo(update, self._context(t, "אורי"))) == bot.MENU
        assert "אין לך רשומות" in self._replies(update)[0]
        assert self._clients(t) == ["של-דני"]

    def test_entry_already_gone_by_confirmation_time(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        from gadash.db import delete_work_entry
        t = mock_gsheet.tenant_id
        self._entry(t, "x", "דני")
        context = self._context(t, "דני")
        asyncio.run(bot.bot_undo(self._update(), context))
        delete_work_entry(t, context.user_data["_undo_row_id"])  # e.g. removed on the website
        update = self._update("כן")
        asyncio.run(bot.confirm_undo(update, context))
        assert "כבר לא קיימת" in self._replies(update)[0]

    def test_unregistered_caller_is_sent_to_start_instead_of_crashing(self):
        import asyncio
        from unittest.mock import MagicMock
        import gadash.bot as bot
        from telegram.ext import ConversationHandler
        context = MagicMock()
        context.user_data = {}
        update = self._update()
        assert asyncio.run(bot.bot_undo(update, context)) == ConversationHandler.END
        assert "/start" in self._replies(update)[0]

    def test_new_entries_record_the_worker_name_not_the_telegram_display_name(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        from gadash.db import load_work_entries
        t = mock_gsheet.tenant_id
        context = self._context(t, "דני")
        context.user_data.update({"שם לקוח": "גל", "תאריך": "2026-10-20", "עבודה": "חריש"})
        asyncio.run(bot.confirm(self._update("כן", display_name="Some Display Name"), context))
        assert load_work_entries(t).iloc[0]["מזין"] == "דני"

    def test_confirm_undo_is_wired_into_the_conversation(self):
        import inspect
        import gadash.bot as bot
        src = inspect.getsource(bot.start_telegram_bot)
        assert "CONFIRM_UNDO" in src and "confirm_undo" in src



class TestTelegramShowsOnlyOwnEntries:
    """The recent list, the search, and the edit picker used to read the whole
    tenant. They now read only the signed-in worker's entries, like the
    website's worker portal."""

    helpers = _TelegramHelpers()

    def _seed(self, t):
        for client, by in [("של-דני-1", "דני"), ("של-אורי-1", "אורי"), ("של-דני-2", "דני"), ("עוד-של-אורי", "אורי")]:
            self.helpers._entry(t, client, by)

    def test_recent_list_shows_only_own_entries(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        self._seed(mock_gsheet.tenant_id)
        update = self.helpers._update()
        asyncio.run(bot.bot_recent(update, self.helpers._context(mock_gsheet.tenant_id, "דני")))
        text = self.helpers._replies(update)[0]
        assert text.count("דני") == 2 and "אורי" not in text

    def test_recent_list_when_the_worker_has_none(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        self.helpers._entry(mock_gsheet.tenant_id, "של-אורי", "אורי")
        update = self.helpers._update()
        asyncio.run(bot.bot_recent(update, self.helpers._context(mock_gsheet.tenant_id, "דני")))
        assert "אין לך עבודות" in self.helpers._replies(update)[0]

    def test_search_finds_only_own_entries(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        self._seed(mock_gsheet.tenant_id)
        context = self.helpers._context(mock_gsheet.tenant_id, "דני")

        both = self.helpers._update("של")  # matches all four clients in the tenant
        asyncio.run(bot.bot_search_results(both, context))
        assert "נמצאו 2" in self.helpers._replies(both)[0]

        theirs = self.helpers._update("עוד-של-אורי")  # exists, but isn't דני's
        asyncio.run(bot.bot_search_results(theirs, context))
        assert "לא נמצאו תוצאות" in self.helpers._replies(theirs)[0]

    def test_edit_picker_offers_only_own_entries(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        from gadash.db import load_work_entries
        t = mock_gsheet.tenant_id
        self._seed(t)
        context = self.helpers._context(t, "דני")
        update = self.helpers._update()
        assert asyncio.run(bot.bot_edit_last(update, context)) == bot.EDIT_SELECT

        own_ids = list(load_work_entries(t, entered_by="דני")["_row_id"])
        assert context.user_data["_edit_row_ids"] == own_ids and len(own_ids) == 2
        text = self.helpers._replies(update)[0]
        assert "2." in text and "3." not in text

    def test_edit_picker_when_the_worker_has_none(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        self.helpers._entry(mock_gsheet.tenant_id, "של-אורי", "אורי")
        update = self.helpers._update()
        assert asyncio.run(bot.bot_edit_last(update, self.helpers._context(mock_gsheet.tenant_id, "דני"))) == bot.MENU
        assert "אין לך עבודות" in self.helpers._replies(update)[0]

    def test_choosing_an_entry_shows_its_details(self, mock_gsheet):
        import asyncio
        import gadash.bot as bot
        t = mock_gsheet.tenant_id
        self._seed(t)
        context = self.helpers._context(t, "דני")
        asyncio.run(bot.bot_edit_last(self.helpers._update(), context))
        pick = self.helpers._update("1")
        asyncio.run(bot.bot_edit_select(pick, context))
        reply = self.helpers._replies(pick)[0]
        assert "של-דני-1" in reply and "/edit/" in reply

    def test_a_stale_or_foreign_row_id_cannot_reveal_someone_elses_entry(self, mock_gsheet):
        """The picked id comes from per-conversation state; even if it pointed
        at another worker's row, the details are read through the own-entries
        view, so nothing leaks."""
        import asyncio
        import gadash.bot as bot
        from gadash.db import load_work_entries
        t = mock_gsheet.tenant_id
        self._seed(t)
        foreign_id = int(load_work_entries(t, entered_by="אורי").iloc[-1]["_row_id"])
        context = self.helpers._context(t, "דני")
        context.user_data["_edit_row_ids"] = [foreign_id]
        pick = self.helpers._update("1")
        asyncio.run(bot.bot_edit_select(pick, context))
        reply = self.helpers._replies(pick)[0]
        assert "כבר לא קיימת" in reply and "עוד-של-אורי" not in reply
