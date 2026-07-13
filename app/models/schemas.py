"""
app/models/schemas.py — Pydantic request/response models for DEUS 3.0 API
"""

from pydantic import BaseModel, Field
from typing import Any, Optional


class AgentInfo(BaseModel):
    name: str
    display_name: str
    description: str
    healthy: bool
    health_status: str
    health_message: str


class AgentRunRequest(BaseModel):
    kwargs: dict = Field(default_factory=dict, description="Additional arguments to pass to the agent")


class AgentRunResponse(BaseModel):
    success: bool
    message: str
    agent_name: str
    data: Any = None
    stats: dict = Field(default_factory=dict)
    duration: float = 0.0


class PipelineInfo(BaseModel):
    name: str
    display_name: str
    description: str
    steps: list[str]


class PipelineRunRequest(BaseModel):
    kwargs: dict = Field(default_factory=dict, description="Arguments passed to all steps")


class PipelineRunResponse(BaseModel):
    success: bool
    message: str
    pipeline_name: str
    steps_completed: int
    total_duration: float
    stats: dict = Field(default_factory=dict)


class CommandRequest(BaseModel):
    command: str = Field(..., description="Command string to process")


class CommandResponse(BaseModel):
    success: bool
    message: str
    agent_name: str = ""
    pipeline_name: str = ""


class HealthResponse(BaseModel):
    status: str
    groq: bool
    gemini: bool
    calendly: bool
    smtp: bool
    agents_healthy: int
    agents_total: int
