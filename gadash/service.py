from gadash.db import append_work_entry, log_audit
from gadash.models import WorkEntry


def create_entry(tenant_id: int, entry: WorkEntry, actor: str):
    append_work_entry(tenant_id, entry)
    log_audit(tenant_id, "add", actor, f"{entry.client} | {entry.date} | {entry.task}")
