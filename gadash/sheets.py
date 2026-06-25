import json
import os
import threading
import time

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from gadash.models import COLUMNS, _N_COLS, WorkEntry

_gs_client  = None
_gs_lock    = threading.Lock()
_cache_data = None
_cache_time = 0.0
_CACHE_TTL  = 300

_coords_cache      = None
_coords_cache_time = 0.0

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
    global _coords_cache, _coords_cache_time
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
        # Update in-memory cache immediately so next read is instant
        with _gs_lock:
            if _coords_cache is not None:
                _coords_cache[name] = {"lat": lat, "lng": lng}
                _coords_cache_time = time.time()
    except Exception as e:
        print(f"[FieldCoords] save error: {e}")


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


def load_data_from_gsheet() -> pd.DataFrame:
    global _cache_data, _cache_time
    with _gs_lock:
        if _cache_data is not None and (time.time() - _cache_time) < _CACHE_TTL:
            return _cache_data.copy()
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
        with _gs_lock:
            _cache_data = df
            _cache_time = time.time()
        return df.copy()
    except Exception as e:
        print(f"[GSheet] load error: {e}")
        return pd.DataFrame(columns=COLUMNS)


def append_row_to_gsheet(entry: WorkEntry):
    sheet = _get_sheet()
    sheet.append_row(entry.to_sheet_row(), value_input_option="USER_ENTERED")
    _invalidate_cache()


def edit_row_in_gsheet(row_id: int, entry: WorkEntry):
    sheet = _get_sheet()
    sheet_row = row_id + 2
    end_col = chr(64 + _N_COLS)
    sheet.update([entry.to_sheet_row()], f"A{sheet_row}:{end_col}{sheet_row}",
                 value_input_option="USER_ENTERED")
    _invalidate_cache()


def delete_row_in_gsheet(row_id: int):
    sheet = _get_sheet()
    sheet.delete_rows(row_id + 2)
    _invalidate_cache()


def bulk_delete_rows_in_gsheet(row_ids: list):
    sheet = _get_sheet()
    for df_idx in sorted(row_ids, reverse=True):
        sheet.delete_rows(df_idx + 2)
    _invalidate_cache()


def patch_cell_in_gsheet(row_id: int, field: str, value: str):
    sheet = _get_sheet()
    col_idx = COLUMNS.index(field) + 1
    sheet.update_cell(row_id + 2, col_idx, value)
    _invalidate_cache()


def save_data_to_gsheet(df: pd.DataFrame):
    sheet = _get_sheet()
    sheet.clear()
    sheet.append_row(COLUMNS)
    if not df.empty:
        rows = df[COLUMNS].fillna("").astype(str).values.tolist()
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
    _invalidate_cache()
