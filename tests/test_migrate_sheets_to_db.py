"""Tests the one-time Sheets → Postgres migration script's logic against a
real local Postgres, with gadash/sheets.py's reads mocked (there's no live
Google Sheet to hit from here) — same approach as tests/test_db.py.

This can't verify the script against the *actual* production spreadsheet
(only running it for real can), but it does verify: every category of data
lands in the right shape, a field name covered by a new-style pin doesn't
also get a duplicate from legacy FieldCoords, and re-running without
--force refuses instead of duplicating rows.
"""
import pytest

from gadash.models import WorkEntry
from scripts import migrate_sheets_to_db as migrate_script


@pytest.fixture
def fake_sheets(monkeypatch, mock_gsheet):
    """Points the migration script's Sheets reads at fixed fake data instead
    of a real spreadsheet. Returns the fake data dict so tests can vary it."""
    data = {
        "work_entries": [
            WorkEntry(client="איתמר", date="2026-06-01", task="חריש",
                      field_name="חלקה א", hours="4", entered_by="Web").to_dict(),
            WorkEntry(client="מאי", date="2026-06-02", task="ריסוס",
                      field_name="חלקה ב", hours="2.5", entered_by="דני").to_dict(),
        ],
        "workers": [["דני", "pbkdf2:sha256:fake-hash", "555"]],
        "polygons": [{"uid": "p1", "name": "חלקה א", "color": "#fff",
                      "coordinates": [[32.0, 35.0], [32.1, 35.0], [32.1, 35.1]]}],
        "pins": [{"uid": "n1", "name": "חלקה ג", "color": "", "lat": 32.2, "lng": 35.2}],
        "legacy_coords": {
            "חלקה ג": {"lat": 99.0, "lng": 99.0},   # already covered by a new-style pin — must be skipped
            "חלקה ישנה": {"lat": 31.9, "lng": 34.9},  # not covered — must be migrated
        },
        "rates": {"חריש": {"revenue": 100.0, "cost": 30.0}},
        "audit_rows": [["2026-06-01 08:00:00", "add", "Web", "איתמר | 2026-06-01 | חריש"]],
        "subscriber_rows": [["12345"], ["-6789"]],
    }
    import pandas as pd
    from gadash.models import COLUMNS

    monkeypatch.setattr(migrate_script, "load_data_from_gsheet",
                         lambda: pd.DataFrame(data["work_entries"], columns=COLUMNS))
    monkeypatch.setattr(migrate_script, "load_polygons_from_sheet", lambda: data["polygons"])
    monkeypatch.setattr(migrate_script, "load_pins_from_sheet", lambda: data["pins"])
    monkeypatch.setattr(migrate_script, "_load_field_coords", lambda: data["legacy_coords"])
    monkeypatch.setattr(migrate_script, "load_rates_from_sheet", lambda: data["rates"])

    class _FakeSheet:
        def __init__(self, rows):
            self._rows = rows
        def get_all_values(self):
            return [["header"]] + self._rows

    monkeypatch.setattr(migrate_script, "_get_workers_sheet", lambda: _FakeSheet(data["workers"]))
    monkeypatch.setattr(migrate_script, "_get_audit_sheet", lambda: _FakeSheet(data["audit_rows"]))
    monkeypatch.setattr(migrate_script, "_get_subscribers_sheet", lambda: _FakeSheet(data["subscriber_rows"]))
    return data


def test_migrate_populates_every_category(fake_sheets, mock_gsheet):
    from gadash import auth
    from gadash.db import (
        get_subscribers, load_pins, load_polygons, load_rates,
        load_work_entries, read_audit_log,
    )

    migrate_script.migrate("חוות בדיקה", "mgr-import", "pw123456", "import-test", force=False)

    tenant_id = auth.get_tenant_id_by_slug("import-test")
    assert tenant_id is not None
    assert auth.verify_manager("mgr-import", "pw123456")[0] == tenant_id

    df = load_work_entries(tenant_id)
    assert len(df) == 2
    assert set(df["שם לקוח"]) == {"איתמר", "מאי"}

    polys = load_polygons(tenant_id)
    assert len(polys) == 1 and polys[0]["name"] == "חלקה א"

    pins = load_pins(tenant_id)
    pin_names = {p["name"] for p in pins}
    # New-style pin "חלקה ג" kept once (not duplicated by the overlapping
    # legacy FieldCoords entry of the same name); "חלקה ישנה" migrated fresh.
    assert pin_names == {"חלקה ג", "חלקה ישנה"}
    assert len(pins) == 2
    helka_g = next(p for p in pins if p["name"] == "חלקה ג")
    assert helka_g["lat"] == 32.2  # from the new-style pin, not the stale legacy coord (99.0)

    rates = load_rates(tenant_id)
    assert rates["חריש"] == {"revenue": 100.0, "cost": 30.0}

    audit = read_audit_log(tenant_id)
    assert len(audit) == 1 and audit[0]["action"] == "add"

    assert get_subscribers(tenant_id) == {12345, -6789}


def test_migrate_refuses_duplicate_slug_without_force(fake_sheets, mock_gsheet):
    migrate_script.migrate("חוות בדיקה", "mgr1", "pw123456", "dup-test", force=False)
    with pytest.raises(SystemExit):
        migrate_script.migrate("חוות בדיקה שוב", "mgr2", "pw123456", "dup-test", force=False)


def test_migrate_workers_copies_hash_and_telegram_id(fake_sheets, mock_gsheet):
    from gadash import auth
    from gadash.workers import _get_worker_by_telegram_id, _load_workers

    migrate_script.migrate("חוות בדיקה", "mgr-import2", "pw123456", "import-workers", force=False)
    tenant_id = auth.get_tenant_id_by_slug("import-workers")

    workers = _load_workers(tenant_id)
    assert len(workers) == 1
    assert workers[0]["שם"] == "דני"
    assert workers[0]["password_hash"] == "pbkdf2:sha256:fake-hash"

    found = _get_worker_by_telegram_id(555)
    assert found == {"שם": "דני", "tenant_id": tenant_id}
