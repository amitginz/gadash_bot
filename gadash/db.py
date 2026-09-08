"""Postgres-backed data access layer — replaces gadash/sheets.py on the live
path. Every function takes an explicit tenant_id and only ever touches that
tenant's rows; there is no code path here that can leak one contractor's
data into another's.

Function names/shapes deliberately mirror gadash/sheets.py (e.g.
load_work_entries still returns a pd.DataFrame with the same Hebrew
COLUMNS) so the ~30 call sites in app.py/gadash/bot.py mostly just gain a
tenant_id argument rather than being rewritten. Two exceptions, both
upgrades over the Sheets version:

- Row identity: Sheets used a row's *position* in the sheet as its id,
  which is fragile under concurrent edits (see the old comment in app.py's
  edit route about needing force_refresh right before a write). Here
  "_row_id" is the real database primary key, stable regardless of
  ordering or concurrent changes.
- No caching: the Sheets layer cached reads for 5 minutes to stay under
  Google's API rate limits. Postgres has no such limit, so every read here
  is live — force_refresh is accepted for call-site compatibility but is a
  no-op.
"""
import math
from datetime import datetime

import pandas as pd

from gadash.models import COLUMNS, VALID_TASKS, WorkEntry
from gadash.models_db import AuditLogEntry, Field, Rate, Subscriber, Tenant, WorkEntryRow, db


def list_tenant_ids() -> list:
    """All tenant ids — used by the bot's daily/weekly broadcast jobs, which
    have to run once per tenant since subscribers and job data are
    tenant-scoped (one shared bot serves every tenant)."""
    return [t.id for t in Tenant.query.with_entities(Tenant.id).all()]

_FIELD_MAP = {
    "שם לקוח": "client", "תאריך": "date", "עבודה": "task", "שם חלקה": "field_name",
    "גידול": "crop", "כמות": "amount", "שעות": "hours", "כלי": "tool",
    "מפעיל": "operator", "הערות": "notes",
}


# ── Work entries (main "Data" sheet equivalent) ─────────────────────────────

def load_work_entries(tenant_id: int, force_refresh: bool = False) -> pd.DataFrame:
    rows = (WorkEntryRow.query.filter_by(tenant_id=tenant_id)
            .order_by(WorkEntryRow.id).all())
    records = [{
        "שם לקוח": r.client, "תאריך": r.date, "עבודה": r.task,
        "שם חלקה": r.field_name or "", "גידול": r.crop or "",
        "כמות": r.amount or "", "שעות": r.hours or "",
        "כלי": r.tool or "", "מפעיל": r.operator or "",
        "הערות": r.notes or "", "מזין": r.entered_by or "",
        "_row_id": r.id,
    } for r in rows]
    if not records:
        df = pd.DataFrame(columns=[*COLUMNS, "_row_id"])
        return df
    return pd.DataFrame(records)


def append_work_entry(tenant_id: int, entry: WorkEntry):
    db.session.add(WorkEntryRow(
        tenant_id=tenant_id, client=entry.client, date=entry.date, task=entry.task,
        field_name=entry.field_name, crop=entry.crop, amount=entry.amount,
        hours=entry.hours, tool=entry.tool, operator=entry.operator,
        notes=entry.notes, entered_by=entry.entered_by,
    ))
    db.session.commit()


def bulk_insert_work_entries(tenant_id: int, entries: list):
    """Used by /import — inserts only the (already deduped) new rows,
    instead of the old rewrite-the-whole-sheet dance save_data_to_gsheet did."""
    db.session.add_all([WorkEntryRow(
        tenant_id=tenant_id, client=e.client, date=e.date, task=e.task,
        field_name=e.field_name, crop=e.crop, amount=e.amount,
        hours=e.hours, tool=e.tool, operator=e.operator,
        notes=e.notes, entered_by=e.entered_by,
    ) for e in entries])
    db.session.commit()


def edit_work_entry(tenant_id: int, row_id: int, entry: WorkEntry):
    row = WorkEntryRow.query.filter_by(tenant_id=tenant_id, id=row_id).first()
    if not row:
        raise ValueError("הרשומה לא נמצאה")
    row.client, row.date, row.task = entry.client, entry.date, entry.task
    row.field_name, row.crop, row.amount = entry.field_name, entry.crop, entry.amount
    row.hours, row.tool, row.operator = entry.hours, entry.tool, entry.operator
    row.notes, row.entered_by = entry.notes, entry.entered_by
    db.session.commit()


def delete_work_entry(tenant_id: int, row_id: int):
    WorkEntryRow.query.filter_by(tenant_id=tenant_id, id=row_id).delete()
    db.session.commit()


def bulk_delete_work_entries(tenant_id: int, row_ids: list):
    if not row_ids:
        return
    (WorkEntryRow.query
     .filter(WorkEntryRow.tenant_id == tenant_id, WorkEntryRow.id.in_(row_ids))
     .delete(synchronize_session=False))
    db.session.commit()


def patch_work_entry_cell(tenant_id: int, row_id: int, field: str, value: str):
    attr = _FIELD_MAP.get(field)
    if not attr:
        raise ValueError(f"שדה לא תקין: {field}")
    row = WorkEntryRow.query.filter_by(tenant_id=tenant_id, id=row_id).first()
    if not row:
        raise ValueError("הרשומה לא נמצאה")
    setattr(row, attr, value)
    db.session.commit()


# ── Fields (merges the old Polygons/Pins/legacy-FieldCoords sheets) ────────

def calculate_polygon_dunam_area(coords: list) -> float:
    """Area of a lat/lng polygon in Dunams (1 Dunam = 1000 sq meters).

    Equirectangular projection centered on the polygon's average latitude —
    accurate enough for single-field-sized areas, no geo library needed.
    """
    if not coords or len(coords) < 3:
        return 0.0
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    avg_lat = sum(lats) / len(lats)
    lat_meters = 111000.0
    lon_meters = 111000.0 * math.cos(math.radians(avg_lat))
    x = [lon * lon_meters for lon in lons]
    y = [lat * lat_meters for lat in lats]
    area_sq_m = 0.5 * abs(sum(x[i] * y[i - 1] - x[i - 1] * y[i] for i in range(len(coords))))
    return round(area_sq_m / 1000.0, 2)


def load_polygons(tenant_id: int) -> list:
    rows = Field.query.filter_by(tenant_id=tenant_id, kind="polygon").all()
    return [{"uid": r.uid, "name": r.name, "color": r.color or "", "coordinates": r.coordinates}
            for r in rows]


def load_pins(tenant_id: int) -> list:
    rows = Field.query.filter_by(tenant_id=tenant_id, kind="pin").all()
    return [{"uid": r.uid, "name": r.name, "color": r.color or "", "lat": r.lat, "lng": r.lng}
            for r in rows]


def save_polygon(tenant_id: int, uid: str, name: str, color: str, coords: list):
    row = Field.query.filter_by(tenant_id=tenant_id, uid=uid).first()
    if row:
        row.name, row.color, row.kind = name, color, "polygon"
        row.coordinates, row.lat, row.lng = coords, None, None
    else:
        db.session.add(Field(tenant_id=tenant_id, uid=uid, name=name, color=color,
                              kind="polygon", coordinates=coords))
    db.session.commit()


def save_pin(tenant_id: int, uid: str, name: str, color: str, lat: float, lng: float):
    row = Field.query.filter_by(tenant_id=tenant_id, uid=uid).first()
    if row:
        row.name, row.color, row.kind = name, color, "pin"
        row.lat, row.lng, row.coordinates = lat, lng, None
    else:
        db.session.add(Field(tenant_id=tenant_id, uid=uid, name=name, color=color,
                              kind="pin", lat=lat, lng=lng))
    db.session.commit()


def delete_field(tenant_id: int, uid: str):
    """Deletes whichever of pin/polygon exists under this uid — one row now,
    since a uid identifies a single field regardless of kind (unlike the old
    two-separate-sheets Polygons/Pins model)."""
    Field.query.filter_by(tenant_id=tenant_id, uid=uid).delete()
    db.session.commit()


# ── Rates ────────────────────────────────────────────────────────────────

def load_rates(tenant_id: int) -> dict:
    rates = {t: {"revenue": 0.0, "cost": 0.0} for t in VALID_TASKS}
    for r in Rate.query.filter_by(tenant_id=tenant_id).all():
        rates[r.task] = {"revenue": r.revenue_rate, "cost": r.cost_rate}
    return rates


def save_rates(tenant_id: int, rates: dict):
    for task, r in rates.items():
        row = Rate.query.filter_by(tenant_id=tenant_id, task=task).first()
        if row:
            row.revenue_rate, row.cost_rate = r["revenue"], r["cost"]
        else:
            db.session.add(Rate(tenant_id=tenant_id, task=task,
                                 revenue_rate=r["revenue"], cost_rate=r["cost"]))
    db.session.commit()


# ── Audit log ────────────────────────────────────────────────────────────
# No write-ahead file + batched flush thread here — that existed only to
# stay under the Sheets API's rate limit for frequent small appends.
# A direct Postgres insert per event is fast enough to just do synchronously.

def log_audit(tenant_id: int, action: str, user: str, detail: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.session.add(AuditLogEntry(tenant_id=tenant_id, ts=ts, action=action, user=user, detail=detail))
    db.session.commit()


def read_audit_log(tenant_id: int, limit: int = 200) -> list:
    rows = (AuditLogEntry.query.filter_by(tenant_id=tenant_id)
            .order_by(AuditLogEntry.id.desc()).limit(limit).all())
    return [{"ts": r.ts, "action": r.action, "user": r.user, "detail": r.detail} for r in rows]


# ── Subscribers (Telegram broadcast recipients) ─────────────────────────────

def get_subscribers(tenant_id: int) -> set:
    return {r.chat_id for r in Subscriber.query.filter_by(tenant_id=tenant_id).all()}


def add_subscriber(tenant_id: int, chat_id: int):
    exists = Subscriber.query.filter_by(tenant_id=tenant_id, chat_id=chat_id).first()
    if exists:
        return
    db.session.add(Subscriber(tenant_id=tenant_id, chat_id=chat_id))
    db.session.commit()
