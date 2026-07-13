"""
nexus_bridge.py — DEUS 3.0 CLI Orchestrator
=============================================
Routes natural language commands to the correct agent.
Uses Groq for intent detection and general queries.
"""

import os
import sys
import json
import importlib
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


def _import_agent(module_name: str, class_name: str):
    """Lazy-import an agent class, returning None on failure."""
    try:
        mod = importlib.import_module(module_name)
        return getattr(mod, class_name)
    except Exception as e:
        logger.warning("Failed to import %s.%s: %s", module_name, class_name, e)
        return None


class NexusBridge:
    def __init__(self):
        self.client = None
        if GROQ_API_KEY:
            try:
                from groq import Groq
                self.client = Groq(api_key=GROQ_API_KEY)
            except Exception as e:
                logger.warning("Groq init failed: %s", e)
        self._cache = {}

    def _get_agent(self, name: str):
        if name not in self._cache:
            registry = {
                "LeadScoutAgent": ("lead_scout_agent", "LeadScoutAgent"),
                "OutreachAgent": ("outreach_agent", "OutreachAgent"),
                "FollowupAgent": ("followup_agent", "FollowupAgent"),
                "AppointmentAgent": ("appointment_agent", "AppointmentAgent"),
                "DealCloserAgent": ("deal_closer_agent", "DealCloserAgent"),
                "ReportAgent": ("report_agent", "ReportAgent"),
                "SystemCheckerAgent": ("system_checker_agent", "SystemCheckerAgent"),
            }
            if name in registry:
                mod, cls = registry[name]
                self._cache[name] = _import_agent(mod, cls)
            else:
                self._cache[name] = None
        return self._cache[name]

    def think(self, prompt: str) -> str:
        if not self.client:
            return "[ERROR: GROQ_API_KEY not configured]"
        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Groq request failed: %s", e)
            return f"[Error: {e}]"

    def detect_intent(self, task: str) -> str:
        tl = task.lower()
        if any(k in tl for k in ("scout", "find lead", "search lead", "discover", "find business")):
            return "lead_scout"
        if any(k in tl for k in ("outreach", "email", "contact", "send message")):
            return "outreach"
        if any(k in tl for k in ("follow", "followup", "re-engage", "reengage")):
            return "followup"
        if any(k in tl for k in ("appointment", "calendly", "book", "schedule", "meeting")):
            return "appointment"
        if any(k in tl for k in ("deal", "close", "closer")):
            return "deal_closer"
        if any(k in tl for k in ("report", "summary", "stats", "status")):
            return "report"
        if any(k in tl for k in ("check", "health", "system")):
            return "system_checker"
        return "general"

    def run_task(self, task: str) -> str:
        intent = self.detect_intent(task)
        logger.info("Intent detected: %s", intent)

        agent_map = {
            "lead_scout": ("LeadScoutAgent", "run"),
            "outreach": ("OutreachAgent", "run"),
            "followup": ("FollowupAgent", "run"),
            "appointment": ("AppointmentAgent", "run"),
            "deal_closer": ("DealCloserAgent", "run"),
            "report": ("ReportAgent", "run"),
            "system_checker": ("SystemCheckerAgent", "report"),
        }

        if intent in agent_map:
            name, method = agent_map[intent]
            cls = self._get_agent(name)
            if cls is None:
                return f"[{name}] Agent not available (import failed). Check dependencies."
            try:
                agent = cls()
                fn = getattr(agent, method, None)
                if fn is None:
                    return f"[{name}] Method '{method}' not found."
                result = fn(task)
                return str(result)
            except TypeError:
                try:
                    agent = cls()
                    return str(agent.run())
                except Exception as e:
                    return f"[{name}] Error: {e}"
            except Exception as e:
                return f"[{name}] Error: {e}"

        return self.think(task)

    def status(self) -> str:
        lines = []
        for name in ["LeadScoutAgent", "OutreachAgent", "FollowupAgent",
                       "AppointmentAgent", "DealCloserAgent", "ReportAgent",
                       "SystemCheckerAgent"]:
            cls = self._get_agent(name)
            lines.append(f"  {name}: {'Ready' if cls else 'Unavailable'}")
        return "NEXUS Bridge Status:\n" + "\n".join(lines)

    def start(self):
        print("\n" + "=" * 55)
        print("  DEUS / NEXUS Bridge  —  Multi-Agent Orchestrator")
        print("=" * 55)
        print("  Type a task or 'quit' to exit.")
        print("  Examples:")
        print("    > find leads for dental clinics in Lahore")
        print("    > run outreach for all ready leads")
        print("    > show me today's report")
        print("    > system health check")
        print("=" * 55 + "\n")

        while True:
            try:
                task = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not task:
                continue
            if task.lower() in ("quit", "exit"):
                break
            result = self.run_task(task)
            print(result)
            print()


if __name__ == "__main__":
    bridge = NexusBridge()
    bridge.start()
