"""
rules_engine.py — DEUS 3.0
===========================
Loads and caches rules/regulations from a PDF file. Provides extracted text
context that can be injected into LLM prompts for all agents.

Usage:
    context = get_rules_context()   # returns cached PDF text or "" if no PDF
    reload_rules()                  # force re-read from disk

Place your rules PDF at:  RULES_PDF_PATH (default: "rules.pdf" in project root)
The engine checks file mtime and auto-reloads when the PDF changes.
Gracefully returns "" when no PDF exists — no-op for all callers.
"""

import os
import time
import logging

logger = logging.getLogger(__name__)

RULES_PDF_PATH = os.getenv("RULES_PDF_PATH", os.path.join(os.path.dirname(__file__), "rules.pdf"))

_cached_text: str = ""
_cached_mtime: float = 0.0


def _extract_pdf_text(path: str) -> str:
    """Extract text from a PDF file using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        return "\n\n".join(pages)
    except ImportError:
        logger.warning("pypdf not installed — run: pip install pypdf")
        return ""
    except Exception as e:
        logger.warning("Failed to extract PDF text from %s: %s", path, e)
        return ""


def get_rules_context() -> str:
    """
    Return the cached rules/regulations text from the PDF.
    Auto-reloads if the file has been modified since last read.
    Returns empty string if no PDF exists — safe for all callers.
    """
    global _cached_text, _cached_mtime

    if not os.path.exists(RULES_PDF_PATH):
        if _cached_text:
            _cached_text = ""
            _cached_mtime = 0.0
        return ""

    current_mtime = os.path.getmtime(RULES_PDF_PATH)
    if current_mtime != _cached_mtime or not _cached_text:
        logger.info("Loading rules from %s...", RULES_PDF_PATH)
        _cached_text = _extract_pdf_text(RULES_PDF_PATH)
        _cached_mtime = current_mtime
        if _cached_text:
            logger.info("Rules loaded: %d characters", len(_cached_text))
        else:
            logger.info("Rules PDF found but no extractable text")

    return _cached_text


def reload_rules() -> str:
    """Force re-read the PDF and return updated context."""
    global _cached_text, _cached_mtime
    _cached_text = ""
    _cached_mtime = 0.0
    return get_rules_context()


def has_rules() -> bool:
    """Check if a rules PDF exists and has content."""
    return bool(get_rules_context())
