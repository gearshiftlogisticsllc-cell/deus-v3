"""
app/database.py — DEUS 3.0 Database Layer
==========================================
SQLite by default. Set DATABASE_URL env var to switch to PostgreSQL
(all 50+ functions work unchanged via db_adapter.py).
"""

import os
import json
import time
import hashlib
import secrets
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Backend selection — DATABASE_URL triggers PostgreSQL mode
# ---------------------------------------------------------------------------
_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_USE_PG = bool(_DATABASE_URL)

if _USE_PG:
    import logging
    logger = logging.getLogger(__name__)

    def get_db():
        from app.db_adapter import PgConnection
        return PgConnection(_DATABASE_URL)

    @contextmanager
    def db_conn():
        from app.db_adapter import pg_conn
        with pg_conn() as conn:
            yield conn

    # All functions below use db_conn() internally — they work unchanged.
    # The adapter rewrites SQL (?→%s), handles lastrowid (RETURNING id),
    # and converts INSERT OR REPLACE/IGNORE to ON CONFLICT syntax.

else:
    import sqlite3

    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deus.db")
    _VOLUME_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.getenv("RAILWAY_VOLUME_PATH", "")
    if _VOLUME_PATH:
        DB_PATH = os.path.join(_VOLUME_PATH, "deus.db")

    def get_db():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def db_conn():
        conn = get_db()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()



def init_db():
    if _USE_PG:
        from app.db_adapter import init_pg_db
        init_pg_db()
    else:
        _init_db_sqlite()


def _init_db_sqlite():
    with db_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at REAL DEFAULT (strftime('%s','now')),
                last_login REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at REAL DEFAULT (strftime('%s','now')),
                expires_at REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS pending_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at REAL DEFAULT (strftime('%s','now')),
                reviewed_by INTEGER,
                reviewed_at REAL,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (reviewed_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS email_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_email TEXT NOT NULL,
                lead_name TEXT,
                subject TEXT,
                status TEXT DEFAULT 'sent',
                channel TEXT DEFAULT 'email',
                agent TEXT DEFAULT 'OutreachAgent',
                message_id TEXT,
                opened_at REAL,
                replied_at REAL,
                bounced_at REAL,
                bounce_reason TEXT,
                complained_at REAL,
                sent_at REAL DEFAULT (strftime('%s','now')),
                smtp_profile TEXT
            );

            CREATE TABLE IF NOT EXISTS email_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_log_id INTEGER,
                event_type TEXT NOT NULL,
                event_data TEXT,
                timestamp REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (email_log_id) REFERENCES email_log(id)
            );

            CREATE TABLE IF NOT EXISTS pdf_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                content TEXT,
                uploaded_by INTEGER,
                uploaded_at REAL DEFAULT (strftime('%s','now')),
                active INTEGER DEFAULT 1,
                FOREIGN KEY (uploaded_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS custom_pipelines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                steps TEXT NOT NULL,
                created_by INTEGER,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric TEXT NOT NULL,
                value REAL DEFAULT 0,
                date TEXT,
                extra TEXT,
                UNIQUE(metric, date)
            );

            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                first_contacted_at REAL,
                last_contacted_at REAL,
                contact_count INTEGER DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now')),
                updated_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(business_email);
            CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
            CREATE INDEX IF NOT EXISTS idx_leads_outreach_ready ON leads(outreach_ready);
        """)

        # Migration: add columns that may not exist on older DBs
        try:
            conn.execute("ALTER TABLE email_log ADD COLUMN agent TEXT DEFAULT 'OutreachAgent'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE email_log ADD COLUMN complained_at REAL")
        except Exception:
            pass

        # Lead type / import tracking
        for col, typedef in [
            ("lead_type", "TEXT DEFAULT 'cold'"),        # cold | warm | referral | inbound
            ("import_batch_id", "TEXT"),
            ("import_filename", "TEXT"),
            ("email_verified", "INTEGER DEFAULT 0"),
            ("email_verified_at", "REAL"),
            ("verification_method", "TEXT"),              # syntax | mx | smtp
        ]:
            try:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {typedef}")
            except Exception:
                pass

        # Indexes for new columns
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_type ON leads(lead_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_verified ON leads(email_verified)")
        except Exception:
            pass

        # Migration: fix existing leads that have lead_type='cold' (old default)
        try:
            conn.execute("UPDATE leads SET lead_type = 'imported' WHERE source = 'manual_import' AND lead_type = 'cold'")
            conn.execute("UPDATE leads SET lead_type = 'scraped' WHERE source NOT IN ('manual_import','import') AND (lead_type IS NULL OR lead_type = 'cold')")
        except Exception:
            pass

        # Campaign tables (created here too for safety; campaign.py also creates them)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at REAL DEFAULT (strftime('%s','now')),
                updated_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS campaign_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                step_order INTEGER NOT NULL,
                day_offset INTEGER NOT NULL DEFAULT 0,
                subject_template TEXT DEFAULT '',
                body_template TEXT DEFAULT '',
                channel TEXT DEFAULT 'email',
                is_ai_generated INTEGER DEFAULT 1,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
            );

            CREATE TABLE IF NOT EXISTS campaign_enrollments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                lead_id INTEGER NOT NULL,
                current_step INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                enrolled_at REAL DEFAULT (strftime('%s','now')),
                last_sent_at REAL,
                completed_at REAL,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                agent_name TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL DEFAULT 60,
                config TEXT DEFAULT '{}',
                enabled INTEGER DEFAULT 1,
                last_run_at REAL DEFAULT 0,
                next_run_at REAL DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS schedule_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                started_at REAL DEFAULT (strftime('%s','now')),
                completed_at REAL,
                success INTEGER DEFAULT 0,
                result_message TEXT,
                result_stats TEXT,
                duration_seconds REAL DEFAULT 0,
                FOREIGN KEY (schedule_id) REFERENCES schedules(id)
            );

            CREATE TABLE IF NOT EXISTS daemon_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_number INTEGER,
                started_at REAL DEFAULT (strftime('%s','now')),
                completed_at REAL,
                replies_found INTEGER DEFAULT 0,
                leads_marked INTEGER DEFAULT 0,
                campaign_emails_sent INTEGER DEFAULT 0,
                followup_emails_sent INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                error_message TEXT,
                duration_seconds REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_ce_campaign ON campaign_enrollments(campaign_id);
            CREATE INDEX IF NOT EXISTS idx_ce_lead ON campaign_enrollments(lead_id);
            CREATE INDEX IF NOT EXISTS idx_ce_status ON campaign_enrollments(status);
            CREATE INDEX IF NOT EXISTS idx_sr_schedule ON schedule_runs(schedule_id);
            CREATE INDEX IF NOT EXISTS idx_dl_cycle ON daemon_log(cycle_number);

            -- Geography / Word Map
            CREATE TABLE IF NOT EXISTS geo_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                scheduled_day TEXT DEFAULT '',
                scheduled_time TEXT DEFAULT '',
                target_type TEXT DEFAULT 'scout',
                enabled INTEGER DEFAULT 1,
                created_at REAL DEFAULT (strftime('%s','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_geo_country ON geo_targets(country);

            -- Campaign calendar entries (followup campaign)
            CREATE TABLE IF NOT EXISTS campaign_calendar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                scheduled_date TEXT NOT NULL,
                lead_source TEXT DEFAULT 'all',
                template_html TEXT DEFAULT '',
                template_text TEXT DEFAULT '',
                subject_template TEXT DEFAULT '',
                interval_days INTEGER DEFAULT 1,
                active INTEGER DEFAULT 1,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
            );

            -- Analytics detail: inbox placement tracking
            CREATE TABLE IF NOT EXISTS analytics_delivery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_log_id INTEGER,
                inbox_status TEXT DEFAULT 'unknown',
                spam_reason TEXT,
                bounce_type TEXT,
                domain TEXT,
                checked_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (email_log_id) REFERENCES email_log(id)
            );

            -- Analytics: aggregated daily stats
            CREATE TABLE IF NOT EXISTS analytics_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL DEFAULT 0,
                dimension TEXT DEFAULT '',
                UNIQUE(date, metric, dimension)
            );
        """)

        # Daemon per-agent configuration
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daemon_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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

            -- Insert defaults for all known agents
            INSERT OR IGNORE INTO daemon_config (agent_name, display_name, enabled, lead_type_filter, max_per_run)
            VALUES
                ('lead_scout', 'Lead Scout', 1, 'scraped', 0),
                ('outreach', 'Outreach', 1, 'scraped', 10),
                ('followup', 'Followup', 1, '', 0),
                ('reply_scan', 'Reply Scan', 1, '', 0),
                ('campaign', 'Campaign Steps', 1, '', 0),
                ('appointment', 'Appointment', 1, '', 0),
                ('deal_closer', 'Deal Closer', 1, '', 0),
                ('report', 'Report Agent', 1, '', 0);
        """)

        # LinkedIn outreach queue
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS linkedin_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER,
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
                connection_sent_at REAL,
                message_sent_at REAL,
                replied_at REAL,
                notes TEXT,
                source TEXT DEFAULT 'scout',
                created_at REAL DEFAULT (strftime('%s','now')),
                updated_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_liq_status ON linkedin_queue(status);
            CREATE INDEX IF NOT EXISTS idx_liq_lead ON linkedin_queue(lead_id);
        """)

        # Gmail API token (persists across Railway restarts)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gmail_tokens (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                token_json TEXT NOT NULL,
                sender_email TEXT DEFAULT '',
                updated_at REAL DEFAULT (strftime('%s','now'))
            )
        """)

        # Migration: add geo_target columns that may not exist on older DBs
        for col, typedef in [
            ("scheduled_day", "TEXT DEFAULT ''"),
            ("scheduled_time", "TEXT DEFAULT ''"),
            ("scheduled_date", "TEXT DEFAULT ''"),  # specific date YYYY-MM-DD (in addition to day-of-week)
        ]:
            try:
                conn.execute(f"ALTER TABLE geo_targets ADD COLUMN {col} {typedef}")
            except Exception:
                pass

        # Seed default users if not present
        existing = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        if existing == 0:
            _create_user(conn, "optima", "Sh.739235511", "admin")
            _create_user(conn, "Taha", "Dr.tk@uol.com", "user")

        # Migrate leads.json into leads table if table is empty
        lead_count = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()["c"]
        if lead_count == 0:
            _migrate_leads_json(conn)


def _create_user(conn, username: str, password: str, role: str):
    salt = secrets.token_hex(16)
    pw_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, role) VALUES (?, ?, ?, ?)",
        (username, pw_hash, salt, role),
    )


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((password + salt).encode()).hexdigest()


def authenticate_user(username: str, password: str) -> dict:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            return None
        pw_hash = hash_password(password, row["salt"])
        if pw_hash != row["password_hash"]:
            return None
        # Update last login
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (time.time(), row["id"]),
        )
        return {"id": row["id"], "username": row["username"], "role": row["role"]}


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.time() + 86400  # 24 hours
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
    return token


def validate_session(token: str) -> dict:
    if not token:
        return None
    with db_conn() as conn:
        row = conn.execute(
            "SELECT s.*, u.username, u.role FROM sessions s "
            "JOIN users u ON s.user_id = u.id "
            "WHERE s.token = ? AND s.expires_at > ?",
            (token, time.time()),
        ).fetchone()
        if not row:
            return None
        return {"user_id": row["user_id"], "username": row["username"], "role": row["role"]}


def delete_session(token: str):
    with db_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def create_pending_change(user_id: int, action: str, target: str, payload: dict) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pending_changes (user_id, action, target, payload) VALUES (?, ?, ?, ?)",
            (user_id, action, target, json.dumps(payload)),
        )
        return cur.lastrowid


def get_pending_changes(status: str = "pending") -> list:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT pc.*, u.username FROM pending_changes pc "
            "JOIN users u ON pc.user_id = u.id "
            "WHERE pc.status = ? ORDER BY pc.created_at DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]


def review_pending_change(change_id: int, reviewer_id: int, approved: bool):
    with db_conn() as conn:
        conn.execute(
            "UPDATE pending_changes SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            ("approved" if approved else "rejected", reviewer_id, time.time(), change_id),
        )


def log_email(lead_email: str, lead_name: str, subject: str, status: str,
              channel: str = "email", message_id: str = "", smtp_profile: str = "",
              agent: str = "OutreachAgent") -> int:
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO email_log (lead_email, lead_name, subject, status, channel, message_id, smtp_profile, agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_email, lead_name, subject, status, channel, message_id, smtp_profile, agent),
        )
        return cur.lastrowid


def update_email_event(email_log_id: int, event_type: str, event_data: str = ""):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO email_events (email_log_id, event_type, event_data) VALUES (?, ?, ?)",
            (email_log_id, event_type, event_data),
        )
        if event_type == "opened":
            conn.execute("UPDATE email_log SET opened_at = ? WHERE id = ?",
                        (time.time(), email_log_id))
        elif event_type == "replied":
            conn.execute("UPDATE email_log SET replied_at = ? WHERE id = ?",
                        (time.time(), email_log_id))
        elif event_type == "bounced":
            conn.execute("UPDATE email_log SET bounced_at = ?, bounce_reason = ? WHERE id = ?",
                        (time.time(), event_data, email_log_id))


def get_email_analytics() -> dict:
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM email_log").fetchone()["c"]
        sent = conn.execute("SELECT COUNT(*) as c FROM email_log WHERE status='sent'").fetchone()["c"]
        delivered = conn.execute("SELECT COUNT(*) as c FROM email_log WHERE status='delivered'").fetchone()["c"]
        opened = conn.execute("SELECT COUNT(*) as c FROM email_log WHERE opened_at IS NOT NULL").fetchone()["c"]
        replied = conn.execute("SELECT COUNT(*) as c FROM email_log WHERE replied_at IS NOT NULL").fetchone()["c"]
        bounced = conn.execute("SELECT COUNT(*) as c FROM email_log WHERE bounced_at IS NOT NULL").fetchone()["c"]
        complained = conn.execute("SELECT COUNT(*) as c FROM email_log WHERE complained_at IS NOT NULL").fetchone()["c"]
        failed = conn.execute("SELECT COUNT(*) as c FROM email_log WHERE status='failed'").fetchone()["c"]
        recent = conn.execute(
            "SELECT *, lead_email as to_email FROM email_log ORDER BY sent_at DESC LIMIT 50"
        ).fetchall()

        return {
            "total": total,
            "total_sent": sent,
            "total_delivered": delivered,
            "total_opened": opened,
            "total_replies": replied,
            "total_bounced": bounced,
            "total_complained": complained,
            "total_failed": failed,
            "sent": sent,
            "delivered": delivered,
            "opened": opened,
            "replied": replied,
            "bounced": bounced,
            "response_rate": round(replied / max(delivered, 1) * 100, 1),
            "delivery_rate": round(delivered / max(sent, 1) * 100, 1),
            "open_rate": round(opened / max(delivered, 1) * 100, 1),
            "reply_rate": round(replied / max(delivered, 1) * 100, 1),
            "bounce_rate": round(bounced / max(sent, 1) * 100, 1),
            "recent": [dict(r) for r in recent],
        }


def save_pdf_rules(filename: str, content: str, uploaded_by: int) -> int:
    with db_conn() as conn:
        # Deactivate old rules
        conn.execute("UPDATE pdf_rules SET active = 0 WHERE active = 1")
        cur = conn.execute(
            "INSERT INTO pdf_rules (filename, content, uploaded_by, active) VALUES (?, ?, ?, 1)",
            (filename, content, uploaded_by),
        )
        return cur.lastrowid


def get_active_pdf_rules() -> dict:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pdf_rules WHERE active = 1 ORDER BY uploaded_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def save_custom_pipeline(name: str, description: str, steps: list, created_by: int) -> int:
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT OR REPLACE INTO custom_pipelines (name, description, steps, created_by) VALUES (?, ?, ?, ?)",
            (name, description, json.dumps(steps), created_by),
        )
        return cur.lastrowid


def get_custom_pipelines() -> list:
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM custom_pipelines ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("steps"), str):
                try:
                    d["steps"] = json.loads(d["steps"])
                except Exception:
                    d["steps"] = [d["steps"]]
            result.append(d)
        return result


def delete_custom_pipeline(pipeline_id: int):
    with db_conn() as conn:
        conn.execute("DELETE FROM custom_pipelines WHERE id = ?", (pipeline_id,))


def record_analytics(metric: str, value: float, date: str, extra: str = ""):
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO analytics (metric, value, date, extra) VALUES (?, ?, ?, ?)",
            (metric, value, date, extra),
        )


def get_analytics_summary() -> dict:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT metric, SUM(value) as total FROM analytics GROUP BY metric"
        ).fetchall()
        return {r["metric"]: r["total"] for r in rows}


# ---------------------------------------------------------------------------
# Leads CRUD
# ---------------------------------------------------------------------------

_LEAD_FIELDS = [
    "business_name", "owner_name", "business_email", "phone", "website",
    "address", "niche", "category", "services_offered", "linkedin_url",
    "instagram_handle", "facebook_url", "source", "status", "outreach_ready",
    "needs_human", "needs_human_reason", "channel_used", "preferred_channel",
    "score", "notes", "lead_type", "import_batch_id", "import_filename",
    "email_verified", "email_verified_at", "verification_method",
]


def _row_to_dict(row) -> dict:
    d = dict(row)
    d.pop("extra_json", None)
    return d


def upsert_lead(lead: dict) -> int:
    """Insert or update a lead. Returns the lead ID."""
    with db_conn() as conn:
        email = lead.get("business_email", "")
        existing = None
        if email:
            existing = conn.execute(
                "SELECT id FROM leads WHERE business_email = ?", (email,)
            ).fetchone()

        if existing:
            lid = existing["id"]
            sets = []
            vals = []
            for f in _LEAD_FIELDS:
                if f in lead:
                    sets.append(f"{f} = ?")
                    vals.append(lead[f])
            sets.append("updated_at = strftime('%s','now')")
            vals.append(lid)
            conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", vals)
            return lid
        else:
            cols = [f for f in _LEAD_FIELDS if f in lead]
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            vals = [lead[f] for f in cols]
            cur = conn.execute(
                f"INSERT INTO leads ({col_names}) VALUES ({placeholders})", vals
            )
            return cur.lastrowid


def upsert_leads_batch(leads: list[dict]) -> dict:
    """Insert/update multiple leads. Returns counts."""
    imported = 0
    skipped = 0
    for lead in leads:
        email = lead.get("business_email", "")
        if not email and not lead.get("phone"):
            skipped += 1
            continue
        # Set defaults
        lead.setdefault("status", "new")
        lead.setdefault("outreach_ready", bool(lead.get("business_email")))
        lead.setdefault("needs_human", 0)
        lead.setdefault("preferred_channel", "email")
        lead.setdefault("source", "import")
        lead.setdefault("score", 0)
        upsert_lead(lead)
        imported += 1
    return {"imported": imported, "skipped": skipped}


def get_lead(lead_id: int) -> dict:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return _row_to_dict(row) if row else None


def get_leads(status: str = None, outreach_ready: bool = None,
              has_email: bool = None, lead_type: str = None,
              limit: int = 500, offset: int = 0) -> list[dict]:
    with db_conn() as conn:
        query = "SELECT * FROM leads WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if outreach_ready is not None:
            query += " AND outreach_ready = ?"
            params.append(1 if outreach_ready else 0)
        if has_email is not None:
            if has_email:
                query += " AND business_email IS NOT NULL AND business_email != ''"
            else:
                query += " AND (business_email IS NULL OR business_email = '')"
        if lead_type:
            query += " AND lead_type = ?"
            params.append(lead_type)
        query += " ORDER BY score DESC, id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def count_leads(status: str = None, outreach_ready: bool = None) -> int:
    with db_conn() as conn:
        query = "SELECT COUNT(*) as c FROM leads WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if outreach_ready is not None:
            query += " AND outreach_ready = ?"
            params.append(1 if outreach_ready else 0)
        return conn.execute(query, params).fetchone()["c"]


def update_lead(lead_id: int, updates: dict):
    with db_conn() as conn:
        sets = []
        vals = []
        for k, v in updates.items():
            if k in _LEAD_FIELDS or k in ("first_contacted_at", "last_contacted_at", "contact_count",
                                           "email_verified", "email_verified_at", "verification_method"):
                sets.append(f"{k} = ?")
                vals.append(v)
        if sets:
            sets.append("updated_at = strftime('%s','now')")
            vals.append(lead_id)
            conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", vals)


def update_leads_batch(ids: list[int], updates: dict):
    for lid in ids:
        update_lead(lid, updates)


def delete_lead(lead_id: int):
    with db_conn() as conn:
        conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))


def get_outreach_candidates(limit: int = 25, lead_type: str = None) -> list[dict]:
    """Get leads ready for outreach: has email, not yet contacted. Optional lead_type filter."""
    with db_conn() as conn:
        query = """SELECT * FROM leads
                   WHERE business_email IS NOT NULL AND business_email != ''
                   AND status != 'contacted'"""
        params = []
        if lead_type:
            query += " AND lead_type = ?"
            params.append(lead_type)
        query += " ORDER BY score DESC, id ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def is_email_already_contacted(email: str) -> bool:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM leads WHERE business_email = ? AND status = 'contacted' LIMIT 1",
            (email,),
        ).fetchone()
        return row is not None


def is_lead_unsubscribed(email: str) -> bool:
    """Check if a lead has unsubscribed or been blacklisted."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM leads WHERE business_email = ? AND status IN ('unsubscribed', 'blocked') LIMIT 1",
            (email,),
        ).fetchone()
        return row is not None


def mark_lead_unsubscribed(email: str) -> bool:
    """Mark a lead as unsubscribed. Returns True if found and updated."""
    with db_conn() as conn:
        cursor = conn.execute(
            "UPDATE leads SET status = 'unsubscribed', updated_at = ? WHERE business_email = ?",
            (time.time(), email),
        )
        return cursor.rowcount > 0


def get_contacted_emails() -> set:
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT business_email FROM leads WHERE status = 'contacted' AND business_email IS NOT NULL AND business_email != ''"
        ).fetchall()
        return {r["business_email"] for r in rows}


def mark_leads_contacted(ids: list[int], channel: str = "email"):
    import time as _time
    now = _time.time()
    with db_conn() as conn:
        for lid in ids:
            conn.execute(
                """UPDATE leads SET status = 'contacted', channel_used = ?,
                   last_contacted_at = ?, contact_count = contact_count + 1,
                   updated_at = strftime('%s','now')
                   WHERE id = ?""",
                (channel, now, lid),
            )


def _migrate_leads_json(conn):
    """Import leads.json into the leads table on first run."""
    leads_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "leads.json")
    if not os.path.exists(leads_path):
        return
    try:
        with open(leads_path) as f:
            leads = json.load(f)
    except Exception:
        return

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
        # Auto-set outreach_ready
        if "outreach_ready" not in cols:
            cols.append("outreach_ready")
            vals.append(1 if email else 0)
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        try:
            conn.execute(f"INSERT INTO leads ({col_names}) VALUES ({placeholders})", vals)
            imported += 1
        except Exception:
            pass
    print(f"[DB MIGRATION] Imported {imported} leads from leads.json into database.")


# ---------------------------------------------------------------------------
# Gmail Token persistence (DB-backed, survives Railway restarts)
# ---------------------------------------------------------------------------

def save_gmail_token(token_json: str, sender_email: str = ""):
    """Upsert the Gmail API OAuth token into the database."""
    with db_conn() as conn:
        existing = conn.execute("SELECT id FROM gmail_tokens WHERE id = 1").fetchone()
        if existing:
            conn.execute(
                "UPDATE gmail_tokens SET token_json = ?, sender_email = ?, updated_at = ? WHERE id = 1",
                (token_json, sender_email, time.time()),
            )
        else:
            conn.execute(
                "INSERT INTO gmail_tokens (id, token_json, sender_email, updated_at) VALUES (1, ?, ?, ?)",
                (token_json, sender_email, time.time()),
            )


def get_gmail_token() -> str | None:
    """Return the stored Gmail token JSON, or None if not configured."""
    with db_conn() as conn:
        row = conn.execute("SELECT token_json FROM gmail_tokens WHERE id = 1").fetchone()
        return row["token_json"] if row else None


def delete_gmail_token():
    """Remove the stored Gmail token."""
    with db_conn() as conn:
        conn.execute("DELETE FROM gmail_tokens WHERE id = 1")


# ---------------------------------------------------------------------------
# Daemon per-agent configuration
# ---------------------------------------------------------------------------

def get_daemon_configs() -> list[dict]:
    """Get all daemon agent configurations."""
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM daemon_config ORDER BY id").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["config_json"] = json.loads(d.get("config_json", "{}"))
            except Exception:
                d["config_json"] = {}
            result.append(d)
        return result


def get_daemon_config(agent_name: str) -> dict:
    """Get a single daemon agent configuration."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM daemon_config WHERE agent_name = ?", (agent_name,)
        ).fetchone()
        if row:
            d = dict(row)
            try:
                d["config_json"] = json.loads(d.get("config_json", "{}"))
            except Exception:
                d["config_json"] = {}
            return d
        return None


def save_daemon_config(agent_name: str, config: dict):
    """Insert or update a daemon agent configuration."""
    with db_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM daemon_config WHERE agent_name = ?", (agent_name,)
        ).fetchone()
        if existing:
            sets = []
            vals = []
            for field in ["enabled", "lead_type_filter", "max_per_run",
                          "interval_override", "run_at_time", "run_on_days"]:
                if field in config:
                    sets.append(f"{field} = ?")
                    vals.append(config[field])
            if "config_json" in config:
                sets.append("config_json = ?")
                vals.append(json.dumps(config["config_json"]) if isinstance(config["config_json"], dict) else config["config_json"])
            if sets:
                vals.append(existing["id"])
                conn.execute(f"UPDATE daemon_config SET {', '.join(sets)} WHERE id = ?", vals)
        else:
            conn.execute(
                """INSERT INTO daemon_config (agent_name, display_name, enabled, lead_type_filter,
                   max_per_run, interval_override, run_at_time, run_on_days, config_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_name,
                    config.get("display_name", agent_name),
                    config.get("enabled", True),
                    config.get("lead_type_filter", ""),
                    config.get("max_per_run", 0),
                    config.get("interval_override", 0),
                    config.get("run_at_time", ""),
                    config.get("run_on_days", ""),
                    json.dumps(config.get("config_json", {})),
                ),
            )


def reset_daemon_configs():
    """Reset all daemon configurations to defaults."""
    with db_conn() as conn:
        conn.execute("DELETE FROM daemon_config")
        conn.executescript("""
            INSERT INTO daemon_config (agent_name, display_name, enabled, lead_type_filter, max_per_run)
            VALUES
                ('lead_scout', 'Lead Scout', 1, 'scraped', 0),
                ('outreach', 'Outreach', 1, 'scraped', 10),
                ('followup', 'Followup', 1, '', 0),
                ('reply_scan', 'Reply Scan', 1, '', 0),
                ('campaign', 'Campaign Steps', 1, '', 0),
                ('appointment', 'Appointment', 1, '', 0),
                ('deal_closer', 'Deal Closer', 1, '', 0),
                ('report', 'Report Agent', 1, '', 0);
        """)


# ---------------------------------------------------------------------------
# LinkedIn Outreach Queue
# ---------------------------------------------------------------------------

def add_to_linkedin_queue(lead: dict, message_template: str = "") -> int:
    """Add a lead to the LinkedIn outreach queue. Returns queue entry ID."""
    with db_conn() as conn:
        linkedin_url = lead.get("linkedin_url") or lead.get("linkedin", "")
        if not linkedin_url:
            return 0
        existing = conn.execute(
            "SELECT id FROM linkedin_queue WHERE linkedin_url = ?", (linkedin_url,)
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO linkedin_queue
               (lead_id, lead_name, lead_email, linkedin_url, profile_title, company,
                industry, location, niche, message_template, status, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                lead.get("id"),
                lead.get("business_name", ""),
                lead.get("business_email", ""),
                linkedin_url,
                lead.get("profile_title", ""),
                lead.get("company", ""),
                lead.get("industry", ""),
                lead.get("location", "") or lead.get("address", ""),
                lead.get("niche", ""),
                message_template,
                lead.get("source", "scout"),
            ),
        )
        return cur.lastrowid


def get_linkedin_queue(status: str = None, limit: int = 100) -> list[dict]:
    """Get LinkedIn queue entries, optionally filtered by status."""
    with db_conn() as conn:
        query = "SELECT * FROM linkedin_queue WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_linkedin_queue(entry_id: int, updates: dict):
    """Update a LinkedIn queue entry."""
    with db_conn() as conn:
        sets = []
        vals = []
        for field in ("status", "message_template", "message_personalized",
                      "connection_sent_at", "message_sent_at", "replied_at", "notes"):
            if field in updates:
                sets.append(f"{field} = ?")
                vals.append(updates[field])
        if sets:
            import time
            sets.append("updated_at = ?")
            vals.append(time.time())
            vals.append(entry_id)
            conn.execute(f"UPDATE linkedin_queue SET {', '.join(sets)} WHERE id = ?", vals)


def delete_linkedin_entry(entry_id: int):
    """Delete a LinkedIn queue entry."""
    with db_conn() as conn:
        conn.execute("DELETE FROM linkedin_queue WHERE id = ?", (entry_id,))


def count_linkedin_queue(status: str = None) -> int:
    """Count LinkedIn queue entries."""
    with db_conn() as conn:
        query = "SELECT COUNT(*) as c FROM linkedin_queue WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        return conn.execute(query, params).fetchone()["c"]


def export_linkedin_csv(status: str = None) -> str:
    """Generate CSV text for LinkedIn queue export (Dux-Soup compatible format)."""
    entries = get_linkedin_queue(status=status, limit=10000)
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["First Name", "Last Name", "Email", "Company", "Position",
                      "Industry", "Location", "LinkedIn URL", "Personalized Note", "Status"])
    for e in entries:
        name_parts = (e.get("lead_name") or "").strip().split(" ", 1)
        first = name_parts[0] if name_parts else ""
        last = name_parts[1] if len(name_parts) > 1 else ""
        note = e.get("message_personalized") or e.get("message_template") or ""
        writer.writerow([
            first, last, e.get("lead_email", ""), e.get("company", ""),
            e.get("profile_title", ""), e.get("industry", ""),
            e.get("location", ""), e.get("linkedin_url", ""),
            note, e.get("status", "pending"),
        ])
    return output.getvalue()
