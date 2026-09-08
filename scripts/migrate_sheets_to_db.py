#!/usr/bin/env python3
"""One-time migration: copy the existing "Gadash Data" Google Sheet into
Postgres as a single tenant.

Needs both credential sets configured, same as the app itself:
  - GOOGLE_CREDS (or credentials.json) — to read the live Sheet
  - DATABASE_URL — the Postgres target to write into

Usage:
    python scripts/migrate_sheets_to_db.py \
        --name "שם העסק" --username <manager-login> --password <manager-password> \
        --slug <company-code>

Refuses to run if a tenant with that slug already exists, unless --force
is passed — re-running this by accident should not silently duplicate
every row. This is meant to run once, right before cutting the live app
over from gadash/sheets.py to gadash/db.py.
"""
import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from gadash import auth
from gadash.db import add_subscriber, bulk_insert_work_entries, save_pin, save_polygon, save_rates
from gadash.models import WorkEntry
from gadash.models_db import AuditLogEntry, Worker, db
from gadash.sheets import (
    _get_audit_sheet, _get_subscribers_sheet, _get_workers_sheet,
    _load_field_coords, load_data_from_gsheet, load_pins_from_sheet,
    load_polygons_from_sheet, load_rates_from_sheet,
)


def _make_app() -> Flask:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        sys.exit("❌ DATABASE_URL לא מוגדר")
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url.replace("postgres://", "postgresql://", 1)
    db.init_app(app)
    return app


def migrate(name: str, username: str, password: str, slug: str, force: bool):
    if auth.get_tenant_id_by_slug(slug) is not None:
        if not force:
            sys.exit(f"❌ טננט עם קוד '{slug}' כבר קיים. השתמש ב---force כדי לייבא שוב בכל זאת "
                      f"(עלול לשכפל נתונים).")
    tenant_id = auth.create_tenant(name, username, password, slug=slug)
    print(f"✅ נוצר טננט #{tenant_id} ({slug})")

    # ── Work entries ─────────────────────────────────────────────────────
    df = load_data_from_gsheet()
    entries, skipped = [], 0
    for _, row in df.iterrows():
        try:
            entries.append(WorkEntry.from_dict(row.to_dict()))
        except ValueError:
            skipped += 1
    if entries:
        bulk_insert_work_entries(tenant_id, entries)
    print(f"✅ {len(entries)} עבודות יובאו" + (f", {skipped} דולגו (לא תקינות)" if skipped else ""))

    # ── Workers — password hashes copied as-is (same Werkzeug scheme the ──
    # new Worker model uses); legacy raw-SHA256 hashes from very old rows
    # won't verify post-migration and need a manager-issued password reset.
    ws = _get_workers_sheet()
    worker_count = 0
    if ws:
        for r in ws.get_all_values()[1:]:
            if not r or not r[0]:
                continue
            worker_name = r[0]
            pw_hash = r[1] if len(r) > 1 and r[1] else auth.generate_password_hash(uuid.uuid4().hex)
            telegram_id = r[2] if len(r) > 2 and r[2] else None
            db.session.add(Worker(tenant_id=tenant_id, name=worker_name,
                                   password_hash=pw_hash, telegram_id=telegram_id))
            worker_count += 1
        db.session.commit()
    print(f"✅ {worker_count} עובדים יובאו")

    # ── Fields: new-style polygons + pins, then legacy single-point ────────
    # FieldCoords entries not already covered by a new-style pin.
    poly_count = 0
    for p in load_polygons_from_sheet():
        save_polygon(tenant_id, p["uid"], p["name"], p.get("color", ""), p["coordinates"])
        poly_count += 1

    pin_count = 0
    pin_names = set()
    for p in load_pins_from_sheet():
        save_pin(tenant_id, p["uid"], p["name"], p.get("color", ""), p["lat"], p["lng"])
        pin_names.add(p["name"])
        pin_count += 1

    legacy_count = 0
    for field_name, coord in _load_field_coords().items():
        if field_name in pin_names:
            continue
        save_pin(tenant_id, str(uuid.uuid4()), field_name, "", coord["lat"], coord["lng"])
        legacy_count += 1
    print(f"✅ {poly_count} פוליגונים + {pin_count} נקודות + {legacy_count} נקודות ישנות (FieldCoords) יובאו")

    # ── Rates ────────────────────────────────────────────────────────────
    save_rates(tenant_id, load_rates_from_sheet())
    print("✅ תעריפים יובאו")

    # ── Audit log (best-effort; preserves original timestamps) ─────────────
    audit_ws = _get_audit_sheet()
    audit_count = 0
    if audit_ws:
        for r in audit_ws.get_all_values()[1:]:
            if len(r) >= 4:
                db.session.add(AuditLogEntry(tenant_id=tenant_id, ts=r[0], action=r[1], user=r[2], detail=r[3]))
                audit_count += 1
        db.session.commit()
    print(f"✅ {audit_count} רשומות יומן ביקורת יובאו")

    # ── Telegram broadcast subscribers ──────────────────────────────────────
    subs_ws = _get_subscribers_sheet()
    subs_count = 0
    if subs_ws:
        for r in subs_ws.get_all_values()[1:]:
            if r and r[0].lstrip("-").isdigit():
                add_subscriber(tenant_id, int(r[0]))
                subs_count += 1
    print(f"✅ {subs_count} מנויים לשידורי הבוט יובאו")

    print(f"\n🎉 הגירה הושלמה. קוד חברה לכניסת עובדים: {slug} | שם משתמש למנהל: {username}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="שם העסק (למשל 'חוות הדר')")
    parser.add_argument("--username", required=True, help="שם משתמש חדש למנהל")
    parser.add_argument("--password", required=True, help="סיסמה חדשה למנהל")
    parser.add_argument("--slug", required=True, help="קוד חברה לכניסת עובדים (אותיות/מספרים לועזיים)")
    parser.add_argument("--force", action="store_true", help="ייבוא גם אם קוד החברה כבר קיים")
    args = parser.parse_args()

    app = _make_app()
    with app.app_context():
        db.create_all()
        migrate(args.name, args.username, args.password, args.slug, args.force)
