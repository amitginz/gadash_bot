"""SQLAlchemy schema for the multi-tenant Postgres backend.

Replaces the Google-Sheets-per-worksheet storage in gadash/sheets.py.
Every table except `tenants` carries a tenant_id — this is the boundary that
keeps one contractor's data invisible to another. `workers.telegram_id` is
the one column that's intentionally *not* tenant-scoped: it's globally
unique so the single shared Telegram bot can resolve which tenant a message
belongs to from one lookup, instead of running one bot per tenant.
"""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy


def _utcnow():
    return datetime.now(timezone.utc)


db = SQLAlchemy()


class Tenant(db.Model):
    __tablename__ = "tenants"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(200), nullable=False)
    # Worker names are only unique *within* a tenant (unlike telegram_id/manager
    # username), so the web worker-login form needs one more piece of context
    # to know which tenant's "דני" is meant — this is that: a short code the
    # manager gives each worker once during onboarding.
    slug       = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    managers     = db.relationship("Manager", back_populates="tenant", cascade="all, delete-orphan")
    workers      = db.relationship("Worker", back_populates="tenant", cascade="all, delete-orphan")
    work_entries = db.relationship("WorkEntryRow", back_populates="tenant", cascade="all, delete-orphan")
    fields       = db.relationship("Field", back_populates="tenant", cascade="all, delete-orphan")
    rates        = db.relationship("Rate", back_populates="tenant", cascade="all, delete-orphan")


class Manager(db.Model):
    """A logged-in dashboard user. Replaces the single global WEB_PASSWORD."""
    __tablename__ = "managers"

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    username      = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)

    tenant = db.relationship("Tenant", back_populates="managers")


class Worker(db.Model):
    __tablename__ = "workers"
    __table_args__ = (db.UniqueConstraint("tenant_id", "name", name="uq_worker_tenant_name"),)

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    name          = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # Globally unique (not per-tenant) — see module docstring.
    telegram_id   = db.Column(db.String(32), unique=True, nullable=True, index=True)

    tenant = db.relationship("Tenant", back_populates="workers")


class WorkEntryRow(db.Model):
    """One logged job. Replaces a row in the main 'Data' worksheet."""
    __tablename__ = "work_entries"

    id         = db.Column(db.Integer, primary_key=True)
    tenant_id  = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    client     = db.Column(db.String(200), nullable=False)
    date       = db.Column(db.String(10), nullable=False)  # kept as YYYY-MM-DD text — matches
                                                             # WorkEntry's own validation/format,
                                                             # and every report already does
                                                             # string comparison/pd.to_datetime on it
    task       = db.Column(db.String(50), nullable=False)
    field_name = db.Column(db.String(200), default="")
    crop       = db.Column(db.String(200), default="")
    amount     = db.Column(db.String(100), default="")
    hours      = db.Column(db.String(50), default="")
    tool       = db.Column(db.String(100), default="")
    operator   = db.Column(db.String(120), default="")
    notes      = db.Column(db.Text, default="")
    entered_by = db.Column(db.String(120), default="")
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    tenant = db.relationship("Tenant", back_populates="work_entries")

    __table_args__ = (db.Index("ix_work_entries_tenant_date", "tenant_id", "date"),)


class Field(db.Model):
    """A saved map pin or polygon. Merges the old Polygons/Pins/FieldCoords sheets."""
    __tablename__ = "fields"
    __table_args__ = (db.UniqueConstraint("tenant_id", "uid", name="uq_field_tenant_uid"),)

    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    uid         = db.Column(db.String(64), nullable=False)
    name        = db.Column(db.String(200), nullable=False)
    color       = db.Column(db.String(20), default="")
    kind        = db.Column(db.String(10), nullable=False)  # 'pin' | 'polygon'
    lat         = db.Column(db.Float, nullable=True)
    lng         = db.Column(db.Float, nullable=True)
    coordinates = db.Column(db.JSON, nullable=True)  # polygon vertex list; null for pins

    tenant = db.relationship("Tenant", back_populates="fields")


class Rate(db.Model):
    __tablename__ = "rates"
    __table_args__ = (db.PrimaryKeyConstraint("tenant_id", "task"),)

    tenant_id     = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    task          = db.Column(db.String(50), nullable=False)
    revenue_rate  = db.Column(db.Float, nullable=False, default=0.0)
    cost_rate     = db.Column(db.Float, nullable=False, default=0.0)

    tenant = db.relationship("Tenant", back_populates="rates")


class AuditLogEntry(db.Model):
    __tablename__ = "audit_log"

    id        = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    ts        = db.Column(db.String(20), nullable=False)
    action    = db.Column(db.String(50), nullable=False)
    user      = db.Column(db.String(120), nullable=False)
    detail    = db.Column(db.Text, default="")


class Subscriber(db.Model):
    """A Telegram chat_id subscribed to daily/weekly broadcast jobs."""
    __tablename__ = "subscribers"
    __table_args__ = (db.UniqueConstraint("tenant_id", "chat_id", name="uq_subscriber_tenant_chat"),)

    id        = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False, index=True)
    chat_id   = db.Column(db.BigInteger, nullable=False)
