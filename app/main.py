"""
app/main.py — DEUS 3.0 FastAPI Application
=============================================
Entry point for Railway cloud deployment.
Run with: uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

import os
import sys
import json
import time

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_project_root, ".env"))
except ImportError:
    pass

from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.routes import router
from app.auth import router as auth_router, get_current_user, require_auth, require_admin
from app.database import (
    init_db, get_email_analytics, save_pdf_rules, get_active_pdf_rules,
    save_custom_pipeline, get_custom_pipelines, delete_custom_pipeline,
    get_pending_changes, review_pending_change, create_pending_change,
    log_email, record_analytics, get_analytics_summary,
)

app = FastAPI(
    title="DEUS 3.0 API",
    description="Digital Entity Unification System — Cloud Backend",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(router)

# Initialize DB
init_db()

# Start daemon on boot (if enabled)
try:
    from daemon import get_daemon
    daemon_enabled = os.getenv("DAEMON_AUTO_START", "false").lower() == "true"
    if daemon_enabled:
        daemon = get_daemon()
        daemon.start()
except Exception as e:
    print(f"[DEUS] Daemon auto-start skipped: {e}")


# --- Page routes ---

@app.get("/login")
def login_page():
    html_path = os.path.join(os.path.dirname(__file__), "login.html")
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/")
def root(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


# --- PDF Rules ---

@app.post("/api/rules/upload")
async def upload_rules(request: Request, file: UploadFile = File(...)):
    user = require_admin(request)
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    rule_id = save_pdf_rules(file.filename, text, user["user_id"])
    return {"success": True, "rule_id": rule_id, "filename": file.filename}


@app.get("/api/rules/active")
def get_rules():
    rules = get_active_pdf_rules()
    return rules or {"message": "No rules uploaded yet"}


# --- Custom Pipelines ---

@app.get("/api/pipelines/custom")
def list_custom_pipelines():
    return get_custom_pipelines()


@app.post("/api/pipelines/custom")
def create_pipeline(request: Request, payload: dict):
    user = require_auth(request)
    if user["role"] != "admin":
        # Normal user: submit as pending change
        change_id = create_pending_change(
            user["user_id"], "create_pipeline", payload.get("name", ""), payload
        )
        return {"success": True, "pending": True, "change_id": change_id}
    pipe_id = save_custom_pipeline(
        payload.get("name", ""),
        payload.get("description", ""),
        payload.get("steps", []),
        user["user_id"],
    )
    return {"success": True, "pipeline_id": pipe_id}


@app.delete("/api/pipelines/custom/{pipeline_id}")
def remove_pipeline(request: Request, pipeline_id: int):
    user = require_admin(request)
    delete_custom_pipeline(pipeline_id)
    return {"success": True}


# --- Email Analytics ---

@app.get("/api/analytics/emails")
def email_analytics():
    return get_email_analytics()


@app.get("/api/analytics/summary")
def analytics_summary():
    return get_analytics_summary()


@app.get("/api/analytics/dashboard")
def dashboard_stats(request: Request):
    user = require_auth(request)
    email_stats = get_email_analytics()
    analytics = get_analytics_summary()
    pending = len(get_pending_changes("pending")) if user["role"] == "admin" else 0

    # Count leads by status
    leads_path = os.path.join(_project_root, "leads.json")
    leads_by_status = {}
    total_leads = 0
    try:
        with open(leads_path) as f:
            leads = json.load(f)
        for l in leads:
            if isinstance(l, dict):
                st = l.get("status", "new")
                leads_by_status[st] = leads_by_status.get(st, 0) + 1
                total_leads += 1
    except Exception:
        pass

    return {
        "user": user,
        "leads": {"total": total_leads, "by_status": leads_by_status},
        "emails": email_stats,
        "pending_changes": pending,
        "analytics": analytics,
    }


# --- Pending Changes (for normal users) ---

@app.get("/api/pending/all")
def all_pending(request: Request):
    user = require_auth(request)
    if user["role"] == "admin":
        return get_pending_changes("pending")
    # Normal user sees their own
    from app.database import db_conn
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT pc.*, u.username FROM pending_changes pc "
            "JOIN users u ON pc.user_id = u.id "
            "WHERE pc.user_id = ? ORDER BY pc.created_at DESC",
            (user["user_id"],),
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/pending/{change_id}/approve")
def approve_change(request: Request, change_id: int):
    user = require_admin(request)
    review_pending_change(change_id, user["user_id"], True)
    return {"success": True}


@app.post("/api/pending/{change_id}/reject")
def reject_change(request: Request, change_id: int):
    user = require_admin(request)
    review_pending_change(change_id, user["user_id"], False)
    return {"success": True}
