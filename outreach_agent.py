"""
outreach_agent.py — DEUS 3.0
==============================
Sends the first outreach message to leads via email or other channels.

Reads:  leads.json, outreach_style_config.json, smtp_profiles.json
Writes: leads.json (updates status), outreach_log.json (append-only log)
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
            if port == 465:
                with smtplib.SMTP_SSL(self.smtp_profile.smtp_host, port, context=context) as server:
                    server.login(self.smtp_profile.smtp_email, self.smtp_profile.smtp_password)
                    server.sendmail(self.smtp_profile.smtp_email, to_email, msg.as_string())
            else:
                with smtplib.SMTP(self.smtp_profile.smtp_host, port) as server:
                    server.starttls(context=context)
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
        channel = kwargs.get("channel", "auto")

        if channel not in ("auto", *self.AVAILABLE_CHANNELS):
            channel = "auto"

        leads = self._load_leads()
        if not leads:
            return make_result(True, "No leads found in leads.json.",
                              stats={}, duration=time.time() - start)

        sent_count = 0
        failed_count = 0
        skipped_count = 0
        no_channel_count = 0
        log_entries = []
        senders = self._senders()

        for lead in leads:
            if lead.get("status") == "contacted":
                continue

            if not lead.get("outreach_ready"):
                skipped_count += 1
                continue

            requested = lead.get("preferred_channel") or channel
            resolved_channel = self.resolve_channel(lead, requested)

            if resolved_channel is None:
                lead["needs_human"] = True
                lead["needs_human_reason"] = f"Requested channel '{requested}' has no matching contact info."
                no_channel_count += 1
                continue

            message = self.generate_outreach_message(lead)
            sender = senders[resolved_channel]
            sent = sender(lead, message)

            if sent:
                lead["status"] = "contacted"
                lead["channel_used"] = resolved_channel
                sent_count += 1
            else:
                lead["status"] = "send_failed"
                failed_count += 1

            log_entries.append({
                "business_name": lead.get("business_name"),
                "business_email": lead.get("business_email"),
                "channel": resolved_channel,
                "sent": sent,
                "message": message,
                "smtp_profile": self.smtp_profile.profile_name if self.smtp_profile else None,
            })

        self._save_leads(leads)
        self._append_outreach_log(log_entries)

        summary = (
            f"Sent: {sent_count} | Failed: {failed_count} | "
            f"Skipped (needs human): {skipped_count} | No channel: {no_channel_count}"
        )
        logger.info("Outreach complete (channel: %s). %s", channel, summary)

        stats = {
            "sent": sent_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "no_channel": no_channel_count,
        }
        return make_result(True, summary, stats=stats, duration=time.time() - start)

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
