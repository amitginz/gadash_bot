import asyncio
import math
import os
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO
from urllib.parse import urlencode

import pandas as pd
from flask import (Flask, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

import gadash.bot as _bot_module
from gadash.audit import _flush_audit_to_sheets, _log_audit, _read_audit_log
from gadash.bot import start_telegram_bot
from gadash.models import COLUMNS, VALID_TASKS, WorkEntry
from gadash.service import create_entry
from gadash.sheets import (
    _invalidate_cache, _load_field_coords, _save_field_coord,
    append_row_to_gsheet, bulk_delete_rows_in_gsheet,
    delete_row_in_gsheet, edit_row_in_gsheet,
    load_data_from_gsheet, load_passwords_from_sheet,
    patch_cell_in_gsheet, save_data_to_gsheet,
    save_passwords_to_sheet,
)
from gadash.workers import (
    _add_worker, _delete_worker, _load_workers,
    _verify_worker,
)

PAGE_SIZE = 50

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gadash-dev-secret-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

_current_password = os.environ.get("WEB_PASSWORD", "gadash2025")
_worker_password  = os.environ.get("WORKER_PASSWORD", "worker2025")

try:
    saved = load_passwords_from_sheet()
    if saved.get("web_password"):
        _current_password = saved["web_password"]
    if saved.get("worker_password"):
        _worker_password = saved["worker_password"]
except Exception:
    pass


def _get_csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(32)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = _get_csrf_token


@app.before_request
def _csrf_protect():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.endpoint in ("login", "static"):
        return
    if not session.get("logged_in") and not session.get("worker_logged_in"):
        return
    token = (request.form.get("csrf_token")
             or request.headers.get("X-CSRFToken"))
    if not token or token != session.get("_csrf"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "CSRF token invalid"}), 403
        flash("בקשה לא תקינה (CSRF) ❌", "danger")
        return redirect(request.referrer or url_for("index"))


# ── Rate limiter on login ──────────────────────────────────────────────────────

_login_attempts: dict = {}
_LOGIN_MAX    = 5
_LOGIN_WINDOW = 60


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= _LOGIN_MAX


def _record_attempt(ip: str):
    _login_attempts.setdefault(ip, []).append(time.time())


# ── Auth decorators ────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def worker_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("worker_logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    if session.get("worker_logged_in"):
        return redirect(url_for("worker_index"))
    ip = request.remote_addr
    if request.method == "POST":
        if _check_rate_limit(ip):
            flash("יותר מדי ניסיונות — המתן דקה ❌", "danger")
            role = request.form.get("role", "manager")
            return render_template("login.html", selected_role=role)
        role = request.form.get("role", "manager")
        pwd  = request.form.get("password", "")
        if role == "worker":
            name = request.form.get("name", "").strip() or "עובד"
            workers = _load_workers()
            if workers:
                ok = _verify_worker(name, pwd)
            else:
                ok = (pwd == _worker_password)
            if ok:
                session.permanent = True
                session["worker_logged_in"] = True
                session["worker_name"]      = name
                return redirect(url_for("worker_index"))
            _record_attempt(ip)
            flash("שם עובד או סיסמה שגויים ❌", "danger")
            return render_template("login.html", selected_role="worker", form_name=name)
        else:
            if pwd == _current_password:
                session.permanent = True
                session["logged_in"] = True
                return redirect(url_for("index"))
            _record_attempt(ip)
            remaining = _LOGIN_MAX - len(_login_attempts.get(ip, []))
            flash(f"סיסמה שגויה ❌ ({remaining} ניסיונות נותרו)", "danger")
            return render_template("login.html", selected_role="manager")
    return render_template("login.html", selected_role="manager")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/health")
def health():
    from gadash.sheets import _get_sheet
    try:
        _get_sheet()
        return jsonify({"status": "ok", "sheets": "connected"})
    except Exception as e:
        return jsonify({"status": "degraded", "sheets": str(e)}), 503


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    global _current_password
    if request.method == "POST":
        old  = request.form.get("old_password", "")
        new1 = request.form.get("new_password", "")
        new2 = request.form.get("confirm_password", "")
        if old != _current_password:
            flash("הסיסמה הנוכחית שגויה ❌", "danger")
        elif new1 != new2:
            flash("הסיסמאות החדשות אינן תואמות ❌", "danger")
        elif len(new1) < 4:
            flash("הסיסמה חייבת לכלול לפחות 4 תווים ❌", "danger")
        else:
            _current_password = new1
            save_passwords_to_sheet(_current_password, _worker_password)
            flash("הסיסמה שונתה בהצלחה ✅", "success")
    return render_template("change_password.html")


# ── Shared filter helper ───────────────────────────────────────────────────────

def _apply_filters(df):
    q         = request.args.get("q",         "").strip()
    client    = request.args.get("client",    "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to",   "").strip()
    task      = request.args.get("task",      "").strip()

    if q:
        mask = df.apply(
            lambda row: row.astype(str).str.contains(q, case=False, na=False).any(),
            axis=1,
        )
        df = df[mask]
    if client:
        df = df[df["שם לקוח"].str.contains(client, case=False, na=False)]
    if date_from:
        df = df[df["תאריך"] >= date_from]
    if date_to:
        df = df[df["תאריך"] <= date_to]
    if task:
        df = df[df["עבודה"] == task]
    return df


def _autocomplete_lists(df: pd.DataFrame) -> dict:
    return {
        "client_list":   sorted(df["שם לקוח"].dropna().unique().tolist()),
        "field_list":    sorted(df["שם חלקה"].dropna().unique().tolist()),
        "crop_list":     sorted(df["גידול"].dropna().replace("", pd.NA).dropna().unique().tolist()),
        "operator_list": sorted(df["מפעיל"].dropna().unique().tolist()),
        "tool_list":     sorted(df["כלי"].dropna().unique().tolist()),
    }


# ── Manager routes ─────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    try:
        df = load_data_from_gsheet()
        total_count  = len(df)
        month_prefix = date.today().strftime("%Y-%m")
        month_count  = int(df["תאריך"].str.startswith(month_prefix).sum()) if total_count else 0
        top_client   = df["שם לקוח"].mode()[0] if total_count else "—"
        top_task     = df["עבודה"].mode()[0] if total_count else "—"

        df = df.reset_index().rename(columns={"index": "_row_id"})
        df = df.sort_values(by="תאריך", ascending=False)
        df = _apply_filters(df)

        filtered_count = len(df)
        page           = request.args.get("page", 1, type=int)
        total_pages    = max(1, math.ceil(filtered_count / PAGE_SIZE))
        page           = max(1, min(page, total_pages))
        df             = df.iloc[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

        full_df       = load_data_from_gsheet()
        task_counts   = full_df["עבודה"].value_counts().to_dict()
        client_counts = full_df["שם לקוח"].value_counts().head(6).to_dict()
        auto          = _autocomplete_lists(full_df)

        return render_template(
            "index.html",
            records=df.to_dict(orient="records"),
            total_count=total_count,
            filtered_count=filtered_count,
            month_count=month_count,
            top_client=top_client,
            top_task=top_task,
            q_filter=request.args.get("q", "").strip(),
            client_filter=request.args.get("client", "").strip(),
            date_from=request.args.get("date_from", "").strip(),
            date_to=request.args.get("date_to", "").strip(),
            task_filter=request.args.get("task", "").strip(),
            task_options=sorted(VALID_TASKS),
            task_counts=task_counts,
            client_counts=client_counts,
            page=page,
            total_pages=total_pages,
            today=date.today().strftime("%Y-%m-%d"),
            **auto,
        )
    except Exception as e:
        return render_template(
            "index.html",
            records=[], total_count=0, filtered_count=0,
            month_count=0, top_client="—", top_task="—",
            q_filter="", client_filter="", date_from="", date_to="", task_filter="",
            task_options=[], task_counts={}, client_counts={},
            page=1, total_pages=1,
            today=date.today().strftime("%Y-%m-%d"),
            client_list=[], field_list=[], crop_list=[],
            operator_list=[], tool_list=[],
            error=str(e),
        )


@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    today = date.today().strftime("%Y-%m-%d")
    if request.method == "POST":
        try:
            entry = WorkEntry.from_form(request.form, entered_by="Web")
            create_entry(entry, "Web")
            flash("הרשומה נוספה בהצלחה ✅", "success")
            return redirect(url_for("index"))
        except ValueError as e:
            flash(f"שגיאת אימות: {e} ❌", "danger")
        except Exception as e:
            flash(f"שגיאה בשמירה: {e} ❌", "danger")
    prefill = {col: request.args.get(col, "") for col in COLUMNS if col != "מזין"}
    if not prefill.get("תאריך"):
        prefill["תאריך"] = today
    lists = {}
    try:
        lists = _autocomplete_lists(load_data_from_gsheet())
    except Exception:
        pass
    return render_template("add.html", today=today, prefill=prefill, **lists)


@app.route("/duplicate/<int:row_id>")
@login_required
def duplicate(row_id):
    try:
        df  = load_data_from_gsheet()
        row = df.iloc[row_id].to_dict()
        row["תאריך"] = date.today().strftime("%Y-%m-%d")
        qs = urlencode({k: v for k, v in row.items() if k != "מזין"})
        return redirect(f"/add?{qs}")
    except Exception:
        return redirect(url_for("add"))


@app.route("/edit/<int:row_id>", methods=["GET", "POST"])
@login_required
def edit(row_id):
    df = load_data_from_gsheet()
    if request.method == "POST":
        try:
            original_entered_by = df.at[row_id, "מזין"] if row_id < len(df) else "Web"
            entry = WorkEntry.from_form(request.form, entered_by=original_entered_by)
            edit_row_in_gsheet(row_id, entry)
            _log_audit("edit", "Web", f"row {row_id}: {entry.client} | {entry.date}")
            flash("הרשומה עודכנה בהצלחה ✅", "success")
            return redirect(url_for("index"))
        except ValueError as e:
            flash(f"שגיאת אימות: {e} ❌", "danger")
            try:
                lists = _autocomplete_lists(df)
                return render_template("edit.html", row=df.iloc[row_id].to_dict(), row_id=row_id, **lists)
            except Exception:
                pass
        except Exception as e:
            flash(f"שגיאה בעדכון: {e} ❌", "danger")
    try:
        lists = _autocomplete_lists(df)
        return render_template("edit.html", row=df.iloc[row_id].to_dict(), row_id=row_id, **lists)
    except Exception as e:
        return f"שגיאה בטעינת שורה: {e}"


@app.route("/delete/<int:row_id>", methods=["POST"])
@login_required
def delete(row_id):
    df = load_data_from_gsheet()
    detail = df.iloc[row_id].get("שם לקוח", str(row_id)) if row_id < len(df) else str(row_id)
    delete_row_in_gsheet(row_id)
    _log_audit("delete", "Web", f"row {row_id}: {detail}")
    flash("הרשומה נמחקה ✅", "success")
    return redirect(url_for("index"))


@app.route("/bulk-delete", methods=["POST"])
@login_required
def bulk_delete():
    row_ids = [int(r) for r in request.form.getlist("row_ids")]
    if not row_ids:
        flash("לא נבחרו רשומות ⚠️", "warning")
        return redirect(url_for("index"))
    bulk_delete_rows_in_gsheet(row_ids)
    _log_audit("bulk-delete", "Web", f"{len(row_ids)} rows: {row_ids}")
    flash(f"{len(row_ids)} רשומות נמחקו ✅", "success")
    return redirect(url_for("index"))


@app.route("/summary")
@login_required
def summary():
    try:
        df = load_data_from_gsheet()
        if df.empty:
            return render_template("summary.html", monthly=[], client_totals=[], task_types=[])
        df["חודש"] = pd.to_datetime(df["תאריך"], errors="coerce").dt.strftime("%Y-%m")
        df = df.dropna(subset=["חודש"])
        pivot = df.groupby(["חודש", "עבודה"]).size().unstack(fill_value=0)
        task_types = list(pivot.columns)
        pivot["סה\"כ"] = pivot.sum(axis=1)
        monthly = pivot.reset_index().sort_values("חודש", ascending=False).to_dict(orient="records")
        client_totals = (
            df.groupby("שם לקוח").size()
            .sort_values(ascending=False).head(15)
            .reset_index().rename(columns={0: "סה\"כ"})
            .to_dict(orient="records")
        )
        return render_template("summary.html", monthly=monthly, client_totals=client_totals, task_types=task_types)
    except Exception as e:
        return render_template("summary.html", monthly=[], client_totals=[], task_types=[], error=str(e))


@app.route("/audit")
@login_required
def audit():
    entries = _read_audit_log(200)
    return render_template("audit.html", entries=entries)


@app.route("/print")
@login_required
def print_report():
    df = load_data_from_gsheet()
    df = _apply_filters(df)
    return render_template(
        "print_report.html",
        records=df.to_dict(orient="records"),
        client_filter=request.args.get("client", "").strip(),
        date_from=request.args.get("date_from", "").strip(),
        date_to=request.args.get("date_to", "").strip(),
        task_filter=request.args.get("task", "").strip(),
        generated=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


@app.route("/import", methods=["GET", "POST"])
@login_required
def import_data():
    if request.method == "POST":
        file = request.files.get("file")
        if file and file.filename.endswith(".xlsx"):
            try:
                new_df = pd.read_excel(file)
                existing_df = load_data_from_gsheet()
                key_cols = ["שם לקוח", "תאריך", "עבודה", "שם חלקה"]
                skipped = 0
                if not existing_df.empty:
                    existing_keys = set(
                        tuple(str(v) for v in row)
                        for row in existing_df[key_cols].values.tolist()
                    )
                    unique_rows, skip_rows = [], []
                    for _, row in new_df.iterrows():
                        key = tuple(str(row.get(c, "")) for c in key_cols)
                        (skip_rows if key in existing_keys else unique_rows).append(row)
                    skipped = len(skip_rows)
                    new_df = pd.DataFrame(unique_rows, columns=new_df.columns) if unique_rows else pd.DataFrame()
                if skipped:
                    flash(f"⚠️ {skipped} שורות כפולות דולגו", "warning")
                if not new_df.empty:
                    combined = pd.concat([existing_df, new_df], ignore_index=True) if not existing_df.empty else new_df
                    save_data_to_gsheet(combined)
                    _log_audit("import", "Web", f"{len(new_df)} rows imported, {skipped} skipped")
                    flash(f"{len(new_df)} שורות יובאו בהצלחה ✅", "success")
                    return redirect(url_for("index"))
                else:
                    flash("כל השורות בקובץ כבר קיימות ⚠️", "warning")
            except Exception as e:
                flash(f"שגיאה בייבוא: {e} ❌", "danger")
        else:
            flash("יש לבחור קובץ Excel תקני (.xlsx) ❌", "danger")
    return render_template("import.html")


@app.route("/export")
@login_required
def export():
    df = load_data_from_gsheet()
    if df.empty:
        flash("אין נתונים לייצוא ❌", "danger")
        return redirect(url_for("index"))
    df = _apply_filters(df)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Data")
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="gadash_data.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/export/csv")
@login_required
def export_csv():
    df = load_data_from_gsheet()
    if df.empty:
        flash("אין נתונים לייצוא ❌", "danger")
        return redirect(url_for("index"))
    df = _apply_filters(df)
    output = BytesIO()
    df.to_csv(output, index=False, encoding="utf-8-sig")
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="gadash_data.csv",
                     mimetype="text/csv; charset=utf-8-sig")


# ── REST API ───────────────────────────────────────────────────────────────────

@app.route("/api/docs")
@login_required
def api_docs():
    return render_template("api_docs.html")


@app.route("/api/entries")
@login_required
def api_entries():
    df = load_data_from_gsheet()
    df = _apply_filters(df)
    return jsonify(df.fillna("").to_dict(orient="records"))


@app.route("/api/entries/<int:row_id>", methods=["PATCH"])
@login_required
def api_patch_entry(row_id):
    data  = request.get_json(force=True, silent=True) or {}
    field = data.get("field", "")
    value = str(data.get("value", ""))
    editable = [c for c in COLUMNS if c != "מזין"]
    if field not in editable:
        return jsonify({"error": f"שדה לא תקין: {field}"}), 400
    df = load_data_from_gsheet()
    if row_id >= len(df):
        return jsonify({"error": "שורה לא קיימת"}), 404
    patch_cell_in_gsheet(row_id, field, value)
    _log_audit("edit-inline", "Web", f"row {row_id}: {field}={value}")
    return jsonify({"ok": True, "row_id": row_id, "field": field, "value": value})


# ── Worker management (manager only) ──────────────────────────────────────────

@app.route("/workers", methods=["GET", "POST"])
@login_required
def manage_workers():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pwd  = request.form.get("password", "").strip()
        if not name or not pwd:
            flash("שם וסיסמה הם שדות חובה ❌", "danger")
        elif len(pwd) < 4:
            flash("הסיסמה חייבת לכלול לפחות 4 תווים ❌", "danger")
        elif not _add_worker(name, pwd):
            flash(f"עובד בשם '{name}' כבר קיים ❌", "danger")
        else:
            flash(f"עובד '{name}' נוסף בהצלחה ✅", "success")
        return redirect(url_for("manage_workers"))
    return render_template("workers.html", workers=_load_workers())


@app.route("/workers/delete/<name>", methods=["POST"])
@login_required
def delete_worker(name):
    if _delete_worker(name):
        flash(f"עובד '{name}' נמחק ✅", "success")
    else:
        flash(f"עובד '{name}' לא נמצא ❌", "danger")
    return redirect(url_for("manage_workers"))


@app.route("/api/cache/invalidate", methods=["POST"])
@login_required
def api_cache_invalidate():
    _invalidate_cache()
    return jsonify({"ok": True})


@app.route("/webhook/<token>", methods=["POST"])
def telegram_webhook(token):
    if not _bot_module._telegram_app or token != os.environ.get("BOT_TOKEN"):
        return "forbidden", 403
    data = request.get_json(force=True, silent=True)
    if data and _bot_module._telegram_loop:
        from telegram import Update as TGUpdate
        update = TGUpdate.de_json(data, _bot_module._telegram_app.bot)
        asyncio.run_coroutine_threadsafe(
            _bot_module._telegram_app.process_update(update),
            _bot_module._telegram_loop,
        )
    return "ok"


# ── Worker portal ──────────────────────────────────────────────────────────────

@app.route("/worker/login")
def worker_login():
    return redirect(url_for("login"))


@app.route("/worker/logout")
def worker_logout():
    session.pop("worker_logged_in", None)
    session.pop("worker_name", None)
    return redirect(url_for("login"))


@app.route("/worker/change-password", methods=["POST"])
@worker_required
def worker_change_password():
    global _worker_password
    old  = request.form.get("old_password", "")
    new1 = request.form.get("new_password", "")
    new2 = request.form.get("confirm_password", "")
    if old != _worker_password:
        flash("הסיסמה הנוכחית שגויה ❌", "danger")
    elif new1 != new2:
        flash("הסיסמאות החדשות אינן תואמות ❌", "danger")
    elif len(new1) < 4:
        flash("הסיסמה חייבת לכלול לפחות 4 תווים ❌", "danger")
    else:
        _worker_password = new1
        save_passwords_to_sheet(_current_password, _worker_password)
        flash("הסיסמה שונתה בהצלחה ✅", "success")
    return redirect(url_for("worker_index"))


@app.route("/worker", methods=["GET", "POST"])
@worker_required
def worker_index():
    worker_name = session.get("worker_name", "עובד")
    today = date.today().strftime("%Y-%m-%d")
    lists = {}
    try:
        lists = _autocomplete_lists(load_data_from_gsheet())
    except Exception:
        pass

    if request.method == "POST":
        try:
            entry = WorkEntry.from_form(request.form, entered_by=worker_name)
            create_entry(entry, worker_name)
            flash("הרשומה נוספה בהצלחה ✅", "success")
        except ValueError as e:
            flash(f"שגיאת אימות: {e} ❌", "danger")
        except Exception as e:
            flash(f"שגיאה בשמירה: {e} ❌", "danger")
        return redirect(url_for("worker_index"))

    try:
        df = load_data_from_gsheet()
        my_df = df[df["מזין"].str.contains(worker_name, case=False, na=False)]
        recent = my_df.tail(20).sort_values("תאריך", ascending=False).to_dict(orient="records")
        my_count = len(my_df)
    except Exception:
        recent, my_count = [], 0

    return render_template("worker_index.html",
                           worker_name=worker_name, today=today,
                           recent=recent, my_count=my_count, **lists)


# ── Reports ────────────────────────────────────────────────────────────────────

@app.route("/client-report")
@login_required
def client_report():
    client_name = request.args.get("client", "").strip()
    date_from   = request.args.get("date_from", "").strip()
    date_to     = request.args.get("date_to", "").strip()
    try:
        df = load_data_from_gsheet()
        auto = _autocomplete_lists(df)
        if not client_name:
            return render_template("client_report.html", client_name="", records=[],
                                   total_hours=0, total_entries=0, date_range="",
                                   field_hours=[], crop_hours=[], task_counts=[],
                                   monthly_hours=[], date_from=date_from, date_to=date_to,
                                   **auto)
        cdf = df[df["שם לקוח"].str.contains(client_name, case=False, na=False)].copy()
        if date_from:
            cdf = cdf[cdf["תאריך"] >= date_from]
        if date_to:
            cdf = cdf[cdf["תאריך"] <= date_to]
        cdf["_שעות"] = pd.to_numeric(cdf["שעות"], errors="coerce").fillna(0)
        total_hours   = float(cdf["_שעות"].sum())
        total_entries = len(cdf)
        date_range    = f"{cdf['תאריך'].min()} — {cdf['תאריך'].max()}" if total_entries else "—"

        field_hours = (
            cdf.groupby("שם חלקה")["_שעות"].sum()
            .reset_index().rename(columns={"שם חלקה": "label", "_שעות": "hours"})
            .sort_values("hours", ascending=False).to_dict(orient="records")
        )
        crop_hours = (
            cdf.groupby("גידול")["_שעות"].sum()
            .reset_index().rename(columns={"גידול": "label", "_שעות": "hours"})
            .sort_values("hours", ascending=False).to_dict(orient="records")
        )
        task_counts = (
            cdf["עבודה"].value_counts()
            .reset_index().rename(columns={"עבודה": "label", "count": "cnt"})
            .to_dict(orient="records")
        )
        cdf["_month"] = pd.to_datetime(cdf["תאריך"], errors="coerce").dt.strftime("%Y-%m")
        monthly_hours = (
            cdf.groupby("_month").agg(entries=("שם לקוח", "count"), hours=("_שעות", "sum"))
            .reset_index().rename(columns={"_month": "month"})
            .sort_values("month").to_dict(orient="records")
        )
        records = cdf.sort_values("תאריך", ascending=False).to_dict(orient="records")
        return render_template("client_report.html",
                               client_name=client_name, records=records,
                               total_hours=total_hours, total_entries=total_entries,
                               date_range=date_range, field_hours=field_hours,
                               crop_hours=crop_hours, task_counts=task_counts,
                               monthly_hours=monthly_hours,
                               date_from=date_from, date_to=date_to, **auto)
    except Exception as e:
        return render_template("client_report.html", client_name=client_name, records=[],
                               total_hours=0, total_entries=0, date_range="",
                               field_hours=[], crop_hours=[], task_counts=[],
                               monthly_hours=[], date_from=date_from, date_to=date_to,
                               client_list=[], field_list=[], crop_list=[],
                               operator_list=[], tool_list=[], error=str(e))


@app.route("/field-report")
@login_required
def field_report():
    try:
        df = load_data_from_gsheet()
        if df.empty:
            return render_template("field_report.html",
                                   rows=[], crop_pivot=[], crops=[], field_totals=[],
                                   crop_totals=[], total_hours=0,
                                   date_from="", date_to="", client_filter="",
                                   client_list=[], field_list=[], crop_list=[])

        client_filter = request.args.get("client", "").strip()
        date_from     = request.args.get("date_from", "").strip()
        date_to       = request.args.get("date_to", "").strip()

        fdf = df.copy()
        if client_filter:
            fdf = fdf[fdf["שם לקוח"].str.contains(client_filter, case=False, na=False)]
        if date_from:
            fdf = fdf[fdf["תאריך"] >= date_from]
        if date_to:
            fdf = fdf[fdf["תאריך"] <= date_to]

        fdf["_שעות"] = pd.to_numeric(fdf["שעות"], errors="coerce").fillna(0)
        fdf["גידול_label"] = fdf["גידול"].fillna("").replace("", "לא צוין")
        fdf["שם חלקה_label"] = fdf["שם חלקה"].fillna("").replace("", "לא צוין")

        field_totals = (
            fdf.groupby("שם חלקה_label")
            .agg(עבודות=("שם לקוח", "count"), שעות=("_שעות", "sum"))
            .reset_index().rename(columns={"שם חלקה_label": "שם חלקה"})
            .sort_values("שעות", ascending=False).to_dict(orient="records")
        )
        crop_totals = (
            fdf.groupby("גידול_label")
            .agg(עבודות=("שם לקוח", "count"), שעות=("_שעות", "sum"))
            .reset_index().rename(columns={"גידול_label": "גידול"})
            .sort_values("שעות", ascending=False).to_dict(orient="records")
        )
        pivot = fdf.pivot_table(
            index="שם חלקה_label", columns="גידול_label",
            values="_שעות", aggfunc="sum", fill_value=0
        )
        crops = list(pivot.columns)
        pivot["סה\"כ"] = pivot.sum(axis=1)
        pivot = pivot.reset_index().rename(columns={"שם חלקה_label": "שם חלקה"})
        crop_pivot = pivot.to_dict(orient="records")
        total_hours = float(fdf["_שעות"].sum())
        auto = _autocomplete_lists(df)
        return render_template(
            "field_report.html",
            crop_pivot=crop_pivot, crops=crops,
            field_totals=field_totals, crop_totals=crop_totals,
            total_hours=total_hours, date_from=date_from, date_to=date_to,
            client_filter=client_filter, **auto,
        )
    except Exception as e:
        return render_template("field_report.html",
                               rows=[], crop_pivot=[], crops=[], field_totals=[],
                               crop_totals=[], total_hours=0,
                               date_from="", date_to="", client_filter="",
                               client_list=[], field_list=[], crop_list=[],
                               error=str(e))


@app.route("/field-report/print")
@login_required
def field_report_print():
    try:
        df = load_data_from_gsheet()
        client_filter = request.args.get("client", "").strip()
        date_from     = request.args.get("date_from", "").strip()
        date_to       = request.args.get("date_to", "").strip()
        fdf = df.copy()
        if client_filter:
            fdf = fdf[fdf["שם לקוח"].str.contains(client_filter, case=False, na=False)]
        if date_from:
            fdf = fdf[fdf["תאריך"] >= date_from]
        if date_to:
            fdf = fdf[fdf["תאריך"] <= date_to]
        fdf["_שעות"] = pd.to_numeric(fdf["שעות"], errors="coerce").fillna(0)
        fdf["גידול_label"] = fdf["גידול"].fillna("").replace("", "לא צוין")
        fdf["שם חלקה_label"] = fdf["שם חלקה"].fillna("").replace("", "לא צוין")
        field_totals = (
            fdf.groupby("שם חלקה_label")
            .agg(עבודות=("שם לקוח", "count"), שעות=("_שעות", "sum"))
            .reset_index().rename(columns={"שם חלקה_label": "שם חלקה"})
            .sort_values("שעות", ascending=False).to_dict(orient="records")
        )
        crop_totals = (
            fdf.groupby("גידול_label")
            .agg(עבודות=("שם לקוח", "count"), שעות=("_שעות", "sum"))
            .reset_index().rename(columns={"גידול_label": "גידול"})
            .sort_values("שעות", ascending=False).to_dict(orient="records")
        )
        pivot = fdf.pivot_table(
            index="שם חלקה_label", columns="גידול_label",
            values="_שעות", aggfunc="sum", fill_value=0
        )
        crops = list(pivot.columns)
        pivot['סה"כ'] = pivot.sum(axis=1)
        pivot = pivot.reset_index().rename(columns={"שם חלקה_label": "שם חלקה"})
        crop_pivot = pivot.to_dict(orient="records")
        total_hours = float(fdf["_שעות"].sum())
        return render_template("field_report_print.html",
                               crop_pivot=crop_pivot, crops=crops,
                               field_totals=field_totals, crop_totals=crop_totals,
                               total_hours=total_hours, date_from=date_from, date_to=date_to,
                               client_filter=client_filter,
                               generated=datetime.now().strftime("%d/%m/%Y %H:%M"))
    except Exception as e:
        return f"שגיאה: {e}"


# ── AI Summary ─────────────────────────────────────────────────────────────────

@app.route("/api/ai-summary", methods=["POST"])
@login_required
def api_ai_summary():
    try:
        df = load_data_from_gsheet()
        if df.empty:
            return jsonify({"summary": "אין נתונים לניתוח."})

        df["_שעות"] = pd.to_numeric(df["שעות"], errors="coerce").fillna(0)
        df["_תאריך"] = pd.to_datetime(df["תאריך"], errors="coerce")

        now = datetime.now()
        cur_m, cur_y = now.month, now.year
        prev_m = cur_m - 1 if cur_m > 1 else 12
        prev_y = cur_y if cur_m > 1 else cur_y - 1

        this_m = df[(df["_תאריך"].dt.month == cur_m) & (df["_תאריך"].dt.year == cur_y)]
        last_m = df[(df["_תאריך"].dt.month == prev_m) & (df["_תאריך"].dt.year == prev_y)]

        def _mode(series):
            m = series.dropna().replace("", None).dropna().mode()
            return m.iloc[0] if not m.empty else "—"

        stats = {
            "month_label":      now.strftime("%m/%Y"),
            "jobs":             int(len(this_m)),
            "hours":            round(float(this_m["_שעות"].sum()), 1),
            "prev_jobs":        int(len(last_m)),
            "prev_hours":       round(float(last_m["_שעות"].sum()), 1),
            "top_client":       _mode(this_m["שם לקוח"]),
            "top_task":         _mode(this_m["עבודה"]),
            "top_operator":     _mode(this_m["מפעיל"]),
            "active_clients":   int(this_m["שם לקוח"].nunique()),
            "active_fields":    int(this_m["שם חלקה"].nunique()),
        }

        gemini_key = os.environ.get("GEMINI_API_KEY")
        if gemini_key:
            prompt = f"""אתה מנהל חקלאי מנוסה. כתוב סיכום חודשי מקצועי בעברית (5-6 משפטים בלבד) בהתבסס על:

חודש {stats['month_label']}:
- עבודות: {stats['jobs']} (חודש קודם: {stats['prev_jobs']})
- שעות: {stats['hours']} (חודש קודם: {stats['prev_hours']})
- לקוח מוביל: {stats['top_client']}
- עבודה שכיחה: {stats['top_task']}
- מפעיל מוביל: {stats['top_operator']}
- לקוחות פעילים: {stats['active_clients']}
- חלקות פעילות: {stats['active_fields']}

כלול: השוואה לחודש הקודם, נקודת חוזק אחת, נקודת חולשה אחת, והמלצה מעשית אחת. כתוב בגוף ראשון רבים ("בחנו", "ראינו")."""
            try:
                import google.generativeai as _genai
                _genai.configure(api_key=gemini_key)
                _model = _genai.GenerativeModel("gemini-2.5-flash")
                r = _model.generate_content(prompt)
                summary = r.text
            except Exception as ai_err:
                summary = f"שגיאת AI: {ai_err}"
        else:
            trend     = "עלייה" if stats["jobs"] >= stats["prev_jobs"] else "ירידה"
            diff_jobs = abs(stats["jobs"] - stats["prev_jobs"])
            diff_hrs  = round(abs(stats["hours"] - stats["prev_hours"]), 1)
            rec = ("כדאי לשקול הגדלת כוח אדם לעמידה בקצב הגובר."
                   if stats["jobs"] > stats["prev_jobs"]
                   else "מומלץ לפנות ללקוחות שלא טופלו החודש ולתזמן עבודות נוספות.")
            summary = (
                f"בחודש {stats['month_label']} בוצעו **{stats['jobs']} עבודות** — "
                f"{trend} של {diff_jobs} עבודות לעומת החודש הקודם ({stats['prev_jobs']}). "
                f"סך שעות העבודה עמד על **{stats['hours']:.0f} שעות** "
                f"({'גידול' if stats['hours'] >= stats['prev_hours'] else 'ירידה'} של {diff_hrs} שעות).\n\n"
                f"הלקוח המוביל החודש היה **{stats['top_client']}**, "
                f"עבודת ה**{stats['top_task']}** הייתה הנפוצה ביותר, "
                f"והמפעיל הפעיל ביותר — **{stats['top_operator']}**. "
                f"עסקנו עם **{stats['active_clients']} לקוחות פעילים** ב-**{stats['active_fields']} חלקות**.\n\n"
                f"**המלצה:** {rec}\n\n"
                f"_⚠️ מצב הדגמה — חבר מפתח Gemini לסיכום AI מלא_"
            )

        return jsonify({"summary": summary, "stats": stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Field map ──────────────────────────────────────────────────────────────────

@app.route("/fields-map")
@login_required
def fields_map():
    return render_template("fields_map.html")


@app.route("/api/fields")
@login_required
def api_fields():
    try:
        df = load_data_from_gsheet()
        coords = _load_field_coords()
        if df.empty:
            return jsonify([])

        df["_שעות"] = pd.to_numeric(df["שעות"], errors="coerce").fillna(0)
        df["שם חלקה"] = df["שם חלקה"].fillna("").str.strip()
        df = df[df["שם חלקה"] != ""]

        result = []
        for field_name, grp in df.groupby("שם חלקה"):
            crops   = grp["גידול"].dropna().replace("", None).dropna()
            clients = grp["שם לקוח"].dropna()
            dates   = grp["תאריך"].dropna()
            result.append({
                "name":      field_name,
                "hours":     round(float(grp["_שעות"].sum()), 1),
                "jobs":      int(len(grp)),
                "crop":      crops.mode().iloc[0] if not crops.empty else "",
                "client":    clients.mode().iloc[0] if not clients.empty else "",
                "last_date": dates.max() if not dates.empty else "",
                "lat":       coords[field_name]["lat"] if field_name in coords else None,
                "lng":       coords[field_name]["lng"] if field_name in coords else None,
            })

        result.sort(key=lambda x: x["hours"], reverse=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fields/coords", methods=["POST"])
@login_required
def api_fields_coords():
    data = request.get_json(silent=True)
    if not data or "name" not in data or "lat" not in data or "lng" not in data:
        return jsonify({"error": "missing fields"}), 400
    try:
        _save_field_coord(str(data["name"]), float(data["lat"]), float(data["lng"]))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    try:
        df  = load_data_from_gsheet()
        cls = sorted(df["שם לקוח"].dropna().unique().tolist()) if not df.empty else []
    except Exception:
        cls = []
    return render_template("dashboard.html", client_list=cls)


@app.route("/api/dashboard")
@login_required
def api_dashboard():
    try:
        df = load_data_from_gsheet()

        empty_resp = {
            "kpis": {"total": 0, "this_month": 0, "prev_month": 0,
                     "hours_this_month": 0.0, "hours_prev_month": 0.0,
                     "active_clients": 0, "active_clients_prev": 0,
                     "total_hours": 0.0, "avg_hours": 0.0},
            "daily_trend": [], "task_dist": {}, "top_clients": [],
            "top_fields": [], "crop_hours": [], "monthly_trend": [],
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }
        if df.empty:
            return jsonify(empty_resp)

        date_from = request.args.get("from", "")
        date_to   = request.args.get("to",   "")

        now          = datetime.now()
        cur_m, cur_y = now.month, now.year
        prev_m       = cur_m - 1 if cur_m > 1 else 12
        prev_y       = cur_y if cur_m > 1 else cur_y - 1
        month_prefix = now.strftime("%Y-%m")
        prev_prefix  = f"{prev_y:04d}-{prev_m:02d}"

        this_m = df[df["תאריך"].str.startswith(month_prefix, na=False)].copy()
        last_m = df[df["תאריך"].str.startswith(prev_prefix,  na=False)].copy()
        this_m["_h"] = pd.to_numeric(this_m["שעות"], errors="coerce").fillna(0)
        last_m["_h"] = pd.to_numeric(last_m["שעות"], errors="coerce").fillna(0)

        cdf = df.copy()
        if date_from:
            cdf = cdf[cdf["תאריך"] >= date_from]
        if date_to:
            cdf = cdf[cdf["תאריך"] <= date_to]

        cdf["_h"] = pd.to_numeric(cdf["שעות"], errors="coerce").fillna(0)
        cdf["_d"] = pd.to_datetime(cdf["תאריך"], errors="coerce")

        kpis = {
            "total":               len(cdf),
            "this_month":          len(this_m),
            "prev_month":          len(last_m),
            "hours_this_month":    round(float(this_m["_h"].sum()), 1),
            "hours_prev_month":    round(float(last_m["_h"].sum()), 1),
            "active_clients":      int(this_m["שם לקוח"].nunique()),
            "active_clients_prev": int(last_m["שם לקוח"].nunique()),
            "total_hours":         round(float(cdf["_h"].sum()), 1),
            "avg_hours": round(float(cdf.loc[cdf["_h"] > 0, "_h"].mean()), 1)
                         if (cdf["_h"] > 0).any() else 0.0,
        }

        cdf_v = cdf.dropna(subset=["_d"]).copy()
        daily = (
            cdf_v.groupby(cdf_v["_d"].dt.strftime("%Y-%m-%d"))
            .agg(entries=("שם לקוח", "count"), hours=("_h", "sum"))
            .reset_index().rename(columns={"_d": "date"})
            .sort_values("date").tail(60)
        )
        daily["hours"] = daily["hours"].round(1)

        task_dist = cdf["עבודה"].value_counts().to_dict()

        top_clients = (
            cdf.groupby("שם לקוח")
            .agg(count=("עבודה", "count"), hours=("_h", "sum"))
            .reset_index()
            .sort_values("count", ascending=False).head(10)
            .rename(columns={"שם לקוח": "name"})
        )
        top_clients["hours"] = top_clients["hours"].round(1)

        fdf = cdf[cdf["שם חלקה"].fillna("").str.strip() != ""]
        top_fields = (
            fdf.groupby("שם חלקה")
            .agg(hours=("_h", "sum"), entries=("שם לקוח", "count"))
            .reset_index()
            .sort_values("hours", ascending=False).head(10)
            .rename(columns={"שם חלקה": "name"})
        )
        top_fields["hours"] = top_fields["hours"].round(1)

        crp = cdf[cdf["גידול"].fillna("").str.strip() != ""]
        crop_h = (
            crp.groupby("גידול")["_h"].sum()
            .reset_index()
            .sort_values("_h", ascending=False).head(8)
            .rename(columns={"גידול": "name", "_h": "hours"})
        )
        crop_h["hours"] = crop_h["hours"].round(1)

        cdf_v["_month"] = cdf_v["_d"].dt.strftime("%Y-%m")
        monthly = (
            cdf_v.groupby("_month")
            .agg(entries=("שם לקוח", "count"), hours=("_h", "sum"))
            .reset_index().rename(columns={"_month": "month"})
            .sort_values("month").tail(12)
        )
        monthly["hours"] = monthly["hours"].round(1)

        return jsonify({
            "kpis":          kpis,
            "daily_trend":   daily.to_dict(orient="records"),
            "task_dist":     task_dist,
            "top_clients":   top_clients.to_dict(orient="records"),
            "top_fields":    top_fields.to_dict(orient="records"),
            "crop_hours":    crop_h.to_dict(orient="records"),
            "monthly_trend": monthly.to_dict(orient="records"),
            "updated_at":    datetime.now().strftime("%H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Background threads & entry point ──────────────────────────────────────────

_audit_flush_thread = threading.Thread(target=_flush_audit_to_sheets, daemon=True)
_audit_flush_thread.start()

if os.environ.get("BOT_TOKEN"):
    _bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    _bot_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
