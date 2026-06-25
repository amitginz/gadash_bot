from gadash.sheets import append_row_to_gsheet
from gadash.audit import _log_audit
from gadash.models import WorkEntry


def create_entry(entry: WorkEntry, actor: str):
    append_row_to_gsheet(entry)
    _log_audit("add", actor, f"{entry.client} | {entry.date} | {entry.task}")
