"""
app/api/auth_routes.py — Consolidated Auth + Admin + Pending + Pipeline + PDF + Geo + Config
=============================================================================================
Every endpoint wraps in try/except:
  1. ORM via SessionLocal + repositories
  2. Fallback to app.database legacy functions
  3. HTTPException on failure
"""

import json
import time
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, File
from pydantic import BaseModel

from app.auth import get_current_user as _get_user
from app.auth import require_auth as _req_auth
from app.auth import require_admin as _req_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helpers (delegate to app.auth)
# ---------------------------------------------------------------------------

def get_current_user(request: Request) -> Optional[dict]:
    return _get_user(request)

def require_auth(request: Request) -> dict:
    return _req_auth(request)

def require_admin(request: Request) -> dict:
    return _req_admin(request)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str

class PendingSubmitRequest(BaseModel):
    action: str = ""
    target: str = ""
    data: dict = {}

class ReviewRequest(BaseModel):
    approved: bool = False

class PipelineCreateRequest(BaseModel):
    name: str
    description: str = ""
    steps: list = []

class GeoTargetCreateRequest(BaseModel):
    country: str = "United States"
    state: str = ""
    city: str = ""
    niche: str = ""
    target_type: str = "scout"
    scheduled_day: str = ""
    scheduled_date: str = ""
    scheduled_time: str = ""

class GeoTargetUpdateRequest(BaseModel):
    scheduled_day: Optional[str] = None
    scheduled_time: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    niche: Optional[str] = None
    target_type: Optional[str] = None
    enabled: Optional[int] = None


# ===========================================================================
# AUTH
# ===========================================================================

# Simple in-memory rate limiter: {ip: [(timestamp,), ...]}
_login_attempts: dict[str, list[float]] = {}
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW = 900  # 15 minutes


def _check_login_rate(ip: str):
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Prune old entries
    attempts = [t for t in attempts if now - t < LOGIN_RATE_WINDOW]
    if len(attempts) >= LOGIN_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    attempts.append(now)
    _login_attempts[ip] = attempts


@router.post("/api/auth/login")
def login(login_data: LoginRequest, response: Response, http_request: Request = None):
    client_ip = http_request.client.host if http_request and http_request.client else "unknown"
    _check_login_rate(client_ip)
    try:
        from app.db import SessionLocal
        from app.repositories import UserRepository
        with SessionLocal() as session:
            repo = UserRepository(session)
            user = repo.authenticate(login_data.username, login_data.password)
            if not user:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            expires_at = time.time() + 86400
            sess = repo.create_session(user.id, expires_at)
            user.last_login = time.time()
            session.commit()
            token = sess.token
            username = user.username
            role = user.role
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import authenticate_user, create_session
            user = authenticate_user(login_data.username, login_data.password)
            if not user:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            token = create_session(user["id"])
            username = user["username"]
            role = user["role"]
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="Login failed")

    is_https = http_request.url.scheme == "https" if http_request else False
    response.set_cookie(
        key="deus_session", value=token,
        httponly=True, max_age=86400, samesite="lax",
        secure=is_https,
    )
    return {"success": True, "user": {"username": username, "role": role}}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("deus_session")
    if token:
        try:
            from app.db import SessionLocal
            from app.repositories import UserRepository
            with SessionLocal() as session:
                repo = UserRepository(session)
                sess = repo.get_session(token)
                if sess:
                    session.delete(sess)
                    session.commit()
        except Exception:
            try:
                from app.database import delete_session
                delete_session(token)
            except Exception:
                pass
    response.delete_cookie("deus_session")
    return {"success": True}


@router.get("/api/auth/me")
def get_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ===========================================================================
# PENDING CHANGES
# ===========================================================================

@router.post("/api/pending/submit")
def submit_pending_change(request: Request, payload: PendingSubmitRequest):
    user = require_auth(request)
    try:
        from app.db import SessionLocal
        from app.repositories import PendingChangeRepository
        with SessionLocal() as session:
            repo = PendingChangeRepository(session)
            change = repo.create(
                user_id=user["user_id"],
                action=payload.action or "",
                target=payload.target or "",
                payload=json.dumps(payload.data or {}),
                status="pending",
                created_at=time.time(),
            )
            session.commit()
            return {"success": True, "change_id": change.id}
    except Exception:
        try:
            from app.database import create_pending_change
            change_id = create_pending_change(
                user_id=user["user_id"],
                action=payload.action or "",
                target=payload.target or "",
                payload=payload.data or {},
            )
            return {"success": True, "change_id": change_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.get("/api/pending")
def list_pending(request: Request):
    require_admin(request)
    try:
        from app.db import SessionLocal
        from app.repositories import PendingChangeRepository
        with SessionLocal() as session:
            repo = PendingChangeRepository(session)
            changes = repo.list_by_status("pending")
            result = []
            for c in changes:
                d = {col.name: getattr(c, col.name) for col in c.__table__.columns}
                try:
                    d["payload"] = json.loads(d["payload"]) if isinstance(d["payload"], str) else d["payload"]
                except Exception:
                    pass
                result.append(d)
            return result
    except Exception:
        try:
            from app.database import get_pending_changes
            return get_pending_changes("pending")
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.post("/api/pending/{change_id}/review")
def review_change(request: Request, change_id: int, payload: ReviewRequest):
    user = require_admin(request)
    try:
        from app.db import SessionLocal
        from app.repositories import PendingChangeRepository
        with SessionLocal() as session:
            repo = PendingChangeRepository(session)
            change = repo.get(change_id)
            if not change:
                raise HTTPException(status_code=404, detail="Change not found")
            change.status = "approved" if payload.approved else "rejected"
            change.reviewed_by = user["user_id"]
            change.reviewed_at = time.time()
            session.commit()
            return {"success": True, "approved": payload.approved}
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import review_pending_change
            review_pending_change(change_id, user["user_id"], payload.approved)
            return {"success": True, "approved": payload.approved}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


# ===========================================================================
# PDF RULES
# ===========================================================================

@router.post("/api/rules/upload")
async def upload_rules(request: Request, file: UploadFile = File(...)):
    user = require_admin(request)
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    try:
        from app.db import SessionLocal
        from app.repositories import PdfRuleRepository
        with SessionLocal() as session:
            repo = PdfRuleRepository(session)
            repo.session.query(repo.model).filter(repo.model.active == 1).update({"active": 0})
            rule = repo.create(
                filename=file.filename,
                content=text,
                uploaded_by=user["user_id"],
                active=1,
                uploaded_at=time.time(),
            )
            session.commit()
            return {"success": True, "rule_id": rule.id, "filename": file.filename}
    except Exception:
        try:
            from app.database import save_pdf_rules
            rule_id = save_pdf_rules(file.filename, text, user["user_id"])
            return {"success": True, "rule_id": rule_id, "filename": file.filename}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.get("/api/rules/active")
def get_rules():
    try:
        from app.db import SessionLocal
        from app.repositories import PdfRuleRepository
        with SessionLocal() as session:
            repo = PdfRuleRepository(session)
            rules = repo.list_active()
            if not rules:
                return {"message": "No rules uploaded yet"}
            result = []
            for r in rules:
                d = {col.name: getattr(r, col.name) for col in r.__table__.columns}
                result.append(d)
            return result[-1] if len(result) == 1 else result
    except Exception:
        try:
            from app.database import get_active_pdf_rules
            rules = get_active_pdf_rules()
            return rules or {"message": "No rules uploaded yet"}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


# ===========================================================================
# CUSTOM PIPELINES
# ===========================================================================

@router.get("/api/pipelines/custom")
def list_custom_pipelines():
    try:
        from app.db import SessionLocal
        from app.repositories import CustomPipelineRepository
        import json as _json
        with SessionLocal() as session:
            repo = CustomPipelineRepository(session)
            pipelines = repo.list()
            result = []
            for p in pipelines:
                d = {col.name: getattr(p, col.name) for col in p.__table__.columns}
                if isinstance(d.get("steps"), str):
                    try:
                        d["steps"] = _json.loads(d["steps"])
                    except Exception:
                        d["steps"] = [d["steps"]]
                result.append(d)
            return result
    except Exception:
        try:
            from app.database import get_custom_pipelines
            return get_custom_pipelines()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.post("/api/pipelines/custom")
def create_pipeline(request: Request, payload: PipelineCreateRequest):
    user = require_auth(request)
    if user.get("role") != "admin":
        try:
            from app.database import create_pending_change
            change_id = create_pending_change(
                user["user_id"], "create_pipeline", payload.name, payload.model_dump()
            )
            return {"success": True, "pending": True, "change_id": change_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")

    try:
        from app.db import SessionLocal
        from app.repositories import CustomPipelineRepository
        import json as _json
        with SessionLocal() as session:
            repo = CustomPipelineRepository(session)
            pipe = repo.create(
                name=payload.name,
                description=payload.description,
                steps=_json.dumps(payload.steps),
                created_by=user["user_id"],
                created_at=time.time(),
            )
            session.commit()
            return {"success": True, "pipeline_id": pipe.id}
    except Exception:
        try:
            from app.database import save_custom_pipeline
            pipe_id = save_custom_pipeline(
                payload.name, payload.description, payload.steps, user["user_id"]
            )
            return {"success": True, "pipeline_id": pipe_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.delete("/api/pipelines/custom/{pipeline_id}")
def remove_pipeline(request: Request, pipeline_id: int):
    require_admin(request)
    try:
        from app.db import SessionLocal
        from app.repositories import CustomPipelineRepository
        with SessionLocal() as session:
            repo = CustomPipelineRepository(session)
            pipe = repo.get(pipeline_id)
            if not pipe:
                raise HTTPException(status_code=404, detail="Pipeline not found")
            session.delete(pipe)
            session.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import delete_custom_pipeline
            delete_custom_pipeline(pipeline_id)
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


# ===========================================================================
# PENDING ALL / APPROVE / REJECT (admin overrides)
# ===========================================================================

@router.get("/api/pending/all")
def all_pending(request: Request):
    user = require_auth(request)
    try:
        from app.db import SessionLocal
        from app.repositories import PendingChangeRepository
        with SessionLocal() as session:
            repo = PendingChangeRepository(session)
            if user["role"] == "admin":
                changes = repo.list_by_status("pending")
            else:
                changes = repo.list_by_user(user["user_id"])
            result = []
            for c in changes:
                d = {col.name: getattr(c, col.name) for col in c.__table__.columns}
                try:
                    d["payload"] = json.loads(d["payload"]) if isinstance(d["payload"], str) else d["payload"]
                except Exception:
                    pass
                result.append(d)
            return result
    except Exception:
        try:
            from app.database import db_conn
            with db_conn() as conn:
                if user["role"] == "admin":
                    rows = conn.execute(
                        "SELECT pc.*, u.username FROM pending_changes pc "
                        "JOIN users u ON pc.user_id = u.id "
                        "WHERE pc.status = 'pending' ORDER BY pc.created_at DESC"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT pc.*, u.username FROM pending_changes pc "
                        "JOIN users u ON pc.user_id = u.id "
                        "WHERE pc.user_id = ? ORDER BY pc.created_at DESC",
                        (user["user_id"],),
                    ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.post("/api/pending/{change_id}/approve")
def approve_change(request: Request, change_id: int):
    user = require_admin(request)
    try:
        from app.db import SessionLocal
        from app.repositories import PendingChangeRepository
        with SessionLocal() as session:
            repo = PendingChangeRepository(session)
            change = repo.get(change_id)
            if not change:
                raise HTTPException(status_code=404, detail="Change not found")
            change.status = "approved"
            change.reviewed_by = user["user_id"]
            change.reviewed_at = time.time()
            session.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import review_pending_change
            review_pending_change(change_id, user["user_id"], True)
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.post("/api/pending/{change_id}/reject")
def reject_change(request: Request, change_id: int):
    user = require_admin(request)
    try:
        from app.db import SessionLocal
        from app.repositories import PendingChangeRepository
        with SessionLocal() as session:
            repo = PendingChangeRepository(session)
            change = repo.get(change_id)
            if not change:
                raise HTTPException(status_code=404, detail="Change not found")
            change.status = "rejected"
            change.reviewed_by = user["user_id"]
            change.reviewed_at = time.time()
            session.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import review_pending_change
            review_pending_change(change_id, user["user_id"], False)
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


# ===========================================================================
# GEO TARGETS
# ===========================================================================

@router.get("/api/geo/targets")
def list_geo_targets():
    try:
        from app.db import SessionLocal
        from app.repositories import GeoTargetRepository
        with SessionLocal() as session:
            repo = GeoTargetRepository(session)
            targets = repo.list()
            result = []
            for t in targets:
                d = {col.name: getattr(t, col.name) for col in t.__table__.columns}
                result.append(d)
            return result
    except Exception:
        try:
            from app.database import db_conn
            with db_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM geo_targets ORDER BY country, state"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.post("/api/geo/targets")
def add_geo_target(request: GeoTargetCreateRequest):
    try:
        from app.db import SessionLocal
        from app.repositories import GeoTargetRepository
        with SessionLocal() as session:
            repo = GeoTargetRepository(session)
            target = repo.create(
                country=request.country,
                state=request.state,
                city=request.city,
                niche=request.niche,
                target_type=request.target_type,
                scheduled_day=request.scheduled_day,
                scheduled_date=request.scheduled_date,
                scheduled_time=request.scheduled_time,
                enabled=1,
                created_at=time.time(),
            )
            session.commit()
            return {"id": target.id}
    except Exception:
        try:
            from app.database import db_conn
            with db_conn() as conn:
                cur = conn.execute(
                    """INSERT INTO geo_targets (country, state, city, niche, target_type, scheduled_day, scheduled_date, scheduled_time)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        request.country, request.state, request.city,
                        request.niche, request.target_type, request.scheduled_day,
                        request.scheduled_date, request.scheduled_time,
                    ),
                )
                return {"id": cur.lastrowid}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.delete("/api/geo/targets/{target_id}")
def delete_geo_target(target_id: int):
    try:
        from app.db import SessionLocal
        from app.repositories import GeoTargetRepository
        with SessionLocal() as session:
            repo = GeoTargetRepository(session)
            target = repo.get(target_id)
            if not target:
                raise HTTPException(status_code=404, detail="Target not found")
            session.delete(target)
            session.commit()
            return {"deleted": True}
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import db_conn
            with db_conn() as conn:
                conn.execute("DELETE FROM geo_targets WHERE id = ?", (target_id,))
                return {"deleted": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.put("/api/geo/targets/{target_id}")
def update_geo_target(target_id: int, request: GeoTargetUpdateRequest):
    try:
        from app.db import SessionLocal
        from app.repositories import GeoTargetRepository
        with SessionLocal() as session:
            repo = GeoTargetRepository(session)
            target = repo.get(target_id)
            if not target:
                raise HTTPException(status_code=404, detail="Target not found")
            update_fields = request.model_dump(exclude_unset=True)
            for key, val in update_fields.items():
                if val is not None and hasattr(target, key):
                    setattr(target, key, val)
            session.commit()
            return {"updated": True}
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import db_conn
            ALLOWED_COLS = {"scheduled_day", "scheduled_time", "scheduled_date", "niche", "country", "state", "city", "target_type", "enabled"}
            updates = []
            params = []
            data = request.model_dump(exclude_unset=True)
            for key in ALLOWED_COLS:
                if key in data and data[key] is not None:
                    updates.append(f"{key} = ?")
                    params.append(data[key])
            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")
            params.append(target_id)
            with db_conn() as conn:
                conn.execute(
                    f"UPDATE geo_targets SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                return {"updated": True}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal error")


@router.post("/api/geo/targets/auto-scout")
def geo_targets_auto_scout(payload: dict = {}):
    """Trigger lead search for all due geo targets (calls legacy function)."""
    try:
        from app.database import db_conn
        from datetime import datetime
        from lead_scout_agent import LeadScoutAgent, LLM, DuckDuckGoSource, DirectWebSource
        today_name = datetime.now().strftime("%A")
        current_time = datetime.now().strftime("%H:%M")
        req_data = payload or {}
        with db_conn() as conn:
            due = conn.execute(
                """SELECT * FROM geo_targets
                   WHERE enabled = 1 AND scheduled_day = ? AND scheduled_time <= ?""",
                (today_name, current_time),
            ).fetchall()
        results = []
        for target in due:
            t = dict(target)
            try:
                ddg = DuckDuckGoSource()
                direct = DirectWebSource()
                sources = []
                if ddg.enabled:
                    sources.append(ddg)
                if direct.enabled:
                    sources.append(direct)
                llm = LLM()
                agent = LeadScoutAgent(None, None, llm)
                niche = t.get("niche", "").strip() or req_data.get("niche", "").strip()
                location_parts = [p for p in [t.get("city"), t.get("state"), t.get("country")] if p]
                query = f"{niche} in {', '.join(location_parts)}" if niche else ", ".join(location_parts)
                res = agent.run(user_input=query, target=req_data.get("target", 50))
                leads = res.data if res.success else []
                saved = 0
                for lead in leads:
                    lead["lead_type"] = "scraped"
                    lead["source"] = "geo_auto_scout"
                    lead["address"] = lead.get("address", "") or query
                    try:
                        from app.database import upsert_lead
                        upsert_lead(lead)
                        saved += 1
                    except Exception:
                        pass
                results.append({"target_id": t["id"], "leads_found": len(leads), "saved": saved})
            except Exception as e:
                results.append({"target_id": t["id"], "error": str(e)})
        return {"success": True, "targets_due": len(due), "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal error")
