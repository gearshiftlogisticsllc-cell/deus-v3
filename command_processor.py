"""
command_processor.py — DEUS 3.0
=================================
Parses user commands (natural language or direct) and routes them
to the correct agent or pipeline. Returns structured results.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from pipeline import Pipeline, PipelineResult, get_agent_class, get_available_agents, list_pipelines
from base_agent import AgentResult, make_result

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    success: bool
    message: str
    agent_name: str = ""
    pipeline_name: str = ""
    data: any = None
    result: Optional[AgentResult] = None
    pipeline_result: Optional[PipelineResult] = None


# ---------------------------------------------------------------------------
# Command aliases — maps user words to agent names or pipeline actions
# ---------------------------------------------------------------------------

AGENT_ALIASES = {
    "scout": "lead_scout_agent",
    "lead_scout": "lead_scout_agent",
    "leads": "lead_scout_agent",
    "outreach": "outreach_agent",
    "email": "outreach_agent",
    "followup": "followup_agent",
    "follow": "followup_agent",
    "reengagement": "followup_agent",
    "appointment": "appointment_agent",
    "calendly": "appointment_agent",
    "book": "appointment_agent",
    "closer": "deal_closer_agent",
    "deal": "deal_closer_agent",
    "report": "report_agent",
    "summary": "report_agent",
    "health": "system_checker_agent",
    "check": "system_checker_agent",
    "system": "system_checker_agent",
    "status": "system_checker_agent",
}

PIPELINE_ALIASES = {
    "full": "full_auto",
    "full_auto": "full_auto",
    "fullauto": "full_auto",
    "pipeline": "full_auto",
    "auto": "full_auto",
    "scout_only": "scout_only",
    "scout": "scout_only",
    "outreach_cycle": "outreach_cycle",
    "cycle": "outreach_cycle",
    "daily_report": "daily_report",
    "daily": "daily_report",
}


# ---------------------------------------------------------------------------
# Command processor
# ---------------------------------------------------------------------------

class CommandProcessor:
    def __init__(self):
        self.last_result: Optional[CommandResult] = None

    def process(self, raw_input: str) -> CommandResult:
        """Parse and execute a user command string."""
        text = (raw_input or "").strip()
        if not text:
            return CommandResult(False, "No command entered. Type 'help' for available commands.")

        lower = text.lower()
        parts = lower.split()
        command = parts[0]
        args = parts[1:]

        # --- Help ---
        if command in ("help", "?"):
            return self._cmd_help()

        # --- List ---
        if command in ("list", "ls"):
            return self._cmd_list()

        # --- Quit ---
        if command in ("quit", "exit", "q"):
            return CommandResult(True, "quit")

        # --- Run pipeline ---
        if command == "run":
            return self._cmd_run(args)

        # --- Agent direct ---
        if command in AGENT_ALIASES:
            return self._cmd_agent(command, args)

        # --- Pipeline by alias ---
        if command in PIPELINE_ALIASES:
            pipeline_name = PIPELINE_ALIASES[command]
            return self._cmd_run_pipeline(pipeline_name, args)

        # --- Unknown ---
        return CommandResult(
            False,
            f"Unknown command: '{command}'. Type 'help' for available commands.",
        )

    # ----- Built-in commands -----

    def _cmd_help(self) -> CommandResult:
        lines = [
            "DEUS 3.0 — Available Commands:",
            "",
            "  Agents:",
            "    scout / leads      — Run lead discovery",
            "    outreach / email   — Send outreach messages",
            "    followup / follow  — Send follow-ups to contacted leads",
            "    appointment / book — Manage Calendly appointments",
            "    closer / deal      — Process due appointments",
            "    report / summary   — Generate daily report",
            "    health / status    — Run system health check",
            "",
            "  Pipelines:",
            "    run full_auto      — Run full pipeline (all agents)",
            "    run scout_only     — Scout only",
            "    run outreach_cycle — Followup + Deal Closer",
            "    run daily_report   — Generate and email report",
            "",
            "  Other:",
            "    list / ls          — List pipelines and agents",
            "    help / ?           — Show this help",
            "    quit / exit        — Exit",
        ]
        return CommandResult(True, "\n".join(lines))

    def _cmd_list(self) -> CommandResult:
        pipelines = list_pipelines()
        agents = get_available_agents()

        lines = ["Pipelines:"]
        for name, info in pipelines.items():
            steps_str = " -> ".join(info["steps"])
            lines.append(f"  {name:20s} — {info['description']}")
            lines.append(f"  {'':20s}   Steps: {steps_str}")

        lines.append("")
        lines.append("Agents:")
        for a in agents:
            lines.append(f"  {a}")

        return CommandResult(True, "\n".join(lines))

    def _cmd_run(self, args: list) -> CommandResult:
        if not args:
            # Default to full_auto
            return self._cmd_run_pipeline("full_auto", [])

        pipeline_name = PIPELINE_ALIASES.get(args[0], args[0])
        extra_kwargs = self._parse_kwargs(args[1:])
        return self._cmd_run_pipeline(pipeline_name, extra_kwargs)

    def _cmd_run_pipeline(self, pipeline_name: str, extra_kwargs: list) -> CommandResult:
        try:
            pipeline = Pipeline(pipeline_name=pipeline_name)
        except Exception as e:
            return CommandResult(False, f"Failed to load pipeline '{pipeline_name}': {e}")

        if not pipeline.steps:
            return CommandResult(False, f"Pipeline '{pipeline_name}' has no steps.")

        kwargs = self._parse_kwargs(extra_kwargs)
        result = pipeline.run(**kwargs)

        self.last_result = CommandResult(
            success=result.success,
            message=result.message,
            pipeline_name=pipeline_name,
            pipeline_result=result,
        )
        return self.last_result

    def _cmd_agent(self, alias: str, args: list) -> CommandResult:
        agent_name = AGENT_ALIASES.get(alias)
        if not agent_name:
            return CommandResult(False, f"Unknown agent alias: '{alias}'")

        agent_cls = get_agent_class(agent_name)
        if agent_cls is None:
            return CommandResult(False, f"Agent class '{agent_name}' not found.")

        try:
            agent = agent_cls()
        except Exception as e:
            return CommandResult(False, f"Failed to initialize {agent_name}: {e}")

        kwargs = self._parse_kwargs(args)
        try:
            result = agent.run(**kwargs)
        except Exception as e:
            result = make_result(False, f"{agent_name} failed: {e}")

        self.last_result = CommandResult(
            success=result.success,
            message=result.message,
            agent_name=agent_name,
            data=result.data,
            result=result,
        )
        return self.last_result

    # ----- Helpers -----

    @staticmethod
    def _parse_kwargs(args: list) -> dict:
        """Parse key=value pairs from args list."""
        kwargs = {}
        for arg in args:
            if "=" in arg:
                k, v = arg.split("=", 1)
                # Try to parse numbers
                try:
                    v = int(v)
                except ValueError:
                    try:
                        v = float(v)
                    except ValueError:
                        pass
                kwargs[k] = v
            else:
                # Bare arg — treat as target or user_input
                if arg.isdigit():
                    kwargs["target"] = int(arg)
                else:
                    kwargs.setdefault("user_input", arg)
        return kwargs
