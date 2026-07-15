"""
email_sender.py — DEUS 3.0
============================
Unified email sender with fallback chain:
  1. SMTP (local, fast)
  2. Gmail API (OAuth2, works on Railway)
  3. Resend (HTTP, works on Railway)

All agents use this module instead of raw SMTP. The fallback chain
is automatic — if one method fails, the next is tried.

.env:
  SMTP_EMAIL / SMTP_PASSWORD — for SMTP
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GMAIL_SENDER_EMAIL — for Gmail API
  RESEND_API_KEY — for Resend
"""

import os
import logging
import smtplib
import ssl
import time
import random
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

# Try importing Resend
try:
    import resend
    RESEND_AVAILABLE = True
except ImportError:
    RESEND_AVAILABLE = False


class EmailSender:
    """
    Unified email sender with automatic fallback.

    Usage:
        sender = EmailSender()
        result = sender.send(to="user@example.com", subject="Hi", body="Hello")
        # result = {"success": True, "method": "smtp", "message": "..."}
    """

    def __init__(self, smtp_profile=None):
        """
        Args:
            smtp_profile: SmtpProfile instance (from outreach_config.py)
                          If None, uses env vars SMTP_EMAIL/SMTP_PASSWORD
        """
        self.smtp_profile = smtp_profile
        self._gmail_sender = None

    def _get_gmail_sender(self):
        """Lazy-load Gmail sender to avoid import cycles."""
        if self._gmail_sender is None:
            try:
                from gmail_sender import get_gmail_sender
                self._gmail_sender = get_gmail_sender()
            except ImportError:
                logger.info("gmail_sender.py not available")
        return self._gmail_sender

    def _get_resend_key(self) -> str:
        return os.getenv("RESEND_API_KEY", "").strip()

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        from_name: str = "DEUS",
        method: str = "auto",
        lead_name: str = "",
    ) -> dict:
        """
        Send an email with automatic fallback.

        Args:
            to: Recipient email
            subject: Subject line
            body: Email body
            html: True if body is HTML
            from_name: Sender display name
            method: "auto" | "smtp" | "gmail_api" | "resend"
            lead_name: Lead's name for logging

        Returns:
            {"success": bool, "method": str, "message": str}
        """
        if not to:
            return {"success": False, "method": "none", "message": "No recipient email"}

        if method == "smtp":
            return self._send_smtp(to, subject, body, html)
        elif method == "gmail_api":
            return self._send_gmail(to, subject, body, html, from_name)
        elif method == "resend":
            return self._send_resend(to, subject, body, from_name)

        # Auto fallback: Gmail API (works on Railway) → SMTP → Resend
        gmail = self._get_gmail_sender()
        if gmail and gmail.available:
            result = self._send_gmail(to, subject, body, html, from_name)
            if result["success"]:
                return result
            logger.info("Gmail API failed for %s, trying SMTP...", to)
        else:
            logger.info("Gmail API not available, trying SMTP...")

        result = self._send_smtp(to, subject, body, html)
        if result["success"]:
            return result

        logger.info("SMTP failed for %s, trying Resend...", to)
        result = self._send_resend(to, subject, body, from_name)
        if result["success"]:
            return result

        return {"success": False, "method": "all_failed", "message": "All email methods failed"}

    def _send_smtp(self, to: str, subject: str, body: str, html: bool = False) -> dict:
        """Send via SMTP."""
        profile = self.smtp_profile
        if profile is None:
            env_email = os.getenv("SMTP_EMAIL", "")
            env_pass = os.getenv("SMTP_PASSWORD", "")
            if not env_email or not env_pass:
                return {"success": False, "method": "smtp", "message": "No SMTP profile or env vars"}
            from outreach_config import SmtpProfile
            profile = SmtpProfile(
                profile_name="env_default",
                smtp_email=env_email,
                smtp_password=env_pass,
            )

        try:
            msg = MIMEMultipart()
            msg["From"] = profile.smtp_email
            msg["To"] = to
            msg["Subject"] = subject
            content_type = "html" if html else "plain"
            msg.attach(MIMEText(body, content_type))

            context = ssl.create_default_context()
            host = profile.smtp_host or "smtp.gmail.com"
            email_addr = profile.smtp_email
            password = profile.smtp_password
            timeout = 5

            ports = [profile.smtp_port]
            if 465 not in ports:
                ports.append(465)
            if 587 not in ports:
                ports.append(587)

            for port in ports:
                try:
                    if port == 465:
                        with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
                            server.login(email_addr, password)
                            server.sendmail(email_addr, to, msg.as_string())
                    else:
                        with smtplib.SMTP(host, port, timeout=timeout) as server:
                            server.ehlo()
                            server.starttls(context=context)
                            server.ehlo()
                            server.login(email_addr, password)
                            server.sendmail(email_addr, to, msg.as_string())
                    return {"success": True, "method": "smtp", "message": f"Sent via SMTP port {port}"}
                except Exception as port_err:
                    logger.warning("SMTP port %d failed: %s", port, port_err)
                    continue

            return {"success": False, "method": "smtp", "message": "All SMTP ports failed"}

        except Exception as e:
            return {"success": False, "method": "smtp", "message": str(e)}

    def _send_gmail(self, to: str, subject: str, body: str, html: bool, from_name: str) -> dict:
        """Send via Gmail API."""
        gmail = self._get_gmail_sender()
        if gmail is None or not gmail.available:
            return {"success": False, "method": "gmail_api", "message": "Gmail API not available"}

        success = gmail.send_email(to=to, subject=subject, body=body, html=html, from_name=from_name)
        if success:
            return {"success": True, "method": "gmail_api", "message": "Sent via Gmail API"}
        return {"success": False, "method": "gmail_api", "message": "Gmail API send failed"}

    def _send_resend(self, to: str, subject: str, body: str, from_name: str) -> dict:
        """Send via Resend API."""
        if not RESEND_AVAILABLE:
            return {"success": False, "method": "resend", "message": "Resend not installed"}

        resend_key = self._get_resend_key()
        if not resend_key:
            return {"success": False, "method": "resend", "message": "No RESEND_API_KEY"}

        try:
            from_email = self.smtp_profile.smtp_email if self.smtp_profile else os.getenv("SMTP_EMAIL", "")
            resend.api_key = resend_key
            params = {
                "from": f"{from_name} <{from_email}>",
                "to": [to],
                "subject": subject,
                "text": body,
            }
            result = resend.Emails.send(params)
            return {"success": True, "method": "resend", "message": "Sent via Resend"}
        except Exception as e:
            return {"success": False, "method": "resend", "message": str(e)}

    def send_with_delay(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        from_name: str = "DEUS",
        min_delay: float = 15.0,
        max_delay: float = 45.0,
    ) -> dict:
        """Send with random delay for natural sending patterns."""
        delay = random.uniform(min_delay, max_delay)
        time.sleep(delay)
        return self.send(to=to, subject=subject, body=body, html=html, from_name=from_name)

    def check_health(self) -> dict:
        """Check health of all email providers."""
        smtp_ok = bool(
            (self.smtp_profile and self.smtp_profile.smtp_email)
            or (os.getenv("SMTP_EMAIL") and os.getenv("SMTP_PASSWORD"))
        )
        gmail = self._get_gmail_sender()
        gmail_ok = gmail.available if gmail else False
        resend_ok = RESEND_AVAILABLE and bool(self._get_resend_key())

        return {
            "smtp": {"available": smtp_ok, "message": "SMTP configured" if smtp_ok else "No SMTP profile"},
            "gmail_api": gmail.check_health() if gmail else {"available": False, "message": "Not loaded"},
            "resend": {"available": resend_ok, "message": "Resend configured" if resend_ok else "No API key"},
            "any_available": smtp_ok or gmail_ok or resend_ok,
        }


# Singleton
_email_sender: Optional[EmailSender] = None


def get_email_sender(smtp_profile=None) -> EmailSender:
    """Get or create the singleton EmailSender."""
    global _email_sender
    if _email_sender is None:
        _email_sender = EmailSender(smtp_profile=smtp_profile)
    return _email_sender
