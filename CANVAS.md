# DEUS v3 Canvas

**Version:** 3.0.0
**Domain:** Sales outreach automation platform
**Deployment:** Railway (production)
**Sender:** sales@growthdeskva.com

---

## Architecture

```
brain.py (orchestrator)
├── lead_scout_agent.py     — discovers leads via search APIs
├── outreach_agent.py       — sends AI-generated cold emails
├── followup_agent.py       — re-engages non-responsive leads
├── appointment_agent.py    — Calendly booking integration
├── deal_closer_agent.py    — closing messages post-appointment
├── report_agent.py         — daily pipeline summaries
├── reply_detector.py       — monitors inbox for replies (Gmail API)
└── system_checker_agent.py — health checks

daemon.py — background scheduler (4min interval)
nexus_bridge.py — CLI orchestrator for manual commands
```

## Pipeline Flow

```
Lead Scout → Outreach → Follow-up → Appointment → Deal Closer → Report
                               ↕
                         Reply Detector (always active)
```

## Email Delivery

| Method | Status | Notes |
|--------|--------|-------|
| Gmail API (OAuth2) | ✅ Working | Primary method, HTTP-based, works on Railway |
| SMTP | ❌ Blocked | Railway blocks outbound 465/587 |
| Resend | ❌ No key | Not configured |

### Gmail OAuth
- **Scope:** `gmail.modify` (send + read)
- **Callback:** `/api/gmail/callback` (aliased as `/api/google/callback`)
- **Token storage:** SQLite database (`deus.db`, `gmail_tokens` table)
- **Status:** ✅ Authenticated (token saved 2026-07-15 21:19:29)

## LLM Providers

| Provider | Model | Status | Purpose |
|----------|-------|--------|---------|
| Gemini | `gemini-2.5-flash` | ❌ Invalid key | Primary email generation |
| Groq | `llama-3.3-70b-versatile` | ❌ Invalid key / Removed | Fallback generation |
| — | Template fallback | ✅ Working | Used when both LLMs fail |

The fallback chain in `outreach_agent.py`:
```
Gemini → Groq → hardcoded template
```

## Mode Config

| Mode | Daily Cap | Delay Between Sends | Use |
|------|-----------|-------------------|-----|
| TESTING | 15 | 180-300s | Development/debug |
| PRODUCTION | 100 | 90-180s | Live outreach |

Toggle via `/api/mode` endpoint or dashboard button.
Persisted in `mode_state.json`.

## Rate Limits

- Daily: 50 (default), configurable via mode
- Hourly: 10 (default), configurable via mode
- Per-send delay: 15-45s (default), configurable via mode
- Domain reputation: starts at "warming"

## Key Files

| File | Purpose |
|------|---------|
| `gmail_sender.py` | Gmail API send via OAuth2 (singleton, re-inits on failure) |
| `email_sender.py` | Unified sender: Gmail API → SMTP → Resend |
| `reply_detector.py` | Gmail API inbox scan for replies (rewritten from IMAP) |
| `outreach_agent.py` | AI email generation + send orchestration |
| `outreach_config.py` | Email style/subject configuration |
| `mode_config.py` | Testing/production mode toggle |
| `rules_engine.py` | PDF-based business rules loader |
| `send_limiter.py` | Daily/hourly rate limiter |
| `spam_checker.py` | Spam word weight scoring |
| `daemon.py` | Background scheduler (4min loop) |
| `app/database.py` | SQLite DB operations (leads, tokens, settings) |
| `app/api/routes.py` | FastAPI routes (OAuth, mode, lead management) |
| `app/dashboard.html` | Web dashboard with mode toggle, lead table |

## Environment Variables (Railway)

| Variable | Status | Notes |
|----------|--------|-------|
| `GEMINI_API_KEY` | ❌ Needs update | Regenerate at aistudio.google.com |
| `GROQ_API_KEY` | ❌ Removed | No longer used |
| `GOOGLE_CLIENT_ID` | ✅ Set | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | ✅ Set | From Google Cloud Console |
| `GMAIL_SENDER_EMAIL` | ✅ Set | `sales@growthdeskva.com` |
| `GOOGLE_REDIRECT_URI` | ✅ Set | Railway callback URL |
| `RESEND_API_KEY` | ❌ Not set | Optional fallback |
| `GOOGLE_OAUTH_PORT` | ✅ Set | 53127 |

## Recent Fixes (2026-07-15)

1. **GmailSender singleton re-init** — now creates fresh instance if `available=False`, picks up new OAuth tokens from DB
2. **Scope migration** — `gmail.send` → `gmail.modify` in all 3 locations
3. **Spam subject** — "Quick proposal" → "Quick opportunity"
4. **Reply detector** — IMAP → Gmail API rewrite
5. **Batch delays** — added between sends to avoid rate limits
6. **Route aliases** — `/api/google/*` mirrors `/api/gmail/*`
7. **Mode toggle** — TESTING/PRODUCTION with dynamic rate limits

## Next Actions

1. Regenerate `GEMINI_API_KEY` at https://aistudio.google.com/app/apikey
2. Remove `GROQ_API_KEY` from Railway env vars
3. Redeploy Railway
4. Verify next outreach batch sends via Gmail API
