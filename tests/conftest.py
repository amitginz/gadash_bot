"""
Shared pytest fixtures for gadash_bot.

Replaces all Google Sheets I/O with in-memory fakes so the suite is
deterministic, fast, and never depends on live GOOGLE_CREDS/credentials.json.
Without this, calls that fail to authenticate retry 3x with backoff
(gadash/sheets.py:_get_sheet) before falling back to an empty result —
several seconds per call, and different behavior depending on whether the
machine running the tests happens to have real credentials configured.
"""
import pandas as pd
import pytest

import app as app_module
import gadash.service as service_module
import gadash.sheets as sheets_module
from gadash.models import COLUMNS


@pytest.fixture(autouse=True)
def mock_gsheet(request, monkeypatch):
    if "test_offline" in request.node.nodeid or "test_sync" in request.node.nodeid:
        return None
    state = {"df": pd.DataFrame(columns=COLUMNS)}

    def fake_load(force_refresh: bool = False):
        return state["df"].copy()

    def fake_append(entry):
        row = pd.DataFrame([entry.to_dict()], columns=COLUMNS)
        state["df"] = pd.concat([state["df"], row], ignore_index=True)

    def fake_edit(row_id, entry):
        state["df"].loc[row_id] = entry.to_dict()

    def fake_delete(row_id):
        state["df"] = state["df"].drop(index=row_id).reset_index(drop=True)

    def fake_patch(row_id, field, value):
        state["df"].at[row_id, field] = value

    def fake_bulk_delete(row_ids):
        valid = [i for i in row_ids if i < len(state["df"])]
        state["df"] = state["df"].drop(index=valid).reset_index(drop=True)

    def fake_save(df):
        state["df"] = df.reset_index(drop=True)

    fakes = {
        "load_data_from_gsheet":    fake_load,
        "append_row_to_gsheet":     fake_append,
        "edit_row_in_gsheet":       fake_edit,
        "delete_row_in_gsheet":     fake_delete,
        "patch_cell_in_gsheet":     fake_patch,
        "bulk_delete_rows_in_gsheet": fake_bulk_delete,
        "save_data_to_gsheet":      fake_save,
        "_load_field_coords":       lambda: {},
        "_save_field_coord":        lambda name, lat, lng: None,
        "load_passwords_from_sheet": lambda: {},
        "save_passwords_to_sheet":  lambda web, worker: None,
        "_invalidate_cache":        lambda: None,
    }
    for name, fake in fakes.items():
        if hasattr(sheets_module, name):
            monkeypatch.setattr(sheets_module, name, fake, raising=True)
        if hasattr(app_module, name):
            monkeypatch.setattr(app_module, name, fake, raising=True)

    # gadash/service.py did `from gadash.sheets import append_row_to_gsheet`,
    # which binds its own name in that module's namespace — patch it too.
    monkeypatch.setattr(service_module, "append_row_to_gsheet", fake_append, raising=True)

    # app.py's /api/dashboard cache is module-level global state that would
    # otherwise leak between tests (e.g. an empty-data response cached by one
    # test being served to a later test that seeded real rows).
    monkeypatch.setattr(app_module, "_dashboard_cache", {}, raising=True)
    monkeypatch.setattr(app_module, "_dashboard_cache_time", 0.0, raising=True)

    return state
