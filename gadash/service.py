from gadash.sheets import append_row_to_gsheet, add_offline_entry
from gadash.audit import _log_audit
from gadash.models import WorkEntry


def create_entry(entry: WorkEntry, actor: str):
    """Create a new work entry, appending to Google Sheets or saving to offline queue.

    Args:
        entry (WorkEntry): The validated WorkEntry instance to persist.
        actor (str): Username or role of the person creating the record.
    """
    try:
        append_row_to_gsheet(entry)
        _log_audit("add", actor, f"{entry.client} | {entry.date} | {entry.task} | UID: {entry.uid}")
    except Exception as e:
        print(f"[Service] Offline fallback for entry {entry.uid}: {e}")
        add_offline_entry(entry.to_dict())
        _log_audit("add-offline", actor, f"{entry.client} | {entry.date} | {entry.task} | UID: {entry.uid}")

