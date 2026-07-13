"""
pipeline.py — DEUS 3.0
========================
Pipeline orchestrator: loads named configs, instantiates agents,
evaluates conditions, runs steps sequentially, returns structured results.
"""

import os
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from base_agent import BaseAgent, AgentResult, make_result

logger = logging.getLogger(__name__)

PIPELINE_CONFIG_FILE = "pipeline_config.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PipelineStep:
    agent_name: str
    enabled: bool = True
    config: dict = field(default_factory=dict)
    condition: Optional[str] = None


@dataclass
class StepResult:
    agent_name: str
    display_name: str
    result: AgentResult
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class PipelineResult:
    pipeline_name: str
    success: bool
    message: str
    steps: list = field(default_factory=list)   # list[StepResult]
    total_duration: float = 0.0
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent registry — maps config names to classes
# ---------------------------------------------------------------------------

_AGENT_CLASSES: dict = {}


def _load_agent_classes():
    global _AGENT_CLASSES
    if _AGENT_CLASSES:
        return

    try:
        from lead_scout_agent import LeadScoutAgent
        _AGENT_CLASSES["lead_scout_agent"] = LeadScoutAgent
    except ImportError as e:
        logger.warning("Could not import LeadScoutAgent: %s", e)

    try:
        from outreach_agent import OutreachAgent
        _AGENT_CLASSES["outreach_agent"] = OutreachAgent
    except ImportError as e:
        logger.warning("Could not import OutreachAgent: %s", e)

    try:
        from followup_agent import FollowupAgent
        _AGENT_CLASSES["followup_agent"] = FollowupAgent
    except ImportError as e:
        logger.warning("Could not import FollowupAgent: %s", e)

    try:
        from appointment_agent import AppointmentAgent
        _AGENT_CLASSES["appointment_agent"] = AppointmentAgent
    except ImportError as e:
        logger.warning("Could not import AppointmentAgent: %s", e)

    try:
        from deal_closer_agent import DealCloserAgent
        _AGENT_CLASSES["deal_closer_agent"] = DealCloserAgent
    except ImportError as e:
        logger.warning("Could not import DealCloserAgent: %s", e)

    try:
        from report_agent import ReportAgent
        _AGENT_CLASSES["report_agent"] = ReportAgent
    except ImportError as e:
        logger.warning("Could not import ReportAgent: %s", e)

    try:
        from system_checker_agent import SystemCheckerAgent
        _AGENT_CLASSES["system_checker_agent"] = SystemCheckerAgent
    except ImportError as e:
        logger.warning("Could not import SystemCheckerAgent: %s", e)


def get_agent_class(name: str) -> Optional[type]:
    _load_agent_classes()
    return _AGENT_CLASSES.get(name)


def get_available_agents() -> list:
    _load_agent_classes()
    return list(_AGENT_CLASSES.keys())


# ---------------------------------------------------------------------------
# Condition evaluator
# ---------------------------------------------------------------------------

def _load_json_safe(path: str) -> list:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def evaluate_condition(condition: str) -> bool:
    """Returns True if the condition is met (agent should run)."""
    if not condition:
        return True

    checks = {
        "has_outreach_ready_leads": _check_outreach_ready,
        "has_contacted_leads": _check_contacted,
        "has_calendly_key": _check_calendly,
        "has_scheduled_appointments": _check_scheduled,
        "always": lambda: True,
    }

    checker = checks.get(condition)
    if checker is None:
        logger.warning("Unknown condition '%s' — defaulting to True.", condition)
        return True

    return checker()


def _check_outreach_ready() -> bool:
    leads = _load_json_safe("leads.json")
    return any(l.get("outreach_ready") and l.get("status") != "contacted" for l in leads if isinstance(l, dict))


def _check_contacted() -> bool:
    leads = _load_json_safe("leads.json")
    return any(l.get("status") == "contacted" for l in leads if isinstance(l, dict))


def _check_calendly() -> bool:
    return bool(os.getenv("CALENDLY_API_KEY", ""))


def _check_scheduled() -> bool:
    appointments = _load_json_safe("appointments.json")
    return any(a.get("status") == "scheduled" for a in appointments if isinstance(a, dict))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    def __init__(self, pipeline_name: str = "full_auto", custom_steps: list = None):
        _load_agent_classes()
        self.pipeline_name = pipeline_name
        self.steps: list[PipelineStep] = []
        self.progress_callback: Optional[Callable] = None

        if custom_steps:
            for s in custom_steps:
                if isinstance(s, PipelineStep):
                    self.steps.append(s)
                elif isinstance(s, dict):
                    self.steps.append(PipelineStep(
                        agent_name=s.get("agent", ""),
                        enabled=s.get("enabled", True),
                        config=s.get("config", {}),
                        condition=s.get("condition"),
                    ))
        else:
            self._load_from_config(pipeline_name)

    def _load_from_config(self, name: str):
        try:
            with open(PIPELINE_CONFIG_FILE, "r") as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.error("Cannot load %s", PIPELINE_CONFIG_FILE)
            return

        pipeline_cfg = config.get("pipelines", {}).get(name)
        if not pipeline_cfg:
            logger.error("Pipeline '%s' not found in config.", name)
            return

        for step_cfg in pipeline_cfg.get("steps", []):
            self.steps.append(PipelineStep(
                agent_name=step_cfg.get("agent", ""),
                enabled=step_cfg.get("enabled", True),
                config=step_cfg.get("config", {}),
                condition=step_cfg.get("condition"),
            ))

    def set_progress_callback(self, callback: Callable):
        """callback(step_index, total_steps, agent_name, status, message)"""
        self.progress_callback = callback

    def _notify(self, step_idx: int, total: int, agent_name: str, status: str, msg: str = ""):
        if self.progress_callback:
            try:
                self.progress_callback(step_idx, total, agent_name, status, msg)
            except Exception:
                pass

    def run(self, **kwargs) -> PipelineResult:
        start = time.time()
        step_results: list[StepResult] = []
        total = len(self.steps)

        if total == 0:
            return PipelineResult(
                pipeline_name=self.pipeline_name,
                success=False,
                message="No steps configured.",
                total_duration=0.0,
            )

        logger.info("Pipeline '%s' starting (%d steps).", self.pipeline_name, total)

        for idx, step in enumerate(self.steps):
            agent_name = step.agent_name

            # Notify: step starting
            self._notify(idx, total, agent_name, "starting")

            # Check enabled
            if not step.enabled:
                skip_result = StepResult(
                    agent_name=agent_name,
                    display_name=agent_name,
                    result=make_result(True, "Step disabled."),
                    skipped=True,
                    skip_reason="disabled",
                )
                step_results.append(skip_result)
                self._notify(idx, total, agent_name, "skipped", "disabled")
                logger.info("  [%d/%d] %s — SKIPPED (disabled)", idx + 1, total, agent_name)
                continue

            # Check condition
            if step.condition and not evaluate_condition(step.condition):
                skip_result = StepResult(
                    agent_name=agent_name,
                    display_name=agent_name,
                    result=make_result(True, f"Condition not met: {step.condition}"),
                    skipped=True,
                    skip_reason=f"condition: {step.condition}",
                )
                step_results.append(skip_result)
                self._notify(idx, total, agent_name, "skipped", f"condition: {step.condition}")
                logger.info("  [%d/%d] %s — SKIPPED (condition: %s)", idx + 1, total, agent_name, step.condition)
                continue

            # Instantiate agent
            agent_cls = get_agent_class(agent_name)
            if agent_cls is None:
                err_result = StepResult(
                    agent_name=agent_name,
                    display_name=agent_name,
                    result=make_result(False, f"Agent class not found for '{agent_name}'."),
                )
                step_results.append(err_result)
                self._notify(idx, total, agent_name, "error", "agent class not found")
                logger.error("  [%d/%d] %s — ERROR (class not found)", idx + 1, total, agent_name)
                continue

            try:
                agent = agent_cls()
            except Exception as e:
                err_result = StepResult(
                    agent_name=agent_name,
                    display_name=agent_name,
                    result=make_result(False, f"Failed to instantiate {agent_name}: {e}"),
                )
                step_results.append(err_result)
                self._notify(idx, total, agent_name, "error", str(e))
                logger.error("  [%d/%d] %s — ERROR (init: %s)", idx + 1, total, agent_name, e)
                continue

            # Run agent
            display = getattr(agent, "display_name", agent_name)
            self._notify(idx, total, agent_name, "running")

            try:
                merged_config = {**step.config, **kwargs}
                agent_result = agent.run(**merged_config)
            except Exception as e:
                agent_result = make_result(False, f"{agent_name} failed: {e}")

            step_results.append(StepResult(
                agent_name=agent_name,
                display_name=display,
                result=agent_result,
            ))

            status = "ok" if agent_result.success else "error"
            self._notify(idx, total, agent_name, status, agent_result.message[:100])
            logger.info("  [%d/%d] %s — %s (%.1fs)",
                        idx + 1, total, display, status, agent_result.duration_seconds)

        # Aggregate
        duration = time.time() - start
        all_ok = all(
            sr.result.success or sr.skipped
            for sr in step_results
        )
        total_stats = {}
        for sr in step_results:
            if sr.result.stats:
                for k, v in sr.result.stats.items():
                    total_stats[f"{sr.agent_name}.{k}"] = v

        summary_lines = []
        for sr in step_results:
            tag = "SKIP" if sr.skipped else ("OK" if sr.result.success else "FAIL")
            summary_lines.append(f"  [{tag}] {sr.display_name}: {sr.result.message[:80]}")

        summary = f"Pipeline '{self.pipeline_name}' completed in {duration:.1f}s\n" + "\n".join(summary_lines)

        return PipelineResult(
            pipeline_name=self.pipeline_name,
            success=all_ok,
            message=summary,
            steps=step_results,
            total_duration=duration,
            stats=total_stats,
        )


def list_pipelines() -> dict:
    """Returns {name: {name, description, steps}} from pipeline_config.json."""
    try:
        with open(PIPELINE_CONFIG_FILE, "r") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    out = {}
    for name, cfg in config.get("pipelines", {}).items():
        out[name] = {
            "name": cfg.get("name", name),
            "description": cfg.get("description", ""),
            "steps": [s.get("agent", "") for s in cfg.get("steps", []) if s.get("enabled", True)],
        }
    return out
