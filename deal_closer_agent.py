"""
deal_closer_agent.py — DEUS 3.0
=================================
Processes due appointments and sends closing messages.
"""

import os
import json
import time
import logging
from dotenv import load_dotenv

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

APPOINTMENTS_FILE = "appointments.json"


class DealCloserAgent(BaseAgent):
    name = "DealCloserAgent"
    display_name = "Deal Closer"
    description = "Processes due appointments and sends closing messages."
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

    def run(self, **kwargs) -> AgentResult:
        start = time.time()
        appointments = self.load_appointments()
        due_appointments = [
            a for a in appointments
            if a.get("status") == "scheduled" and time.time() > a.get("datetime", float("inf"))
        ]

        if not due_appointments:
            return make_result(True, "No due appointments to process.",
                              stats={"processed": 0}, duration=time.time() - start)

        results = []
        for appointment in due_appointments:
            lead_name = appointment.get("lead_name", "Unknown")
            niche = appointment.get("niche", "general")

            message = self.think(
                f"Generate a closing message for {lead_name} based on {niche} and the offer."
            )

            sent = self.send_closing_message(appointment, message)

            if not sent:
                appointment["status"] = "send_failed"
                results.append((lead_name, "send_failed"))
            elif self.is_deal_closed(appointment):
                appointment["status"] = "deal_closed"
                results.append((lead_name, "deal_closed"))
            else:
                appointment["status"] = "follow_up"
                results.append((lead_name, "follow_up"))

        self.save_appointments(appointments)
        duration = time.time() - start
        stats = {
            "processed": len(results),
            "closed": sum(1 for _, s in results if s == "deal_closed"),
            "follow_up": sum(1 for _, s in results if s == "follow_up"),
            "failed": sum(1 for _, s in results if s == "send_failed"),
        }
        return make_result(True, json.dumps(results), stats=stats, duration=duration)

    def send_closing_message(self, appointment_details: dict, message: str) -> bool:
        channel = appointment_details.get("channel_used")
        lead_name = appointment_details.get("lead_name", "Unknown")

        senders = {
            "email": self.send_email,
            "linkedin": self.send_linkedin_message,
            "instagram": self.send_instagram_message,
            "facebook": self.send_facebook_message,
        }

        sender = senders.get(channel)
        if sender is None:
            return False

        try:
            return sender(lead_name, message)
        except Exception as e:
            logger.error("Failed to send via %s for %s: %s", channel, lead_name, e)
            return False

    def is_deal_closed(self, appointment_details: dict) -> bool:
        return False

    def load_appointments(self) -> list:
        try:
            with open(APPOINTMENTS_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save_appointments(self, appointments: list) -> None:
        try:
            with open(APPOINTMENTS_FILE, "w") as f:
                json.dump(appointments, f, indent=2)
        except Exception as e:
            logger.error("Failed to save appointments.json: %s", e)

    def send_email(self, lead_name: str, message: str) -> bool:
        """Send closing email via OutreachAgent's SMTP profile."""
        try:
            from outreach_agent import OutreachAgent
            sender = OutreachAgent()
            # Build a lead-like dict for the email
            lead = {"business_name": lead_name, "business_email": ""}
            # Find the lead's email from leads.json
            leads = self.load_appointments()  # We don't have leads.json here, use appointment data
            # Try to find email from the appointment
            appointment = None
            for a in self.load_appointments():
                if a.get("lead_name") == lead_name:
                    appointment = a
                    break
            if appointment and appointment.get("lead_email"):
                lead["business_email"] = appointment["lead_email"]
            else:
                logger.warning("No email found for %s", lead_name)
                return False
            return sender.send_email(lead, message)
        except Exception as e:
            logger.error("Email send failed for %s: %s", lead_name, e)
            return False

    def send_linkedin_message(self, lead_name: str, message: str) -> bool:
        return False

    def send_instagram_message(self, lead_name: str, message: str) -> bool:
        return False

    def send_facebook_message(self, lead_name: str, message: str) -> bool:
        return False

    def report(self) -> str:
        appointments = self.load_appointments()
        scheduled = sum(1 for a in appointments if a.get("status") == "scheduled")
        closed = sum(1 for a in appointments if a.get("status") == "deal_closed")
        follow_up = sum(1 for a in appointments if a.get("status") == "follow_up")
        return f"Scheduled: {scheduled} | Closed: {closed} | Follow-up: {follow_up}"

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        groq_ok = keys.get("GROQ_API_KEY", False)
        appointments_ok = os.path.exists(APPOINTMENTS_FILE)
        if groq_ok:
            return make_health(True, "ready", "Deal closer ready.", keys)
        return make_health(True, "degraded", "Deal closer ready (no LLM for message generation).", keys)


if __name__ == "__main__":
    agent = DealCloserAgent()
    print(agent.report())
    result = agent.run()
    print(result)
