"""
app/database.py — DEUS 3.0 SQL Database Layer
==============================================
SQLite for user auth, sessions, pending changes, email tracking, PDF rules.
Can migrate to PostgreSQL later by changing the connection string.
"""

import os
import sqlite3
import hashlib
import secrets
import time
import json
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deus.db")


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

        # Seed default users if not present
        existing = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        if existing == 0:
            _create_user(conn, "optima", "Sh.739235511", "admin")
            _create_user(conn, "Taha", "Dr.tk@uol.com", "user")


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
