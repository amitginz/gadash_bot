import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from gadash.models import WorkEntry, COLUMNS
import gadash.sheets as sheets_mod
from gadash.sync import sync_offline_buffer


class TestSyncOfflineBuffer:

    def setup_method(self):
        import os
        sheets_mod._offline_queue = []
        if os.path.exists(sheets_mod._OFFLINE_QUEUE_PATH):
            try:
                os.remove(sheets_mod._OFFLINE_QUEUE_PATH)
            except Exception:
                pass

    def test_sync_empty_queue_returns_success(self):
        sheets_mod._offline_queue = []
        result = sync_offline_buffer()
        assert result["status"] == "success"
        assert result["synced_count"] == 0
        assert result["remaining_count"] == 0

    @patch("gadash.sync._get_sheet")
    def test_sync_pushes_queued_entry_and_tags_notes(self, mock_get_sheet):
        # Mock worksheet
        mock_ws = MagicMock()
        mock_ws.get_all_values.return_value = [COLUMNS]  # Headers only
        mock_get_sheet.return_value = mock_ws

        # Seed offline queue
        entry = WorkEntry(client="לקוח אופליין 1", date="2025-07-01", task="חריש", notes="עבודה רגילה")
        sheets_mod._offline_queue = [entry.to_dict()]

        result = sync_offline_buffer()

        assert result["status"] == "success"
        assert result["synced_count"] == 1
        assert len(sheets_mod._offline_queue) == 0

        # Verify append_row was called with [Offline Entry] in notes
        mock_ws.append_row.assert_called_once()
        appended_row = mock_ws.append_row.call_args[0][0]
        assert "[Offline Entry] עבודה רגילה" in appended_row[9]  # 10th column (index 9) is notes

    @patch("gadash.sync._get_sheet")
    def test_sync_skips_existing_uid(self, mock_get_sheet):
        mock_ws = MagicMock()
        entry = WorkEntry(client="לקוח קיים", date="2025-07-01", task="ריסוס")
        existing_row = entry.to_sheet_row()

        mock_ws.get_all_values.return_value = [COLUMNS, existing_row]
        mock_get_sheet.return_value = mock_ws

        # Seed offline queue with same entry UID
        sheets_mod._offline_queue = [entry.to_dict()]

        result = sync_offline_buffer()
        assert result["status"] == "success"
        assert result["synced_count"] == 0
        assert not mock_ws.append_row.called
