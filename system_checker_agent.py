"""
system_checker_agent.py — DEUS 3.0
====================================
Health monitor — verifies all agents exist, packages installed, configs valid,
and API keys are set.
"""

import os
import json
import time
from dotenv import load_dotenv

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health

load_dotenv()


class SystemCheckerAgent(BaseAgent):
    name = "SystemCheckerAgent"
    display_name = "System Check"
    description = "Verifies all agents, packages, configs, and API keys."
    requires_keys = []

    def __init__(self):
        self.client = None
        self.llm = os.getenv("LLM", "groq")
        if self.llm == "groq":
            groq_key = os.getenv("GROQ_API_KEY", "")
            if groq_key:
                from groq import Groq
                self.client = Groq(api_key=groq_key)
        elif self.llm == "gemini":
            gemini_key = os.getenv("GEMINI_API_KEY", "")
            if gemini_key:
                from google import genai
                self.client = genai.Client(api_key=gemini_key)

    def think(self, prompt: str) -> str:
        try:
            if self.llm == "groq" and self.client:
                response = self.client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content.strip()
            elif self.llm == "gemini" and self.client:
                response = self.client.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=prompt,
                )
                return response.text.strip()
        except Exception:
            pass
        return ""

    def run(self, **kwargs) -> AgentResult:
        start = time.time()
        task = kwargs.get("task", "Give a brief health summary of the DEUS system.")
        response = self.think(task)
        health_text = response if response else "Health check completed (no LLM response)."
        return make_result(True, health_text, stats={}, duration=time.time() - start)

    def report(self) -> str:
        health_status = ""

        agent_files = [
            "lead_scout_agent.py", "outreach_agent.py", "followup_agent.py",
            "appointment_agent.py", "deal_closer_agent.py", "report_agent.py",
            "brain.py", "nexus_bridge.py",
        ]
        for file in agent_files:
            if os.path.exists(file) and os.path.getsize(file) > 0:
                health_status += f"{file}: HEALTHY\n"
            else:
                health_status += f"{file}: MISSING\n"

        required_packages = [
            "requests", "beautifulsoup4", "groq", "google.genai",
            "dotenv", "schedule",
        ]
        for package in required_packages:
            try:
                __import__(package)
                health_status += f"{package}: HEALTHY\n"
            except ImportError:
                health_status += f"{package}: NOT INSTALLED\n"

        config_files = ["intent_profile.json"]
        for file in config_files:
            if os.path.exists(file):
                try:
                    with open(file, "r") as f:
                        json.load(f)
                    health_status += f"{file}: HEALTHY\n"
                except json.JSONDecodeError:
                    health_status += f"{file}: CORRUPTED\n"
            else:
                health_status += f"{file}: MISSING\n"

        data_files = ["leads.json", "outreach_log.json"]
        for file in data_files:
            if os.path.exists(file):
                try:
                    with open(file, "r") as f:
                        data = json.load(f)
                    count = len(data) if isinstance(data, list) else 0
                    health_status += f"{file}: OK ({count} records)\n"
                except json.JSONDecodeError:
                    health_status += f"{file}: CORRUPTED\n"
            else:
                health_status += f"{file}: NOT FOUND\n"

        api_keys = {
            "GROQ_API_KEY": os.getenv("GROQ_API_KEY", ""),
            "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
            "SMTP_EMAIL": os.getenv("SMTP_EMAIL", ""),
            "CALENDLY_API_KEY": os.getenv("CALENDLY_API_KEY", ""),
            "SERPER_API_KEY": os.getenv("SERPER_API_KEY", ""),
            "BRIGHTDATA_API_KEY": os.getenv("BRIGHTDATA_API_KEY", ""),
        }
        for key, val in api_keys.items():
            if val:
                health_status += f"{key}: SET\n"
            else:
                health_status += f"{key}: NOT SET (optional)\n"

        return health_status

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        groq_ok = keys.get("GROQ_API_KEY", False)
        gemini_ok = keys.get("GEMINI_API_KEY", False)
        if groq_ok or gemini_ok:
            return make_health(True, "ready", "System checker ready (LLM available).", keys)
        return make_health(True, "degraded", "System checker ready (no LLM — health summaries disabled).", keys)


if __name__ == "__main__":
    agent = SystemCheckerAgent()
    print(agent.report())
