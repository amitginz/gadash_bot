"""Integration tests for gadash/db.py — run against a real local Postgres
(not mocks). This is the tenant-isolation boundary for the whole multi-
tenant migration, so it's worth verifying against the real thing rather
than a fake.

Needs DATABASE_URL pointing at a throwaway Postgres (defaults to the local
`gadash_test` database created for this purpose; CI provisions its own via
the postgres service in .github/workflows/tests.yml).
"""
import os

import pytest
from flask import Flask

os.environ.setdefault("DATABASE_URL", "postgresql:///gadash_test")

from gadash import db as gdb
from gadash.models import WorkEntry
from gadash.models_db import Tenant, db


@pytest.fixture(scope="module")
def app_ctx():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def tenants(app_ctx):
    """A clean pair of tenants for every test, so cross-tenant isolation is
    always exercised, not just assumed."""
    with app_ctx.app_context():
        for model in [gdb.WorkEntryRow, gdb.Field, gdb.Rate, gdb.AuditLogEntry, gdb.Subscriber, Tenant]:
            model.query.delete()
        db.session.commit()
        t1 = Tenant(name="קבלן א")
        t2 = Tenant(name="קבלן ב")
        db.session.add_all([t1, t2])
        db.session.commit()
        yield t1.id, t2.id


class TestWorkEntries:

    def test_append_and_load_roundtrip(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.append_work_entry(t1, WorkEntry(client="איתמר", date="2026-06-01", task="חריש", hours="4"))
            df = gdb.load_work_entries(t1)
            assert len(df) == 1
            assert df.iloc[0]["שם לקוח"] == "איתמר"
            assert df.iloc[0]["שעות"] == "4"
            assert "_row_id" in df.columns

    def test_tenant_isolation(self, app_ctx, tenants):
        t1, t2 = tenants
        with app_ctx.app_context():
            gdb.append_work_entry(t1, WorkEntry(client="שייך לקבלן א", date="2026-06-01", task="חריש"))
            gdb.append_work_entry(t2, WorkEntry(client="שייך לקבלן ב", date="2026-06-01", task="קציר"))
            df1 = gdb.load_work_entries(t1)
            df2 = gdb.load_work_entries(t2)
            assert list(df1["שם לקוח"]) == ["שייך לקבלן א"]
            assert list(df2["שם לקוח"]) == ["שייך לקבלן ב"]

    def test_edit_updates_only_targeted_row(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.append_work_entry(t1, WorkEntry(client="לפני", date="2026-06-01", task="חריש"))
            row_id = int(gdb.load_work_entries(t1).iloc[0]["_row_id"])
            gdb.edit_work_entry(t1, row_id, WorkEntry(client="אחרי", date="2026-06-02", task="קציר"))
            df = gdb.load_work_entries(t1)
            assert df.iloc[0]["שם לקוח"] == "אחרי"
            assert df.iloc[0]["תאריך"] == "2026-06-02"

    def test_edit_wrong_tenant_raises(self, app_ctx, tenants):
        t1, t2 = tenants
        with app_ctx.app_context():
            gdb.append_work_entry(t1, WorkEntry(client="x", date="2026-06-01", task="חריש"))
            row_id = int(gdb.load_work_entries(t1).iloc[0]["_row_id"])
            with pytest.raises(ValueError):
                gdb.edit_work_entry(t2, row_id, WorkEntry(client="גניבה", date="2026-06-01", task="חריש"))
            # Row must be untouched — a wrong-tenant edit id must not leak through.
            assert gdb.load_work_entries(t1).iloc[0]["שם לקוח"] == "x"

    def test_delete_removes_only_that_row(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.append_work_entry(t1, WorkEntry(client="נשאר", date="2026-06-01", task="חריש"))
            gdb.append_work_entry(t1, WorkEntry(client="נמחק", date="2026-06-01", task="קציר"))
            df = gdb.load_work_entries(t1)
            to_delete = int(df[df["שם לקוח"] == "נמחק"].iloc[0]["_row_id"])
            gdb.delete_work_entry(t1, to_delete)
            remaining = gdb.load_work_entries(t1)
            assert list(remaining["שם לקוח"]) == ["נשאר"]

    def test_bulk_delete(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            for i in range(3):
                gdb.append_work_entry(t1, WorkEntry(client=f"c{i}", date="2026-06-01", task="חריש"))
            ids = list(gdb.load_work_entries(t1)["_row_id"])
            gdb.bulk_delete_work_entries(t1, [int(ids[0]), int(ids[1])])
            assert len(gdb.load_work_entries(t1)) == 1

    def test_bulk_insert_from_import(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            entries = [WorkEntry(client=f"יובא{i}", date="2026-06-01", task="דיסוק") for i in range(5)]
            gdb.bulk_insert_work_entries(t1, entries)
            assert len(gdb.load_work_entries(t1)) == 5

    def test_patch_cell_updates_correct_field(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.append_work_entry(t1, WorkEntry(client="x", date="2026-06-01", task="חריש", hours="1"))
            row_id = int(gdb.load_work_entries(t1).iloc[0]["_row_id"])
            gdb.patch_work_entry_cell(t1, row_id, "שעות", "9.5")
            assert gdb.load_work_entries(t1).iloc[0]["שעות"] == "9.5"

    def test_patch_invalid_field_raises(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.append_work_entry(t1, WorkEntry(client="x", date="2026-06-01", task="חריש"))
            row_id = int(gdb.load_work_entries(t1).iloc[0]["_row_id"])
            with pytest.raises(ValueError):
                gdb.patch_work_entry_cell(t1, row_id, "לא_שדה_אמיתי", "x")

    def test_load_empty_tenant_has_expected_columns(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            df = gdb.load_work_entries(t1)
            assert df.empty
            assert "שם לקוח" in df.columns and "_row_id" in df.columns


class TestFields:

    def test_polygon_and_pin_are_isolated_by_tenant(self, app_ctx, tenants):
        t1, t2 = tenants
        with app_ctx.app_context():
            gdb.save_pin(t1, "u1", "חלקה א", "", 32.1, 35.1)
            gdb.save_pin(t2, "u1", "חלקה של קבלן אחר", "", 32.2, 35.2)  # same uid, different tenant
            assert gdb.load_pins(t1)[0]["name"] == "חלקה א"
            assert gdb.load_pins(t2)[0]["name"] == "חלקה של קבלן אחר"

    def test_saving_polygon_over_existing_pin_replaces_it_in_place(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.save_pin(t1, "u2", "שדה", "", 32.0, 35.0)
            gdb.save_polygon(t1, "u2", "שדה", "#fff", [[32.0, 35.0], [32.1, 35.0], [32.1, 35.1]])
            assert gdb.load_pins(t1) == []
            assert len(gdb.load_polygons(t1)) == 1

    def test_delete_field_removes_it(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.save_pin(t1, "u3", "למחיקה", "", 1.0, 1.0)
            gdb.delete_field(t1, "u3")
            assert gdb.load_pins(t1) == []

    def test_calculate_polygon_dunam_area_matches_known_value(self):
        # ~1km x 1km square near the equator's degree-to-meter scale.
        coords = [[0.0, 0.0], [0.009, 0.0], [0.009, 0.009], [0.0, 0.009]]
        area = gdb.calculate_polygon_dunam_area(coords)
        assert 900 < area < 1100


class TestRates:

    def test_unset_rates_default_to_zero_for_every_task(self, app_ctx, tenants):
        from gadash.models import VALID_TASKS
        t1, _ = tenants
        with app_ctx.app_context():
            rates = gdb.load_rates(t1)
            assert set(rates.keys()) == VALID_TASKS
            assert all(r == {"revenue": 0.0, "cost": 0.0} for r in rates.values())

    def test_save_and_reload_rates(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.save_rates(t1, {"חריש": {"revenue": 100.0, "cost": 30.0}})
            rates = gdb.load_rates(t1)
            assert rates["חריש"] == {"revenue": 100.0, "cost": 30.0}
            assert rates["קציר"] == {"revenue": 0.0, "cost": 0.0}  # untouched tasks stay default

    def test_rates_isolated_by_tenant(self, app_ctx, tenants):
        t1, t2 = tenants
        with app_ctx.app_context():
            gdb.save_rates(t1, {"חריש": {"revenue": 100.0, "cost": 0.0}})
            assert gdb.load_rates(t2)["חריש"] == {"revenue": 0.0, "cost": 0.0}


class TestAuditLog:

    def test_log_and_read_newest_first(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.log_audit(t1, "add", "Web", "first")
            gdb.log_audit(t1, "edit", "Web", "second")
            entries = gdb.read_audit_log(t1)
            assert [e["detail"] for e in entries] == ["second", "first"]

    def test_audit_isolated_by_tenant(self, app_ctx, tenants):
        t1, t2 = tenants
        with app_ctx.app_context():
            gdb.log_audit(t1, "add", "Web", "tenant1 event")
            assert gdb.read_audit_log(t2) == []


class TestSubscribers:

    def test_add_and_get(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.add_subscriber(t1, 12345)
            assert gdb.get_subscribers(t1) == {12345}

    def test_add_is_idempotent(self, app_ctx, tenants):
        t1, _ = tenants
        with app_ctx.app_context():
            gdb.add_subscriber(t1, 999)
            gdb.add_subscriber(t1, 999)
            assert gdb.get_subscribers(t1) == {999}

    def test_subscribers_isolated_by_tenant(self, app_ctx, tenants):
        t1, t2 = tenants
        with app_ctx.app_context():
            gdb.add_subscriber(t1, 111)
            gdb.add_subscriber(t2, 222)
            assert gdb.get_subscribers(t1) == {111}
            assert gdb.get_subscribers(t2) == {222}
