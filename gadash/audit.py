import collections
import json
import os
import threading
import time
from datetime import datetime

from gadash.sheets import _get_audit_sheet

AUDIT_LOG_FILE = "audit.log"

_audit_lock       = threading.Lock()
_audit_queue      = collections.deque()
_audit_queue_lock = threading.Lock()


def _log_audit(action: str, user: str, detail: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry_json = json.dumps({"ts": ts, "action": action, "user": user, "detail": detail},
                            ensure_ascii=False)
    with _audit_lock:
        try:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(entry_json + "\n")
        except Exception:
            pass
    with _audit_queue_lock:
        _audit_queue.append([ts, action, user, detail])


def _read_audit_log(limit: int = 200) -> list:
    if os.path.exists(AUDIT_LOG_FILE):
        try:
            with _audit_lock:
                with open(AUDIT_LOG_FILE, encoding="utf-8") as f:
                    lines = f.readlines()
            entries = [json.loads(l) for l in lines if l.strip()]
            if entries:
                return list(reversed(entries[-limit:]))
        except Exception:
            pass
    try:
        ws = _get_audit_sheet()
        if ws:
            rows = ws.get_all_values()
            entries = [{"ts": r[0], "action": r[1], "user": r[2], "detail": r[3]}
                       for r in rows[1:] if len(r) >= 4]
            return list(reversed(entries[-limit:]))
    except Exception:
        pass
    return []


_MAX_LOG_BYTES = 1_000_000  # 1 MB


def _flush_audit_to_sheets():
    while True:
        time.sleep(30)
        with _audit_queue_lock:
            if not _audit_queue:
                continue
            rows = list(_audit_queue)
            _audit_queue.clear()
        try:
            ws = _get_audit_sheet()
            if ws:
                ws.append_rows(rows, value_input_option="USER_ENTERED")
                # Truncate local write-ahead buffer once safely flushed to sheet
                with _audit_lock:
                    try:
                        if os.path.exists(AUDIT_LOG_FILE) and os.path.getsize(AUDIT_LOG_FILE) > _MAX_LOG_BYTES:
                            open(AUDIT_LOG_FILE, "w").close()
                    except Exception:
                        pass
        except Exception as e:
            print(f"[Audit] flush error: {e}")
