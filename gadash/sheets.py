import json
import logging
import os
import threading
import time

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from gadash.models import COLUMNS, _N_COLS, WorkEntry

_logger = logging.getLogger(__name__)

_gs_client  = None
_gs_lock    = threading.Lock()
_cache_data = None
_cache_time = 0.0
_CACHE_TTL  = 300

_coords_cache      = None
_coords_cache_time = 0.0

_is_offline = False
_offline_queue = []
_OFFLINE_QUEUE_PATH = os.path.join("data", "offline_queue.json")

_GS_SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _invalidate_cache():
    global _cache_data, _cache_time
    _cache_data = None
    _cache_time = 0.0


def _init_gs_client():
    global _gs_client
    if _gs_client is not None:
        return
    raw = os.environ.get("GOOGLE_CREDS")
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=_GS_SCOPE)
    elif os.path.exists("credentials.json"):
        creds = Credentials.from_service_account_file("credentials.json", scopes=_GS_SCOPE)
    else:
        raise RuntimeError("No Google credentials found.")
    _gs_client = gspread.authorize(creds)


def _get_sheet():
    global _gs_client
    last_exc = None
    for attempt in range(3):
        with _gs_lock:
            try:
                _init_gs_client()
                return _gs_client.open("Gadash Data").sheet1
            except Exception as e:
                last_exc = e
                _gs_client = None
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    raise last_exc


def _get_settings_sheet():
    global _gs_client
    with _gs_lock:
        try:
            _init_gs_client()
            wb = _gs_client.open("Gadash Data")
            try:
                return wb.worksheet("Settings")
            except gspread.WorksheetNotFound:
                return wb.add_worksheet("Settings", rows=10, cols=2)
        except Exception:
            _gs_client = None
            return None


def _get_fieldcoords_sheet():
    global _gs_client
    with _gs_lock:
        try:
            _init_gs_client()
            wb = _gs_client.open("Gadash Data")
            try:
                return wb.worksheet("FieldCoords")
            except gspread.WorksheetNotFound:
                ws = wb.add_worksheet("FieldCoords", rows=200, cols=3)
                ws.append_row(["שם חלקה", "lat", "lng"])
                return ws
        except Exception:
            _gs_client = None
            return None


def _get_audit_sheet():
    global _gs_client
    with _gs_lock:
        try:
            _init_gs_client()
            wb = _gs_client.open("Gadash Data")
            try:
                return wb.worksheet("AuditLog")
            except gspread.WorksheetNotFound:
                ws = wb.add_worksheet("AuditLog", rows=2000, cols=4)
                ws.append_row(["ts", "action", "user", "detail"])
                return ws
        except Exception:
            _gs_client = None
            return None


def _get_subscribers_sheet():
    global _gs_client
    with _gs_lock:
        try:
            _init_gs_client()
            wb = _gs_client.open("Gadash Data")
            try:
                return wb.worksheet("Subscribers")
            except gspread.WorksheetNotFound:
                ws = wb.add_worksheet("Subscribers", rows=200, cols=1)
                ws.append_row(["chat_id"])
                return ws
        except Exception:
            _gs_client = None
            return None


def _get_workers_sheet():
    global _gs_client
    with _gs_lock:
        try:
            _init_gs_client()
            wb = _gs_client.open("Gadash Data")
            try:
                return wb.worksheet("Workers")
            except gspread.WorksheetNotFound:
                ws = wb.add_worksheet("Workers", rows=200, cols=3)
                ws.append_row(["שם", "password_hash", "telegram_id"])
                return ws
        except Exception:
            _gs_client = None
            return None


def _load_field_coords() -> dict:
    global _coords_cache, _coords_cache_time
    with _gs_lock:
        if _coords_cache is not None and (time.time() - _coords_cache_time) < _CACHE_TTL:
            return dict(_coords_cache)
    try:
        ws = _get_fieldcoords_sheet()
        if not ws:
            return {}
        rows = ws.get_all_values()
        coords = {}
        for row in rows[1:]:
            if len(row) >= 3 and row[0] and row[1] and row[2]:
                try:
                    coords[row[0]] = {"lat": float(row[1]), "lng": float(row[2])}
                except ValueError:
                    pass
        with _gs_lock:
            _coords_cache = coords
            _coords_cache_time = time.time()
        return dict(coords)
    except Exception:
        return {}


def _save_field_coord(name: str, lat: float, lng: float):
    """Save field pin coordinates to Google Sheets or update local memory cache if offline.

    Args:
        name (str): Field or point pin name.
        lat (float): Latitude coordinate.
        lng (float): Longitude coordinate.
    """
    global _coords_cache, _coords_cache_time
    with _gs_lock:
        if _coords_cache is None:
            _coords_cache = {}
        _coords_cache[name] = {"lat": lat, "lng": lng}
        _coords_cache_time = time.time()
    try:
        ws = _get_fieldcoords_sheet()
        if not ws:
            return
        rows = ws.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == name:
                ws.update([[name, lat, lng]], f"A{i}:C{i}")
                break
        else:
            ws.append_row([name, lat, lng])
    except Exception as e:
        _logger.error("[FieldCoords] save error: %s", e)


def load_passwords_from_sheet() -> dict:
    try:
        ws = _get_settings_sheet()
        if not ws:
            return {}
        rows = ws.get_all_values()
        return {r[0]: r[1] for r in rows if len(r) >= 2 and r[0] and r[1]}
    except Exception:
        return {}


def save_passwords_to_sheet(web_password: str, worker_password: str):
    try:
        ws = _get_settings_sheet()
        if ws:
            ws.update([["web_password", web_password],
                       ["worker_password", worker_password]], "A1")
    except Exception:
        pass


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize DataFrame to replace nan/NaN/None values with empty strings.

    Args:
        df (pd.DataFrame): DataFrame to sanitize.

    Returns:
        pd.DataFrame: Cleaned DataFrame.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS) if df is None else df
    df_clean = df.fillna("").astype(str)
    for col in df_clean.columns:
        df_clean[col] = df_clean[col].replace(["nan", "NaN", "None", "<NA>"], "")
    return df_clean


def load_data_from_gsheet(force_refresh: bool = False) -> pd.DataFrame:
    """Fetch work data from Google Sheets, returning stale cached data if offline.

    Implements a stale-while-revalidate caching policy: if fetching fresh data fails
    or Google credentials are absent, the last-known cached DataFrame is preserved
    and served indefinitely rather than returning an empty response.

    Args:
        force_refresh (bool): If True, bypass cache and attempt fresh load from Google Sheets.

    Returns:
        pd.DataFrame: DataFrame containing all work entries.
    """
    global _cache_data, _cache_time, _is_offline
    with _gs_lock:
        if not force_refresh and _cache_data is not None and (time.time() - _cache_time) < _CACHE_TTL:
            return _sanitize_df(_cache_data).copy()
    try:
        sheet = _get_sheet()
        all_values = sheet.get_all_values()
        if not all_values or len(all_values) < 2:
            df = pd.DataFrame(columns=COLUMNS)
        else:
            headers = [h.strip() for h in all_values[0]]
            records = [dict(zip(headers, row)) for row in all_values[1:] if any(row)]
            df = pd.DataFrame(records)
            for col in COLUMNS:
                if col not in df.columns:
                    df[col] = ""
            df = df[COLUMNS]
        df = _sanitize_df(df)
        with _gs_lock:
            _cache_data = df
            _cache_time = time.time()
            _is_offline = False
        return df.copy()
    except Exception as e:
        _is_offline = True
        _logger.error("[GSheet] load error: %s", e)
        with _gs_lock:
            if _cache_data is not None:
                return _sanitize_df(_cache_data).copy()
        return pd.DataFrame(columns=COLUMNS)


def _load_offline_queue():
    """Load pending offline records from the local data/offline_queue.json storage file."""
    global _offline_queue
    if os.path.exists(_OFFLINE_QUEUE_PATH):
        try:
            with open(_OFFLINE_QUEUE_PATH, "r", encoding="utf-8") as f:
                _offline_queue = json.load(f)
        except Exception:
            _offline_queue = []


def _save_offline_queue():
    """Persist pending offline records to local data/offline_queue.json file."""
    os.makedirs("data", exist_ok=True)
    try:
        with open(_OFFLINE_QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(_offline_queue, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[OfflineQueue] save error: {e}")


def get_offline_status() -> dict:
    """Return dictionary indicating online connectivity status and pending offline queue count.

    Returns:
        dict: Status mapping containing 'online' (bool), 'pending_count' (int), and 'last_cache_time' (float).
    """
    global _is_offline, _cache_time
    _load_offline_queue()
    return {
        "online": not _is_offline and _has_creds(),
        "pending_count": len(_offline_queue),
        "last_cache_time": _cache_time,
    }


def add_offline_entry(entry_dict: dict):
    """Add a work entry to the local offline queue buffer and update the cached DataFrame.

    Args:
        entry_dict (dict): Work entry fields dictionary.
    """
    global _cache_data, _is_offline
    _load_offline_queue()
    _offline_queue.append(entry_dict)
    _save_offline_queue()
    _is_offline = True

    # Immediately reflect new entry in local cached DataFrame
    with _gs_lock:
        new_df = pd.DataFrame([entry_dict])
        for col in COLUMNS:
            if col not in new_df.columns:
                new_df[col] = ""
        new_df = new_df[COLUMNS]
        if _cache_data is None or _cache_data.empty:
            _cache_data = new_df
        else:
            _cache_data = pd.concat([_cache_data, new_df], ignore_index=True)


def append_row_to_gsheet(entry: WorkEntry):
    sheet = _get_sheet()
    sheet.append_row(entry.to_sheet_row(), value_input_option="USER_ENTERED")
    _invalidate_cache()


def edit_row_in_gsheet(row_id: int, new_entry: WorkEntry):
    """Edit a single row in Google Sheets or local cache if offline.

    Args:
        row_id (int): Zero-based index of row to edit.
        new_entry (WorkEntry): Updated WorkEntry object.
    """
    global _cache_data, _is_offline
    try:
        sheet = _get_sheet()
        row_idx = row_id + 2
        values = [new_entry.to_dict().get(c, "") for c in COLUMNS]
        sheet.update(f"A{row_idx}:L{row_idx}", [values])
        _invalidate_cache()
    except Exception as e:
        _is_offline = True
        _logger.error("[GSheet] edit error: %s", e)
        with _gs_lock:
            if _cache_data is not None and row_id < len(_cache_data):
                for c in COLUMNS:
                    _cache_data.at[row_id, c] = new_entry.to_dict().get(c, "")


def delete_row_in_gsheet(row_id: int):
    """Delete a single row from Google Sheets or local cache if offline.

    Args:
        row_id (int): Zero-based index of row to delete.
    """
    global _cache_data, _is_offline
    try:
        sheet = _get_sheet()
        row_idx = row_id + 2
        sheet.delete_rows(row_idx)
        _invalidate_cache()
    except Exception as e:
        _is_offline = True
        _logger.error("[GSheet] delete error: %s", e)
        with _gs_lock:
            if _cache_data is not None and row_id < len(_cache_data):
                _cache_data = _cache_data.drop(index=row_id).reset_index(drop=True)
                save_data_to_gsheet(_cache_data)


def bulk_delete_rows_in_gsheet(row_ids: list):
    """Bulk delete work entries by index list, updating Google Sheets or local cache if offline.

    Args:
        row_ids (list): List of zero-based row indices to delete.
    """
    df = load_data_from_gsheet(force_refresh=True)
    valid_ids = [i for i in row_ids if i < len(df)]
    if not valid_ids:
        return
    remaining = df.drop(index=valid_ids).reset_index(drop=True)
    save_data_to_gsheet(remaining)


def patch_cell_in_gsheet(row_id: int, field: str, value: str):
    """Patch a single cell value, updating Google Sheets or local cache if offline.

    Args:
        row_id (int): Zero-based row index.
        field (str): Column header name.
        value (str): New cell value.
    """
    global _cache_data, _is_offline
    try:
        sheet = _get_sheet()
        col_idx = COLUMNS.index(field) + 1
        sheet.update_cell(row_id + 2, col_idx, value)
        _invalidate_cache()
    except Exception as e:
        _is_offline = True
        print(f"[GSheet] patch error: {e}. Updating local cell cache.")
        with _gs_lock:
            if _cache_data is not None and row_id < len(_cache_data) and field in _cache_data.columns:
                _cache_data.at[row_id, field] = value
                save_data_to_gsheet(_cache_data)


def save_data_to_gsheet(df: pd.DataFrame):
    """Overwrite Google Sheets data with DataFrame, or fallback to local cache/queue if offline.

    Args:
        df (pd.DataFrame): DataFrame of work entries to save.
    """
    global _cache_data, _cache_time, _is_offline
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df_clean = df[COLUMNS]

    try:
        sheet = _get_sheet()
        sheet.clear()
        sheet.append_row(COLUMNS)
        if not df_clean.empty:
            rows = df_clean[COLUMNS].fillna("").astype(str).values.tolist()
            sheet.append_rows(rows, value_input_option="USER_ENTERED")
        with _gs_lock:
            _cache_data = df_clean.copy()
            _cache_time = time.time()
            _is_offline = False
    except Exception as e:
        _is_offline = True
        print(f"[GSheet] save error: {e}. Saving imported data to offline local cache.")
        with _gs_lock:
            _cache_data = df_clean.copy()
            _cache_time = time.time()
        _load_offline_queue()
        for row_dict in df_clean.to_dict(orient="records"):
            if row_dict not in _offline_queue:
                _offline_queue.append(row_dict)
        _save_offline_queue()

