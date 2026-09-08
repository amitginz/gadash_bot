"""Resolves "which tenant is this?" for the current Flask request.

The Telegram bot has no request/session, so it doesn't use this — bot.py
threads tenant_id explicitly via context.user_data instead (set once per
conversation right after the worker's tenant is resolved), which is
simpler and more reliable than a contextvar for a long-lived conversation
handler.
"""
from flask import session


def current_tenant_id() -> int:
    tid = session.get("tenant_id")
    if tid is None:
        raise RuntimeError("no tenant_id in session — route must be @login_required or @worker_required")
    return tid
