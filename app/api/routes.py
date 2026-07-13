"""
app/api/routes.py — DEUS 3.0 FastAPI routes
"""

import os
import sys
import json
import time
import csv
import io
from typing import Optional

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
    smtp = bool(os.getenv("SMTP_EMAIL", ""))

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
        }
    except Exception:
        return {
            "tone": "professional", "max_words": 150,
            "subject_template": "Quick question about your business, {business_name}",
            "call_to_action": "Would you be open to a quick 15-minute call this week?",
            "signature": "Best regards,\nGrowthDesk VA Team",
            "use_custom_template": False, "custom_template": "",
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


@router.post("/api/outreach/send")
def outreach_send(request: dict):
    """Send emails to confirmed lead IDs only."""
    lead_ids = request.get("lead_ids", [])
    channel = request.get("channel", "email")
    if not lead_ids:
        raise HTTPException(status_code=400, detail="lead_ids is required")
    try:
        from outreach_agent import OutreachAgent
        agent = OutreachAgent()
        result = agent.run(mode="send", lead_ids=lead_ids, channel=channel)
        return {"success": result.success, "message": result.message, "stats": result.stats}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api/leads/list")
def list_leads_db(status: str = None, limit: int = 500, offset: int = 0):
    """List leads from the database."""
    try:
        from app.database import get_leads
        leads = get_leads(status=status, limit=limit, offset=offset)
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
