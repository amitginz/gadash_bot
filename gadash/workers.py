"""Tenant-scoped worker records — replaces the Workers sheet.

telegram_id stays looked-up globally (not tenant-scoped): see the module
docstring in gadash/models_db.py for why — one shared Telegram bot needs to
resolve which tenant a message belongs to from the sender's telegram_id
alone.
"""
from werkzeug.security import check_password_hash, generate_password_hash

from gadash.models_db import Worker, db


def _load_workers(tenant_id: int) -> list:
    rows = Worker.query.filter_by(tenant_id=tenant_id).order_by(Worker.name).all()
    return [{"שם": w.name, "password_hash": w.password_hash,
             "telegram_id": w.telegram_id or ""} for w in rows]


def _verify_worker(tenant_id: int, name: str, password: str) -> bool:
    w = Worker.query.filter_by(tenant_id=tenant_id, name=name).first()
    return bool(w and check_password_hash(w.password_hash, password))


def _add_worker(tenant_id: int, name: str, password: str) -> bool:
    if Worker.query.filter_by(tenant_id=tenant_id, name=name).first():
        return False
    db.session.add(Worker(tenant_id=tenant_id, name=name,
                           password_hash=generate_password_hash(password)))
    db.session.commit()
    return True


def _delete_worker(tenant_id: int, name: str) -> bool:
    deleted = Worker.query.filter_by(tenant_id=tenant_id, name=name).delete()
    db.session.commit()
    return bool(deleted)


def _get_worker_by_telegram_id(telegram_id: int) -> dict | None:
    """Cross-tenant by design — see module docstring."""
    w = Worker.query.filter_by(telegram_id=str(telegram_id)).first()
    if not w:
        return None
    return {"שם": w.name, "tenant_id": w.tenant_id}


def _link_worker_telegram(tenant_id: int, name: str, telegram_id: int) -> bool:
    w = Worker.query.filter_by(tenant_id=tenant_id, name=name).first()
    if not w:
        return False
    w.telegram_id = str(telegram_id)
    db.session.commit()
    return True


def _get_worker_by_whatsapp_number(whatsapp_number: str) -> dict | None:
    """Cross-tenant by design — see module docstring."""
    w = Worker.query.filter_by(whatsapp_number=whatsapp_number).first()
    if not w:
        return None
    return {"שם": w.name, "tenant_id": w.tenant_id}


def _link_worker_whatsapp(tenant_id: int, name: str, whatsapp_number: str) -> bool:
    w = Worker.query.filter_by(tenant_id=tenant_id, name=name).first()
    if not w:
        return False
    w.whatsapp_number = whatsapp_number
    db.session.commit()
    return True
