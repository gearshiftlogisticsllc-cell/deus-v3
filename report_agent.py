"""
report_agent.py — DEUS 3.0
============================
Builds a daily summary across the whole pipeline (leads -> outreach ->
followup -> appointments -> deals) and optionally emails it.
"""

import os
import json
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_FILES = {
    "leads": "leads.json",
    "outreach": "outreach_log.json",
    "followups": "followup_log.json",
    "appointments": "appointments.json",
    "deals": "deals_log.json",
    "replies": "reply_state.json",
}


class ReportAgent(BaseAgent):
    name = "ReportAgent"
    display_name = "Report"
    description = "Generates daily pipeline summaries and optionally emails them."
    requires_keys = ["GROQ_API_KEY"]

    def __init__(self):
        self.client = None
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=groq_key)
            except Exception as e:
                logger.warning("Groq init failed: %s", e)

    def think(self, prompt: str) -> str:
        if not self.client:
            return ""
        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Groq request failed: %s", e)
            return ""

    def _load_json_safe(self, path: str) -> list:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def collect_stats(self) -> dict:
        stats = {}
        for key, path in DATA_FILES.items():
            data = self._load_json_safe(path)
            stats[key] = len(data) if isinstance(data, list) else 0

        leads = self._load_json_safe(DATA_FILES["leads"])
        stats["outreach_ready_leads"] = sum(
            1 for l in leads if isinstance(l, dict) and l.get("outreach_ready")
        )
        stats["needs_human_leads"] = sum(
            1 for l in leads if isinstance(l, dict) and l.get("needs_human")
        )
        stats["contacted_leads"] = sum(
            1 for l in leads if isinstance(l, dict) and l.get("status") == "contacted"
        )
        stats["replied_leads"] = sum(
            1 for l in leads if isinstance(l, dict) and l.get("status") == "replied"
        )
        stats["followed_up_leads"] = sum(
            1 for l in leads if isinstance(l, dict) and l.get("followup_count", 0) > 0
        )
        return stats

    def build_report_text(self, stats: dict) -> str:
        task = (
            f"Write a short, clear daily operations summary from these stats:\n"
            f"{json.dumps(stats, indent=2)}\n\n"
            f"Keep it factual and under 150 words. No markdown headers, plain text only."
        )
        narrative = self.think(task)

        raw_lines = (
            f"Total Leads: {stats.get('leads', 0)}\n"
            f"Outreach-Ready Leads (have email): {stats.get('outreach_ready_leads', 0)}\n"
            f"Leads Needing Human Follow-up: {stats.get('needs_human_leads', 0)}\n"
            f"Contacted: {stats.get('contacted_leads', 0)}\n"
            f"Replied: {stats.get('replied_leads', 0)}\n"
            f"Followed Up: {stats.get('followed_up_leads', 0)}\n"
            f"Total Outreach Sent: {stats.get('outreach', 0)}\n"
            f"Total Followups: {stats.get('followups', 0)}\n"
            f"Total Appointments: {stats.get('appointments', 0)}\n"
            f"Total Deals Closed: {stats.get('deals', 0)}\n"
        )

        if narrative:
            return f"{narrative}\n\n--- Raw Numbers ---\n{raw_lines}"
        return raw_lines

    def report(self) -> str:
        stats = self.collect_stats()
        return self.build_report_text(stats)

    def send_report(self, report_text: str) -> str:
        if os.getenv("REPORT_EMAIL_ENABLED", "False").lower() != "true":
            return "Report email sending is disabled."
        smtp_email = os.getenv("SMTP_EMAIL", "")
        smtp_pass = os.getenv("SMTP_PASSWORD", "")
        receiver = os.getenv("REPORT_RECEIVER_EMAIL", "")
        if not smtp_email or not smtp_pass or not receiver:
            return "Missing SMTP configuration."
        try:
            message = MIMEMultipart()
            message["From"] = smtp_email
            message["To"] = receiver
            message["Subject"] = "DEUS Daily Report"
            message.attach(MIMEText(report_text, "plain"))
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
                server.login(smtp_email, smtp_pass)
                server.sendmail(smtp_email, receiver, message.as_string())
            return "Report sent successfully."
        except Exception as e:
            return f"Failed to send report: {e}"

    def run(self, **kwargs) -> AgentResult:
        import time
        start = time.time()
        report_text = self.report()
        send_result = self.send_report(report_text)
        duration = time.time() - start
        full_text = f"{report_text}\n\n--- Email ---\n{send_result}"
        stats = self.collect_stats()
        return make_result(
            success=True,
            message=full_text,
            stats=stats,
            duration=duration,
        )

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        groq_ok = keys.get("GROQ_API_KEY", False)
        if groq_ok:
            return make_health(True, "ready", "Report agent ready (Groq configured).", keys)
        return make_health(True, "degraded", "Report agent ready but no LLM (Groq key missing).", keys)


if __name__ == "__main__":
    agent = ReportAgent()
    result = agent.run()
    print(result)
