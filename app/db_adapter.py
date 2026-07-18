"""
app/db_adapter.py — PostgreSQL Adapter for DEUS 3.0
===================================================
Provides a drop-in replacement for the SQLite connection/cursor API
so that all 50+ functions in database.py work unchanged against PostgreSQL.

Activated by setting DATABASE_URL env var (e.g. postgresql://user:pass@host/db).
When unset, the original SQLite code path is used.
"""

import os
import re
import time
import json
import logging
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# ---------------------------------------------------------------------------
# SQL Rewriting — convert SQLite dialect to PostgreSQL
# ---------------------------------------------------------------------------

_SQLITE_TO_PG = [
    # Order matters: run these BEFORE ? → %s
    (re.compile(r"strftime\('%s','now'\)", re.IGNORECASE), "EXTRACT(EPOCH FROM NOW())"),
    (re.compile(r"datetime\('now'\)", re.IGNORECASE), "NOW()"),
    (re.compile(r"date\('now'\)", re.IGNORECASE), "CURRENT_DATE"),
    (re.compile(r"IFNULL\(", re.IGNORECASE), "COALESCE("),
    (re.compile(r"GROUP_CONCAT\(", re.IGNORECASE), "STRING_AGG("),
]

# Table-specific conflict columns for INSERT OR REPLACE / INSERT OR IGNORE
_CONFLICT_COLUMNS = {
    "custom_pipelines": ["name"],
    "analytics": ["metric", "date"],
    "daemon_config": ["agent_name"],
    "analytics_daily": ["date", "metric", "dimension"],
    "gmail_tokens": ["id"],
}

def _extract_table_name(sql: str) -> Optional[str]:
    m = re.search(
        r"(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+(\w+)",
        sql, re.IGNORECASE,
    )
    return m.group(1) if m else None


def _rewrite_insert_on_conflict(sql: str) -> str:
    """Rewrite INSERT OR REPLACE/IGNORE to PostgreSQL ON CONFLICT syntax."""
    upper = sql.strip().upper()

    is_replace = upper.startswith("INSERT OR REPLACE")
    is_ignore = upper.startswith("INSERT OR IGNORE")
    if not is_replace and not is_ignore:
        return sql

    table = _extract_table_name(sql)
    if not table:
        return sql

    conflict_cols = _CONFLICT_COLUMNS.get(table.lower())
    if not conflict_cols:
        logger.warning("No conflict columns registered for table '%s' — skipping rewrite", table)
        return sql

    # Strip INSERT OR REPLACE → INSERT
    if is_replace:
        sql = re.sub(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", sql, count=1, flags=re.IGNORECASE)
        cols = ", ".join(conflict_cols)
        # Find the VALUES clause — everything from VALUES to end
        m = re.search(r"VALUES\s*\(.*\)\s*$", sql, re.IGNORECASE | re.DOTALL)
        if m:
            val_part = m.group(0)
            # Build SET clause from the column names in the INSERT
            col_match = re.search(r"INSERT\s+INTO\s+\w+\s*\(([^)]+)\)", sql, re.IGNORECASE)
            if col_match:
                insert_cols = [c.strip() for c in col_match.group(1).split(",")]
                set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in insert_cols)
                sql = sql[:m.start()] + val_part + f" ON CONFLICT ({cols}) DO UPDATE SET {set_clause}"
    elif is_ignore:
        sql = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, count=1, flags=re.IGNORECASE)
        cols = ", ".join(conflict_cols)
        sql = sql.rstrip(";") + f" ON CONFLICT ({cols}) DO NOTHING"

    return sql


def rewrite_sql(sql: str) -> str:
    """Convert a single SQLite statement to PostgreSQL-compatible SQL."""
    if not sql or not sql.strip():
        return sql

    # 0. Handle PRAGMA — silently drop (unsupported in PG)
    stripped = sql.strip().upper()
    if stripped.startswith("PRAGMA"):
        return "-- PRAGMA dropped: " + sql

    # 1. SQLite function replacements (before ? → %s)
    for pattern, replacement in _SQLITE_TO_PG:
        sql = pattern.sub(replacement, sql)

    # 2. INSERT OR REPLACE / OR IGNORE
    sql = _rewrite_insert_on_conflict(sql)

    # 3. Cast REAL → DOUBLE PRECISION in CREATE TABLE
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql, flags=re.IGNORECASE)

    # 4. AUTOINCREMENT → remove (SERIAL already handles this; just drop keyword)
    sql = re.sub(r"\s+AUTOINCREMENT\b", "", sql, flags=re.IGNORECASE)

    # 5. INTEGER PRIMARY KEY → use SERIAL when combined with AUTOINCREMENT (already dropped)
    #    But standalone INTEGER PRIMARY KEY needs no change.

    # 6. Replace ? placeholders with %s (psycopg2 style)
    #    Only replace ? that aren't inside string literals (simple heuristic)
    sql = _replace_qmark_with_pct(sql)

    # 7. Handle RETURNING for INSERT that don't already have it
    #    We add RETURNING so lastrowid works. Only for single-row INSERTs.
    sql = _ensure_returning_id(sql)

    return sql


def _replace_qmark_with_pct(sql: str) -> str:
    """Replace ? parameter placeholders with %s, avoiding string literals."""
    result = []
    in_string = False
    string_char = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_string:
            result.append(ch)
            if ch == "\\" and i + 1 < len(sql):
                i += 1
                result.append(sql[i])
            elif ch == string_char:
                in_string = False
        else:
            if ch in ("'", '"') and (i == 0 or sql[i - 1] != "\\"):
                in_string = True
                string_char = ch
                result.append(ch)
            elif ch == "?":
                result.append("%s")
            else:
                result.append(ch)
        i += 1
    return "".join(result)


def _ensure_returning_id(sql: str) -> str:
    """Append RETURNING id to INSERT statements that don't already have it."""
    upper = sql.strip().upper()
    if not upper.startswith("INSERT"):
        return sql
    if "RETURNING" in upper:
        return sql
    # Don't add RETURNING for INSERT … SELECT (bulk from query)
    if "SELECT" in upper and "VALUES" not in upper:
        return sql
    return sql.rstrip(";") + " RETURNING id"


def rewrite_schema_sql(sql: str) -> str:
    """Convert SQLite CREATE TABLE / seed SQL to PostgreSQL."""
    # Remove PRAGMA lines entirely
    lines = []
    for line in sql.split("\n"):
        stripped = line.strip()
        if stripped.upper().startswith("PRAGMA"):
            continue
        # Replace REAL
        line = re.sub(r"\bREAL\b", "DOUBLE PRECISION", line, flags=re.IGNORECASE)
        # Replace INTEGER PRIMARY KEY AUTOINCREMENT with SERIAL PRIMARY KEY
        line = re.sub(
            r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
            "SERIAL PRIMARY KEY",
            line, flags=re.IGNORECASE,
        )
        lines.append(line)
    sql = "\n".join(lines)
    # Run remaining rewrites but DON'T add RETURNING (not needed for DDL)
    for pattern, replacement in _SQLITE_TO_PG:
        sql = pattern.sub(replacement, sql)
    sql = _replace_qmark_with_pct(sql)
    return sql


# ---------------------------------------------------------------------------
# PgCursor — wraps psycopg2 cursor to quack like sqlite3.Cursor
# ---------------------------------------------------------------------------

class PgCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def execute(self, sql, params=None):
        rewritten = rewrite_sql(sql)
        try:
            if params is not None:
                self._cursor.execute(rewritten, params)
            else:
                self._cursor.execute(rewritten)
        except Exception as e:
            logger.error("PG execute failed: %s\nSQL: %s\nParams: %s", e, rewritten, params)
            raise

        # Store lastrowid from RETURNING if available
        self.lastrowid = None
        upper = rewritten.strip().upper()
        if upper.startswith("INSERT") and "RETURNING" in upper:
            try:
                row = self._cursor.fetchone()
                if row:
                    self.lastrowid = row[0]
            except Exception:
                pass  # no result (e.g. INSERT … ON CONFLICT DO NOTHING with no insert)

        return self

    def executemany(self, sql, seq_of_params):
        rewritten = rewrite_sql(sql)
        self._cursor.executemany(rewritten, seq_of_params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return dict(row) if hasattr(row, "keys") else {k: row[k] for k in row._fields}

    def fetchall(self):
        rows = self._cursor.fetchall()
        result = []
        for r in rows:
            result.append(dict(r) if hasattr(r, "keys") else {k: r[k] for k in r._fields})
        return result

    @property
    def rowcount(self):
        return self._cursor.rowcount


# ---------------------------------------------------------------------------
# PgConnection — wraps psycopg2 connection to quack like sqlite3.Connection
# ---------------------------------------------------------------------------

class PgConnection:
    def __init__(self, dsn: str):
        import psycopg2
        import psycopg2.extras
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = False  # we manage transactions
        # Use RealDictCursor so fetchone/fetchall return dict-like objects
        self._conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        self.row_factory = None  # compatibility stub

    def execute(self, sql, params=None):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return PgCursor(cur).execute(sql, params)

    def executescript(self, sql):
        """Handle multi-statement scripts (used in init_db)."""
        # Remove SQLite comments (-- style)
        cleaned = re.sub(r"--[^\n]*", "", sql)
        statements = self._split_sql(cleaned)
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            rewritten = rewrite_schema_sql(stmt)
            if rewritten.startswith("-- PRAGMA"):
                continue
            try:
                cur = self._conn.cursor()
                cur.execute(rewritten)
                cur.close()
            except Exception as e:
                logger.warning("executescript statement failed (skipping): %s\nSQL: %s", e, rewritten)
                # Don't raise — init_db may try ALTER TABLE ADD COLUMN that already exists

    def commit(self):
        if not self._conn.autocommit:
            self._conn.commit()

    def rollback(self):
        if not self._conn.autocommit:
            self._conn.rollback()

    def close(self):
        self._conn.close()

    @staticmethod
    def _split_sql(sql: str) -> list[str]:
        """Split SQL into individual statements, respecting string literals."""
        statements = []
        current = []
        in_string = False
        string_char = None
        i = 0
        while i < len(sql):
            ch = sql[i]
            if in_string:
                current.append(ch)
                if ch == "\\" and i + 1 < len(sql):
                    i += 1
                    current.append(sql[i])
                elif ch == string_char:
                    in_string = False
            else:
                if ch in ("'", '"'):
                    in_string = True
                    string_char = ch
                    current.append(ch)
                elif ch == ";":
                    statements.append("".join(current))
                    current = []
                else:
                    current.append(ch)
            i += 1
        remaining = "".join(current).strip()
        if remaining:
            statements.append(remaining)
        return statements


# ---------------------------------------------------------------------------
# Context manager (drop-in for database.db_conn)
# ---------------------------------------------------------------------------

@contextmanager
def pg_conn():
    conn = PgConnection(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_pg_available() -> bool:
    return bool(DATABASE_URL)


# ---------------------------------------------------------------------------
# PostgreSQL-specific init_db
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    last_login DOUBLE PRECISION DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    expires_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_changes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS email_log (
    id SERIAL PRIMARY KEY,
    lead_email TEXT NOT NULL,
    lead_name TEXT,
    subject TEXT,
    status TEXT DEFAULT 'sent',
    channel TEXT DEFAULT 'email',
    agent TEXT DEFAULT 'OutreachAgent',
    message_id TEXT,
    opened_at DOUBLE PRECISION,
    replied_at DOUBLE PRECISION,
    bounced_at DOUBLE PRECISION,
    bounce_reason TEXT,
    complained_at DOUBLE PRECISION,
    sent_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    smtp_profile TEXT
);

CREATE TABLE IF NOT EXISTS email_events (
    id SERIAL PRIMARY KEY,
    email_log_id INTEGER REFERENCES email_log(id),
    event_type TEXT NOT NULL,
    event_data TEXT,
    timestamp DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);

CREATE TABLE IF NOT EXISTS pdf_rules (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    content TEXT,
    uploaded_by INTEGER REFERENCES users(id),
    uploaded_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS custom_pipelines (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    steps TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);

CREATE TABLE IF NOT EXISTS analytics (
    id SERIAL PRIMARY KEY,
    metric TEXT NOT NULL,
    value DOUBLE PRECISION DEFAULT 0,
    date TEXT,
    extra TEXT,
    UNIQUE(metric, date)
);

CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    business_name TEXT,
    owner_name TEXT,
    business_email TEXT,
    phone TEXT,
    website TEXT,
    address TEXT,
    niche TEXT,
    category TEXT,
    services_offered TEXT,
    linkedin_url TEXT,
    instagram_handle TEXT,
    facebook_url TEXT,
    source TEXT DEFAULT 'unknown',
    status TEXT DEFAULT 'new',
    outreach_ready INTEGER DEFAULT 0,
    needs_human INTEGER DEFAULT 0,
    needs_human_reason TEXT,
    channel_used TEXT,
    preferred_channel TEXT DEFAULT 'email',
    score INTEGER DEFAULT 0,
    notes TEXT,
    extra_json TEXT,
    first_contacted_at DOUBLE PRECISION,
    last_contacted_at DOUBLE PRECISION,
    contact_count INTEGER DEFAULT 0,
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    updated_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    lead_type TEXT DEFAULT 'cold',
    import_batch_id TEXT,
    import_filename TEXT,
    email_verified INTEGER DEFAULT 0,
    email_verified_at DOUBLE PRECISION,
    verification_method TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(business_email);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_outreach_ready ON leads(outreach_ready);
CREATE INDEX IF NOT EXISTS idx_leads_type ON leads(lead_type);
CREATE INDEX IF NOT EXISTS idx_leads_verified ON leads(email_verified);

CREATE TABLE IF NOT EXISTS campaigns (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    updated_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);

CREATE TABLE IF NOT EXISTS campaign_steps (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    step_order INTEGER NOT NULL,
    day_offset INTEGER NOT NULL DEFAULT 0,
    subject_template TEXT DEFAULT '',
    body_template TEXT DEFAULT '',
    channel TEXT DEFAULT 'email',
    is_ai_generated INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS campaign_enrollments (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    current_step INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    enrolled_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    last_sent_at DOUBLE PRECISION,
    completed_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS schedules (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    agent_name TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    config TEXT DEFAULT '{}',
    enabled INTEGER DEFAULT 1,
    last_run_at DOUBLE PRECISION DEFAULT 0,
    next_run_at DOUBLE PRECISION DEFAULT 0,
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id SERIAL PRIMARY KEY,
    schedule_id INTEGER NOT NULL REFERENCES schedules(id),
    agent_name TEXT NOT NULL,
    started_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    completed_at DOUBLE PRECISION,
    success INTEGER DEFAULT 0,
    result_message TEXT,
    result_stats TEXT,
    duration_seconds DOUBLE PRECISION DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daemon_log (
    id SERIAL PRIMARY KEY,
    cycle_number INTEGER,
    started_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    completed_at DOUBLE PRECISION,
    replies_found INTEGER DEFAULT 0,
    leads_marked INTEGER DEFAULT 0,
    campaign_emails_sent INTEGER DEFAULT 0,
    followup_emails_sent INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    error_message TEXT,
    duration_seconds DOUBLE PRECISION DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ce_campaign ON campaign_enrollments(campaign_id);
CREATE INDEX IF NOT EXISTS idx_ce_lead ON campaign_enrollments(lead_id);
CREATE INDEX IF NOT EXISTS idx_ce_status ON campaign_enrollments(status);
CREATE INDEX IF NOT EXISTS idx_sr_schedule ON schedule_runs(schedule_id);
CREATE INDEX IF NOT EXISTS idx_dl_cycle ON daemon_log(cycle_number);

CREATE TABLE IF NOT EXISTS geo_targets (
    id SERIAL PRIMARY KEY,
    country TEXT NOT NULL,
    state TEXT DEFAULT '',
    city TEXT DEFAULT '',
    scheduled_day TEXT DEFAULT '',
    scheduled_time TEXT DEFAULT '',
    scheduled_date TEXT DEFAULT '',
    target_type TEXT DEFAULT 'scout',
    enabled INTEGER DEFAULT 1,
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);
CREATE INDEX IF NOT EXISTS idx_geo_country ON geo_targets(country);

CREATE TABLE IF NOT EXISTS campaign_calendar (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    scheduled_date TEXT NOT NULL,
    lead_source TEXT DEFAULT 'all',
    template_html TEXT DEFAULT '',
    template_text TEXT DEFAULT '',
    subject_template TEXT DEFAULT '',
    interval_days INTEGER DEFAULT 1,
    active INTEGER DEFAULT 1,
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);

CREATE TABLE IF NOT EXISTS analytics_delivery (
    id SERIAL PRIMARY KEY,
    email_log_id INTEGER REFERENCES email_log(id),
    inbox_status TEXT DEFAULT 'unknown',
    spam_reason TEXT,
    bounce_type TEXT,
    domain TEXT,
    checked_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);

CREATE TABLE IF NOT EXISTS analytics_daily (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    metric TEXT NOT NULL,
    value DOUBLE PRECISION DEFAULT 0,
    dimension TEXT DEFAULT '',
    UNIQUE(date, metric, dimension)
);

CREATE TABLE IF NOT EXISTS daemon_config (
    id SERIAL PRIMARY KEY,
    agent_name TEXT UNIQUE NOT NULL,
    display_name TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    lead_type_filter TEXT DEFAULT '',
    max_per_run INTEGER DEFAULT 0,
    interval_override INTEGER DEFAULT 0,
    run_at_time TEXT DEFAULT '',
    run_on_days TEXT DEFAULT '',
    config_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS linkedin_queue (
    id SERIAL PRIMARY KEY,
    lead_id INTEGER REFERENCES leads(id),
    lead_name TEXT DEFAULT '',
    lead_email TEXT DEFAULT '',
    linkedin_url TEXT DEFAULT '',
    profile_title TEXT DEFAULT '',
    company TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    location TEXT DEFAULT '',
    niche TEXT DEFAULT '',
    message_template TEXT DEFAULT '',
    message_personalized TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    connection_sent_at DOUBLE PRECISION,
    message_sent_at DOUBLE PRECISION,
    replied_at DOUBLE PRECISION,
    notes TEXT,
    source TEXT DEFAULT 'scout',
    created_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW())),
    updated_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);
CREATE INDEX IF NOT EXISTS idx_liq_status ON linkedin_queue(status);
CREATE INDEX IF NOT EXISTS idx_liq_lead ON linkedin_queue(lead_id);

CREATE TABLE IF NOT EXISTS gmail_tokens (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token_json TEXT NOT NULL,
    sender_email TEXT DEFAULT '',
    updated_at DOUBLE PRECISION DEFAULT (EXTRACT(EPOCH FROM NOW()))
);
"""


def init_pg_db():
    """Initialize PostgreSQL schema and seed data."""
    with pg_conn() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        # Create all tables
        conn.executescript(SCHEMA_SQL)

        # Seed default users if not present
        existing = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
        if existing and existing["c"] == 0:
            import hashlib
            import secrets
            for username, password, role in [
                ("optima", "Sh.739235511", "admin"),
                ("Taha", "Dr.tk@uol.com", "user"),
            ]:
                salt = secrets.token_hex(16)
                pw_hash = hashlib.sha256((password + salt).encode()).hexdigest()
                conn.execute(
                    "INSERT INTO users (username, password_hash, salt, role) VALUES (%s, %s, %s, %s)",
                    (username, pw_hash, salt, role),
                )

        # Seed daemon config defaults if missing
        existing = conn.execute("SELECT COUNT(*) as c FROM daemon_config").fetchone()
        if existing and existing["c"] == 0:
            defaults = [
                ("lead_scout", "Lead Scout", 1, "scraped", 0),
                ("outreach", "Outreach", 1, "scraped", 10),
                ("followup", "Followup", 1, "", 0),
                ("reply_scan", "Reply Scan", 1, "", 0),
                ("campaign", "Campaign Steps", 1, "", 0),
                ("appointment", "Appointment", 1, "", 0),
                ("deal_closer", "Deal Closer", 1, "", 0),
                ("report", "Report Agent", 1, "", 0),
            ]
            for name, display, enabled, ltf, mpr in defaults:
                conn.execute(
                    "INSERT INTO daemon_config (agent_name, display_name, enabled, lead_type_filter, max_per_run) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (agent_name) DO NOTHING",
                    (name, display, enabled, ltf, mpr),
                )

        # Migrate leads.json into leads table if table is empty
        lead_count = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()
        if lead_count and lead_count["c"] == 0:
            _migrate_leads_json_pg(conn)


def _migrate_leads_json_pg(conn):
    """Import leads.json into the leads table on first run."""
    import os, json
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "leads.json")
    if not os.path.exists(db_path):
        return
    try:
        with open(db_path) as f:
            leads = json.load(f)
    except Exception:
        return

    from app.database import _LEAD_FIELDS
    imported = 0
    for lead in leads:
        if not isinstance(lead, dict):
            continue
        email = lead.get("business_email", "")
        phone = lead.get("phone", "")
        if not email and not phone:
            continue
        cols = []
        vals = []
        for f in _LEAD_FIELDS:
            if f in lead and lead[f] is not None:
                cols.append(f)
                vals.append(lead[f])
        if not cols:
            continue
        if "outreach_ready" not in cols:
            cols.append("outreach_ready")
            vals.append(1 if email else 0)
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(cols)
        try:
            conn.execute(f"INSERT INTO leads ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING", vals)
            imported += 1
        except Exception:
            pass
    logger.info("[PG MIGRATION] Imported %d leads from leads.json", imported)
