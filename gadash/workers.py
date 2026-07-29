import hashlib

from werkzeug.security import check_password_hash, generate_password_hash

from gadash.sheets import _get_workers_sheet


def _hash_pw(password: str) -> str:
    return generate_password_hash(password)


_DEFAULT_OFFLINE_WORKERS = [
    {"שם": "עובד 1", "password_hash": generate_password_hash("gadash2025"), "telegram_id": ""},
    {"שם": "מנהל",   "password_hash": generate_password_hash("gadash2025"), "telegram_id": ""},
]


def _load_workers() -> list:
    """Load list of registered worker accounts from Google Sheets or default local fallback.

    Returns:
        list: List of dictionaries containing 'שם', 'password_hash', and 'telegram_id'.
    """
    try:
        ws = _get_workers_sheet()
        if not ws:
            return _DEFAULT_OFFLINE_WORKERS
        rows = ws.get_all_values()
        workers = [
            {"שם": r[0],
             "password_hash": r[1] if len(r) > 1 else "",
             "telegram_id":   r[2] if len(r) > 2 else ""}
            for r in rows[1:] if r and r[0]
        ]
        return workers if workers else _DEFAULT_OFFLINE_WORKERS
    except Exception:
        return _DEFAULT_OFFLINE_WORKERS


def _verify_worker(name: str, password: str) -> bool:
    """Verify worker credentials against stored hashes.

    Args:
        name (str): Worker name.
        password (str): Plaintext password to verify.

    Returns:
        bool: True if credentials match, False otherwise.
    """
    for w in _load_workers():
        if w["שם"] != name:
            continue
        stored = w["password_hash"]
        # Support legacy SHA-256 hashes (64 hex chars) alongside new Werkzeug hashes
        if len(stored) == 64 and all(c in "0123456789abcdef" for c in stored):
            return stored == hashlib.sha256(password.encode()).hexdigest()
        return check_password_hash(stored, password)
    return False


def _add_worker(name: str, password: str) -> bool:
    workers = _load_workers()
    if any(w["שם"] == name for w in workers):
        return False
    try:
        ws = _get_workers_sheet()
        if ws:
            ws.append_row([name, _hash_pw(password), ""])
            return True
    except Exception:
        pass
    return False


def _delete_worker(name: str) -> bool:
    try:
        ws = _get_workers_sheet()
        if not ws:
            return False
        rows = ws.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == name:
                ws.delete_rows(i)
                return True
    except Exception:
        pass
    return False


def _get_worker_by_telegram_id(telegram_id: int) -> dict | None:
    tid = str(telegram_id)
    return next((w for w in _load_workers() if w.get("telegram_id") == tid), None)


def _link_worker_telegram(name: str, telegram_id: int) -> bool:
    try:
        ws = _get_workers_sheet()
        if not ws:
            return False
        rows = ws.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == name:
                ws.update_cell(i, 3, str(telegram_id))
                return True
    except Exception:
        pass
    return False
