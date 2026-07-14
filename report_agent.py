"""
report_agent.py — DEUS 3.0
============================
Builds a daily summary across the whole pipeline (leads -> outreach ->
followup -> appointments -> deals) and optionally emails it.

Includes deliverability stats from the new email infrastructure:
  - Email verification stats
  - Spam check results
  - Rate limiter status
  - Campaign progress
  - Daemon activity
"""

import os
import json
import logging
from dotenv import load_dotenv

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class ReportAgent(BaseAgent):
    name = "ReportAgent"
    display_name = "Report"
    description = "Generates daily pipeline summaries with deliverability stats and optionally emails them."
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
        self._email_sender = None

    def _get_email_sender(self):
        if self._email_sender is None:
            from email_sender import get_email_sender
            self._email_sender = get_email_sender()
        return self._email_sender

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

    def collect_stats(self) -> dict:
        stats = {}

        # Database stats
        try:
            from app.database import count_leads, get_email_analytics, db_conn
            stats["total_leads"] = count_leads()
            stats["new_leads"] = count_leads(status="new")
            stats["contacted_leads"] = count_leads(status="contacted")
            stats["replied_leads"] = count_leads(status="replied")
            stats["no_response_leads"] = count_leads(status="no_response")
            stats["outreach_ready"] = count_leads(outreach_ready=True)

            email_stats = get_email_analytics()
            stats["email"] = email_stats

            # Campaign stats
            try:
                from campaign import get_campaign_manager
                cm = get_campaign_manager()
                stats["campaigns"] = cm.get_all_stats()
            except Exception:
                stats["campaigns"] = {}

            # Daemon stats
            try:
                from daemon import get_daemon
                daemon = get_daemon()
                stats["daemon"] = daemon.get_log_stats()
            except Exception:
                stats["daemon"] = {}

            # Scheduler stats
            try:
                from scheduler import get_scheduler
                sched = get_scheduler()
                schedules = sched.list_schedules()
                stats["schedules"] = {
                    "total": len(schedules),
                    "active": sum(1 for s in schedules if s.enabled),
                }
            except Exception:
                stats["schedules"] = {}

        except Exception as e:
            logger.warning("DB stats collection failed: %s", e)
            stats = self._collect_stats_from_json()

        # Email provider health
        try:
            sender = self._get_email_sender()
            stats["email_providers"] = sender.check_health()
        except Exception:
            stats["email_providers"] = {}

        # Rate limiter status
        try:
            from send_limiter import get_send_limiter
            limiter = get_send_limiter()
            stats["rate_limiter"] = limiter.get_all_status()
        except Exception:
            stats["rate_limiter"] = {}

        return stats

    def _collect_stats_from_json(self) -> dict:
        """Fallback: collect stats from JSON files."""
        def _load(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
            except (FileNotFoundError, json.JSONDecodeError):
                return []

        leads = _load("leads.json")
        return {
            "total_leads": len(leads),
            "outreach_ready_leads": sum(1 for l in leads if isinstance(l, dict) and l.get("outreach_ready")),
            "contacted_leads": sum(1 for l in leads if isinstance(l, dict) and l.get("status") == "contacted"),
            "replied_leads": sum(1 for l in leads if isinstance(l, dict) and l.get("status") == "replied"),
            "followed_up_leads": sum(1 for l in leads if isinstance(l, dict) and l.get("followup_count", 0) > 0),
        }

    def build_report_text(self, stats: dict) -> str:
        task = (
            f"Write a short, clear daily operations summary from these stats:\n"
            f"{json.dumps(stats, indent=2)}\n\n"
            f"Keep it factual and under 200 words. No markdown headers, plain text only."
        )
        narrative = self.think(task)

        # Build raw report
        lines = []
        lines.append(f"Total Leads: {stats.get('total_leads', 0)}")
        lines.append(f"New Leads: {stats.get('new_leads', 0)}")
        lines.append(f"Outreach-Ready: {stats.get('outreach_ready', 0)}")
        lines.append(f"Contacted: {stats.get('contacted_leads', 0)}")
        lines.append(f"Replied: {stats.get('replied_leads', 0)}")
        lines.append(f"No Response: {stats.get('no_response_leads', 0)}")

        # Email stats
        email = stats.get("email", {})
        if email:
            lines.append("")
            lines.append("--- Email Stats ---")
            lines.append(f"Total Sent: {email.get('total_sent', 0)}")
            lines.append(f"Delivered: {email.get('delivered', 0)} (rate: {email.get('delivery_rate', 0)}%)")
            lines.append(f"Opened: {email.get('opened', 0)} (rate: {email.get('open_rate', 0)}%)")
            lines.append(f"Replied: {email.get('replied', 0)} (rate: {email.get('reply_rate', 0)}%)")
            lines.append(f"Bounced: {email.get('bounced', 0)} (rate: {email.get('bounce_rate', 0)}%)")
            lines.append(f"Complaints: {email.get('total_complained', 0)}")

        # Campaign stats
        campaigns = stats.get("campaigns", {})
        if campaigns:
            lines.append("")
            lines.append("--- Campaigns ---")
            lines.append(f"Total Campaigns: {campaigns.get('total_campaigns', 0)}")
            lines.append(f"Active: {campaigns.get('active_campaigns', 0)}")
            lines.append(f"Total Enrolled: {campaigns.get('total_enrolled', 0)}")
            lines.append(f"Active Enrollments: {campaigns.get('active_enrollments', 0)}")

        # Daemon stats
        daemon = stats.get("daemon", {})
        if daemon:
            lines.append("")
            lines.append("--- Daemon ---")
            lines.append(f"Total Cycles: {daemon.get('total_cycles', 0)}")
            lines.append(f"Emails Sent: {daemon.get('total_followup_emails', 0)}")
            lines.append(f"Campaign Steps: {daemon.get('total_campaign_emails', 0)}")
            lines.append(f"Replies Detected: {daemon.get('total_replies_detected', 0)}")
            lines.append(f"Errors: {daemon.get('total_errors', 0)}")

        # Rate limiter
        rate = stats.get("rate_limiter", {})
        if rate:
            lines.append("")
            lines.append("--- Rate Limiter ---")
            for name, info in rate.items():
                lines.append(f"  {name}: {info.get('daily_sent', 0)}/{info.get('daily_limit', 0)} daily, "
                            f"{info.get('hourly_sent', 0)}/{info.get('hourly_limit', 0)} hourly")

        raw_lines = "\n".join(lines)

        if narrative:
            return f"{narrative}\n\n--- Raw Numbers ---\n{raw_lines}"
        return raw_lines

    def report(self) -> str:
        stats = self.collect_stats()
        return self.build_report_text(stats)

    def send_report(self, report_text: str) -> str:
        if os.getenv("REPORT_EMAIL_ENABLED", "False").lower() != "true":
            return "Report email sending is disabled."

        receiver = os.getenv("REPORT_RECEIVER_EMAIL", "")
        if not receiver:
            return "Missing REPORT_RECEIVER_EMAIL configuration."

        sender = self._get_email_sender()
        result = sender.send(
            to=receiver,
            subject="DEUS Daily Report",
            body=report_text,
            from_name="DEUS Reports",
        )

        if result["success"]:
            return f"Report sent successfully via {result['method']}."
        return f"Failed to send report: {result['message']}"

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
        sender = self._get_email_sender()
        health = sender.check_health()
        email_ok = health.get("any_available", False)

        parts = []
        if groq_ok:
            parts.append("Groq")
        if email_ok:
            parts.append("Email")

        if parts:
            return make_health(True, "ready", f"Report agent ready ({', '.join(parts)}).", keys)
        return make_health(True, "degraded", "Report agent ready (no LLM or email).", keys)


if __name__ == "__main__":
    agent = ReportAgent()
    result = agent.run()
    print(result)
