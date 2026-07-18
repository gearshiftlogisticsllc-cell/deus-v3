"""
app/db.py — DEUS SQLAlchemy Engine & Session Setup
===================================================
SQLite by default (deus.db). Set DATABASE_URL env var for PostgreSQL.

Usage:
    from app.db import SessionLocal
    with SessionLocal() as session:
        # use ORM models
        ...

Also maintains backward compat with legacy app.database functions.
"""

import os
import time
import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_USE_PG = bool(DATABASE_URL)

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deus.db")
    _VOLUME_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.getenv("RAILWAY_VOLUME_PATH", "")
    if _VOLUME_PATH:
        DB_PATH = os.path.join(_VOLUME_PATH, "deus.db")
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}, poolclass=None)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_session():
    """FastAPI dependency — yields a scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_orm():
    """Create all tables via ORM metadata. Safe to call multiple times."""
    from app.models import Base as ModelsBase
    ModelsBase.metadata.create_all(bind=engine)
    logger.info("ORM tables created/verified")

    # Seed defaults if empty
    with SessionLocal() as session:
        from sqlalchemy import select, func
        from app.models.user import User
        from app.models.daemon import DaemonConfig

        user_count = session.execute(select(func.count()).select_from(User)).scalar() or 0
        if user_count == 0:
            import hashlib, secrets
            for username, password, role in [
                ("optima", "Sh.739235511", "admin"),
                ("Taha", "Dr.tk@uol.com", "user"),
            ]:
                salt = secrets.token_hex(16)
                pw_hash = hashlib.sha256((password + salt).encode()).hexdigest()
                session.add(User(username=username, password_hash=pw_hash, salt=salt, role=role, created_at=time.time()))
            session.commit()
            logger.info("Default users seeded")

        dc_count = session.execute(select(func.count()).select_from(DaemonConfig)).scalar() or 0
        if dc_count == 0:
            defaults = [
                ("lead_scout", "Lead Scout", 1, "scraped", 0, "", "", '{"niche":"Hvac companies hiring administrative roles","target":400,"auto_rotation":true}'),
                ("outreach", "Outreach", 1, "scraped", 10, "", "", "{}"),
                ("followup", "Followup", 1, "", 0, "", "", "{}"),
                ("reply_scan", "Reply Scan", 1, "", 0, "", "", "{}"),
                ("campaign", "Campaign Steps", 1, "", 0, "", "", "{}"),
                ("appointment", "Appointment", 1, "", 0, "", "", "{}"),
                ("deal_closer", "Deal Closer", 1, "", 0, "", "", "{}"),
                ("report", "Report Agent", 1, "", 0, "", "", "{}"),
            ]
            for name, display, enabled, ltf, mpr, rat, rod, cj in defaults:
                session.add(DaemonConfig(agent_name=name, display_name=display, enabled=enabled, lead_type_filter=ltf, max_per_run=mpr, run_at_time=rat, run_on_days=rod, config_json=cj))
            session.commit()
            logger.info("Default daemon configs seeded")

