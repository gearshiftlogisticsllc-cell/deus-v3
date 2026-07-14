"""
app/api/routes.py — DEUS 3.0 FastAPI routes
"""

import os
import sys
import json
import time
import csv
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

# Ensure project root is on sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from pipeline import Pipeline, list_pipelines, get_available_agents, get_agent_class
from command_processor import CommandProcessor
from app.models.schemas import (
    AgentInfo, AgentRunRequest, AgentRunResponse,
    PipelineInfo, PipelineRunRequest, PipelineRunResponse,
    CommandRequest, CommandResponse,
    HealthResponse,
)

router = APIRouter()
_cmd_processor = CommandProcessor()

DATA_DIR = _project_root


def _load_json(filename, default=None):
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else []


def _save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
def health_check():
    groq = bool(os.getenv("GROQ_API_KEY", ""))
    gemini = bool(os.getenv("GEMINI_API_KEY", ""))
    calendly = bool(os.getenv("CALENDLY_API_KEY", ""))
    smtp = bool(os.getenv("RESEND_API_KEY", "") or os.getenv("SMTP_EMAIL", ""))

    agents = get_available_agents()
    healthy = 0
    for name in agents:
        cls = get_agent_class(name)
        if cls:
            try:
                agent = cls()
                h = agent.check_health()
                if h.healthy:
                    healthy += 1
            except Exception:
                pass

    return HealthResponse(
        status="ok",
        groq=groq,
        gemini=gemini,
        calendly=calendly,
        smtp=smtp,
        agents_healthy=healthy,
        agents_total=len(agents),
    )


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@router.get("/api/agents", response_model=list[AgentInfo])
def list_agents():
    result = []
    for name in get_available_agents():
        cls = get_agent_class(name)
        if cls is None:
            continue
        try:
            agent = cls()
            h = agent.check_health()
            result.append(AgentInfo(
                name=name,
                display_name=cls.display_name,
                description=cls.description,
                healthy=h.healthy,
                health_status=h.status,
                health_message=h.message,
            ))
        except Exception as e:
            result.append(AgentInfo(
                name=name,
                display_name=name,
                description="Error initializing",
                healthy=False,
                health_status="error",
                health_message=str(e),
            ))
    return result


@router.get("/api/agents/{name}", response_model=AgentInfo)
def get_agent(name: str):
    cls = get_agent_class(name)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    try:
        agent = cls()
        h = agent.check_health()
        return AgentInfo(
            name=name,
            display_name=cls.display_name,
            description=cls.description,
            healthy=h.healthy,
            health_status=h.status,
            health_message=h.message,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/agents/{name}/run", response_model=AgentRunResponse)
def run_agent(name: str, request: AgentRunRequest = None):
    cls = get_agent_class(name)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    try:
        agent = cls()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to init {name}: {e}")

    kwargs = request.kwargs if request else {}
    start = time.time()
    try:
        result = agent.run(**kwargs)
    except Exception as e:
        return AgentRunResponse(
            success=False,
            message=f"{name} failed: {e}",
            agent_name=name,
            duration=time.time() - start,
        )

    return AgentRunResponse(
        success=result.success,
        message=result.message,
        agent_name=name,
        data=result.data,
        stats=result.stats,
        duration=result.duration_seconds,
    )


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

@router.get("/api/pipelines", response_model=list[PipelineInfo])
def list_all_pipelines():
    result = []
    for name, info in list_pipelines().items():
        result.append(PipelineInfo(
            name=name,
            display_name=info["name"],
            description=info["description"],
            steps=info["steps"],
        ))
    return result


@router.post("/api/pipelines/{name}/run", response_model=PipelineRunResponse)
def run_pipeline(name: str, request: PipelineRunRequest = None):
    try:
        pipeline = Pipeline(pipeline_name=name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Pipeline '{name}' not found: {e}")

    if not pipeline.steps:
        raise HTTPException(status_code=400, detail=f"Pipeline '{name}' has no steps")

    kwargs = request.kwargs if request else {}
    result = pipeline.run(**kwargs)

    return PipelineRunResponse(
        success=result.success,
        message=result.message,
        pipeline_name=name,
        steps_completed=len(result.steps),
        total_duration=result.total_duration,
        stats=result.stats,
    )


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@router.post("/api/command", response_model=CommandResponse)
def process_command(request: CommandRequest):
    result = _cmd_processor.process(request.command)
    return CommandResponse(
        success=result.success,
        message=result.message,
        agent_name=result.agent_name,
        pipeline_name=result.pipeline_name,
    )


# ---------------------------------------------------------------------------
# Dashboard data endpoints
# ---------------------------------------------------------------------------

@router.get("/api/stats")
def get_stats():
    try:
        from app.database import count_leads
        leads = count_leads()
        contacted = count_leads(status="contacted")
    except Exception:
        leads = len(_load_json("leads.json"))
        contacted = 0
    return {
        "leads": leads,
        "outreach": contacted,
        "appointments": len(_load_json("appointments.json")),
        "deals": len(_load_json("deals_log.json")),
    }


@router.get("/api/leads")
def get_leads():
    return _load_json("leads.json")


@router.post("/api/leads/export")
def export_leads_csv():
    leads = _load_json("leads.json")
    if not leads:
        raise HTTPException(status_code=404, detail="No leads to export")

    output = io.StringIO()
    keys = ["score", "business_name", "owner_name", "phone", "business_email",
            "website", "address", "location", "source", "analysis", "outreach_suggestion"]
    writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(leads)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads_export.csv"},
    )


@router.get("/api/outreach/config")
def get_outreach_config():
    try:
        from outreach_config import load_style_config
        s = load_style_config()
        return {
            "tone": s.tone,
            "max_words": s.max_words,
            "subject_template": s.subject_template,
            "call_to_action": s.call_to_action,
            "signature": s.signature,
            "use_custom_template": s.use_custom_template,
            "custom_template": s.custom_template,
            "ai_email_enabled": s.ai_email_enabled,
        }
    except Exception:
        return {
            "tone": "professional", "max_words": 150,
            "subject_template": "Quick question about your business, {business_name}",
            "call_to_action": "Would you be open to a quick 15-minute call this week?",
            "signature": "Best regards,\nGrowthDesk VA Team",
            "use_custom_template": False, "custom_template": "",
            "ai_email_enabled": True,
        }


@router.post("/api/outreach/config")
def save_outreach_config(cfg: dict):
    try:
        from outreach_config import StyleConfig, save_style_config
        save_style_config(StyleConfig(
            tone=cfg.get("tone", "professional"),
            max_words=int(cfg.get("max_words", 150)),
            subject_template=cfg.get("subject_template", ""),
            call_to_action=cfg.get("call_to_action", ""),
            signature=cfg.get("signature", ""),
            use_custom_template=cfg.get("use_custom_template", False),
            custom_template=cfg.get("custom_template", ""),
            ai_email_enabled=cfg.get("ai_email_enabled", True),
        ))
        return {"saved": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/smtp/profiles")
def get_smtp_profiles():
    try:
        from outreach_config import load_smtp_profiles
        profiles = load_smtp_profiles()
        return [{"profile_name": p.profile_name, "smtp_host": p.smtp_host,
                 "smtp_port": p.smtp_port, "smtp_email": p.smtp_email,
                 "is_default": p.is_default} for p in profiles]
    except Exception:
        return []


@router.post("/api/smtp/profiles")
def save_smtp_profile(profile: dict):
    try:
        from outreach_config import SmtpProfile, upsert_smtp_profile
        upsert_smtp_profile(SmtpProfile(
            profile_name=profile["profile_name"],
            smtp_host=profile.get("smtp_host", "smtp.gmail.com"),
            smtp_port=int(profile.get("smtp_port", 465)),
            smtp_email=profile["smtp_email"],
            smtp_password=profile.get("smtp_password", ""),
            is_default=profile.get("is_default", False),
        ))
        return {"saved": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/smtp/profiles/{name}")
def delete_smtp_profile(name: str):
    try:
        from outreach_config import delete_smtp_profile as del_fn
        del_fn(name)
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/lead-scout/search")
def lead_scout_search(request: dict):
    niche = request.get("niche", "").strip()
    if not niche:
        raise HTTPException(status_code=400, detail="niche is required")

    target = int(request.get("target", 50))
    use_serper = request.get("use_serper", False)
    brightdata_key = request.get("brightdata_key", "")

    try:
        from lead_scout_agent import (
            LeadScoutAgent, LLM, SerperSource, BrightDataSource,
            DuckDuckGoSource, DirectWebSource,
        )

        bd = BrightDataSource(
            api_key=brightdata_key,
            dataset_id=os.getenv("BRIGHTDATA_DATASET_ID", ""),
            serp_zone=os.getenv("BRIGHTDATA_SERP_ZONE", ""),
        ) if brightdata_key else None

        serper_key = os.getenv("SERPER_API_KEY", "")
        serper = SerperSource(serper_key) if (use_serper and serper_key) else None

        ddg = DuckDuckGoSource()
        direct = DirectWebSource()

        sources = []
        if ddg.enabled:
            sources.append(ddg)
        if direct.enabled:
            sources.append(direct)
        if bd and bd.enabled:
            sources.append(bd)
        if serper and serper.enabled:
            sources.append(serper)

        llm = LLM()
        agent = LeadScoutAgent(bd, serper, llm)
        leads = agent.run(niche, target=target)

        existing = _load_json("leads.json")

        def _dedup_key(lead):
            return (
                lead.get("business_email")
                or lead.get("website")
                or lead.get("phone")
                or f"{lead.get('business_name', '')}|{lead.get('location', '')}"
                or f"__unknown_{id(lead)}"
            )

        seen = {_dedup_key(l) for l in existing if isinstance(l, dict)}
        new = [l for l in leads if _dedup_key(l) not in seen]
        existing.extend(new)
        _save_json("leads.json", existing)

        return {
            "success": True,
            "leads": leads,
            "total": len(leads),
            "new_saved": len(new),
            "skipped": len(leads) - len(new),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "leads": [], "total": 0}


@router.post("/api/leads/import")
def import_contacts(request: dict):
    import base64
    filename = request.get("filename", "")
    content_b64 = request.get("content_b64", "")
    filepath = request.get("filepath", "")
    niche = request.get("niche", "")

    if not content_b64 and not filepath:
        raise HTTPException(status_code=400, detail="filename + content_b64 or filepath is required")

    try:
        if content_b64:
            # Decode base64 content and parse in-memory
            from contact_importer import parse_csv_text, parse_plain_lines, parse_docx_tables, parse_pdf
            ext = os.path.splitext(filename)[1].lower()
            file_bytes = base64.b64decode(content_b64)

            if ext == ".csv":
                text = file_bytes.decode("utf-8-sig", errors="ignore")
                new_leads = parse_csv_text(text)
            elif ext == ".txt":
                text = file_bytes.decode("utf-8", errors="ignore")
                new_leads = parse_plain_lines(text)
            elif ext == ".docx":
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name
                try:
                    new_leads = parse_docx_tables(tmp_path)
                finally:
                    os.unlink(tmp_path)
            elif ext == ".pdf":
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name
                try:
                    new_leads = parse_pdf(tmp_path)
                finally:
                    os.unlink(tmp_path)
            else:
                raise ValueError(f"Unsupported file type: {ext}")

            # De-dupe
            seen = set()
            deduped = []
            for lead in new_leads:
                key = lead.get("business_email") or lead.get("phone")
                if key and key not in seen:
                    seen.add(key)
                    deduped.append(lead)

            # Set defaults for all leads
            for lead in deduped:
                lead.setdefault("niche", niche)
                lead.setdefault("preferred_channel", "email")
                lead["status"] = lead.get("status", "new")
                lead["source"] = "manual_import"
                lead["outreach_ready"] = bool(lead.get("business_email"))
                lead["needs_human"] = 0 if lead.get("business_email") else 1
                if lead["needs_human"]:
                    lead["needs_human_reason"] = "Imported contact has no email — needs a call."

            # Save to database
            try:
                from app.database import upsert_leads_batch
                result = upsert_leads_batch(deduped)
                imported_count = result["imported"]
                skipped_count = result["skipped"]
            except Exception as e:
                # Fallback to leads.json
                leads_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "leads.json")
                existing = []
                try:
                    with open(leads_path) as f:
                        existing = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    existing = []
                existing_keys = {l.get("business_email") or l.get("phone") for l in existing if isinstance(l, dict)}
                imported_count = 0
                skipped_count = 0
                for lead in deduped:
                    key = lead.get("business_email") or lead.get("phone")
                    if key in existing_keys:
                        skipped_count += 1
                        continue
                    existing.append(lead)
                    existing_keys.add(key)
                    imported_count += 1
                with open(leads_path, "w") as f:
                    json.dump(existing, f, indent=2)

            return {"success": True, "imported": imported_count, "skipped_duplicates": skipped_count, "total_in_file": len(deduped)}
        else:
            # Fallback: file path on server
            from contact_importer import import_contacts as do_import
            result = do_import(filepath, default_niche=niche)
            return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/config/keys")
def get_api_keys():
    try:
        from api_key_registry import get_agents_grouped
        grouped = get_agents_grouped()
        result = {}
        import os as _os
        for agent_name, keys in grouped.items():
            result[agent_name] = []
            for ki in keys:
                val = _os.getenv(ki.env_var, "")
                result[agent_name].append({
                    "env_var": ki.env_var,
                    "required": ki.required,
                    "is_secret": ki.is_secret,
                    "provider": ki.provider,
                    "provider_url": ki.provider_url,
                    "used_by": ki.used_by,
                    "value_set": bool(val),
                    "value_masked": (ki.mask_value(val) if val and ki.is_secret else val) if hasattr(ki, 'mask_value') else val,
                })
        return result
    except Exception:
        return {}


@router.post("/api/config/keys/{env_var}")
def save_api_key(env_var: str, request: dict):
    value = request.get("value", "")
    env_path = os.path.join(DATA_DIR, ".env")
    lines, replaced = [], False
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for ln in f:
                if ln.strip().startswith(f"{env_var}="):
                    lines.append(f"{env_var}={value}\n")
                    replaced = True
                else:
                    lines.append(ln)
    if not replaced:
        lines.append(f"{env_var}={value}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.environ[env_var] = value
    return {"saved": True}


# ---------------------------------------------------------------------------
# Reply Detection
# ---------------------------------------------------------------------------

@router.get("/api/replies/status")
def reply_status():
    try:
        from reply_detector import get_reply_status
        return get_reply_status()
    except Exception as e:
        return {"imap_configured": False, "error": str(e)}


@router.post("/api/replies/scan")
def scan_replies(request: dict = None):
    days_back = 7
    if request:
        days_back = request.get("days_back", 7)
    try:
        from reply_detector import scan_for_replies
        return scan_for_replies(days_back=days_back)
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/api/replies/mark")
def mark_reply(request: dict):
    email_addr = request.get("email", "")
    if not email_addr:
        raise HTTPException(status_code=400, detail="email is required")
    try:
        from reply_detector import mark_lead_replied
        return mark_lead_replied(email_addr)
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/leads/status-summary")
def lead_status_summary():
    try:
        from app.database import count_leads, get_leads
        total = count_leads()
        new = count_leads(status="new")
        contacted = count_leads(status="contacted")
        ready = count_leads(outreach_ready=True)
        return {"total": total, "by_status": {"new": new, "contacted": contacted, "outreach_ready": ready}}
    except Exception:
        leads = _load_json("leads.json")
        summary = {}
        for lead in leads:
            if isinstance(lead, dict):
                status = lead.get("status", "new")
                summary[status] = summary.get(status, 0) + 1
        return {"total": len(leads), "by_status": summary}


# ---------------------------------------------------------------------------
# Outreach Preview + Confirm flow
# ---------------------------------------------------------------------------

@router.post("/api/outreach/preview")
def outreach_preview(request: dict):
    """Preview leads ready for outreach. Returns candidates for user to review."""
    limit = int(request.get("limit", 25))
    channel = request.get("channel", "email")
    try:
        from app.database import get_outreach_candidates
        candidates = get_outreach_candidates(limit=limit)
    except Exception:
        candidates = []
    # Resolve channels
    for c in candidates:
        resolved = channel
        if channel == "auto":
            if c.get("business_email"):
                resolved = "email"
            elif c.get("phone"):
                resolved = "phone/sms"
            else:
                resolved = "none"
        c["channel"] = resolved
    return {"success": True, "candidates": candidates, "count": len(candidates)}


_send_jobs = {}

@router.post("/api/outreach/send")
def outreach_send(request: dict):
    """Send emails to confirmed lead IDs only. Runs in background to avoid timeout."""
    lead_ids = request.get("lead_ids", [])
    channel = request.get("channel", "email")
    if not lead_ids:
        raise HTTPException(status_code=400, detail="lead_ids is required")

    import threading
    job_id = str(int(time.time() * 1000))
    _send_jobs[job_id] = {"status": "running", "sent": 0, "failed": 0, "message": ""}

    def _do_send():
        try:
            from outreach_agent import OutreachAgent
            agent = OutreachAgent()
            result = agent.run(mode="send", lead_ids=lead_ids, channel=channel)
            _send_jobs[job_id] = {
                "status": "done",
                "sent": result.stats.get("sent", 0),
                "failed": result.stats.get("failed", 0),
                "message": result.message,
            }
            logger.info("Background send [%s] complete: %s", job_id, result.message)
        except Exception as e:
            _send_jobs[job_id] = {"status": "error", "sent": 0, "failed": len(lead_ids), "message": str(e)}
            logger.error("Background send [%s] failed: %s", job_id, e)

    t = threading.Thread(target=_do_send, daemon=True)
    t.start()

    return {
        "success": True,
        "message": f"Sending to {len(lead_ids)} leads in background. Check status with job_id.",
        "stats": {"queued": len(lead_ids)},
        "job_id": job_id,
    }


@router.get("/api/outreach/status/{job_id}")
def outreach_status(job_id: str):
    """Check background send job status."""
    job = _send_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/api/leads/list")
def list_leads_db(status: str = None, lead_type: str = None,
                  limit: int = 500, offset: int = 0):
    """List leads from the database."""
    try:
        from app.database import get_leads, count_leads
        leads = get_leads(status=status, lead_type=lead_type, limit=limit, offset=offset)
        total = count_leads(status=status)
        return {"leads": leads, "total": total}
    except Exception:
        return {"leads": [], "total": 0}


@router.get("/api/leads/count")
def leads_count():
    try:
        from app.database import count_leads
        return {
            "total": count_leads(),
            "new": count_leads(status="new"),
            "contacted": count_leads(status="contacted"),
            "outreach_ready": count_leads(outreach_ready=True),
        }
    except Exception:
        return {"total": 0, "new": 0, "contacted": 0, "outreach_ready": 0}


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

@router.get("/api/campaigns")
def list_campaigns():
    try:
        from campaign import get_campaign_manager
        cm = get_campaign_manager()
        campaigns = cm.list_campaigns()
        return [{"id": c.campaign_id, "name": c.name, "description": c.description,
                 "status": c.status, "steps": len(c.steps),
                 "created_at": c.created_at} for c in campaigns]
    except Exception:
        return []


@router.post("/api/campaigns")
def create_campaign(request: dict):
    try:
        from campaign import get_campaign_manager
        cm = get_campaign_manager()
        campaign_id = cm.create_campaign(
            name=request.get("name", "Untitled Campaign"),
            description=request.get("description", ""),
            steps=request.get("steps", []),
        )
        return {"success": True, "campaign_id": campaign_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/campaigns/{campaign_id}")
def get_campaign(campaign_id: int):
    try:
        from campaign import get_campaign_manager
        cm = get_campaign_manager()
        camp = cm.get_campaign(campaign_id)
        if not camp:
            raise HTTPException(status_code=404, detail="Campaign not found")
        stats = cm.get_campaign_stats(campaign_id)
        return {
            "id": camp.campaign_id, "name": camp.name, "description": camp.description,
            "status": camp.status, "created_at": camp.created_at,
            "steps": [{"id": s.step_id, "order": s.step_order, "day_offset": s.day_offset,
                       "subject": s.subject_template, "body": s.body_template,
                       "channel": s.channel, "ai": s.is_ai_generated} for s in camp.steps],
            "stats": stats,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/campaigns/{campaign_id}/status")
def update_campaign_status(campaign_id: int, request: dict):
    try:
        from campaign import get_campaign_manager
        cm = get_campaign_manager()
        cm.update_campaign_status(campaign_id, request.get("status", "active"))
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int):
    try:
        from campaign import get_campaign_manager
        cm = get_campaign_manager()
        cm.delete_campaign(campaign_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/campaigns/{campaign_id}/enroll")
def enroll_leads(campaign_id: int, request: dict):
    try:
        from campaign import get_campaign_manager
        cm = get_campaign_manager()
        result = cm.enroll_leads(campaign_id, request.get("lead_ids", []))
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/campaigns/{campaign_id}/enrollments")
def get_enrollments(campaign_id: int):
    try:
        from campaign import get_campaign_manager
        cm = get_campaign_manager()
        return cm.get_enrollments(campaign_id)
    except Exception as e:
        return []


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

@router.get("/api/schedules")
def list_schedules():
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        schedules = sched.list_schedules()
        return [{"id": s.schedule_id, "name": s.name, "agent": s.agent_name,
                 "interval_minutes": s.interval_minutes, "enabled": s.enabled,
                 "config": s.config, "last_run_at": s.last_run_at,
                 "next_run_at": s.next_run_at} for s in schedules]
    except Exception:
        return []


@router.post("/api/schedules")
def create_schedule(request: dict):
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        schedule_id = sched.create_schedule(
            name=request.get("name", "unnamed"),
            agent_name=request.get("agent_name", ""),
            interval_minutes=int(request.get("interval_minutes", 60)),
            config=request.get("config", {}),
            enabled=request.get("enabled", True),
        )
        return {"success": True, "schedule_id": schedule_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/schedules/{schedule_id}/run")
def run_schedule_now(schedule_id: int):
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        result = sched.run_schedule_now(schedule_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/schedules/{schedule_id}/toggle")
def toggle_schedule(schedule_id: int, request: dict):
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        sched.toggle_schedule(schedule_id, request.get("enabled", True))
        return {"success": True}
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


@router.get("/api/schedules/{schedule_id}/runs")
def schedule_runs(schedule_id: int, limit: int = 50):
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        return sched.get_run_history(schedule_id, limit)
    except Exception:
        return []


@router.get("/api/scheduler/status")
def scheduler_status():
    try:
        from scheduler import get_scheduler
        sched = get_scheduler()
        return {"running": sched.is_running, "schedules": len(sched.list_schedules())}
    except Exception:
        return {"running": False, "schedules": 0}


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

@router.get("/api/daemon/status")
def daemon_status():
    try:
        from daemon import get_daemon
        d = get_daemon()
        return d.status()
    except Exception:
        return {"running": False}


@router.post("/api/daemon/start")
def daemon_start():
    try:
        from daemon import get_daemon
        d = get_daemon()
        d.start()
        return {"success": True, "message": "Daemon started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/daemon/stop")
def daemon_stop():
    try:
        from daemon import get_daemon
        d = get_daemon()
        d.stop()
        return {"success": True, "message": "Daemon stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/daemon/restart")
def daemon_restart(request: dict = None):
    try:
        from daemon import get_daemon
        d = get_daemon()
        interval = request.get("interval_seconds") if request else None
        d.restart(interval_seconds=interval)
        return {"success": True, "message": "Daemon restarted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/daemon/log")
def daemon_log(limit: int = 50):
    try:
        from daemon import get_daemon
        d = get_daemon()
        return d.get_log(limit)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Email Deliverability
# ---------------------------------------------------------------------------

@router.get("/api/email/health")
def email_health():
    try:
        from email_sender import get_email_sender
        sender = get_email_sender()
        return sender.check_health()
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/email/rate-status")
def email_rate_status():
    try:
        from send_limiter import get_send_limiter
        limiter = get_send_limiter()
        return limiter.get_all_status()
    except Exception:
        return {}


@router.post("/api/email/verify")
def verify_email(request: dict):
    email = request.get("email", "")
    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    try:
        from email_verifier import EmailVerifier
        v = EmailVerifier(check_smtp=request.get("check_smtp", False))
        return v.verify(email)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/email/check-spam")
def check_spam(request: dict):
    try:
        from spam_checker import SpamChecker
        checker = SpamChecker()
        return checker.check(
            subject=request.get("subject", ""),
            body=request.get("body", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
