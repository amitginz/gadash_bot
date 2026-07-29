import os
import json
import pandas as pd

from gadash.models import COLUMNS, WorkEntry
from gadash.sheets import (
    _get_sheet,
    _load_offline_queue,
    _save_offline_queue,
    _invalidate_cache,
    load_data_from_gsheet,
    _offline_queue,
    _is_offline,
)
from gadash.audit import _log_audit


def sync_offline_buffer() -> dict:
    """Synchronize pending offline work entries to Google Sheets upon reconnection.

    Attempts to re-establish connection to Google Sheets, extracts existing UIDs
    to prevent duplicate additions, appends queued entries with an '[Offline Entry]' note
    tag, clears the local offline queue, and re-invalidates memory cache.

    Returns:
        dict: A dictionary containing:
            - 'synced_count' (int): Number of entries successfully pushed.
            - 'remaining_count' (int): Number of entries remaining in queue.
            - 'status' (str): 'success' if synced cleanly, or 'failed'.
            - 'error' (str, optional): Error message string if sync failed.
    """
    global _is_offline
    _load_offline_queue()
    import gadash.sheets as sheets_mod

    if not sheets_mod._offline_queue:
        return {"synced_count": 0, "remaining_count": 0, "status": "success"}

    try:
        sheet = _get_sheet()
        all_values = sheet.get_all_values()
        
        # Collect existing UIDs from column L (12th column, index 11)
        existing_uids = set()
        if all_values and len(all_values) >= 2:
            for row in all_values[1:]:
                if len(row) >= 12 and row[11]:
                    existing_uids.add(row[11].strip())

        synced_count = 0
        remaining = []

        for row_dict in list(sheets_mod._offline_queue):
            entry = WorkEntry.from_dict(row_dict)
            
            # Prevent duplicate insertion if UID already exists on Google Sheets
            if entry.uid and entry.uid in existing_uids:
                print(f"[Sync] Skipping already synced entry UID: {entry.uid}")
                continue

            # Tag notes with [Offline Entry] if not already present
            if "[Offline Entry]" not in entry.notes:
                entry.notes = f"[Offline Entry] {entry.notes}".strip()

            sheet.append_row(entry.to_sheet_row(), value_input_option="USER_ENTERED")
            existing_uids.add(entry.uid)
            synced_count += 1
            _log_audit("sync-offline", entry.entered_by or "System", f"UID: {entry.uid} | {entry.client}")

        # Clear offline queue upon successful sync
        sheets_mod._offline_queue = []
        sheets_mod._save_offline_queue()
        sheets_mod._is_offline = False

        # Invalidate and refresh cache
        _invalidate_cache()
        load_data_from_gsheet()

        return {
            "synced_count": synced_count,
            "remaining_count": 0,
            "status": "success",
        }
    except Exception as e:
        sheets_mod._is_offline = True
        print(f"[Sync] Error pushing offline buffer to GSheet: {e}")
        return {
            "synced_count": 0,
            "remaining_count": len(sheets_mod._offline_queue),
            "status": "failed",
            "error": str(e),
        }
