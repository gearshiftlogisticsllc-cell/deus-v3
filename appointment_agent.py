"""
appointment_agent.py — DEUS 3.0
================================
Reads Calendly events, recommends booking links, uses AI for scheduling advice.
Groq is primary LLM. Gemini is fallback. Both optional (Calendly still works).
"""

import os
import time
import logging
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health
from rules_engine import get_rules_context

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CALENDLY_API_KEY = os.getenv("CALENDLY_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class CalendlyClient:
    BASE_URL = "https://api.calendly.com"

    def __init__(self, api_key: str):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.user_uri = self._get_current_user_uri()

    def _get_current_user_uri(self) -> str:
        try:
            resp = requests.get(f"{self.BASE_URL}/users/me", headers=self.headers, timeout=15)
            if resp.status_code != 200:
                return ""
            return resp.json()["resource"]["uri"]
        except Exception:
            return ""

    def get_event_types(self) -> list:
        if not self.user_uri:
            return []
        try:
            resp = requests.get(
                f"{self.BASE_URL}/event_types",
                headers=self.headers,
                params={"user": self.user_uri, "active": True},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            return resp.json().get("collection", [])
        except Exception:
            return []

    def get_scheduled_events(self, days_ahead: int = 7) -> list:
        if not self.user_uri:
            return []
        now = datetime.now(timezone.utc).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).isoformat()
        try:
            resp = requests.get(
                f"{self.BASE_URL}/scheduled_events",
                headers=self.headers,
                params={
                    "user": self.user_uri,
                    "min_start_time": now,
                    "max_start_time": future,
                    "status": "active",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            return resp.json().get("collection", [])
        except Exception:
            return []


class AppointmentAgent(BaseAgent):
    name = "AppointmentAgent"
    display_name = "Appointment"
    description = "Manages Calendly scheduling and appointment recommendations."
    requires_keys = ["CALENDLY_API_KEY"]

    def __init__(self):
        self.groq_client = None
        self.gemini_client = None

        if GROQ_API_KEY:
            try:
                from groq import Groq
                self.groq_client = Groq(api_key=GROQ_API_KEY)
            except Exception as e:
                logger.warning("Groq init failed: %s", e)

        if GEMINI_API_KEY:
            try:
                from google import genai
                self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            except Exception as e:
                logger.warning("Gemini init failed: %s", e)

        self.calendly = CalendlyClient(CALENDLY_API_KEY) if CALENDLY_API_KEY else None

    def think(self, prompt: str) -> str:
        if self.groq_client:
            try:
                response = self.groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                logger.warning("Groq failed: %s — trying Gemini", e)

        if self.gemini_client:
            try:
                response = self.gemini_client.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=prompt,
                )
                return response.text.strip()
            except Exception as e:
                logger.warning("Gemini failed: %s", e)

        return ""

    def _summarise_events(self, events: list) -> str:
        if not events:
            return "No upcoming appointments found."
        lines = []
        for e in events:
            event_type = e.get("event_type", "Unknown type")
            start = e.get("start_time", "Unknown time")
            uri = e.get("uri", "")
            invitees = e.get("invitees_counter", {}).get("total", "?")
            lines.append(f"  - {event_type} | {start} | {invitees} invitees | {uri}")
        return "\n".join(lines)

    def run(self, **kwargs) -> AgentResult:
        start = time.time()
        task = kwargs.get("task", "Recommend the best booking option for a new business lead.")

        if not self.calendly:
            return make_result(True, "Calendly API key not configured. Set CALENDLY_API_KEY in .env.",
                              stats={}, duration=time.time() - start)

        event_types = self.calendly.get_event_types()
        upcoming = self.calendly.get_scheduled_events(days_ahead=7)
        upcoming_summary = self._summarise_events(upcoming)

        booking_links = []
        for et in event_types:
            name = et.get("name", "Meeting")
            scheduling_url = et.get("scheduling_url", "")
            duration = et.get("duration", "?")
            if scheduling_url:
                booking_links.append(f"  - {name} ({duration} min): {scheduling_url}")

        booking_links_str = "\n".join(booking_links) if booking_links else "  (none available)"

        rules = get_rules_context()
        rules_block = f"\n\nRules/regulations that MUST be followed:\n{rules}\n" if rules else ""

        prompt = f"""You are an AI appointment scheduling assistant.

Task: {task}

Available booking links:
{booking_links_str}

Upcoming appointments (next 7 days):
{upcoming_summary}
{rules_block}
Recommend the most suitable event type and provide the booking link. Keep it concise."""

        agent_response = self.think(prompt)
        full_text = f"{agent_response}\n\n--- Upcoming Appointments ---\n{upcoming_summary}"

        stats = {
            "event_types": len(event_types),
            "upcoming_events": len(upcoming),
        }
        return make_result(True, full_text, stats=stats, duration=time.time() - start)

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        calendly_ok = bool(CALENDLY_API_KEY)
        llm_ok = keys.get("GROQ_API_KEY", False) or keys.get("GEMINI_API_KEY", False)

        if calendly_ok and llm_ok:
            return make_health(True, "ready", "Appointment agent ready (Calendly + LLM).", keys)
        if calendly_ok:
            return make_health(True, "degraded", "Calendly configured but no LLM.", keys)
        return make_health(False, "error", "CALENDLY_API_KEY not set.", keys)


if __name__ == "__main__":
    agent = AppointmentAgent()
    result = agent.run(task="Book an appointment with a business owner")
    print(result)
