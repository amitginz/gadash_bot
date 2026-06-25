import threading

from gadash.sheets import _get_subscribers_sheet

_subscribers_cache: set | None = None
_subscribers_lock = threading.Lock()


def _get_subscribers() -> set:
    global _subscribers_cache
    with _subscribers_lock:
        if _subscribers_cache is not None:
            return set(_subscribers_cache)
    try:
        ws = _get_subscribers_sheet()
        if ws:
            rows = ws.get_all_values()
            subs = {int(r[0]) for r in rows[1:] if r and r[0].lstrip("-").isdigit()}
            with _subscribers_lock:
                _subscribers_cache = subs
            return set(subs)
    except Exception:
        pass
    return set()


def _add_subscriber(chat_id: int):
    global _subscribers_cache
    subs = _get_subscribers()
    if chat_id in subs:
        return
    subs.add(chat_id)
    with _subscribers_lock:
        _subscribers_cache = subs
    try:
        ws = _get_subscribers_sheet()
        if ws:
            ws.append_row([str(chat_id)])
    except Exception as e:
        print(f"[Subscribers] save error: {e}")
