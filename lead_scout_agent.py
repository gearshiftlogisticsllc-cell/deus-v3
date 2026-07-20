"""
lead_scout_agent.py  (v4 — query fan-out for scale, phone/domain-normalized dedup,
                          threaded website email enrichment)
=========================================================================================

WHAT CHANGED FROM v3 (and why)
-------------------------------
  1. STUCK AT ~39 LEADS -> FIXED
     Google Places/Search only returns a limited set of results for any ONE exact
     query string (~20-60, often fewer). v3 always ran the exact same query with a
     hardcoded pages=3, so it hit that ceiling every time regardless of `target`.
     v4 asks the LLM once (cheap, single call) to generate many differently-phrased
     query variants (different neighborhoods/districts + business-type synonyms),
     then loops through them — accumulating and deduping — until `target` is hit or
     returns go stale (4 variants in a row add nothing new).

  2. REPEATED / DUPLICATE LEADS -> FIXED
     Old dedup compared raw phone strings and raw domains, so "+92 300 1234567" vs
     "0300-1234567", or "www.acme.com" vs "acme.com", were treated as different
     businesses. v4 normalizes phone (digits only) and domain (strips www.) before
     comparing, and also normalizes business names (lowercase, punctuation-stripped)
     so near-identical entries collapse correctly.

  3. NO EMAILS -> FIXED
     Google Places/Search never return business emails — only what Google indexes.
     v4 adds a fast, threaded EmailEnricher that visits each lead's website (homepage
     + a couple of likely contact paths), looks for a mailto: link or a plain-text
     email via regex, and filters out junk (noreply@, image-file false matches,
     platform domains like sentry.io/wixpress.com). This does NOT use the LLM —
     it's just requests + regex, so it's fast and doesn't burn tokens.

RUNTIME
-------
  - DuckDuckGo + direct web scraping are tried FIRST (free, no API key needed).
  - Bright Data is used when a key is available.
  - Serper is used ONLY as the last resort (when free methods fail to reach target).
  - Groq -> Gemini failover is automatic and transparent.
  - Default target is now 400 leads; you'll be prompted and can override.

INSTALL (once)
--------------
    pip install requests python-dotenv groq google-genai beautifulsoup4 lxml

.env  (all optional except at least ONE LLM)
-----
    # --- Search sources (tried in order: autonomous first) ---
    # DuckDuckGo + direct web scrape — FREE, no API key needed, always active.
    BRIGHTDATA_API_KEY=...           # paid (optional, can paste at runtime)
    BRIGHTDATA_DATASET_ID=...        # optional: a Web-Scraper dataset to trigger
    SERPER_API_KEY=...               # paid fallback (https://serper.dev, 2,500 free)

    # --- LLM (need at least one; Groq tried first, Gemini on failover) ---
    GROQ_API_KEY=...
    GEMINI_API_KEY=...               # https://aistudio.google.com/apikey (free tier)

USAGE
-----
    python lead_scout_agent.py
    # then type your niche, e.g.:  dental clinics in Lahore
"""

import os
import re
import csv
import json
import time
import logging
import concurrent.futures
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any

import requests
from dotenv import load_dotenv

from base_agent import BaseAgent, AgentResult, AgentHealth, make_result, make_health

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("lead_scout")


# ===========================================================================
# Config
# ===========================================================================
TARGET_LEADS = 400                      # collection goal (was 200)
GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.5-flash"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
MAILTO_RE = re.compile(r"mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", re.I)
SOCIAL_DOMAINS = ("facebook.com", "linkedin.com", "instagram.com", "twitter.com", "x.com")

# domains/prefixes that show up in scraped HTML but are never a real business contact
EMAIL_JUNK_DOMAINS = (
    "sentry.io", "wixpress.com", "godaddy.com", "example.com", "domain.com",
    "yourdomain.com", "schema.org", "w3.org", "gstatic.com", "cloudflare.com",
    "sentry-next.wixpress.com", "wordpress.com", "wp.com",
)
EMAIL_JUNK_PREFIXES = ("noreply@", "no-reply@", "donotreply@", "do-not-reply@")
EMAIL_JUNK_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")

CONTACT_PATHS = ["", "/contact", "/contact-us", "/contactus", "/about", "/about-us", "/aboutus", "/team", "/leadership", "/management"]
PHONE_RE = re.compile(
    r"(?:(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4})"
    r"(?!\w*@)"
)
# Owner/leadership name patterns in raw HTML or plain text
OWNER_PATTERNS = [
    re.compile(r"(?:Owner|Founder|CEO|President)\s*[:\s–\-]+\s*([A-Z][a-z]*(?:\s+[A-Z][a-z]*)+)"),
    re.compile(r"([A-Z][a-z]*(?:\s+[A-Z][a-z]*)+)\s*[–\-–]\s*(?:Owner|Founder|CEO|President)"),
    re.compile(r"(?:Director|Manager|Head)\s+of\s+\w+\s*[:\s–\-]+\s*([A-Z][a-z]*(?:\s+[A-Z][a-z]*)+)"),
    re.compile(r'(?:by|with|under)\s+([A-Z][a-z]*(?:\s+[A-Z][a-z]*)+)', re.I),
    re.compile(r'(?:founded|led|run|owned|operated)\s+(?:by|under)\s+([A-Z][a-z]*(?:\s+[A-Z][a-z]*)+)', re.I),
]

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "").strip()


# ===========================================================================
# LLM client with Groq -> Gemini failover
# ===========================================================================
class LLM:
    """
    Calls Groq first. On rate-limit / quota / auth failure, transparently
    switches to Gemini for the rest of the run. If neither is configured,
    AI features are skipped gracefully (scraping still works).
    """

    def __init__(self):
        self.groq = None
        self.gemini = None
        self.use_gemini = False  # sticky switch once Groq is exhausted

        if GROQ_API_KEY:
            try:
                from groq import Groq
                self.groq = Groq(api_key=GROQ_API_KEY)
            except Exception as e:
                logger.warning("Groq init failed (%s); will rely on Gemini.", e)

        if GEMINI_API_KEY:
            try:
                from google import genai as _genai
                _client = _genai.Client(api_key=GEMINI_API_KEY)
                self.gemini = _client
            except Exception as e:
                logger.warning("Gemini init failed (%s).", e)

        if not self.groq and not self.gemini:
            logger.warning("No LLM configured. Niche parsing, query fan-out, and scoring will be skipped.")

    @property
    def available(self) -> bool:
        return bool(self.groq or self.gemini)

    @staticmethod
    def _is_quota_error(err: Exception) -> bool:
        s = str(err).lower()
        return any(k in s for k in (
            "rate limit", "rate_limit", "quota", "429", "too many requests",
            "insufficient", "exceeded", "tokens per", "tpm", "rpm",
        ))

    def _groq_call(self, prompt: str, timeout: int) -> str:
        resp = self.groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            timeout=timeout,
        )
        return (resp.choices[0].message.content or "").strip()

    def _gemini_call(self, prompt: str) -> str:
        resp = self.gemini.models.generate_content(
            model=GEMINI_MODEL, contents=prompt
        )
        return (resp.text or "").strip()

    def ask(self, prompt: str, timeout: int = 60) -> str:
        """Return text, with automatic Groq->Gemini failover."""
        if self.groq and not self.use_gemini:
            try:
                return self._groq_call(prompt, timeout)
            except Exception as e:
                if self._is_quota_error(e) and self.gemini:
                    logger.warning("Groq quota/rate-limit hit -> switching to Gemini for the rest of this run.")
                    self.use_gemini = True
                elif self.gemini:
                    logger.warning("Groq error (%s) -> trying Gemini.", e)
                    self.use_gemini = True
                else:
                    logger.error("Groq failed and no Gemini fallback: %s", e)
                    return ""
        if self.gemini:
            try:
                return self._gemini_call(prompt)
            except Exception as e:
                logger.error("Gemini failed: %s", e)
                return ""
        return ""

    def ask_json(self, prompt: str, timeout: int = 60):
        raw = self.ask(prompt + "\n\nReturn ONLY valid JSON. No markdown, no code fences, no prose.", timeout)
        if not raw:
            return None
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.lstrip().lower().startswith("json"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[4:]
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
            logger.warning("JSON parse failed. Raw head: %s", raw[:200])
            return None


# ===========================================================================
# Helpers
# ===========================================================================
def _clean_email(email: str) -> Optional[str]:
    if not email:
        return None
    if any(d in email.lower() for d in SOCIAL_DOMAINS):
        return None
    return email


def _valid_scraped_email(email: str) -> bool:
    if not email:
        return False
    e = email.lower().strip()
    if any(bad in e for bad in EMAIL_JUNK_DOMAINS):
        return False
    if e.startswith(EMAIL_JUNK_PREFIXES):
        return False
    if e.endswith(EMAIL_JUNK_EXTENSIONS):
        return False
    if any(d in e for d in SOCIAL_DOMAINS):
        return False
    return True


def _domain(url: str) -> str:
    """Normalize a URL down to a comparable domain (no scheme, no www., no path)."""
    if not url:
        return ""
    u = re.sub(r"^https?://", "", url.strip().lower())
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0]


def _norm_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy-safe name comparison."""
    n = (name or "").lower().strip()
    n = re.sub(r"[^\w\s]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n


def _norm_phone(phone: str) -> str:
    """Digits only, so '+92 300 1234567' and '0300-1234567' compare equal."""
    return re.sub(r"\D", "", phone or "")


def _deduplicate(leads: List[dict]) -> List[dict]:
    seen, unique = set(), []
    for lead in leads:
        name_key = _norm_name(lead.get("business_name"))
        domain_key = _domain(lead.get("website") or "")
        phone_key = _norm_phone(lead.get("phone") or "")

        if not name_key:
            # no name at all -> can't safely judge duplicate, keep it
            unique.append(lead)
            continue

        if domain_key and (name_key, "d", domain_key) in seen:
            continue
        if phone_key and (name_key, "p", phone_key) in seen:
            continue
        # also catch same domain across different name spellings (same business, same site)
        if domain_key and ("d", domain_key) in seen:
            continue
        if phone_key and ("p", phone_key) in seen:
            continue

        if domain_key:
            seen.add((name_key, "d", domain_key))
            seen.add(("d", domain_key))
        if phone_key:
            seen.add((name_key, "p", phone_key))
            seen.add(("p", phone_key))
        seen.add((name_key,))
        unique.append(lead)
    return unique


def _empty_lead() -> dict:
    return {
        "business_name": "", "owner_name": "", "category": "", "address": "",
        "phone": "", "business_email": "", "website": "", "services_offered": "",
        "niche": "", "location": "", "source": "", "linkedin": "",
        "score": "", "analysis": "", "outreach_suggestion": "",
    }


def _save_outputs(leads: List[dict], json_path="leads.json", csv_path="leads.csv"):
    if not leads:
        logger.warning("No leads to save.")
        return
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, ensure_ascii=False)
    fieldnames = list(_empty_lead().keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for lead in leads:
            row = _empty_lead()
            row.update({k: lead.get(k, "") for k in fieldnames})
            w.writerow(row)
    # Also save to database
    try:
        from app.database import upsert_leads_batch
        result = upsert_leads_batch(leads)
        logger.info("Database import: %d imported, %d skipped", result["imported"], result["skipped"])
    except Exception as e:
        logger.warning("Database save failed (JSON/CSV still saved): %s", e)
    logger.info("Saved %d leads -> %s and %s", len(leads), json_path, csv_path)


# ===========================================================================
# Search sources
# ===========================================================================
class BrightDataSource:
    """
    PRIMARY source.

      A) SERP API  — Google results as structured JSON. Reliable, instant.
      B) Web Scraper dataset trigger — if you set BRIGHTDATA_DATASET_ID to a
         business/maps/LinkedIn dataset, we trigger a collection and poll for it.
    """

    SERP_ENDPOINT = "https://api.brightdata.com/request"
    TRIGGER_ENDPOINT = "https://api.brightdata.com/datasets/v3/trigger"
    SNAPSHOT_ENDPOINT = "https://api.brightdata.com/datasets/v3/snapshot"

    def __init__(self, api_key: str, dataset_id: str = "", serp_zone: str = ""):
        self.api_key = api_key.strip()
        self.dataset_id = (dataset_id or "").strip()
        self.serp_zone = (serp_zone or os.getenv("BRIGHTDATA_SERP_ZONE", "")).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def serp_search(self, query: str, gl: str = "", num: int = 20) -> List[dict]:
        if not self.serp_zone:
            logger.info("Bright Data SERP skipped (no BRIGHTDATA_SERP_ZONE set). Using dataset/Serper instead.")
            return []
        google_url = "https://www.google.com/search?" + requests.compat.urlencode(
            {"q": query, "num": min(num, 100), "brd_json": "1"}
        )
        payload = {"zone": self.serp_zone, "url": google_url, "format": "raw"}
        try:
            r = requests.post(self.SERP_ENDPOINT, headers=self._headers(), json=payload, timeout=60)
            if r.status_code != 200:
                logger.warning("Bright Data SERP %s: %s", r.status_code, r.text[:200])
                return []
            data = r.json()
        except Exception as e:
            logger.warning("Bright Data SERP request failed: %s", e)
            return []

        out = []
        organic = data.get("organic", []) if isinstance(data, dict) else []
        for item in organic:
            lead = _empty_lead()
            lead["business_name"] = item.get("title", "")
            lead["website"] = item.get("link", "")
            lead["services_offered"] = item.get("description", "") or item.get("snippet", "")
            lead["source"] = "brightdata_serp"
            if "linkedin.com" in (lead["website"] or "").lower():
                lead["linkedin"] = lead["website"]
            out.append(lead)
        return out

    def dataset_collect(self, query: str, location: str = "", limit: int = 200, poll_secs: int = 8, max_wait: int = 240) -> List[dict]:
        if not self.dataset_id:
            return []
        trigger_url = f"{self.TRIGGER_ENDPOINT}?dataset_id={self.dataset_id}&include_errors=true"
        body = [{"query": query, "location": location, "limit": limit}]
        try:
            r = requests.post(trigger_url, headers=self._headers(), json=body, timeout=60)
            if r.status_code not in (200, 202):
                logger.warning("Bright Data trigger %s: %s", r.status_code, r.text[:200])
                return []
            snapshot_id = r.json().get("snapshot_id")
            if not snapshot_id:
                logger.warning("Bright Data trigger returned no snapshot_id.")
                return []
        except Exception as e:
            logger.warning("Bright Data trigger failed: %s", e)
            return []

        logger.info("Bright Data collection running (snapshot %s)... polling.", snapshot_id)
        waited = 0
        while waited < max_wait:
            time.sleep(poll_secs)
            waited += poll_secs
            try:
                s = requests.get(
                    f"{self.SNAPSHOT_ENDPOINT}/{snapshot_id}?format=json",
                    headers=self._headers(), timeout=60,
                )
                if s.status_code == 202:
                    continue
                if s.status_code != 200:
                    logger.warning("Snapshot fetch %s: %s", s.status_code, s.text[:200])
                    return []
                rows = s.json()
                if isinstance(rows, dict) and rows.get("status") in ("running", "building"):
                    continue
                return self._normalize_dataset_rows(rows)
            except Exception as e:
                logger.warning("Snapshot poll error: %s", e)
        logger.warning("Bright Data collection timed out after %ss.", max_wait)
        return []

    @staticmethod
    def _normalize_dataset_rows(rows: Any) -> List[dict]:
        if not isinstance(rows, list):
            return []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            lead = _empty_lead()
            lead["business_name"] = row.get("name") or row.get("business_name") or row.get("company") or row.get("title", "")
            lead["owner_name"] = row.get("owner") or row.get("owner_name") or row.get("contact_name", "")
            lead["category"] = row.get("category") or row.get("industry", "")
            lead["address"] = row.get("address") or row.get("full_address", "")
            lead["phone"] = row.get("phone") or row.get("phone_number", "")
            lead["business_email"] = _clean_email(row.get("email") or row.get("business_email", "")) or ""
            lead["website"] = row.get("website") or row.get("url", "")
            lead["linkedin"] = row.get("linkedin") or row.get("linkedin_url", "")
            lead["source"] = "brightdata_dataset"
            out.append(lead)
        return out

    def find(self, query: str, location: str, target: int) -> List[dict]:
        leads = self.dataset_collect(query, location, limit=target)
        if len(leads) < target:
            leads += self.serp_search(f"{query} {location}".strip(), num=min(target, 100))
        return leads


class SerperSource:
    """
    SECONDARY source. Google 'places' (Maps) gives the richest business leads
    (name, phone, address, website). Regular 'search' supplements with websites.
    """

    PLACES_URL = "https://google.serper.dev/places"
    SEARCH_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"X-API-KEY": self.api_key, "Content-Type": "application/json"}

    def places(self, query: str, location: str = "", pages: int = 3) -> List[dict]:
        out = []
        for page in range(1, pages + 1):
            payload = {"q": query, "page": page}
            if location:
                payload["location"] = location
            try:
                r = requests.post(self.PLACES_URL, headers=self._headers(), json=payload, timeout=45)
                if r.status_code != 200:
                    logger.warning("Serper places %s: %s", r.status_code, r.text[:200])
                    break
                places = r.json().get("places", [])
            except Exception as e:
                logger.warning("Serper places failed: %s", e)
                break
            if not places:
                break
            for p in places:
                lead = _empty_lead()
                lead["business_name"] = p.get("title", "")
                lead["category"] = p.get("category", "")
                lead["address"] = p.get("address", "")
                lead["phone"] = p.get("phoneNumber", "")
                lead["website"] = p.get("website", "")
                lead["source"] = "serper_places"
                out.append(lead)
        return out

    def search(self, query: str, num: int = 30) -> List[dict]:
        payload = {"q": query, "num": min(num, 100)}
        try:
            r = requests.post(self.SEARCH_URL, headers=self._headers(), json=payload, timeout=45)
            if r.status_code != 200:
                logger.warning("Serper search %s: %s", r.status_code, r.text[:200])
                return []
            organic = r.json().get("organic", [])
        except Exception as e:
            logger.warning("Serper search failed: %s", e)
            return []
        out = []
        for item in organic:
            lead = _empty_lead()
            lead["business_name"] = item.get("title", "")
            lead["website"] = item.get("link", "")
            lead["services_offered"] = item.get("snippet", "")
            lead["source"] = "serper_search"
            if "linkedin.com" in (lead["website"] or "").lower():
                lead["linkedin"] = lead["website"]
            out.append(lead)
        return out


# ===========================================================================
# Autonomous search sources (free, no API key required)
# ===========================================================================
class DuckDuckGoSource:
    """
    PRIMARY autonomous source. Uses the duckduckgo_search library to search
    the web for business listings — completely free, no API key needed.
    Falls back to direct HTML scraping of DuckDuckGo if the library is not
    installed.
    """

    def __init__(self):
        self._ddgs = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }

    @property
    def enabled(self) -> bool:
        return True

    def _get_ddgs(self):
        if self._ddgs is not None:
            return self._ddgs
        try:
            from duckduckgo_search import DDGS
            self._ddgs = DDGS()
        except ImportError:
            self._ddgs = False
        return self._ddgs

    def search(self, query: str, max_results: int = 30) -> List[dict]:
        out = []
        ddgs = self._get_ddgs()
        if ddgs:
            try:
                for r in ddgs.text(query, max_results=max_results):
                    lead = _empty_lead()
                    lead["business_name"] = r.get("title", "")
                    lead["website"] = r.get("href", "")
                    lead["services_offered"] = r.get("body", "")
                    lead["source"] = "duckduckgo"
                    if "linkedin.com" in (lead["website"] or "").lower():
                        lead["linkedin"] = lead["website"]
                    out.append(lead)
                if out:
                    logger.info("DuckDuckGo returned %d results for %r", len(out), query)
                    return out
            except Exception as e:
                logger.warning("DuckDuckGo library search failed: %s", e)

        # Fallback: scrape DuckDuckGo HTML directly
        try:
            url = "https://html.duckduckgo.com/html/"
            params = {"q": query}
            r = requests.get(url, params=params, headers=self.headers, timeout=15)
            if r.status_code == 200:
                import re as _re
                for link in _re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', r.text):
                    href, title = link[0], link[1]
                    lead = _empty_lead()
                    lead["business_name"] = title.strip()
                    lead["website"] = href
                    lead["source"] = "duckduckgo_scrape"
                    out.append(lead)
                for snippet in _re.findall(r'<a[^>]+class="result__snippet"[^>]*>([^<]+)</a>', r.text):
                    if len(out) > len(snippet):
                        out[len(snippet)]["services_offered"] = snippet.strip()
                logger.info("DuckDuckGo scrape returned %d results for %r", len(out), query)
        except Exception as e:
            logger.warning("DuckDuckGo scrape failed: %s", e)

        return out


class DirectWebSource:
    """
    SECONDARY autonomous source. Scrapes Google search results directly
    using requests + regex. Fragile (Google may change HTML), but free.
    Also tries common business directory sites.
    """

    GOOGLE_URL = "https://www.google.com/search"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }

    @property
    def enabled(self) -> bool:
        return True

    def _parse_google_results(self, html: str, source_tag: str) -> List[dict]:
        out = []
        try:
            import re as _re
            # Extract search result blocks
            blocks = _re.split(r'<div[^>]*class="[^"]*g[^"]*"[^>]*>', html)[1:]
            for block in blocks:
                lead = _empty_lead()
                # Title
                title_match = _re.search(r'<h3[^>]*>([^<]+)</h3>', block)
                if title_match:
                    lead["business_name"] = title_match.group(1).strip()
                # URL
                url_match = _re.search(r'<a[^>]*href="https?://([^"]+)"[^>]*>', block)
                if url_match:
                    lead["website"] = "https://" + url_match.group(1)
                # Snippet
                snippet_match = _re.search(r'<div[^>]*class="[^"]*VwiC3b[^"]*"[^>]*>(.*?)</div>', block)
                if snippet_match:
                    lead["services_offered"] = _re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                lead["source"] = source_tag
                if lead.get("business_name"):
                    out.append(lead)
        except Exception as e:
            logger.warning("Google HTML parse error: %s", e)
        return out

    def search_google(self, query: str, num: int = 20) -> List[dict]:
        out = []
        try:
            params = {"q": query, "num": min(num, 100), "hl": "en"}
            r = requests.get(self.GOOGLE_URL, params=params, headers=self.HEADERS, timeout=15)
            if r.status_code == 200:
                out = self._parse_google_results(r.text, "direct_google")
                logger.info("Direct Google scrape returned %d results for %r", len(out), query)
            return out
        except Exception as e:
            logger.warning("Direct Google scrape failed: %s", e)
            return out

    def search_directory(self, query: str, location: str) -> List[dict]:
        """Scrape Yelp for business listings — yields rich structured data."""
        out = []
        search_q = f"{query} {location}".strip()
        yelp_url = f"https://www.yelp.com/search?find_desc={requests.compat.quote(query)}&find_loc={requests.compat.quote(location or '')}"
        try:
            r = requests.get(yelp_url, headers=self.HEADERS, timeout=15)
            if r.status_code == 200:
                import re as _re
                biz_pattern = _re.compile(
                    r'<h3[^>]*class="[^"]*searchResultTitle[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
                    _re.DOTALL
                )
                for match in biz_pattern.finditer(r.text):
                    lead = _empty_lead()
                    lead["business_name"] = match.group(2).strip()
                    lead["website"] = "https://www.yelp.com" + match.group(1)
                    lead["source"] = "yelp_directory"
                    lead["services_offered"] = f"Listed on Yelp for {search_q}"
                    out.append(lead)
                if out:
                    logger.info("Yelp directory returned %d results for %r", len(out), search_q)
        except Exception as e:
            logger.warning("Yelp directory scrape failed: %s", e)

        # Also try YellowPages
        try:
            yp_url = f"https://www.yellowpages.com/search?search_terms={requests.compat.quote(query)}&geo_location_terms={requests.compat.quote(location or '')}"
            r = requests.get(yp_url, headers=self.HEADERS, timeout=15)
            if r.status_code == 200:
                import re as _re
                biz_names = _re.findall(r'<a[^>]*class="[^"]*business-name[^"]*"[^>]*>([^<]+)</a>', r.text)
                biz_phones = _re.findall(r'<div[^>]*class="[^"]*phone[^"]*"[^>]*>([^<]+)</div>', r.text)
                for i, name in enumerate(biz_names):
                    lead = _empty_lead()
                    lead["business_name"] = name.strip()
                    if i < len(biz_phones):
                        lead["phone"] = biz_phones[i].strip()
                    lead["source"] = "yellowpages_directory"
                    lead["services_offered"] = f"Listed on YellowPages for {search_q}"
                    out.append(lead)
                if biz_names:
                    logger.info("YellowPages returned %d results for %r", len(biz_names), search_q)
        except Exception as e:
            logger.warning("YellowPages scrape failed: %s", e)

        return out

    def search_all(self, query: str, location: str) -> List[dict]:
        out = self.search_google(f"{query} {location}".strip())
        out += self.search_directory(query, location)
        return out


# ===========================================================================
# Email enrichment (fast, threaded, no LLM — regex + mailto: only)
# ===========================================================================
class EmailEnricher:
    """
    Visits each lead's website (homepage + a couple of likely contact paths) and
    extracts a real business email via mailto: links or plain-text regex.
    Runs concurrently across leads since this is pure I/O wait.
    """

    def __init__(self, timeout: int = 6, max_workers: int = 20):
        self.timeout = timeout
        self.max_workers = max_workers
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }

    def _extract_from_url(self, url: str) -> Optional[str]:
        try:
            r = requests.get(url, headers=self.headers, timeout=self.timeout, allow_redirects=True)
            if r.status_code != 200 or not r.text:
                return None
            text = r.text
        except Exception:
            return None

        for m in MAILTO_RE.findall(text):
            if _valid_scraped_email(m):
                return m
        for m in EMAIL_RE.findall(text):
            if _valid_scraped_email(m):
                return m
        return None

    def find_email(self, website: str) -> str:
        if not website:
            return ""
        site = website.strip()
        if not site.startswith("http"):
            site = "https://" + site
        parsed = urlparse(site)
        if not parsed.netloc:
            return ""
        base = f"{parsed.scheme}://{parsed.netloc}"
        for path in CONTACT_PATHS:
            email = self._extract_from_url(base + path)
            if email:
                return email
        return ""

    def enrich(self, leads: List[dict]) -> List[dict]:
        targets = [l for l in leads if l.get("website") and not l.get("business_email")]
        if not targets:
            return leads
        logger.info("Enriching emails for %d leads that have a website...", len(targets))

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            future_map = {ex.submit(self.find_email, l["website"]): l for l in targets}
            done = 0
            for fut in concurrent.futures.as_completed(future_map):
                lead = future_map[fut]
                try:
                    email = fut.result()
                except Exception:
                    email = ""
                if email:
                    lead["business_email"] = email
                done += 1
                if done % 25 == 0 or done == len(targets):
                    logger.info("  ...checked %d/%d websites", done, len(targets))

        found = sum(1 for l in leads if l.get("business_email"))
        logger.info("Email enrichment done: %d/%d total leads now have an email.", found, len(leads))
        return leads


# ===========================================================================
# Website enrichment – owner names, phone numbers, and emails from business sites
# ===========================================================================
class WebsiteEnricher:
    """
    Visits each lead's website and extracts:
      - business email    (via existing EmailEnricher logic)
      - phone number      (regex from footer/header/body)
      - owner name        (regex from About/Team pages)

    All pure requests + regex — no LLM cost.
    """

    def __init__(self, timeout: int = 6, max_workers: int = 20):
        self.timeout = timeout
        self.max_workers = max_workers
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }

    def _fetch(self, url: str) -> Optional[str]:
        try:
            r = requests.get(url, headers=self.headers, timeout=self.timeout, allow_redirects=True)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception:
            pass
        return None

    def _extract_email(self, text: str) -> Optional[str]:
        for m in MAILTO_RE.findall(text):
            if _valid_scraped_email(m):
                return m
        for m in EMAIL_RE.findall(text):
            if _valid_scraped_email(m):
                return m
        return None

    def _extract_phone(self, text: str) -> Optional[str]:
        phones = PHONE_RE.findall(text)
        for p in phones:
            p = p.strip()
            digits = re.sub(r"\D", "", p)
            if 7 <= len(digits) <= 15:
                return p
        return None

    def _extract_owner(self, text: str) -> Optional[str]:
        STOP_WORDS = {"in", "for", "of", "on", "at", "by", "to", "with", "under", "since", "and", "the", "a", "an"}
        for pattern in OWNER_PATTERNS:
            m = pattern.search(text)
            if m:
                raw = m.group(1).strip()
                # Truncate at stop words to avoid greedy overmatching
                parts = raw.split()
                cleaned_parts = []
                for p in parts:
                    if p.lower() in STOP_WORDS:
                        break
                    cleaned_parts.append(p)
                name = " ".join(cleaned_parts).strip()
                if not name:
                    continue
                parts = name.split()
                # sanity: name should have 2-4 parts, each with uppercase start, at least 2 normal-length parts
                if 2 <= len(parts) <= 4 and all(p[0].isupper() for p in parts):
                    long_parts = [p for p in parts if len(p) > 1]
                    if len(long_parts) >= 2:
                        return name
        return None

    def _enrich_one(self, lead: dict) -> dict:
        website = lead.get("website", "") or lead.get("linkedin", "")
        if not website:
            return lead
        if not website.startswith("http"):
            website = "https://" + website
        parsed = urlparse(website)
        if not parsed.netloc:
            return lead
        base = f"{parsed.scheme}://{parsed.netloc}"

        for path in CONTACT_PATHS:
            text = self._fetch(base + path)
            if not text:
                continue
            if not lead.get("business_email"):
                email = self._extract_email(text)
                if email:
                    lead["business_email"] = email
            if not lead.get("phone"):
                phone = self._extract_phone(text)
                if phone:
                    lead["phone"] = phone
            if not lead.get("owner_name"):
                owner = self._extract_owner(text)
                if owner:
                    lead["owner_name"] = owner
            if lead.get("business_email") and lead.get("phone") and lead.get("owner_name"):
                break
        return lead

    def enrich(self, leads: List[dict]) -> List[dict]:
        targets = [l for l in leads if l.get("website") or l.get("linkedin")]
        if not targets:
            logger.info("Website enrichment: no leads with websites to check.")
            return leads
        logger.info("Website enriching %d leads (owner + phone + email)...", len(targets))

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            future_map = {ex.submit(self._enrich_one, l): l for l in targets}
            done = 0
            for fut in concurrent.futures.as_completed(future_map):
                done += 1
                if done % 25 == 0 or done == len(targets):
                    logger.info("  ...enriched %d/%d websites", done, len(targets))

        owners = sum(1 for l in leads if l.get("owner_name"))
        phones = sum(1 for l in leads if l.get("phone"))
        emails = sum(1 for l in leads if l.get("business_email"))
        logger.info("Website enrichment done: %d owners, %d phones, %d emails across %d leads.",
                     owners, phones, emails, len(leads))
        return leads


# ===========================================================================
# Agent
# ===========================================================================
class LeadScoutAgent(BaseAgent):
    name = "LeadScoutAgent"
    display_name = "Lead Scout"
    description = "Autonomous lead generation with multi-source search, dedup, and email enrichment."
    requires_keys = []  # LLM is optional; search sources are optional

    def __init__(self, brightdata=None, serper=None, llm=None):
        self.bd = brightdata
        self.serper = serper
        self.llm = llm or LLM()
        self.email_enricher = EmailEnricher()
        self.website_enricher = WebsiteEnricher()
        self.duckduckgo = DuckDuckGoSource()
        self.direct_web = DirectWebSource()

    def parse_niche(self, user_input: str) -> Dict[str, str]:
        fallback = {"query": user_input.strip(), "location": "", "context": ""}
        if not self.llm.available:
            return fallback
        result = self.llm.ask_json(
            f'Extract structured fields from this lead-search request: "{user_input}".\n'
            f'Keys:\n'
            f'  - query: the core business type/niche (e.g. "hvac companies", "dental clinics")\n'
            f'  - location: the city/state/country or empty\n'
            f'  - context: any extra filters or criteria (e.g. "hiring administrative roles",\n'
            f'    "offering financing", "family-owned") — or empty if none\n'
            f'Return ONLY a JSON object with those 3 keys.'
        )
        if isinstance(result, dict) and result.get("query"):
            return {
                "query": str(result.get("query", "")).strip(),
                "location": str(result.get("location", "")).strip(),
                "context": str(result.get("context", "")).strip(),
            }
        return fallback

    def _load_niches_from_rules(self) -> List[str]:
        try:
            from app.database import get_active_pdf_rules
            rules = get_active_pdf_rules()
            if not rules or not rules.get("content"):
                return []
            text = rules["content"].strip()
            if not self.llm.available:
                return []
            result = self.llm.ask_json(
                f'Below is a set of lead-generation rules. Extract 1-3 concise search queries '
                f'that would find matching businesses. Return ONLY a JSON array of strings.\n\n'
                f'Rules:\n{text[:3000]}'
            )
            if isinstance(result, list):
                queries = [str(q).strip() for q in result if isinstance(q, str) and q.strip()]
                if queries:
                    logger.info("Extracted %d search queries from rules: %s", len(queries), queries)
                    return queries
            logger.warning("LLM could not extract queries from rules text, falling back to first line")
            lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 10]
            return lines[:1] if lines else []
        except Exception as e:
            logger.warning("Failed to load niches from rules: %s", e)
            return []

    @staticmethod
    def _get_rules_context() -> str:
        try:
            from rules_engine import get_rules_context
            ctx = get_rules_context()
            if ctx:
                return ctx
        except Exception:
            pass
        try:
            from app.database import get_active_pdf_rules
            rules = get_active_pdf_rules()
            if rules and rules.get("content"):
                return rules["content"][:2000]
        except Exception:
            pass
        return ""

    def generate_query_variants(self, query: str, location: str, context: str = "", want: int = 15) -> List[str]:
        """
        Google only returns a limited set of results per exact query string, so to
        reach a high lead target we need many differently-phrased queries: real
        neighborhoods/districts within the city, nearby towns, and synonyms for the
        business type. One LLM call generates these; falls back to the plain query
        if no LLM is configured.
        The `context` parameter (e.g. "hiring administrative roles") is woven into
        variant suggestions so the search targets businesses that match the filter.
        """
        base = f"{query} {location}".strip()
        if context:
            base = f"{base} {context}"
        if not self.llm.available:
            return [base]

        context_instruction = ""
        if context:
            context_instruction = (
                f'IMPORTANT: Each query must weave in this context/filter: "{context}". '
                f'For example, if the context is "hiring administrative roles", generate queries like '
                f'"hvac companies hiring administrative staff virginia", "hvac careers administrative virginia", etc.\n'
            )

        prompt = (
            f'I need to find as many distinct "{query}" businesses as possible in '
            f'"{location or "the target area"}" using Google Search/Maps. Google only returns a '
            f'limited set of results per exact query, so I need multiple differently-phrased '
            f'queries to surface different businesses.\n'
            f'{context_instruction}'
            f'Generate {want} short Google-search-ready query strings. Each should combine the '
            f'business type (use real synonyms/related terms where sensible, e.g. "dentist" vs '
            f'"dental clinic" vs "orthodontist") with a specific real neighborhood, district, or '
            f'nearby town within "{location}" (if location is a city, use its actual '
            f'neighborhoods/areas; if location is empty, just vary the business-type phrasing).\n'
            f'Return ONLY a JSON array of strings, e.g. '
            f'["dentist DHA Lahore", "dental clinic Gulberg Lahore", "orthodontist Johar Town Lahore"].'
        )
        result = self.llm.ask_json(prompt, timeout=60)
        variants = []
        if isinstance(result, list):
            variants = [str(v).strip() for v in result if str(v).strip()]
        if not variants:
            return [base]
        if base not in variants:
            variants.insert(0, base)
        return variants[:want]

    def collect(self, query: str, location: str, context: str = "", target: int = TARGET_LEADS) -> List[dict]:
        leads: List[dict] = []

        # --- TIER 1: Autonomous free sources (DuckDuckGo + direct web scrape) ---
        logger.info("=== TIER 1: Autonomous search ===")
        want_variants = max(5, min(20, target // 10))
        variants = self.generate_query_variants(query, location, context=context, want=want_variants)
        logger.info("Generated %d search-query variants.", len(variants))

        # DuckDuckGo (free, no API key)
        if self.duckduckgo.enabled:
            logger.info("Collecting from DuckDuckGo (free)...")
            stale = 0
            for variant in variants:
                if len(leads) >= target:
                    break
                before = len(leads)
                try:
                    new_leads = self.duckduckgo.search(variant, max_results=20)
                except Exception as e:
                    logger.warning("DuckDuckGo error on %r: %s", variant, e)
                    new_leads = []
                leads = _deduplicate(leads + new_leads)
                gained = len(leads) - before
                logger.info("  DDG %r -> +%d (total %d)", variant, gained, len(leads))
                if gained == 0:
                    stale += 1
                    if stale >= 4:
                        break
                else:
                    stale = 0
                time.sleep(0.5)

        # Direct web scrape (Google + directories)
        if len(leads) < target and self.direct_web.enabled:
            logger.info("Collecting from direct web scrape (free)...")
            for variant in variants[:8]:
                if len(leads) >= target:
                    break
                before = len(leads)
                try:
                    new_leads = self.direct_web.search_all(query, location if variant == query else "")
                except Exception as e:
                    logger.warning("Direct web error on %r: %s", variant, e)
                    new_leads = []
                leads = _deduplicate(leads + new_leads)
                gained = len(leads) - before
                logger.info("  Web %r -> +%d (total %d)", variant, gained, len(leads))
                time.sleep(1)

        leads = _deduplicate(leads)
        logger.info("After autonomous sources: %d unique leads.", len(leads))

        # --- TIER 2: Bright Data (paid, when available) ---
        if len(leads) < target and self.bd and self.bd.enabled:
            logger.info("=== TIER 2: Bright Data (paid) ===")
            try:
                new_leads = self.bd.find(query, location, target - len(leads))
                leads = _deduplicate(leads + new_leads)
                logger.info("After Bright Data: %d unique leads.", len(leads))
            except Exception as e:
                logger.warning("Bright Data source error: %s", e)

        # --- TIER 3: Serper (paid fallback, only when absolutely necessary) ---
        if len(leads) < target and self.serper and self.serper.enabled:
            logger.info("=== TIER 3: Serper (paid fallback) ===")
            stale_rounds = 0
            for variant in variants:
                if len(leads) >= target:
                    break
                before = len(leads)
                try:
                    new_leads = self.serper.places(variant, location="", pages=2)
                    new_leads += self.serper.search(variant, num=20)
                except Exception as e:
                    logger.warning("Serper error on %r: %s", variant, e)
                    new_leads = []
                leads = _deduplicate(leads + new_leads)
                gained = len(leads) - before
                logger.info("  Serper %r -> +%d (total %d)", variant, gained, len(leads))
                if gained == 0:
                    stale_rounds += 1
                    if stale_rounds >= 4:
                        logger.info("4 Serper variants in a row added nothing new; stopping.")
                        break
                else:
                    stale_rounds = 0

            # LinkedIn supplement if still short
            if len(leads) < target:
                try:
                    li_leads = self.serper.search(
                        f"{query} {location} site:linkedin.com/company".strip(), num=30
                    )
                    leads = _deduplicate(leads + li_leads)
                except Exception as e:
                    logger.warning("LinkedIn supplement failed: %s", e)

        for lead in leads:
            lead["niche"] = query
            lead["location"] = location
        leads = leads[:target]
        logger.info("Collected %d unique leads (target was %d).", len(leads), target)
        return leads

    def score_batch(self, leads: List[dict], original_query: str) -> List[dict]:
        """
        Batched LLM scoring, chunked so large lead counts (e.g. 400) don't blow a
        single prompt's context/token limits. Skipped silently if no LLM.
        """
        if not leads or not self.llm.available:
            return leads

        CHUNK = 60  # leads per scoring call, keeps prompts small and fast
        for start in range(0, len(leads), CHUNK):
            chunk = leads[start:start + CHUNK]
            slim = [{
                "i": idx,
                "business_name": l.get("business_name", ""),
                "category": l.get("category", ""),
                "website": l.get("website", ""),
                "snippet": (l.get("services_offered", "") or "")[:160],
            } for idx, l in enumerate(chunk)]

            rules_ctx = self._get_rules_context()
            rules_block = f'\nRules/Criteria to follow when scoring:\n{rules_ctx}\n' if rules_ctx else ''
            prompt = (
                f'A user wants leads for: "{original_query}".\n'
                f"{rules_block}"
                f"For EACH lead below, judge fit and return a JSON array. Each element: "
                f'{{"i": <index>, "score": <1-100>, "analysis": "<one short sentence>", '
                f'"outreach_suggestion": "<one short opening line>"}}.\n'
                f"Leads:\n{json.dumps(slim, ensure_ascii=False)}"
            )
            result = self.llm.ask_json(prompt, timeout=120)
            if isinstance(result, list):
                by_idx = {int(r.get("i", -1)): r for r in result if isinstance(r, dict)}
                for idx, lead in enumerate(chunk):
                    r = by_idx.get(idx)
                    if r:
                        lead["score"] = r.get("score", "")
                        lead["analysis"] = r.get("analysis", "")
                        lead["outreach_suggestion"] = r.get("outreach_suggestion", "")
            else:
                logger.warning("Batched scoring returned no usable JSON for chunk starting at %d.", start)

        def _s(l):
            try:
                return -float(l.get("score") or 0)
            except Exception:
                return 0
        leads.sort(key=_s)
        return leads

    def _llm_snippet_enrich(self, leads: List[dict], user_input: str) -> List[dict]:
        """
        Use the LLM to analyse search-result snippets and extract owner names,
        phone numbers, and other contact details that regex-based scrapers miss.
        Only runs on leads that are still missing owner_name and have a snippet.
        """
        if not self.llm.available:
            return leads
        candidates = [l for l in leads if not l.get("owner_name") and l.get("services_offered")]
        if not candidates:
            return leads

        chunk_size = 40
        for start in range(0, len(candidates), chunk_size):
            chunk = candidates[start:start + chunk_size]
            slim = [{
                "i": idx,
                "business_name": l.get("business_name", ""),
                "snippet": (l.get("services_offered", "") or "")[:300],
            } for idx, l in enumerate(chunk)]

            prompt = (
                f'A user searched for leads with this request: "{user_input}".\n'
                f"Below are search-result snippets for businesses. For EACH, extract:\n"
                f'  - owner_name: the business owner / founder / CEO name, if visible in the snippet\n'
                f'  - phone: any phone number visible in the snippet\n'
                f'Return a JSON array of objects, one per lead in order:\n'
                f'[{{"i": 0, "owner_name": "...", "phone": "..."}}, ...]\n'
                f'Use empty string for fields not found. Be thorough — owner names often appear as '
                f'"…owned by John Smith…" or "…under the leadership of Jane Doe…".\n'
                f'Leads:\n{json.dumps(slim, ensure_ascii=False)}'
            )
            result = self.llm.ask_json(prompt, timeout=90)
            if isinstance(result, list):
                by_idx = {int(r.get("i", -1)): r for r in result if isinstance(r, dict)}
                for idx, lead in enumerate(chunk):
                    r = by_idx.get(idx)
                    if r:
                        if r.get("owner_name") and not lead.get("owner_name"):
                            lead["owner_name"] = r["owner_name"]
                        if r.get("phone") and not lead.get("phone"):
                            lead["phone"] = r["phone"]
            else:
                logger.warning("LLM snippet enrich returned no usable JSON for chunk starting at %d.", start)
        return leads

    def push_to_linkedin_queue(self, leads: List[dict], message_template: str = "") -> int:
        """Push leads with LinkedIn URLs into the linkedin_queue for third-party tool processing."""
        queued = 0
        try:
            from app.database import add_to_linkedin_queue
            for lead in leads:
                li_url = lead.get("linkedin_url") or lead.get("linkedin", "")
                if li_url:
                    lead["source"] = lead.get("source", "scout")
                    if add_to_linkedin_queue(lead, message_template):
                        queued += 1
            if queued:
                logger.info("Pushed %d leads with LinkedIn URLs to queue.", queued)
        except Exception as e:
            logger.warning("Failed to push to LinkedIn queue: %s", e)
        return queued

    def run(self, **kwargs) -> AgentResult:
        start = time.time()
        user_input = kwargs.get("user_input", "")
        target = kwargs.get("target", TARGET_LEADS)

        rules_niches = []
        if not user_input:
            rules_niches = self._load_niches_from_rules()
            if rules_niches:
                logger.info("No niche given — loaded %d niches from PDF rules", len(rules_niches))
        if not user_input and not rules_niches:
            return make_result(False, "No niche provided.",
                              data=[], stats={}, duration=time.time() - start)

        niches_to_search = rules_niches if (not user_input and rules_niches) else [user_input]

        all_leads = []
        for i, single_niche in enumerate(niches_to_search):
            logger.info("Searching niche %d/%d: %s", i + 1, len(niches_to_search), single_niche)
            parsed = self.parse_niche(single_niche)
            query = parsed["query"]
            location = parsed["location"]
            context = parsed.get("context", "")
            per_niche_target = max(5, target // len(niches_to_search)) if len(niches_to_search) > 1 else target
            logger.info("  Query: %r | Location: %r | Context: %r | Target: %d",
                        query, location, context, per_niche_target)

            batch = self.collect(query, location, context=context, target=per_niche_target)
            if batch:
                all_leads.extend(batch)
                logger.info("  -> +%d leads (total %d)", len(batch), len(all_leads))
            else:
                logger.info("  -> 0 leads for this niche")
            if len(all_leads) >= target:
                all_leads = all_leads[:target]
                break

        all_leads = _deduplicate(all_leads)
        if not all_leads:
            return make_result(False, "No leads found. Check that at least one search source has a valid key.",
                              data=[], stats={}, duration=time.time() - start)

        logger.info("Enriching %d leads...", len(all_leads))
        all_leads = self.email_enricher.enrich(all_leads)
        all_leads = self.website_enricher.enrich(all_leads)
        all_leads = self._llm_snippet_enrich(all_leads, "; ".join(niches_to_search))
        all_leads = self.score_batch(all_leads, "; ".join(niches_to_search))
        leads = all_leads

        # Auto-push LinkedIn URLs to queue if requested
        if kwargs.get("push_linkedin", False):
            template = kwargs.get("linkedin_message_template", "")
            self.push_to_linkedin_queue(leads, message_template=template)

        duration = time.time() - start
        stats = {
            "total_leads": len(leads),
            "with_email": sum(1 for l in leads if l.get("business_email")),
            "with_phone": sum(1 for l in leads if l.get("phone")),
            "with_owner": sum(1 for l in leads if l.get("owner_name")),
            "linkedin_queued": sum(1 for l in leads if l.get("linkedin_url") or l.get("linkedin")),
        }
        return make_result(True, f"Collected {len(leads)} leads.",
                          data=leads, stats=stats, duration=duration)

    def check_health(self) -> AgentHealth:
        keys = self._check_keys()
        groq_ok = keys.get("GROQ_API_KEY", False)
        gemini_ok = keys.get("GEMINI_API_KEY", False)
        bd_ok = keys.get("BRIGHTDATA_API_KEY", False)
        serper_ok = keys.get("SERPER_API_KEY", False)

        sources = []
        sources.append("DuckDuckGo+Web (free)")
        if bd_ok:
            sources.append("BrightData")
        if serper_ok:
            sources.append("Serper")

        llm_status = "Groq+Gemini" if (groq_ok and gemini_ok) else ("Groq" if groq_ok else ("Gemini" if gemini_ok else "None"))

        msg = f"LLM: {llm_status} | Sources: {', '.join(sources)}"
        healthy = bool(groq_ok or gemini_ok)
        return make_health(healthy, "ready" if healthy else "degraded", msg, keys)


# ===========================================================================
# Entry point
# ===========================================================================
def _prompt_brightdata_key() -> str:
    env_key = os.getenv("BRIGHTDATA_API_KEY", "").strip()
    if env_key:
        logger.info("Using BRIGHTDATA_API_KEY from .env.")
        return env_key
    try:
        entered = input("Bright Data API key (Enter to skip and use Serper only): ").strip()
    except EOFError:
        entered = ""
    return entered


def _prompt_target(default: int = TARGET_LEADS) -> int:
    try:
        raw = input(f"How many leads do you want (Enter for default {default}): ").strip()
    except EOFError:
        raw = ""
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def main():
    print("=" * 60)
    print(" Lead Scout Agent  —  Autonomous (DDG+Web) + Bright Data + Serper")
    print("=" * 60)
    print(" Autonomous mode: DuckDuckGo + direct web scrape (FREE, always on)")
    print(" Paid sources:    Bright Data + Serper (optional, for higher volume)")
    print("=" * 60)

    bd_key = _prompt_brightdata_key()
    brightdata = BrightDataSource(
        api_key=bd_key,
        dataset_id=os.getenv("BRIGHTDATA_DATASET_ID", ""),
        serp_zone=os.getenv("BRIGHTDATA_SERP_ZONE", ""),
    ) if bd_key else None

    serper = SerperSource(SERPER_API_KEY) if SERPER_API_KEY else None

    llm = LLM()

    if not llm.available:
        logger.warning("No LLM configured. Lead scoring and query generation will be skipped.")
    if not (brightdata and brightdata.enabled) and not (serper and serper.enabled):
        logger.info("No paid sources configured — will use autonomous (free) search only.")

    agent = LeadScoutAgent(brightdata, serper, llm)

    try:
        niche = input("\nEnter your niche (e.g. 'dental clinics in Lahore'): ").strip()
    except EOFError:
        niche = ""
    if not niche:
        raise SystemExit("No niche entered. Exiting.")

    target = _prompt_target()

    result = agent.run(user_input=niche, target=target)
    leads = result.data if result.data else []
    _save_outputs(leads)

    print(f"\nDone. {len(leads)} leads written to leads.json / leads.csv")
    print(result.message)
    if leads:
        print("Sources used:", set(l.get("source", "?") for l in leads))
        print(f"Enrichment: {sum(1 for l in leads if l.get('owner_name'))} have owner names, "
              f"{sum(1 for l in leads if l.get('phone'))} have phones, "
              f"{sum(1 for l in leads if l.get('business_email'))} have emails.")
        for l in leads[:10]:
            owner = f" owner={l.get('owner_name','')}" if l.get('owner_name') else ""
            print(f"  - {l.get('business_name','?')} | {l.get('phone','')} | "
                  f"{l.get('business_email','')} | {l.get('website','')}{owner} | score={l.get('score','')}")


if __name__ == "__main__":
    main()