"""
spam_checker.py — DEUS 3.0
============================
Email content anti-spam scoring. Analyzes email body and subject
for spam trigger words, formatting issues, and content patterns
that reduce inbox placement.

Usage:
    checker = SpamChecker()
    result = checker.check(subject="Quick proposal", body="Hi, ...")
    # result = {"score": 15, "level": "safe", "issues": [...], "suggestions": [...]}

Score ranges:
    0-20:  SAFE — high inbox placement
    21-40: WARN — may land in spam for some providers
    41-60: RISK — likely spam filtered
    61-100: BLOCK — will almost certainly be flagged
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# --- Spam trigger words (weighted) ---
SPAM_WORDS = {
    # High weight (score +8 each)
    "free": 8, "winner": 8, "congratulations": 8, "urgent": 8,
    "act now": 8, "limited time": 8, "exclusive deal": 8,
    "click here": 8, "buy now": 8, "order now": 8,
    "100% free": 10, "no cost": 8, "risk free": 8,
    "cash bonus": 8, "earn money": 8, "make money": 8,
    "double your": 8, "increase your": 6,

    # Medium weight (score +5 each)
    "discount": 5, "special offer": 5, "best price": 5,
    "lowest price": 5, "incredible deal": 5, "amazing": 5,
    "revolutionary": 5, "breakthrough": 5, "miracle": 5,
    "guarantee": 5, "satisfaction": 4, "no obligation": 5,
    "apply now": 5, "subscribe now": 5, "join now": 5,
    "you have been selected": 7, "you won": 7,
    "dear friend": 6, "dear valued": 6,

    # Low weight (score +3 each)
    "proposal": 3, "business opportunity": 3, "partnership": 2,
    "quick call": 2, "schedule a meeting": 2, "book a call": 2,
    "free trial": 3, "no credit card": 3, "unlimited": 3,
    "best regards": 1, "looking forward": 1,
}

# --- Formatting red flags ---
ALL_CAPS_RATIO_THRESHOLD = 0.3  # > 30% caps = suspicious
EXCESSIVE_EXCLAMATION_THRESHOLD = 3  # > 3 exclamation marks
EXCESSIVE_PUNCTUATION_THRESHOLD = 5  # > 5 dots/!/? in sequence
URL_COUNT_THRESHOLD = 5  # > 5 URLs = suspicious
SHORT_BODY_THRESHOLD = 20  # < 20 words = suspicious (too short = template-ish)


class SpamChecker:
    """Checks email content for spam indicators."""

    def __init__(self):
        self.spam_words = SPAM_WORDS

    def check(self, subject: str = "", body: str = "") -> dict:
        """
        Analyze email content for spam indicators.

        Returns:
            {
                "score": int (0-100),
                "level": "safe" | "warn" | "risk" | "block",
                "issues": list[str],
                "suggestions": list[str],
                "details": {
                    "word_score": int,
                    "format_score": int,
                    "url_score": int,
                    "length_score": int
                }
            }
        """
        score = 0
        issues = []
        suggestions = []
        details = {
            "word_score": 0,
            "format_score": 0,
            "url_score": 0,
            "length_score": 0,
        }

        combined_text = f"{subject} {body}".lower()

        # 1. Spam trigger words
        word_score = self._check_spam_words(combined_text)
        details["word_score"] = word_score
        score += word_score
        if word_score > 10:
            issues.append(f"Contains {word_score // 3}+ spam trigger words")
            suggestions.append("Remove words like FREE, ACT NOW, URGENT, CLICK HERE")

        # 2. All caps ratio
        caps_score = self._check_caps_ratio(subject, body)
        details["format_score"] += caps_score
        score += caps_score
        if caps_score > 0:
            issues.append("Excessive use of ALL CAPS")
            suggestions.append("Use normal capitalization — ALL CAPS triggers spam filters")

        # 3. Excessive punctuation
        punct_score = self._check_punctuation(body)
        details["format_score"] += punct_score
        score += punct_score
        if punct_score > 0:
            issues.append("Excessive punctuation (!!! or ??? or ...)")
            suggestions.append("Limit exclamation marks to 1-2 per email")

        # 4. URL count
        url_score = self._check_urls(body)
        details["url_score"] = url_score
        score += url_score
        if url_score > 0:
            issues.append(f"Too many URLs in body ({url_score // 2}+)")
            suggestions.append("Limit to 1-2 relevant URLs per email")

        # 5. Body length
        length_score = self._check_body_length(body)
        details["length_score"] = length_score
        score += length_score
        if length_score > 0:
            issues.append("Body too short — looks like a template")
            suggestions.append("Write a more personalized, detailed message (50+ words)")

        # 6. Subject line checks
        subject_score = self._check_subject(subject)
        details["format_score"] += subject_score
        score += subject_score
        if subject_score > 0:
            issues.append("Subject line has spam indicators")
            suggestions.append("Keep subject under 50 words, no ALL CAPS, no spam words")

        # Clamp score
        score = min(100, max(0, score))

        # Determine level
        if score <= 20:
            level = "safe"
        elif score <= 40:
            level = "warn"
        elif score <= 60:
            level = "risk"
        else:
            level = "block"

        return {
            "score": score,
            "level": level,
            "issues": issues,
            "suggestions": suggestions,
            "details": details,
        }

    def _check_spam_words(self, text: str) -> int:
        """Check for spam trigger words in text."""
        score = 0
        for word, weight in self.spam_words.items():
            count = text.count(word.lower())
            if count > 0:
                score += weight * count
        return min(40, score)  # Cap at 40

    def _check_caps_ratio(self, subject: str, body: str) -> int:
        """Check ratio of ALL CAPS characters."""
        full_text = f"{subject} {body}"
        if len(full_text) < 10:
            return 0

        # Only check alphabetic characters
        alpha_chars = [c for c in full_text if c.isalpha()]
        if not alpha_chars:
            return 0

        caps_count = sum(1 for c in alpha_chars if c.isupper())
        ratio = caps_count / len(alpha_chars)

        if ratio > ALL_CAPS_RATIO_THRESHOLD:
            return int(15 * (ratio - ALL_CAPS_RATIO_THRESHOLD) / 0.7)
        return 0

    def _check_punctuation(self, body: str) -> int:
        """Check for excessive punctuation."""
        score = 0
        # Count consecutive exclamation/question marks
        excl = len(re.findall(r"!{2,}", body))
        quest = len(re.findall(r"\?{2,}", body))
        dots = len(re.findall(r"\.{4,}", body))

        if excl > 0:
            score += excl * 5
        if quest > 0:
            score += quest * 5
        if dots > 0:
            score += dots * 3

        return min(20, score)

    def _check_urls(self, body: str) -> int:
        """Check URL count in body."""
        urls = re.findall(r"https?://\S+", body)
        if len(urls) > URL_COUNT_THRESHOLD:
            return (len(urls) - URL_COUNT_THRESHOLD) * 3
        return 0

    def _check_body_length(self, body: str) -> int:
        """Check if body is too short (looks like a template)."""
        words = body.split()
        if len(words) < SHORT_BODY_THRESHOLD:
            return 10  # Penalty for very short bodies
        return 0

    def _check_subject(self, subject: str) -> int:
        """Check subject line for spam indicators."""
        if not subject:
            return 0

        score = 0
        subject_lower = subject.lower()

        # ALL CAPS subject
        if subject.isupper() and len(subject) > 5:
            score += 10

        # Spam words in subject
        for word in ["free", "urgent", "act now", "limited", "winner", "congratulations"]:
            if word in subject_lower:
                score += 5

        # Subject too long (>60 chars)
        if len(subject) > 60:
            score += 3

        # Subject starts with RE: or FW: (can look suspicious if fake)
        if subject_lower.startswith(("re:", "fw:", "fwd:")):
            score += 2

        return min(15, score)

    def check_before_send(self, subject: str, body: str) -> dict:
        """
        Quick check before sending. Returns should_send flag.
        Use this in the outreach flow.
        """
        result = self.check(subject=subject, body=body)

        return {
            "should_send": result["level"] in ("safe", "warn"),
            "score": result["score"],
            "level": result["level"],
            "issues": result["issues"],
            "suggestions": result["suggestions"],
            "message": (
                f"Spam score: {result['score']}/100 ({result['level'].upper()})"
                + (f" — {'; '.join(result['issues'][:3])}" if result["issues"] else " — All clear")
            ),
        }
