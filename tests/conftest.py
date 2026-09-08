"""
Shared pytest fixtures for gadash_bot.

Runs against a real local Postgres (DATABASE_URL, defaulting to the
gadash_test database created for this purpose — see the migration plan)
rather than mocking gadash/sheets.py, since that module is no longer the
live data path. Each test gets a fresh tenant and an empty schema.

`mock_gsheet["df"] = pd.DataFrame(rows, columns=COLUMNS)` and
`mock_gsheet["rates"]["חריש"] = {...}` — the seeding patterns most of
tests/test_app.py already used — still work unchanged: the returned state
object writes straight through to the tenant's rows in Postgres instead of
an in-memory frame, so the many existing `_seed(mock_gsheet, [...])`
helpers didn't need touching.
"""
import os
import secrets

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql:///gadash_test")

import app as app_module
from gadash import auth
from gadash.db import (
    append_work_entry, load_rates, load_work_entries, save_rates,
)
from gadash.models import WorkEntry
from gadash.models_db import (
    AuditLogEntry, Field, Manager, Rate, Subscriber, Tenant, Worker, WorkEntryRow, db,
)

_SCHEMA_READY = False


def _ensure_schema():
    global _SCHEMA_READY
    if not _SCHEMA_READY:
        db.create_all()
        _SCHEMA_READY = True


class _RatesProxy(dict):
    """`mock_gsheet["rates"][task] = {...}` writes straight through to the DB."""

    def __init__(self, tenant_id):
        super().__init__(load_rates(tenant_id))
        self._tenant_id = tenant_id

    def __setitem__(self, task, value):
        save_rates(self._tenant_id, {task: value})
        super().__setitem__(task, value)


class _TenantState(dict):
    """`mock_gsheet["df"] = <DataFrame>` replaces the tenant's work entries in
    Postgres; `mock_gsheet["df"]` reads them back the same shape tests
    already expect (a plain frame over COLUMNS)."""

    def __init__(self, tenant_id):
        super().__init__()
        self.tenant_id = tenant_id

    def __setitem__(self, key, value):
        if key == "df":
            WorkEntryRow.query.filter_by(tenant_id=self.tenant_id).delete()
            db.session.commit()
            for _, row in value.iterrows():
                append_work_entry(self.tenant_id, WorkEntry.from_dict(row.to_dict()))
            return
        super().__setitem__(key, value)

    def __getitem__(self, key):
        if key == "df":
            return load_work_entries(self.tenant_id)
        if key == "rates":
            return _RatesProxy(self.tenant_id)
        return super().__getitem__(key)


@pytest.fixture(autouse=True)
def mock_gsheet():
    """Named to match the pre-Postgres fixture — see module docstring."""
    with app_module.app.app_context():
        _ensure_schema()
        for model in [WorkEntryRow, Field, Rate, AuditLogEntry, Subscriber, Worker, Manager, Tenant]:
            model.query.delete()
        db.session.commit()
        yield _TenantState(auth.create_tenant(
            "טננט בדיקה", f"testmgr-{secrets.token_hex(4)}", "testpass123",
            slug=f"test-{secrets.token_hex(4)}",
        ))


@pytest.fixture
def client(mock_gsheet):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["tenant_id"] = mock_gsheet.tenant_id
            sess["manager_id"] = Manager.query.filter_by(tenant_id=mock_gsheet.tenant_id).first().id
            sess["_csrf"]     = "test-csrf-token"
        yield c
