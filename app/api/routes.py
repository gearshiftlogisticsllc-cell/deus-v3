"""
app/api/routes.py — DEUS 3.0 FastAPI routes
"""

import os
import sys
import time

from fastapi import APIRouter, HTTPException
from typing import Optional

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
