"""
app/api/support_routes.py — LinkedIn queue, Daemon, Manual Send, Health endpoints
"""

import os
import sys
import time
import csv
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

router = APIRouter()
# LinkedIn Queue
# ---------------------------------------------------------------------------

@router.get("/api/linkedin/queue")
def list_linkedin_queue(status: str = None, limit: int = 50, search: str = ""):
    try:
        from app.db import SessionLocal
        from app.repositories import LinkedInQueueRepository
        with SessionLocal() as session:
            repo = LinkedInQueueRepository(session)
            if status:
                entries = repo.list_by_status(status, limit=limit)
            else:
                entries = repo.list(filters={"status": status} if status else None, limit=limit)
            counts = repo.count_by_status()
            result = [{c.name: getattr(e, c.name) for c in e.__table__.columns} for e in entries]
            return {"entries": result, "counts": counts}
    except Exception:
        try:
            from app.database import get_linkedin_queue, count_linkedin_queue
            entries = get_linkedin_queue(status=status, limit=limit)
            counts = {
                "total": count_linkedin_queue(),
                "pending": count_linkedin_queue("pending"),
                "connection_sent": count_linkedin_queue("connection_sent"),
                "message_sent": count_linkedin_queue("message_sent"),
                "replied": count_linkedin_queue("replied"),
            }
            return {"entries": entries, "counts": counts}
        except Exception:
            return {"entries": [], "counts": {}}


@router.post("/api/linkedin/queue/add")
def add_linkedin_queue_entry(request: dict):
    try:
        from app.db import SessionLocal
        from app.repositories import LinkedInQueueRepository
        with SessionLocal() as session:
            repo = LinkedInQueueRepository(session)
            entry = repo.create(**request)
            session.commit()
            d = {c.name: getattr(entry, c.name) for c in entry.__table__.columns}
            return {"success": True, "id": entry.id, "entry": d}
    except Exception:
        try:
            from app.database import add_to_linkedin_queue
            entry_id = add_to_linkedin_queue(request, request.get("message_template", ""))
            return {"success": bool(entry_id), "id": entry_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/linkedin/queue/{entry_id}")
def update_linkedin_entry(entry_id: int, request: dict):
    try:
        from app.db import SessionLocal
        from app.repositories import LinkedInQueueRepository
        with SessionLocal() as session:
            repo = LinkedInQueueRepository(session)
            entry = repo.update(entry_id, **request)
            if not entry:
                raise HTTPException(status_code=404, detail="Entry not found")
            session.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import update_linkedin_queue
            update_linkedin_queue(entry_id, request)
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/linkedin/queue/{entry_id}")
def delete_linkedin_entry(entry_id: int):
    try:
        from app.db import SessionLocal
        from app.repositories import LinkedInQueueRepository
        with SessionLocal() as session:
            repo = LinkedInQueueRepository(session)
            ok = repo.delete(entry_id)
            session.commit()
            if not ok:
                raise HTTPException(status_code=404, detail="Entry not found")
            return {"success": True}
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import delete_linkedin_entry as legacy_delete
            legacy_delete(entry_id)
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/linkedin/queue/bulk-delete")
def bulk_delete_linkedin(request: dict):
    ids = request.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="ids is required")
    try:
        from app.db import SessionLocal
        from app.repositories import LinkedInQueueRepository
        deleted = 0
        with SessionLocal() as session:
            repo = LinkedInQueueRepository(session)
            for eid in ids:
                if repo.delete(eid):
                    deleted += 1
            session.commit()
        return {"deleted": deleted}
    except Exception:
        try:
            from app.database import delete_linkedin_entry as legacy_delete
            for eid in ids:
                legacy_delete(eid)
            return {"deleted": len(ids)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/linkedin/export")
def export_linkedin_csv(status: str = None):
    try:
        from app.database import export_linkedin_csv as gen_csv
        csv_text = gen_csv(status=status or None)
        return StreamingResponse(
            io.BytesIO(csv_text.encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=linkedin_queue.csv"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/linkedin/queue/bulk-add")
def bulk_add_to_linkedin(request: dict):
    try:
        from app.db import SessionLocal
        from app.repositories import LinkedInQueueRepository
        from app.models.linkedin import LinkedInQueue
        lead_type = request.get("lead_type", "")
        lead_ids = request.get("lead_ids", [])
        message_template = request.get("message_template", "")
        added = 0

        with SessionLocal() as session:
            repo = LinkedInQueueRepository(session)
            if lead_ids:
                from sqlalchemy import select
                from app.models.lead import Lead
                stmt = select(Lead).where(Lead.id.in_(lead_ids))
                leads = list(session.execute(stmt).scalars().all())
                for lead in leads:
                    if lead.linkedin_url:
                        existing = repo.get_by_lead_id(lead.id)
                        if existing:
                            continue
                        repo.create(
                            lead_id=lead.id,
                            lead_name=getattr(lead, "business_name", ""),
                            lead_email=getattr(lead, "business_email", ""),
                            linkedin_url=lead.linkedin_url,
                            profile_title=getattr(lead, "profile_title", ""),
                            company=getattr(lead, "company", ""),
                            industry=getattr(lead, "industry", ""),
                            location=getattr(lead, "location", "") or getattr(lead, "address", ""),
                            niche=getattr(lead, "niche", ""),
                            message_template=message_template,
                            source="scout",
                        )
                        added += 1
            elif lead_type:
                from sqlalchemy import select
                from app.models.lead import Lead
                stmt = select(Lead).where(
                    Lead.lead_type == lead_type,
                    Lead.linkedin_url.isnot(None),
                    Lead.linkedin_url != "",
                )
                leads = list(session.execute(stmt).scalars().all())
                for lead in leads:
                    if repo.get_by_lead_id(lead.id):
                        continue
                    repo.create(
                        lead_id=lead.id,
                        lead_name=getattr(lead, "business_name", ""),
                        lead_email=getattr(lead, "business_email", ""),
                        linkedin_url=lead.linkedin_url,
                        profile_title=getattr(lead, "profile_title", ""),
                        company=getattr(lead, "company", ""),
                        industry=getattr(lead, "industry", ""),
                        location=getattr(lead, "location", "") or getattr(lead, "address", ""),
                        niche=getattr(lead, "niche", ""),
                        message_template=message_template,
                        source="scout",
                    )
                    added += 1
            session.commit()
        return {"success": True, "added": added}
    except Exception:
        try:
            from app.database import db_conn, add_to_linkedin_queue as db_add
            lead_type = request.get("lead_type", "")
            lead_ids = request.get("lead_ids", [])
            message_template = request.get("message_template", "")
            added = 0

            if lead_ids:
                placeholders = ",".join("?" * len(lead_ids))
                with db_conn() as conn:
                    rows = conn.execute(
                        f"SELECT * FROM leads WHERE id IN ({placeholders})", lead_ids
                    ).fetchall()
                    for row in rows:
                        if db_add(dict(row), message_template):
                            added += 1
            elif lead_type:
                with db_conn() as conn:
                    rows = conn.execute(
                        "SELECT * FROM leads WHERE lead_type = ? AND (linkedin_url IS NOT NULL AND linkedin_url != '')",
                        (lead_type,),
                    ).fetchall()
                    for row in rows:
                        if db_add(dict(row), message_template):
                            added += 1

            return {"success": True, "added": added}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Daemon control
# ---------------------------------------------------------------------------

@router.get("/api/daemon/status")
def daemon_status():
    try:
        from daemon import get_daemon
        return get_daemon().status()
    except Exception:
        return {"running": False}


@router.post("/api/daemon/start")
def daemon_start():
    try:
        from daemon import get_daemon
        get_daemon().start()
        return {"success": True, "message": "Daemon started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/daemon/start-auto")
def daemon_start_auto(request: dict = None):
    hours = int(request.get("hours", 24)) if request else 24
    try:
        from daemon import get_daemon
        get_daemon().start(auto_stop_hours=hours)
        return {"success": True, "message": f"Daemon started — will auto-stop in {hours} hours"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/daemon/stop")
def daemon_stop():
    try:
        from daemon import get_daemon
        get_daemon().stop()
        return {"success": True, "message": "Daemon stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/daemon/restart")
def daemon_restart(request: dict = None):
    try:
        from daemon import get_daemon
        interval = request.get("interval_seconds") if request else None
        get_daemon().restart(interval_seconds=interval)
        return {"success": True, "message": "Daemon restarted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/daemon/log")
def daemon_log(limit: int = 50):
    try:
        from daemon import get_daemon
        return get_daemon().get_log(limit)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Daemon config
# ---------------------------------------------------------------------------

@router.get("/api/daemon/config")
def list_daemon_configs():
    try:
        from app.db import SessionLocal
        from app.repositories import DaemonConfigRepository
        with SessionLocal() as session:
            repo = DaemonConfigRepository(session)
            entries = repo.list(limit=100)
            result = []
            for e in entries:
                d = {c.name: getattr(e, c.name) for c in e.__table__.columns}
                try:
                    import json
                    d["config_json"] = json.loads(d.get("config_json", "{}"))
                except Exception:
                    d["config_json"] = {}
                result.append(d)
            return result
    except Exception:
        try:
            from app.database import get_daemon_configs
            return get_daemon_configs()
        except Exception as e:
            return {"error": str(e)}


@router.get("/api/daemon/config/{agent_name}")
def get_daemon_config(agent_name: str):
    try:
        from app.db import SessionLocal
        from app.repositories import DaemonConfigRepository
        with SessionLocal() as session:
            repo = DaemonConfigRepository(session)
            entry = repo.get_by_agent_name(agent_name)
            if not entry:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
            d = {c.name: getattr(entry, c.name) for c in entry.__table__.columns}
            try:
                import json
                d["config_json"] = json.loads(d.get("config_json", "{}"))
            except Exception:
                d["config_json"] = {}
            return d
    except HTTPException:
        raise
    except Exception:
        try:
            from app.database import get_daemon_config
            cfg = get_daemon_config(agent_name)
            if not cfg:
                raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
            return cfg
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/daemon/config/{agent_name}")
def save_daemon_config(agent_name: str, request: dict):
    try:
        from app.db import SessionLocal
        from app.repositories import DaemonConfigRepository
        with SessionLocal() as session:
            repo = DaemonConfigRepository(session)
            existing = repo.get_by_agent_name(agent_name)
            if existing:
                repo.update(existing.id, **request)
            else:
                repo.create(agent_name=agent_name, **request)
            session.commit()
            return {"success": True}
    except Exception:
        try:
            from app.database import save_daemon_config as legacy_save
            legacy_save(agent_name, request)
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/daemon/config/reset")
def reset_daemon_configs():
    try:
        from app.db import SessionLocal
        from app.repositories import DaemonConfigRepository
        from app.models.daemon import DaemonConfig
        with SessionLocal() as session:
            repo = DaemonConfigRepository(session)
            entries = repo.list(limit=100)
            for e in entries:
                session.delete(e)
            defaults = [
                ("lead_scout", "Lead Scout", 1, "scraped", 0),
                ("outreach", "Outreach", 1, "scraped", 10),
                ("followup", "Followup", 1, "", 0),
                ("reply_scan", "Reply Scan", 1, "", 0),
                ("campaign", "Campaign Steps", 1, "", 0),
                ("appointment", "Appointment", 1, "", 0),
                ("deal_closer", "Deal Closer", 1, "", 0),
                ("report", "Report Agent", 1, "", 0),
            ]
            for name, display, enabled, ltf, mpr in defaults:
                session.add(DaemonConfig(
                    agent_name=name, display_name=display,
                    enabled=enabled, lead_type_filter=ltf, max_per_run=mpr
                ))
            session.commit()
            return {"success": True}
    except Exception:
        try:
            from app.database import reset_daemon_configs as legacy_reset
            legacy_reset()
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Agent Schedules (scheduler.py)
# ---------------------------------------------------------------------------

@router.get("/api/schedules")
def list_schedules():
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        rows = sched.list_schedules()
        result = []
        for s in rows:
            result.append({
                "schedule_id": s.schedule_id,
                "name": s.name,
                "agent_name": s.agent_name,
                "interval_minutes": s.interval_minutes,
                "config": s.config,
                "enabled": s.enabled,
                "last_run_at": s.last_run_at,
                "next_run_at": s.next_run_at,
                "created_at": s.created_at,
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/schedules")
def create_schedule(payload: dict):
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        sid = sched.create_schedule(
            name=payload.get("name", ""),
            agent_name=payload.get("agent_name", ""),
            interval_minutes=int(payload.get("interval_minutes", 60)),
            config=payload.get("config", {}),
            enabled=payload.get("enabled", True),
        )
        return {"id": sid, "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        sched.delete_schedule(schedule_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/schedules/{schedule_id}/toggle")
def toggle_schedule(schedule_id: int, payload: dict = {}):
    try:
        enabled = payload.get("enabled", True)
        from scheduler import get_scheduler
        sched = get_scheduler()
        sched.toggle_schedule(schedule_id, enabled)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/schedules/{schedule_id}/run-now")
def run_schedule_now(schedule_id: int):
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        result = sched.run_schedule_now(schedule_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Manual Send
# ---------------------------------------------------------------------------

@router.get("/api/manual-send/active")
def manual_send_active():
    try:
        from outreach_agent import manual_send_active as _active
        return {"active": _active, "email": ""}
    except Exception:
        return {"active": False, "email": ""}
