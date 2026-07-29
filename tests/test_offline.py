import pytest
import os
import time
import pandas as pd
from gadash.models import WorkEntry, COLUMNS
from gadash.sheets import (
    load_data_from_gsheet,
    get_offline_status,
    add_offline_entry,
    _save_offline_queue,
    _load_offline_queue,
    _OFFLINE_QUEUE_PATH,
)

class TestOfflineQueueAndStaleCache:

    def test_stale_cache_preserved_when_offline(self):
        # Seed initial cache
        import gadash.sheets as sheets_mod
        test_entry = WorkEntry(client="לקוח בדיקה", date="2025-05-05", task="חריש")
        df_seed = pd.DataFrame([test_entry.to_dict()])[COLUMNS]
        sheets_mod._cache_data = df_seed
        sheets_mod._cache_time = 0.0  # Force stale (older than 300s)

        # Call load_data_from_gsheet without credentials -> should return cached df_seed instead of empty DataFrame
        df_result = load_data_from_gsheet()
        assert not df_result.empty
        assert df_result.iloc[0]["שם לקוח"] == "לקוח בדיקה"
        assert sheets_mod._is_offline is True

    def test_add_offline_entry_updates_queue_and_cache(self):
        import gadash.sheets as sheets_mod
        sheets_mod._cache_data = None
        sheets_mod._offline_queue = []

        entry = WorkEntry(client="לקוח אופליין", date="2025-06-01", task="ריסוס")
        add_offline_entry(entry.to_dict())

        status = get_offline_status()
        assert status["online"] is False
        assert status["pending_count"] >= 1

        df = load_data_from_gsheet()
        assert not df.empty
        assert "לקוח אופליין" in df["שם לקוח"].values

    def test_save_data_to_gsheet_offline_fallback(self):
        import gadash.sheets as sheets_mod
        from gadash.sheets import save_data_to_gsheet
        sheets_mod._cache_data = None
        sheets_mod._offline_queue = []

        entry = WorkEntry(client="לקוח ייבוא", date="2025-06-02", task="קציר")
        df_import = pd.DataFrame([entry.to_dict()])

        # Call save_data_to_gsheet without credentials -> should save to local cache/queue without throwing
        save_data_to_gsheet(df_import)

        status = get_offline_status()
        assert status["online"] is False
        df_cache = load_data_from_gsheet()
        assert not df_cache.empty
        assert "לקוח ייבוא" in df_cache["שם לקוח"].values

    def test_nan_sanitized_in_loaded_data(self):
        import gadash.sheets as sheets_mod
        entry_dict = WorkEntry(client="לקוח בדיקה", date="2025-06-03", task="חריש").to_dict()
        entry_dict["הערות"] = "nan"
        entry_dict["כלי"] = "None"
        df_nan = pd.DataFrame([entry_dict])[COLUMNS]
        sheets_mod._cache_data = df_nan
        sheets_mod._cache_time = time.time()

        df_loaded = load_data_from_gsheet()
        assert df_loaded.iloc[0]["הערות"] == ""
        assert df_loaded.iloc[0]["כלי"] == ""


