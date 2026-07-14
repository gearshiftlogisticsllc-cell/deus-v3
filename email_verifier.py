"""
email_verifier.py — DEUS 3.0
==============================
Email address verification for maximum inbox deliverability.

Checks:
  1. Syntax validation (regex)
  2. MX record lookup (domain accepts email)
  3. Disposable email detection (blocks temp-mail domains)
  4. Role-based email detection (flags info@, admin@, etc.)
  5. SMTP handshake (optional — verifies mailbox exists)

Usage:
    verifier = EmailVerifier()
    result = verifier.verify("user@example.com")
    # result = {"valid": True, "score": 85, "checks": {...}}
"""

import os
import re
import logging
import dns.resolver
from typing import Optional

logger = logging.getLogger(__name__)

# --- Disposable email domains (top 200+) ---
DISPOSABLE_DOMAINS = {
    "10minutemail.com", "guerrillamail.com", "guerrillamail.net",
    "tempmail.com", "throwaway.email", "temp-mail.org", "tempail.com",
    "discard.email", "discardmail.com", "mailinator.com", "yopmail.com",
    "yopmail.fr", "getairmail.com", "maildrop.cc", "sharklasers.com",
    "guerrillamailblock.com", "grr.la", "dispostable.com", "10minutemail.co.za",
    "trashmail.com", "trashmail.net", "trashmail.org", "trashmail.me",
    "fakeinbox.com", "tempinbox.com", "mailcatch.com", "mailexpire.com",
    "mailnull.com", "spamgourmet.com", "meltmail.com", "nospam.ze.tc",
    "nomail.xl.cx", "mailme.ir", "binkmail.com", "bobmail.info",
    "chacuo.net", "devnullmail.com", "letthemeatspam.com", "mail114.net",
    "mailscrap.com", "webmailservice.net", "jetable.com", "jetable.fr.nf",
    "jetable.net", "mytemp.email", "tempmailaddress.com", "tmpmail.net",
    "tmpmail.org", "tmpmail2.com", "burnermail.io", "harakirimail.com",
    "tmail.io", "tmail.ws", "tempr.email", "tmpmailer.com",
    "mailnesia.com", "templermail.com", "spambox.us", "spamfree24.org",
    "spaml.com", "spamoff.de", "supermailer.jp", "safetymail.info",
    " inboxalias.com", " killmail.com", " spamex.com",
    " fileonq.com", " spambox.us",
}

# --- Role-based email prefixes (lower deliverability) ---
ROLE_PREFIXES = (
    "info@", "admin@", "support@", "sales@", "contact@", "hello@",
    "office@", "team@", "staff@", "help@", "service@", "billing@",
    "abuse@", "postmaster@", "webmaster@", "noreply@", "no-reply@",
    "marketing@", "hr@", "legal@", "press@", "media@", "jobs@",
)

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


class EmailVerifier:
    """Verifies email addresses for deliverability."""

    def __init__(self, check_smtp: bool = False, timeout: int = 5):
        """
        Args:
            check_smtp: If True, does SMTP RCPT TO handshake (slower but more accurate)
            timeout: Timeout in seconds for DNS/network checks
        """
        self.check_smtp = check_smtp
        self.timeout = timeout

    def verify(self, email: str) -> dict:
        """
        Run all verification checks on an email address.

        Returns:
            {
                "email": str,
                "valid": bool,
                "score": int (0-100),
                "checks": {
                    "syntax": bool,
                    "mx": bool,
                    "disposable": bool (True = is disposable = BAD),
                    "role_based": bool (True = is role-based = WARN),
                    "smtp": bool | None (None if not checked)
                },
                "warnings": list[str],
                "blockers": list[str],
                "verification_method": str
            }
        """
        result = {
            "email": email,
            "valid": False,
            "score": 0,
            "checks": {
                "syntax": False,
                "mx": False,
                "disposable": False,
                "role_based": False,
                "smtp": None,
            },
            "warnings": [],
            "blockers": [],
            "verification_method": "syntax",
        }

        if not email or not isinstance(email, str):
            result["blockers"].append("Empty or invalid email")
            return result

        email = email.strip().lower()

        # 1. Syntax check
        result["checks"]["syntax"] = bool(EMAIL_REGEX.match(email))
        if not result["checks"]["syntax"]:
            result["blockers"].append(f"Invalid email syntax: {email}")
            return result

        domain = email.split("@")[1]

        # 2. MX record check
        result["checks"]["mx"] = self._check_mx(domain)
        if not result["checks"]["mx"]:
            result["blockers"].append(f"No MX records for domain: {domain}")
            return result

        result["verification_method"] = "mx"

        # 3. Disposable email check
        result["checks"]["disposable"] = domain in DISPOSABLE_DOMAINS
        if result["checks"]["disposable"]:
            result["blockers"].append(f"Disposable email domain: {domain}")
            result["score"] = 0
            return result

        # 4. Role-based email check
        result["checks"]["role_based"] = any(email.startswith(prefix) for prefix in ROLE_PREFIXES)
        if result["checks"]["role_based"]:
            result["warnings"].append(f"Role-based email (lower deliverability): {email}")

        # Calculate score
        score = 100
        if result["checks"]["role_based"]:
            score -= 20
        result["score"] = score

        # 5. SMTP handshake (optional)
        if self.check_smtp:
            smtp_ok = self._smtp_check(email, domain)
            result["checks"]["smtp"] = smtp_ok
            result["verification_method"] = "smtp"
            if not smtp_ok:
                result["blockers"].append("SMTP RCPT TO check failed — mailbox may not exist")
                result["score"] = 0
                return result

        result["valid"] = len(result["blockers"]) == 0
        return result

    def verify_batch(self, emails: list) -> dict:
        """
        Verify a batch of emails.

        Returns:
            {
                "total": int,
                "valid": int,
                "invalid": int,
                "warnings": int,
                "results": list[dict]
            }
        """
        results = []
        valid = 0
        invalid = 0
        warnings = 0

        for email in emails:
            r = self.verify(email)
            results.append(r)
            if r["valid"]:
                valid += 1
                if r["warnings"]:
                    warnings += 1
            else:
                invalid += 1

        return {
            "total": len(emails),
            "valid": valid,
            "invalid": invalid,
            "warnings": warnings,
            "results": results,
        }

    def _check_mx(self, domain: str) -> bool:
        """Check if domain has MX records (accepts email)."""
        try:
            mx_records = dns.resolver.resolve(domain, "MX")
            return len(mx_records) > 0
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return False
        except dns.resolver.LifetimeTimeout:
            logger.warning("MX lookup timed out for %s", domain)
            return False
        except Exception as e:
            logger.warning("MX lookup error for %s: %s", domain, e)
            return False

    def _smtp_check(self, email: str, domain: str) -> bool:
        """Verify mailbox exists via SMTP RCPT TO handshake."""
        import smtplib
        import socket

        try:
            # Get MX server
            mx_records = dns.resolver.resolve(domain, "MX")
            mx_host = str(mx_records[0].exchange).rstrip(".")

            with smtplib.SMTP(mx_host, 25, timeout=self.timeout) as server:
                server.ehlo("deus-verify.local")
                server.mail("verify@deus-verify.local")
                code, _ = server.rcpt(email)
                return code == 250

        except Exception as e:
            logger.debug("SMTP check failed for %s: %s", email, e)
            return False

    def mark_leads_verified(self, leads: list, db_module=None) -> dict:
        """
        Verify a batch of leads and mark them in the database.

        Args:
            leads: List of lead dicts with 'business_email' field
            db_module: Database module with update_lead() function

        Returns:
            {"verified": int, "failed": int, "skipped": int}
        """
        verified = 0
        failed = 0
        skipped = 0

        for lead in leads:
            email = lead.get("business_email", "")
            if not email:
                skipped += 1
                continue

            result = self.verify(email)

            if db_module and lead.get("id"):
                try:
                    db_module.update_lead(lead["id"], {
                        "email_verified": 1 if result["valid"] else 0,
                        "email_verified_at": __import__("time").time(),
                        "verification_method": result["verification_method"],
                    })
                except Exception as e:
                    logger.warning("Failed to update lead %d: %s", lead["id"], e)

            if result["valid"]:
                verified += 1
            else:
                failed += 1

        return {"verified": verified, "failed": failed, "skipped": skipped}
