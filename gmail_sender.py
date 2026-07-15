"""
gmail_sender.py — DEUS 3.0
============================
Gmail API OAuth2 email sender. Uses Google's Gmail API to send emails
via HTTP (not SMTP), which works on Railway and other platforms that
block outbound SMTP.

IMPORTANT: This is NOT the Gemini API. This is the Gmail API for sending
emails using OAuth2 authentication with a real Gmail account.

Setup:
  1. Go to https://console.cloud.google.com
  2. Create project → Enable Gmail API
  3. Create OAuth2 credentials (Desktop app)
  4. Download credentials.json → place in project root
  5. First run will open browser for OAuth consent
  6. token.json is saved for subsequent runs

.env:
  GOOGLE_CLIENT_ID=...
  GOOGLE_CLIENT_SECRET=...
  GMAIL_SENDER_EMAIL=your@gmail.com
"""

import os
import json
import base64
import logging
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


class GmailSender:
    """Sends emails via Gmail API (OAuth2). Works where SMTP is blocked."""

    def __init__(self):
        self.sender_email = os.getenv("GMAIL_SENDER_EMAIL", "")
        self._service = None
        self._available = False
        self._init_service()

    def _init_service(self):
        """Initialize Gmail API service with OAuth2 credentials."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

            creds = None

            # 1) Try database first (persists across Railway restarts)
            try:
                from app.database import get_gmail_token
                token_json = get_gmail_token()
                if token_json:
                    creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
                    logger.info("Gmail token loaded from database")
            except Exception as e:
                logger.debug("DB token load failed (expected if DB not ready): %s", e)

            # 2) Fall back to token.json (local dev)
            if not creds and os.path.exists(TOKEN_FILE):
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                logger.info("Gmail token loaded from %s", TOKEN_FILE)

            # 3) If expired and has refresh token, refresh
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    logger.info("Gmail token refreshed")
                except Exception as e:
                    logger.warning("Token refresh failed: %s", e)
                    creds = None

            if not creds:
                logger.info("No Gmail token available — Gmail API disabled")
                return

            self._service = build("gmail", "v1", credentials=creds)
            self._available = True
            logger.info("Gmail API initialized — sender: %s", self.sender_email)

        except ImportError:
            logger.info("Gmail API packages not installed — pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        except Exception as e:
            logger.warning("Gmail API init failed: %s", e)

    @property
    def available(self) -> bool:
        return self._available and self._service is not None

    def check_health(self) -> dict:
        """Return health status of Gmail API connection."""
        return {
            "available": self.available,
            "sender": self.sender_email if self.available else None,
            "message": "Gmail API ready" if self.available else "Gmail API not configured",
        }

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        from_name: str = "DEUS",
    ) -> bool:
        """
        Send an email via Gmail API.

        Args:
            to: Recipient email address
            subject: Email subject line
            body: Email body (plain text or HTML)
            html: If True, body is HTML. If False, body is plain text.
            from_name: Display name for the sender

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.available:
            logger.error("Gmail API not available — cannot send email")
            return False

        try:
            message = MIMEText(body, "html" if html else "plain")
            message["to"] = to
            message["from"] = f"{from_name} <{self.sender_email}>"
            message["subject"] = subject

            # Encode message for Gmail API
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

            result = self._service.users().messages().send(
                userId="me",
                body={"raw": raw_message},
            ).execute()

            logger.info("Gmail API: email sent to %s (id: %s)", to, result.get("id"))
            return True

        except Exception as e:
            logger.error("Gmail API send failed to %s: %s", to, e)
            return False

    def send_batch(
        self,
        emails: list,
        from_name: str = "DEUS",
        delay_seconds: float = 1.0,
    ) -> dict:
        """
        Send multiple emails with optional delay between sends.

        Args:
            emails: List of dicts with keys: to, subject, body, html (optional)
            from_name: Display name for sender
            delay_seconds: Delay between sends (rate limiting)

        Returns:
            Dict with sent_count, failed_count, errors
        """
        import time

        sent = 0
        failed = 0
        errors = []

        for i, email_data in enumerate(emails):
            success = self.send_email(
                to=email_data["to"],
                subject=email_data["subject"],
                body=email_data["body"],
                html=email_data.get("html", False),
                from_name=from_name,
            )

            if success:
                sent += 1
            else:
                failed += 1
                errors.append({"to": email_data["to"], "error": "send_failed"})

            # Rate limit delay (except after last email)
            if delay_seconds > 0 and i < len(emails) - 1:
                time.sleep(delay_seconds)

        return {
            "sent": sent,
            "failed": failed,
            "total": len(emails),
            "errors": errors,
        }


# Singleton instance
_gmail_sender: Optional[GmailSender] = None


def get_gmail_sender() -> GmailSender:
    """Get or create the singleton GmailSender instance."""
    global _gmail_sender
    if _gmail_sender is None:
        _gmail_sender = GmailSender()
    return _gmail_sender
