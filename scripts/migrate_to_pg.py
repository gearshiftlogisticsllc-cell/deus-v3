"""
scripts/migrate_to_pg.py — DEUS 3.0 SQLite → PostgreSQL Migration
==================================================================
Usage:
    set DATABASE_URL=postgresql://user:pass@host:5432/deus
    python scripts/migrate_to_pg.py

Reads the existing deus.db SQLite file and migrates ALL data to PostgreSQL.
Safe to re-run (skips existing tables / uses INSERT ON CONFLICT DO NOTHING).
"""

import os
import sys
import sqlite3
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is not set.")
    print("Usage: set DATABASE_URL=postgresql://user:pass@host:5432/deus && python scripts/migrate_to_pg.py")
    sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deus.db")
if not os.path.exists(DB_PATH):
    print(f"ERROR: SQLite database not found at {DB_PATH}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 1. Initialize PostgreSQL schema
# ---------------------------------------------------------------------------
from app.db_adapter import init_pg_db, PgConnection

print("Initializing PostgreSQL schema...")
init_pg_db()
print("  Schema created.")

# ---------------------------------------------------------------------------
# 2. Read all data from SQLite
# ---------------------------------------------------------------------------
sq = sqlite3.connect(DB_PATH)
sq.row_factory = sqlite3.Row

def fetch_table(name: str) -> list[dict]:
    try:
        rows = sq.execute(f"SELECT * FROM \"{name}\"").fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"  WARNING: Could not read table '{name}': {e}")
        return []

# Tables in dependency order (FK-safe)
TABLE_ORDER = [
    "users", "sessions", "pending_changes",
    "leads",
    "email_log", "email_events", "pdf_rules",
    "custom_pipelines", "analytics",
    "campaigns", "campaign_steps", "campaign_enrollments",
    "schedules", "schedule_runs", "daemon_log",
    "geo_targets", "campaign_calendar",
    "analytics_delivery", "analytics_daily",
    "daemon_config", "linkedin_queue", "gmail_tokens",
]

all_data = {}
for tbl in TABLE_ORDER:
    data = fetch_table(tbl)
    all_data[tbl] = data
    print(f"  Read {len(data)} rows from {tbl}")

# ---------------------------------------------------------------------------
# 3. Write data to PostgreSQL (using PgConnection for compatibility)
# ---------------------------------------------------------------------------
pg = PgConnection(DATABASE_URL)

def insert_pg(table: str, rows: list[dict]):
    if not rows:
        return 0
    # Build INSERT with column list from first row
    cols = list(rows[0].keys())
    col_names = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    imported = 0
    skipped = 0
    for row in rows:
        vals = [row[c] for c in cols]
        try:
            cur = pg._conn.cursor()
            cur.execute(sql, vals)
            cur.close()
            imported += 1
        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f"    SKIP: {e}")
    pg._conn.commit()
    return imported

print("\nMigrating data to PostgreSQL...")
total = 0
for tbl in TABLE_ORDER:
    data = all_data.get(tbl, [])
    if not data:
        continue
    n = insert_pg(tbl, data)
    total += n
    print(f"  Migrated {n}/{len(data)} rows to {tbl}")

pg.close()
sq.close()

# ---------------------------------------------------------------------------
# 4. Verify
# ---------------------------------------------------------------------------
print("\nVerifying...")
pg2 = PgConnection(DATABASE_URL)
for tbl in TABLE_ORDER:
    cur = pg2._conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM \"{tbl}\"")
        cnt = cur.fetchone()[0]
        print(f"  {tbl}: {cnt} rows")
    except Exception as e:
        print(f"  {tbl}: ERROR - {e}")
    cur.close()
pg2.close()

print(f"\nDone! Migrated {total} total rows to PostgreSQL at {DATABASE_URL}")
print("Set DATABASE_URL in your environment and restart DEUS to use PostgreSQL.")
