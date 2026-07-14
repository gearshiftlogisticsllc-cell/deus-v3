"""
outreach_agent.py — DEUS 3.0
==============================
Sends the first outreach message to leads via email or other channels.

Two modes:
  - preview: Returns leads ready for outreach, asks user to confirm
  - send:    Sends emails to the confirmed lead IDs only

Reads:  leads (database), outreach_style_config.json, smtp_profiles.json
Writes: leads (database), outreach_log.json (append-only log)
"""

import os
import json
import logging
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health
from reply_detector import EmailTracker

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from outreach_config import (
    load_style_config, render_custom_template,
    get_default_smtp_profile, get_smtp_profile, SmtpProfile,
)

ENV_SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
ENV_SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

LEADS_FILE = "leads.json"
OUTREACH_LOG_FILE = "outreach_log.json"


class OutreachAgent(BaseAgent):
    name = "OutreachAgent"
    display_name = "Outreach"
    description = "Sends cold outreach messages to leads via email or social channels."
    requires_keys = ["GROQ_API_KEY"]

    def __init__(self, smtp_profile_name: str = None):
        self.client = None
        self.style = load_style_config()

        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=groq_key)
            except Exception as e:
                logger.warning("Groq init failed: %s", e)

        profile = None
        if smtp_profile_name:
            profile = get_smtp_profile(smtp_profile_name)
            if profile is None:
                logger.warning("SMTP profile '%s' not found — falling back to default.", smtp_profile_name)

        if profile is None:
            profile = get_default_smtp_profile()

        if profile is None and ENV_SMTP_EMAIL and ENV_SMTP_PASSWORD:
            profile = SmtpProfile(
                profile_name="env_default",
                smtp_email=ENV_SMTP_EMAIL,
                smtp_password=ENV_SMTP_PASSWORD,
            )

        self.smtp_profile = profile
        self.tracker = EmailTracker()

    def think(self, prompt: str) -> str:
        if not self.client:
            return ""
        try:
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                timeout=15,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Groq request failed: %s", e)
            return ""

    def generate_outreach_message(self, lead: dict) -> str:
        if self.style.use_custom_template and self.style.custom_template:
            return render_custom_template(self.style.custom_template, lead, self.style.signature)

        niche = lead.get("niche", "their industry")
        business_name = lead.get("business_name", "there")
        services = lead.get("services_offered", "")

        task = (
            f"Write a short, {self.style.tone} cold outreach email to "
            f"'{business_name}', a business in the {niche} niche "
            f"(services: {services or 'unspecified'}). "
            f"The goal is to introduce a business proposal and request a "
            f"brief appointment/call to discuss it — specifically: "
            f"\"{self.style.call_to_action}\" "
            f"Keep it under {self.style.max_words} words. "
            f"Sign off generically without inventing a sender name. "
            f"Do not include a subject line, just the email body."
        )
        message = self.think(task)
        if not message:
            message = (
                f"Hi {business_name} team,\n\n"
                f"I'd like to share a quick business proposal relevant to your "
                f"{niche} work. {self.style.call_to_action}\n\n"
                f"Best regards"
            )

        if self.style.signature:
            message = f"{message}\n\n{self.style.signature}"

        return message

    def render_subject(self, lead: dict) -> str:
        try:
            return self.style.subject_template.format(
                business_name=lead.get("business_name", "your business"),
                niche=lead.get("niche", ""),
            )
        except (KeyError, IndexError):
            return f"Quick proposal for {lead.get('business_name', 'your business')}"

    def send_email(self, lead: dict, message: str) -> bool:
        to_email = lead.get("business_email", "")
        if not to_email:
            return False
        if self.smtp_profile is None:
            logger.error("No SMTP profile configured — cannot send email.")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self.smtp_profile.smtp_email
            msg["To"] = to_email
            msg["Subject"] = self.render_subject(lead)
            msg.attach(MIMEText(message, "plain"))

            context = ssl.create_default_context()
            port = self.smtp_profile.smtp_port
            host = self.smtp_profile.smtp_host
            timeout = 10

            if port == 465:
                with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
                    server.login(self.smtp_profile.smtp_email, self.smtp_profile.smtp_password)
                    server.sendmail(self.smtp_profile.smtp_email, to_email, msg.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(self.smtp_profile.smtp_email, self.smtp_profile.smtp_password)
                    server.sendmail(self.smtp_profile.smtp_email, to_email, msg.as_string())

            logger.info("Email sent to %s (%s) via profile '%s'",
                        lead.get("business_name"), to_email, self.smtp_profile.profile_name)
            return True
        except Exception as e:
            logger.error("Failed to send email to %s: %s", to_email, e)
            return False

    def send_linkedin_message(self, lead: dict, message: str) -> bool:
        return False

    def send_instagram_message(self, lead: dict, message: str) -> bool:
        return False

    def send_facebook_message(self, lead: dict, message: str) -> bool:
        return False

    def send_phone_sms(self, lead: dict, message: str) -> bool:
        return False

    CHANNEL_REQUIREMENTS = {
        "email": "business_email",
        "linkedin": "linkedin_url",
        "instagram": "instagram_handle",
        "facebook": "facebook_url",
        "phone/sms": "phone",
    }

    AVAILABLE_CHANNELS = ["email", "linkedin", "instagram", "facebook", "phone/sms"]

    def _senders(self) -> dict:
        return {
            "email": self.send_email,
            "linkedin": self.send_linkedin_message,
            "instagram": self.send_instagram_message,
            "facebook": self.send_facebook_message,
            "phone/sms": self.send_phone_sms,
        }

    def resolve_channel(self, lead: dict, requested_channel: str) -> str | None:
        if requested_channel == "auto":
            for channel in self.AVAILABLE_CHANNELS:
                required_field = self.CHANNEL_REQUIREMENTS[channel]
                if lead.get(required_field):
                    return channel
            return None

        required_field = self.CHANNEL_REQUIREMENTS.get(requested_channel)
        if required_field is None:
            return None
        if not lead.get(required_field):
            return None
        return requested_channel

    def run(self, **kwargs) -> AgentResult:
        start = time.time()
        mode = kwargs.get("mode", "preview")
        channel = kwargs.get("channel", "auto")
        limit = int(kwargs.get("limit", 25))

        if channel not in ("auto", *self.AVAILABLE_CHANNELS):
            channel = "auto"

        if mode == "send":
            return self._send_confirmed(kwargs, start)

        return self._preview(kwargs, start)

    def _preview(self, kwargs: dict, start: float) -> AgentResult:
        """Preview leads ready for outreach. Returns list for user to confirm."""
        limit = int(kwargs.get("limit", 25))
        channel = kwargs.get("channel", "auto")

        try:
            from app.database import get_outreach_candidates
            candidates = get_outreach_candidates(limit=limit)
        except Exception:
            candidates = self._get_candidates_from_json(limit)

        if not candidates:
            return make_result(True, "No leads ready for outreach. Import leads with email addresses first.",
                              stats={"candidates": 0}, duration=time.time() - start)

        preview_data = []
        for lead in candidates:
            resolved = self.resolve_channel(lead, lead.get("preferred_channel") or channel)
            preview_data.append({
                "id": lead.get("id"),
                "business_name": lead.get("business_name", "Unknown"),
                "business_email": lead.get("business_email", ""),
                "phone": lead.get("phone", ""),
                "niche": lead.get("niche", ""),
                "channel": resolved or "none",
                "has_email": bool(lead.get("business_email")),
            })

        summary = f"Found {len(preview_data)} leads ready for outreach. Review and confirm to send."
        return make_result(True, summary, data=preview_data,
                          stats={"candidates": len(preview_data)},
                          duration=time.time() - start)

    def _send_confirmed(self, kwargs: dict, start: float) -> AgentResult:
        """Send emails to confirmed lead IDs only."""
        lead_ids = kwargs.get("lead_ids", [])
        channel = kwargs.get("channel", "email")

        if not lead_ids:
            return make_result(False, "No lead IDs provided. Use preview first, then send with lead_ids.",
                              stats={}, duration=time.time() - start)

        try:
            from app.database import get_lead, mark_leads_contacted
            leads = [get_lead(lid) for lid in lead_ids]
            leads = [l for l in leads if l]
        except Exception:
            leads = self._get_leads_by_ids(lead_ids)

        if not leads:
            return make_result(False, "No valid leads found for the given IDs.",
                              stats={}, duration=time.time() - start)

        sent_count = 0
        failed_count = 0
        log_entries = []

        for lead in leads:
            resolved = self.resolve_channel(lead, channel)
            if resolved != "email":
                failed_count += 1
                continue

            message = self.generate_outreach_message(lead)
            sent = self.send_email(lead, message)
            if sent:
                sent_count += 1
                log_entries.append({
                    "lead_id": lead.get("id"),
                    "business_name": lead.get("business_name"),
                    "business_email": lead.get("business_email"),
                    "channel": resolved,
                    "sent": True,
                    "message": message[:200],
                    "smtp_profile": self.smtp_profile.profile_name if self.smtp_profile else None,
                })
            else:
                failed_count += 1

        if log_entries:
            try:
                from app.database import mark_leads_contacted, log_email
                ids = [e["lead_id"] for e in log_entries if e.get("lead_id")]
                mark_leads_contacted(ids, channel)
                for e in log_entries:
                    if e.get("business_email"):
                        log_email(
                            lead_email=e["business_email"],
                            lead_name=e.get("business_name", ""),
                            subject="Outreach",
                            status="sent",
                            agent="OutreachAgent",
                        )
            except Exception:
                pass

            self._append_outreach_log(log_entries)

        summary = f"Sent: {sent_count} | Failed: {failed_count} | Total confirmed: {len(lead_ids)}"
        stats = {"sent": sent_count, "failed": failed_count, "total": len(lead_ids)}
        return make_result(True, summary, stats=stats, duration=time.time() - start)

    def _get_candidates_from_json(self, limit: int) -> list:
        """Fallback: read from leads.json if database unavailable."""
        leads = self._load_leads()
        candidates = []
        for lead in leads:
            if lead.get("status") == "contacted":
                continue
            if not lead.get("business_email"):
                continue
            if len(candidates) >= limit:
                break
            candidates.append(lead)
        return candidates

    def _get_leads_by_ids(self, ids: list) -> list:
        """Fallback: find leads in leads.json by business_email match."""
        leads = self._load_leads()
        return [l for l in leads if l.get("business_email") and id(l) in ids][:len(ids)]

    def _load_leads(self) -> list:
        try:
            with open(LEADS_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_leads(self, leads: list) -> None:
        try:
            with open(LEADS_FILE, "w") as f:
                json.dump(leads, f, indent=2)
        except Exception as e:
            logger.error("Failed to save %s: %s", LEADS_FILE, e)

    def _append_outreach_log(self, entries: list) -> None:
        if not entries:
            return
        try:
            existing = []
            try:
                with open(OUTREACH_LOG_FILE, "r") as f:
                    existing = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                existing = []
            existing.extend(entries)
            with open(OUTREACH_LOG_FILE, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.error("Failed to write %s: %s", OUTREACH_LOG_FILE, e)

    def report(self) -> str:
        leads = self._load_leads()
        contacted = sum(1 for l in leads if l.get("status") == "contacted")
        failed = sum(1 for l in leads if l.get("status") == "send_failed")
        profile_name = self.smtp_profile.profile_name if self.smtp_profile else "none configured"
        return f"Outreach Agent (SMTP: {profile_name}). Contacted: {contacted} | Failed: {failed}"

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        groq_ok = keys.get("GROQ_API_KEY", False)
        smtp_ok = bool(self.smtp_profile)
        if groq_ok and smtp_ok:
            return make_health(True, "ready", "Outreach agent ready (Groq + SMTP).", keys)
        issues = []
        if not groq_ok:
            issues.append("GROQ_API_KEY not set")
        if not smtp_ok:
            issues.append("No SMTP profile configured")
        return make_health(True, "degraded", f"Degraded: {', '.join(issues)}", keys)


if __name__ == "__main__":
    agent = OutreachAgent()
    print(agent.report())
    result = agent.run(channel="auto")
    print(result)
