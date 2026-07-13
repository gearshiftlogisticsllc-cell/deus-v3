"""
base_agent.py — DEUS 3.0 Abstract Agent Interface
===================================================
All agents inherit from BaseAgent. Provides standardized attributes,
methods, and return types so the GUI, pipeline, and CLI can treat
every agent uniformly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import os
import time


@dataclass
class AgentResult:
    """Structured return type from every agent.run() call."""
    success: bool
    message: str
    data: Any = None            # agent-specific data (leads list, stats dict, etc.)
    stats: dict = field(default_factory=dict)  # numeric stats for the pipeline/GUI
    duration_seconds: float = 0.0

    def __str__(self):
        return self.message


@dataclass
class AgentHealth:
    """Health check result from check_health()."""
    healthy: bool
    status: str                 # "ready", "degraded", "error"
    message: str
    details: dict = field(default_factory=dict)

    def __str__(self):
        return f"[{self.status}] {self.message}"


class BaseAgent(ABC):
    """
    Abstract base class for all DEUS 3.0 agents.

    Subclasses MUST set these class attributes:
        name            = "AgentClassName"      # e.g. "LeadScoutAgent"
        display_name    = "Lead Scout"          # human-readable
        description     = "Finds business leads..."

    Subclasses MUST implement:
        run(**kwargs) -> AgentResult
        check_health() -> AgentHealth

    Subclasses SHOULD implement (optional with defaults):
        think(prompt) -> str
        report() -> str
    """

    name: str = "BaseAgent"
    display_name: str = "Base Agent"
    description: str = ""
    requires_keys: list = field(default_factory=list)

    @abstractmethod
    def run(self, **kwargs) -> AgentResult:
        """Execute the agent's core logic. Returns AgentResult."""
        ...

    def think(self, prompt: str) -> str:
        """Send a prompt to the LLM and return the response.
        Override in subclasses that have LLM access."""
        return ""

    def report(self) -> str:
        """Return a human-readable status report.
        Override in subclasses for agent-specific reporting."""
        return f"{self.display_name}: Ready"

    @abstractmethod
    def check_health(self) -> AgentHealth:
        """Verify the agent can run (API keys set, dependencies available).
        Returns AgentHealth with status and details."""
        ...

    def _check_keys(self) -> dict:
        """Helper: check which required API keys are set."""
        results = {}
        for key in self.requires_keys:
            val = os.getenv(key, "").strip()
            results[key] = bool(val)
        return results

    def _time_run(self, fn, *args, **kwargs) -> tuple:
        """Helper: time a function call and return (result, duration)."""
        start = time.time()
        result = fn(*args, **kwargs)
        duration = time.time() - start
        return result, duration


def make_result(success: bool, message: str, data=None, stats=None, duration=0.0) -> AgentResult:
    """Convenience constructor for AgentResult."""
    return AgentResult(
        success=success,
        message=message,
        data=data,
        stats=stats or {},
        duration_seconds=duration,
    )


def make_health(healthy: bool, status: str, message: str, details=None) -> AgentHealth:
    """Convenience constructor for AgentHealth."""
    return AgentHealth(
        healthy=healthy,
        status=status,
        message=message,
        details=details or {},
    )
