import asyncio
import logging
import math
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO
from urllib.parse import urlencode

import click
import pandas as pd
from dotenv import load_dotenv
from flask import (Flask, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

try:
    import google.generativeai as _genai
except ImportError:
    _genai = None

import gadash.bot as _bot_module
from gadash import whatsapp
from gadash.auth import change_manager_password, change_worker_password, verify_manager, verify_worker
from gadash.bot import start_telegram_bot
from gadash.models import COLUMNS, VALID_TASKS, WorkEntry
from gadash.service import create_entry
from gadash.db import (
    append_work_entry, bulk_delete_work_entries, bulk_insert_work_entries,
    calculate_polygon_dunam_area, delete_field,
    delete_work_entry, edit_work_entry,
    load_pins, load_polygons, load_rates, load_work_entries,
    log_audit, patch_work_entry_cell, read_audit_log,
    save_pin, save_polygon, save_rates,
)
from gadash.workers import (
    _add_worker, _delete_worker, _load_workers,
    _verify_worker,
)
from gadash.tenancy import current_tenant_id
from flask_migrate import Migrate
from gadash.models_db import Manager, db

PAGE_SIZE = 50
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gadash-dev-secret-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

_logger = logging.getLogger(__name__)

if app.secret_key == "gadash-dev-secret-key":
    _logger.warning("[SECURITY] SECRET_KEY is the insecure default — set SECRET_KEY env var in production!")

# ── Postgres (multi-tenant migration, in progress — no route reads from this
# yet; see gadash/models_db.py and the migration plan) ─────────────────────────
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "postgresql:///gadash_dev"
).replace("postgres://", "postgresql://", 1)  # Heroku/Fly-style URLs use the old scheme name
db.init_app(app)
migrate = Migrate(app, db)


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
    if attempts:
        _login_attempts[ip] = attempts
    else:
        _login_attempts.pop(ip, None)
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
            slug = request.form.get("tenant_slug", "").strip()
            result = verify_worker(slug, name, pwd)
            if result:
                tenant_id, worker_name = result
                session.permanent = True
                session["worker_logged_in"] = True
                session["worker_name"]      = worker_name
                session["tenant_id"]        = tenant_id
                return redirect(url_for("worker_index"))
            _record_attempt(ip)
            flash("קוד חברה, שם עובד או סיסמה שגויים ❌", "danger")
            return render_template("login.html", selected_role="worker", form_name=name, form_slug=slug)
        else:
            username = request.form.get("username", "").strip()
            result = verify_manager(username, pwd)
            if result is not None:
                tenant_id, manager_id = result
                session.permanent = True
                session["logged_in"] = True
                session["tenant_id"] = tenant_id
                session["manager_id"] = manager_id
                return redirect(url_for("index"))
            _record_attempt(ip)
            remaining = _LOGIN_MAX - len(_login_attempts.get(ip, []))
            flash(f"שם משתמש או סיסמה שגויים ❌ ({remaining} ניסיונות נותרו)", "danger")
            return render_template("login.html", selected_role="manager", form_username=username)
    return render_template("login.html", selected_role="manager")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/health")
def health():
    try:
        db.session.execute(db.select(1))
        return jsonify({"status": "ok", "database": "connected"})
    except Exception as e:
        return jsonify({"status": "degraded", "database": str(e)}), 503


@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html")


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old  = request.form.get("old_password", "")
        new1 = request.form.get("new_password", "")
        new2 = request.form.get("confirm_password", "")
        if new1 != new2:
            flash("הסיסמאות החדשות אינן תואמות ❌", "danger")
        elif len(new1) < 4:
            flash("הסיסמה חייבת לכלול לפחות 4 תווים ❌", "danger")
        elif not change_manager_password(session["manager_id"], old, new1):
            flash("הסיסמה הנוכחית שגויה ❌", "danger")
        else:
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
        q_safe = re.escape(q)
        mask = df.apply(
            lambda row: row.astype(str).str.contains(q_safe, case=False, na=False).any(),
            axis=1,
        )
        df = df[mask]
    if client:
        df = df[df["שם לקוח"].str.contains(re.escape(client), case=False, na=False)]
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
        tenant_id = current_tenant_id()
        df = load_work_entries(tenant_id)
        total_count  = len(df)
        month_prefix = date.today().strftime("%Y-%m")
        month_count  = int(df["תאריך"].str.startswith(month_prefix).sum()) if total_count else 0
        top_client   = df["שם לקוח"].mode()[0] if total_count else "—"
        top_task     = df["עבודה"].mode()[0] if total_count else "—"

        df = df.sort_values(by="תאריך", ascending=False)
        df = _apply_filters(df)

        filtered_count = len(df)
        page           = request.args.get("page", 1, type=int)
        total_pages    = max(1, math.ceil(filtered_count / PAGE_SIZE))
        page           = max(1, min(page, total_pages))
        df             = df.iloc[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

        full_df       = load_work_entries(tenant_id)
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
            create_entry(current_tenant_id(), entry, "Web")
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
        lists = _autocomplete_lists(load_work_entries(current_tenant_id()))
    except Exception:
        pass
    return render_template("add.html", today=today, prefill=prefill, **lists)


@app.route("/duplicate/<int:row_id>")
@login_required
def duplicate(row_id):
    try:
        df  = load_work_entries(current_tenant_id())
        row = df[df["_row_id"] == row_id].iloc[0].to_dict()
        row["תאריך"] = date.today().strftime("%Y-%m-%d")
        qs = urlencode({k: v for k, v in row.items() if k not in ("מזין", "_row_id")})
        return redirect(f"/add?{qs}")
    except Exception:
        return redirect(url_for("add"))


@app.route("/edit/<int:row_id>", methods=["GET", "POST"])
@login_required
def edit(row_id):
    tenant_id = current_tenant_id()
    df = load_work_entries(tenant_id)
    match = df[df["_row_id"] == row_id]
    if request.method == "POST":
        try:
            if match.empty:
                flash("הרשומה כבר לא קיימת — ייתכן שנמחקה על ידי משתמש אחר ❌", "danger")
                return redirect(url_for("index"))
            original_entered_by = match.iloc[0]["מזין"]
            entry = WorkEntry.from_form(request.form, entered_by=original_entered_by)
            edit_work_entry(tenant_id, row_id, entry)
            log_audit(tenant_id, "edit", "Web", f"row {row_id}: {entry.client} | {entry.date}")
            flash("הרשומה עודכנה בהצלחה ✅", "success")
            return redirect(url_for("index"))
        except ValueError as e:
            flash(f"שגיאת אימות: {e} ❌", "danger")
            try:
                lists = _autocomplete_lists(df)
                return render_template("edit.html", row=match.iloc[0].to_dict(), row_id=row_id, **lists)
            except Exception:
                pass
        except Exception as e:
            flash(f"שגיאה בעדכון: {e} ❌", "danger")
    try:
        lists = _autocomplete_lists(df)
        return render_template("edit.html", row=match.iloc[0].to_dict(), row_id=row_id, **lists)
    except Exception as e:
        return f"שגיאה בטעינת שורה: {e}"


@app.route("/delete/<int:row_id>", methods=["POST"])
@login_required
def delete(row_id):
    tenant_id = current_tenant_id()
    try:
        df = load_work_entries(tenant_id)
        match = df[df["_row_id"] == row_id]
        if match.empty:
            flash("הרשומה כבר לא קיימת — ייתכן שנמחקה על ידי משתמש אחר ⚠️", "warning")
            return redirect(url_for("index"))
        detail = match.iloc[0].get("שם לקוח", str(row_id))
        delete_work_entry(tenant_id, row_id)
        log_audit(tenant_id, "delete", "Web", f"row {row_id}: {detail}")
        flash("הרשומה נמחקה ✅", "success")
    except Exception as e:
        flash(f"שגיאה במחיקה: {e} ❌", "danger")
    return redirect(url_for("index"))


@app.route("/bulk-delete", methods=["POST"])
@login_required
def bulk_delete():
    tenant_id = current_tenant_id()
    row_ids = [int(r) for r in request.form.getlist("row_ids")]
    if not row_ids:
        flash("לא נבחרו רשומות ⚠️", "warning")
        return redirect(url_for("index"))
    bulk_delete_work_entries(tenant_id, row_ids)
    log_audit(tenant_id, "bulk-delete", "Web", f"{len(row_ids)} rows: {row_ids}")
    flash(f"{len(row_ids)} רשומות נמחקו ✅", "success")
    return redirect(url_for("index"))


@app.route("/summary")
@login_required
def summary():
    try:
        df = load_work_entries(current_tenant_id())
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
    entries = read_audit_log(current_tenant_id(), 200)
    return render_template("audit.html", entries=entries)


@app.route("/print")
@login_required
def print_report():
    df = load_work_entries(current_tenant_id())
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
        tenant_id = current_tenant_id()
        file = request.files.get("file")
        if file and file.filename.endswith(".xlsx"):
            file.seek(0, 2)
            file_size = file.tell()
            file.seek(0)
            if file_size > _MAX_UPLOAD_BYTES:
                flash(f"הקובץ גדול מדי — מקסימום 5 MB ❌", "danger")
                return render_template("import.html")
            try:
                raw_df = pd.read_excel(file)
                if "תאריך" in raw_df.columns:
                    # Excel date-formatted cells come back as Timestamp objects, not
                    # "YYYY-MM-DD" strings, which WorkEntry's date validation requires.
                    raw_df["תאריך"] = pd.to_datetime(raw_df["תאריך"], errors="coerce").dt.strftime("%Y-%m-%d")
                raw_df = raw_df.fillna("")
                valid_entries, invalid_count = [], 0
                for _, row in raw_df.iterrows():
                    try:
                        valid_entries.append(WorkEntry.from_dict(row.to_dict()))
                    except ValueError:
                        invalid_count += 1
                if invalid_count:
                    flash(f"⚠️ {invalid_count} שורות לא תקינות דולגו (תאריך/סוג עבודה/לקוח חסר)", "warning")
                existing_df = load_work_entries(tenant_id)
                key_cols = ["שם לקוח", "תאריך", "עבודה", "שם חלקה"]
                existing_keys = set(
                    tuple(str(v) for v in row)
                    for row in existing_df[key_cols].values.tolist()
                ) if not existing_df.empty else set()
                new_entries = [e for e in valid_entries
                               if (e.client, e.date, e.task, e.field_name) not in existing_keys]
                skipped = len(valid_entries) - len(new_entries)
                if skipped:
                    flash(f"⚠️ {skipped} שורות כפולות דולגו", "warning")
                if new_entries:
                    bulk_insert_work_entries(tenant_id, new_entries)
                    log_audit(tenant_id, "import", "Web", f"{len(new_entries)} rows imported, {skipped} skipped")
                    flash(f"{len(new_entries)} שורות יובאו בהצלחה ✅", "success")
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
    df = load_work_entries(current_tenant_id())
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
    df = load_work_entries(current_tenant_id())
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
    df = load_work_entries(current_tenant_id())
    df = _apply_filters(df)
    return jsonify(df.fillna("").to_dict(orient="records"))


@app.route("/api/entries/<int:row_id>", methods=["PATCH"])
@login_required
def api_patch_entry(row_id):
    tenant_id = current_tenant_id()
    data  = request.get_json(force=True, silent=True) or {}
    field = data.get("field", "")
    value = str(data.get("value", ""))
    editable = [c for c in COLUMNS if c != "מזין"]
    if field not in editable:
        return jsonify({"error": f"שדה לא תקין: {field}"}), 400
    try:
        patch_work_entry_cell(tenant_id, row_id, field, value)
    except ValueError:
        return jsonify({"error": "שורה לא קיימת"}), 404
    log_audit(tenant_id, "edit-inline", "Web", f"row {row_id}: {field}={value}")
    return jsonify({"ok": True, "row_id": row_id, "field": field, "value": value})


# ── Worker management (manager only) ──────────────────────────────────────────

@app.route("/workers", methods=["GET", "POST"])
@login_required
def manage_workers():
    tenant_id = current_tenant_id()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pwd  = request.form.get("password", "").strip()
        if not name or not pwd:
            flash("שם וסיסמה הם שדות חובה ❌", "danger")
        elif len(pwd) < 4:
            flash("הסיסמה חייבת לכלול לפחות 4 תווים ❌", "danger")
        elif not _add_worker(tenant_id, name, pwd):
            flash(f"עובד בשם '{name}' כבר קיים ❌", "danger")
        else:
            flash(f"עובד '{name}' נוסף בהצלחה ✅", "success")
        return redirect(url_for("manage_workers"))
    return render_template("workers.html", workers=_load_workers(tenant_id))


@app.route("/workers/delete/<name>", methods=["POST"])
@login_required
def delete_worker(name):
    if _delete_worker(current_tenant_id(), name):
        flash(f"עובד '{name}' נמחק ✅", "success")
    else:
        flash(f"עובד '{name}' לא נמצא ❌", "danger")
    return redirect(url_for("manage_workers"))


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


@app.route("/webhook/whatsapp", methods=["GET"])
def whatsapp_verify():
    """Meta's one-time webhook subscription handshake, run once when the
    webhook URL is configured in the Meta app dashboard."""
    challenge = whatsapp.verify_webhook(
        request.args.get("hub.mode", ""),
        request.args.get("hub.verify_token", ""),
        request.args.get("hub.challenge", ""),
    )
    if challenge is None:
        return "forbidden", 403
    return challenge


@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    for msg in whatsapp.parse_incoming(payload):
        if whatsapp.is_duplicate_message(msg["id"]):
            _logger.info("[WhatsApp] duplicate webhook delivery for message %s — skipping", msg["id"])
            continue
        audio_bytes = None
        if msg["audio_id"]:
            try:
                audio_bytes = whatsapp.download_media(msg["audio_id"])
            except Exception as e:
                _logger.warning("[WhatsApp] media download failed: %s", e)
                whatsapp.send_text_message(msg["phone"], "❌ לא הצלחתי להוריד את ההקלטה. נסה שוב.")
                continue
        reply = whatsapp.handle_message(msg["phone"], msg["text"], audio_bytes)
        whatsapp.send_text_message(msg["phone"], reply)
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
    old  = request.form.get("old_password", "")
    new1 = request.form.get("new_password", "")
    new2 = request.form.get("confirm_password", "")
    if new1 != new2:
        flash("הסיסמאות החדשות אינן תואמות ❌", "danger")
    elif len(new1) < 4:
        flash("הסיסמה חייבת לכלול לפחות 4 תווים ❌", "danger")
    elif not change_worker_password(current_tenant_id(), session["worker_name"], old, new1):
        flash("הסיסמה הנוכחית שגויה ❌", "danger")
    else:
        flash("הסיסמה שונתה בהצלחה ✅", "success")
    return redirect(url_for("worker_index"))


@app.route("/worker/undo-last", methods=["POST"])
@worker_required
def worker_undo_last():
    tenant_id   = current_tenant_id()
    worker_name = session.get("worker_name", "")
    try:
        df = load_work_entries(tenant_id)
        my = df[df["מזין"].str.strip().str.casefold() == worker_name.strip().casefold()]
        if my.empty:
            flash("אין עבודות למחיקה ❌", "danger")
            return redirect(url_for("worker_index"))
        last_row_id = int(my.iloc[-1]["_row_id"])
        last_client = str(my.iloc[-1].get("שם לקוח", ""))
        last_date   = str(my.iloc[-1].get("תאריך", ""))
        delete_work_entry(tenant_id, last_row_id)
        log_audit(tenant_id, "worker-undo", worker_name, f"row {last_row_id}: {last_client} | {last_date}")
        flash(f"הרשומה האחרונה נמחקה ✅ ({last_client} | {last_date})", "success")
    except Exception as e:
        flash(f"שגיאה: {e} ❌", "danger")
    return redirect(url_for("worker_index"))


@app.route("/worker", methods=["GET", "POST"])
@worker_required
def worker_index():
    worker_name = session.get("worker_name", "עובד")
    tenant_id = current_tenant_id()
    today = date.today().strftime("%Y-%m-%d")
    lists = {}
    try:
        lists = _autocomplete_lists(load_work_entries(tenant_id))
    except Exception:
        pass

    if request.method == "POST":
        try:
            entry = WorkEntry.from_form(request.form, entered_by=worker_name)
            create_entry(tenant_id, entry, worker_name)
            flash("הרשומה נוספה בהצלחה ✅", "success")
        except ValueError as e:
            flash(f"שגיאת אימות: {e} ❌", "danger")
        except Exception as e:
            flash(f"שגיאה בשמירה: {e} ❌", "danger")
        return redirect(url_for("worker_index"))

    try:
        df = load_work_entries(tenant_id)
        my_df = df[df["מזין"].str.strip().str.casefold() == worker_name.strip().casefold()]
        recent = my_df.tail(20).sort_values("תאריך", ascending=False).to_dict(orient="records")
        my_count = len(my_df)
    except Exception:
        recent, my_count = [], 0

    return render_template("worker_index.html",
                           worker_name=worker_name, today=today,
                           recent=recent, my_count=my_count,
                           task_options=sorted(VALID_TASKS),
                           **lists)


# ── Reports ────────────────────────────────────────────────────────────────────

@app.route("/client-report")
@login_required
def client_report():
    client_name = request.args.get("client", "").strip()
    date_from   = request.args.get("date_from", "").strip()
    date_to     = request.args.get("date_to", "").strip()
    try:
        df = load_work_entries(current_tenant_id())
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


@app.route("/client-report/billing")
@login_required
def client_billing_summary():
    """A printable per-client billing summary, priced from the /profit rates.

    Deliberately not called a "חשבונית" (invoice) — in Israel that word means
    a legally regulated tax document, which this isn't. This is meant to feed
    the numbers into the contractor's real invoicing/accounting system.
    """
    client_name = request.args.get("client", "").strip()
    date_from   = request.args.get("date_from", "").strip()
    date_to     = request.args.get("date_to", "").strip()
    if not client_name:
        return redirect(url_for("client_report"))
    try:
        df = load_work_entries(current_tenant_id())
        cdf = df[df["שם לקוח"].str.contains(client_name, case=False, na=False)].copy()
        if date_from:
            cdf = cdf[cdf["תאריך"] >= date_from]
        if date_to:
            cdf = cdf[cdf["תאריך"] <= date_to]
        cdf = cdf.sort_values("תאריך")

        rates = load_rates(current_tenant_id())
        cdf["_שעות"] = pd.to_numeric(cdf["שעות"], errors="coerce").fillna(0)
        cdf["_rate"] = cdf["עבודה"].apply(lambda t: rates.get(t, {}).get("revenue", 0.0))
        cdf["_total"] = cdf["_שעות"] * cdf["_rate"]

        unrated_tasks = sorted(set(
            cdf.loc[(cdf["_rate"] == 0) & (cdf["_שעות"] > 0), "עבודה"].dropna().unique()
        ))
        line_items = cdf.to_dict(orient="records")
        grand_total = float(cdf["_total"].sum())
        total_hours = float(cdf["_שעות"].sum())

        return render_template(
            "billing_summary.html",
            client_name=client_name, date_from=date_from, date_to=date_to,
            line_items=line_items, grand_total=grand_total, total_hours=total_hours,
            unrated_tasks=unrated_tasks,
            generated=datetime.now().strftime("%d/%m/%Y %H:%M"),
        )
    except Exception as e:
        return f"שגיאה בהפקת סיכום החיוב: {e}"


@app.route("/field-report")
@login_required
def field_report():
    try:
        df = load_work_entries(current_tenant_id())
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


@app.route("/profit", methods=["GET", "POST"])
@login_required
def profit():
    tenant_id = current_tenant_id()
    if request.method == "POST":
        rates = {}
        for task in VALID_TASKS:
            try:
                revenue = float(request.form.get(f"revenue_{task}", "") or 0)
            except ValueError:
                revenue = 0.0
            try:
                cost = float(request.form.get(f"cost_{task}", "") or 0)
            except ValueError:
                cost = 0.0
            rates[task] = {"revenue": revenue, "cost": cost}
        save_rates(tenant_id, rates)
        flash("התעריפים נשמרו בהצלחה ✅", "success")
        return redirect(url_for("profit"))

    rates     = load_rates(tenant_id)
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to", "").strip()
    try:
        df = load_work_entries(tenant_id)
        if date_from:
            df = df[df["תאריך"] >= date_from]
        if date_to:
            df = df[df["תאריך"] <= date_to]

        if df.empty:
            return render_template("profit.html", rates=rates, rows=[], totals=None,
                                    date_from=date_from, date_to=date_to)

        df = df.copy()
        df["_שעות"]   = pd.to_numeric(df["שעות"], errors="coerce").fillna(0)
        df["_revenue"] = df.apply(lambda r: r["_שעות"] * rates.get(r["עבודה"], {}).get("revenue", 0), axis=1)
        df["_cost"]    = df.apply(lambda r: r["_שעות"] * rates.get(r["עבודה"], {}).get("cost", 0), axis=1)
        df["_profit"]  = df["_revenue"] - df["_cost"]
        df["שם חלקה"]  = df["שם חלקה"].fillna("").str.strip().replace("", "לא צוין")

        grouped = (
            df.groupby("שם חלקה")
            .agg(hours=("_שעות", "sum"), revenue=("_revenue", "sum"),
                 cost=("_cost", "sum"), profit=("_profit", "sum"), jobs=("עבודה", "count"))
            .reset_index().rename(columns={"שם חלקה": "field"})
        )
        grouped["margin"] = grouped.apply(
            lambda r: round(r["profit"] / r["revenue"] * 100, 1) if r["revenue"] > 0 else 0.0, axis=1
        )
        grouped = grouped.sort_values("profit", ascending=False).round(2)
        rows = grouped.to_dict(orient="records")

        totals = {
            "hours":   round(float(df["_שעות"].sum()), 1),
            "revenue": round(float(df["_revenue"].sum()), 2),
            "cost":    round(float(df["_cost"].sum()), 2),
            "profit":  round(float(df["_profit"].sum()), 2),
        }
        return render_template("profit.html", rates=rates, rows=rows, totals=totals,
                                date_from=date_from, date_to=date_to)
    except Exception as e:
        return render_template("profit.html", rates=rates, rows=[], totals=None,
                                date_from=date_from, date_to=date_to, error=str(e))


@app.route("/field-report/print")
@login_required
def field_report_print():
    try:
        df = load_work_entries(current_tenant_id())
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
        df = load_work_entries(current_tenant_id())
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
        if gemini_key and _genai:
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
                _genai.configure(api_key=gemini_key)
                _model = _genai.GenerativeModel("gemini-3.6-flash")
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


@app.route("/api/fields", methods=["GET", "POST"])
@login_required
def api_fields():
    tenant_id = current_tenant_id()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        uid   = data.get("uid")
        name  = data.get("name")
        color = data.get("color", "")
        type_ = data.get("type")  # 'polygon' or 'pin'

        if not uid or not name or not type_:
            return jsonify({"error": "missing uid, name, or type"}), 400
        try:
            if type_ == "polygon":
                coords = data.get("coordinates")
                if not coords:
                    return jsonify({"error": "missing coordinates"}), 400
                save_polygon(tenant_id, uid, name, color, coords)
                log_audit(tenant_id, "save-polygon", "Web", f"field: {name}")
                return jsonify({"ok": True, "uid": uid, "area_dunam": calculate_polygon_dunam_area(coords)})
            elif type_ == "pin":
                lat = data.get("lat")
                lng = data.get("lng")
                if lat is None or lng is None:
                    return jsonify({"error": "missing lat/lng"}), 400
                save_pin(tenant_id, uid, name, color, float(lat), float(lng))
                log_audit(tenant_id, "save-pin", "Web", f"field: {name}")
                return jsonify({"ok": True, "uid": uid})
            else:
                return jsonify({"error": "invalid type"}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # GET — merge job stats with saved polygons/pins for the map
    try:
        df = load_work_entries(tenant_id)
        polys = {p["uid"]: p for p in load_polygons(tenant_id)}
        pins = {p["uid"]: p for p in load_pins(tenant_id)}

        if not df.empty:
            df["_שעות"] = pd.to_numeric(df["שעות"], errors="coerce").fillna(0)
            df["שם חלקה"] = df["שם חלקה"].fillna("").str.strip()
            df = df[df["שם חלקה"] != ""]

        result = []
        processed_names = set()

        if not df.empty:
            for field_name, grp in df.groupby("שם חלקה"):
                processed_names.add(field_name)
                poly = polys.get(field_name) or next((p for p in polys.values() if p["name"] == field_name), None)
                pin  = pins.get(field_name)  or next((p for p in pins.values()  if p["name"] == field_name), None)

                crops   = grp["גידול"].dropna().replace("", None).dropna()
                clients = grp["שם לקוח"].dropna()
                dates   = grp["תאריך"].dropna()

                result.append({
                    "uid":        (poly or pin or {}).get("uid"),
                    "name":       field_name,
                    "color":      (poly or pin or {}).get("color", ""),
                    "hours":      round(float(grp["_שעות"].sum()), 1),
                    "jobs":       int(len(grp)),
                    "crop":       crops.mode().iloc[0] if not crops.empty else "",
                    "client":     clients.mode().iloc[0] if not clients.empty else "",
                    "last_date":  dates.max() if not dates.empty else "",
                    "lat":        pin["lat"] if pin else None,
                    "lng":        pin["lng"] if pin else None,
                    "polygon":    poly["coordinates"] if poly else None,
                    "area_dunam": calculate_polygon_dunam_area(poly["coordinates"]) if poly else 0.0,
                })

        # Fields/points with a saved pin or polygon but no job history yet
        all_names = {p["name"] for p in polys.values()} | {p["name"] for p in pins.values()}
        for field_name in all_names - processed_names:
            poly = next((p for p in polys.values() if p["name"] == field_name), None)
            pin  = next((p for p in pins.values()  if p["name"] == field_name), None)
            result.append({
                "uid":        (poly or pin or {}).get("uid"),
                "name":       field_name,
                "color":      (poly or pin or {}).get("color", ""),
                "hours":      0.0,
                "jobs":       0,
                "crop":       "",
                "client":     "",
                "last_date":  "",
                "lat":        pin["lat"] if pin else None,
                "lng":        pin["lng"] if pin else None,
                "polygon":    poly["coordinates"] if poly else None,
                "area_dunam": calculate_polygon_dunam_area(poly["coordinates"]) if poly else 0.0,
            })

        result.sort(key=lambda x: x["hours"], reverse=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fields/<uid>", methods=["DELETE"])
@login_required
def api_fields_delete(uid):
    """Delete a field's saved polygon or pin by UID (job history is untouched)."""
    try:
        tenant_id = current_tenant_id()
        delete_field(tenant_id, uid)
        log_audit(tenant_id, "delete-field", "Web", f"uid: {uid}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Dashboard ──────────────────────────────────────────────────────────────────
# No cache here (there was a 2-minute one) — it was keyed only by date range,
# not tenant, so two tenants viewing the dashboard within the same window
# could see each other's numbers. Postgres reads are fast enough not to need
# a cache workaround the way the old Sheets-API-rate-limit one did.


@app.route("/dashboard")
@login_required
def dashboard():
    try:
        df  = load_work_entries(current_tenant_id())
        cls = sorted(df["שם לקוח"].dropna().unique().tolist()) if not df.empty else []
    except Exception:
        cls = []
    return render_template("dashboard.html", client_list=cls)


@app.route("/api/dashboard")
@login_required
def api_dashboard():
    date_from = request.args.get("from", "")
    date_to   = request.args.get("to",   "")
    try:
        df = load_work_entries(current_tenant_id())

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

        result = {
            "kpis":          kpis,
            "daily_trend":   daily.to_dict(orient="records"),
            "task_dist":     task_dist,
            "top_clients":   top_clients.to_dict(orient="records"),
            "top_fields":    top_fields.to_dict(orient="records"),
            "crop_hours":    crop_h.to_dict(orient="records"),
            "monthly_trend": monthly.to_dict(orient="records"),
            "updated_at":    datetime.now().strftime("%H:%M:%S"),
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    _logger.exception("500 error: %s", e)
    return render_template("500.html"), 500


# ── Manual tenant onboarding (no public signup yet) ────────────────────────────

@app.cli.command("create-tenant")
@click.argument("name")
@click.argument("manager_username")
@click.argument("manager_password")
@click.option("--slug", default=None, help="קוד חברה (ברירת מחדל: נגזר מהשם)")
def create_tenant_command(name, manager_username, manager_password, slug):
    """Onboard one pilot customer: flask create-tenant "שם החברה" user1 pass1234"""
    from gadash.auth import create_tenant
    try:
        tenant_id = create_tenant(name, manager_username, manager_password, slug=slug)
        from gadash.auth import get_tenant_slug
        click.echo(f"✅ נוצר טננט #{tenant_id} — קוד חברה: {get_tenant_slug(tenant_id)}")
    except ValueError as e:
        click.echo(f"❌ {e}")


# ── Background threads & entry point ──────────────────────────────────────────

if os.environ.get("BOT_TOKEN"):
    _bot_thread = threading.Thread(target=start_telegram_bot, args=(app,), daemon=True)
    _bot_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
