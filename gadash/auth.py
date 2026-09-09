"""Per-tenant authentication — replaces the single global WEB_PASSWORD /
WORKER_PASSWORD model.

Manager identity resolves the tenant on its own (usernames are globally
unique). Worker names are only unique *within* a tenant, so a worker also
supplies their tenant's slug — a short code the manager hands out once
during onboarding (there's no self-serve signup to discover it from).
"""
from __future__ import annotations

import re
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from gadash.models_db import Manager, Tenant, Worker, db


def slugify(text: str) -> str:
    """Business names here are typically Hebrew, which this strips entirely
    (slugs are ASCII, for URL-safety) — falling back to a fixed word like
    "tenant" would collide on the very first two Hebrew-named tenants, so an
    empty result gets a random suffix instead. Pass --slug explicitly on
    `flask create-tenant` for a readable one; this is just a safety net."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or f"tenant-{secrets.token_hex(4)}"


def create_tenant(name: str, manager_username: str, manager_password: str, slug: str | None = None) -> int:
    """Onboards one pilot customer: a tenant plus their first manager login.
    Used by the `flask create-tenant` CLI command — there is no public
    signup flow yet."""
    slug = slug or slugify(name)
    if Tenant.query.filter_by(slug=slug).first():
        raise ValueError(f"קוד חברה '{slug}' כבר תפוס")
    if Manager.query.filter_by(username=manager_username).first():
        raise ValueError(f"שם משתמש '{manager_username}' כבר תפוס")

    tenant = Tenant(name=name, slug=slug)
    db.session.add(tenant)
    db.session.flush()  # assigns tenant.id without committing yet

    db.session.add(Manager(
        tenant_id=tenant.id, username=manager_username,
        password_hash=generate_password_hash(manager_password),
    ))
    db.session.commit()
    return tenant.id


def verify_manager(username: str, password: str) -> tuple[int, int] | None:
    """Returns (tenant_id, manager_id) on success, None on failure. Username
    alone resolves the tenant — no slug needed for managers."""
    manager = Manager.query.filter_by(username=username).first()
    if manager and check_password_hash(manager.password_hash, password):
        return manager.tenant_id, manager.id
    return None


def verify_worker(tenant_slug: str, name: str, password: str) -> tuple[int, str] | None:
    """Returns (tenant_id, worker_name) on success, None on failure."""
    tenant = Tenant.query.filter_by(slug=tenant_slug.strip()).first()
    if not tenant:
        return None
    worker = Worker.query.filter_by(tenant_id=tenant.id, name=name.strip()).first()
    if worker and check_password_hash(worker.password_hash, password):
        return tenant.id, worker.name
    return None


def get_tenant_slug(tenant_id: int) -> str:
    tenant = db.session.get(Tenant, tenant_id)
    return tenant.slug if tenant else ""


def get_tenant_id_by_slug(slug: str) -> int | None:
    tenant = Tenant.query.filter_by(slug=slug.strip()).first()
    return tenant.id if tenant else None


def change_manager_password(manager_id: int, old_password: str, new_password: str) -> bool:
    manager = db.session.get(Manager, manager_id)
    if not manager or not check_password_hash(manager.password_hash, old_password):
        return False
    manager.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return True


def change_worker_password(tenant_id: int, worker_name: str, old_password: str, new_password: str) -> bool:
    worker = Worker.query.filter_by(tenant_id=tenant_id, name=worker_name).first()
    if not worker or not check_password_hash(worker.password_hash, old_password):
        return False
    worker.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return True
