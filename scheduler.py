"""
scheduler.py — DEUS 3.0
=========================
Agent scheduling engine. Runs agents on configurable intervals,
stores schedule definitions in the database, and tracks execution history.

DB Table: schedules, schedule_runs

Usage:
    sched = AgentScheduler()
    sched.create_schedule("followup_check", "FollowupAgent", interval_minutes=60)
    sched.create_schedule("lead_scout", "LeadScoutAgent", interval_minutes=480,
                          config={"niche": "restaurants", "target": 20})
    sched.start()  # Runs in background thread
    sched.stop()
"""

import os
import sys
import time
import json
import threading
import logging
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Agent name -> kwargs mapping for scheduled runs
_AGENT_KWARGS_MAP = {
    "LeadScoutAgent": {"mode": "preview"},
    "OutreachAgent": {"mode": "preview"},
    "FollowupAgent": {},
    "DealCloserAgent": {},
    "ReportAgent": {},
    "SystemCheckerAgent": {},
}


@dataclass
class Schedule:
    schedule_id: int = 0
    name: str = ""
    agent_name: str = ""
    interval_minutes: int = 60
    config: dict = None
    enabled: bool = True
    last_run_at: float = 0.0
    next_run_at: float = 0.0
    created_at: float = 0.0

    def __post_init__(self):
        if self.config is None:
            self.config = {}


class AgentScheduler:
    """Runs agents on configurable schedules in background threads."""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._callback: Optional[Callable] = None
        self._ensure_tables()

    def _ensure_tables(self):
        """Create tables if they don't exist."""
        try:
            from app.database import db_conn
            with db_conn() as conn:
                conn.executescript("""
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

                    CREATE INDEX IF NOT EXISTS idx_sr_schedule ON schedule_runs(schedule_id);
                """)
        except Exception as e:
            logger.warning("Could not create scheduler tables: %s", e)

    # ------------------------------------------------------------------
    # Schedule CRUD
    # ------------------------------------------------------------------

    def create_schedule(
        self,
        name: str,
        agent_name: str,
        interval_minutes: int = 60,
        config: dict = None,
        enabled: bool = True,
    ) -> int:
        """Create a new schedule. Returns schedule ID."""
        from app.database import db_conn
        now = time.time()
        with db_conn() as conn:
            cur = conn.execute(
                """INSERT INTO schedules
                   (name, agent_name, interval_minutes, config, enabled, next_run_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    agent_name,
                    interval_minutes,
                    json.dumps(config or {}),
                    1 if enabled else 0,
                    now + (interval_minutes * 60),
                ),
            )
            schedule_id = cur.lastrowid
        logger.info("Schedule created: %s (agent=%s, every %dm)", name, agent_name, interval_minutes)
        return schedule_id

    def get_schedule(self, schedule_id: int) -> Optional[Schedule]:
        """Get a schedule by ID."""
        from app.database import db_conn
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_schedule(row)

    def list_schedules(self, enabled_only: bool = False) -> list[Schedule]:
        """List all schedules."""
        from app.database import db_conn
        with db_conn() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM schedules WHERE enabled = 1 ORDER BY name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schedules ORDER BY name"
                ).fetchall()
            return [self._row_to_schedule(r) for r in rows]

    def update_schedule(self, schedule_id: int, updates: dict):
        """Update a schedule's configuration."""
        from app.database import db_conn
        allowed = {"name", "agent_name", "interval_minutes", "config", "enabled"}
        sets = []
        vals = []
        for k, v in updates.items():
            if k in allowed:
                if k == "config":
                    v = json.dumps(v) if isinstance(v, dict) else v
                if k == "enabled":
                    v = 1 if v else 0
                sets.append(f"{k} = ?")
                vals.append(v)
        if sets:
            vals.append(schedule_id)
            with db_conn() as conn:
                conn.execute(
                    f"UPDATE schedules SET {', '.join(sets)} WHERE id = ?",
                    vals,
                )

    def delete_schedule(self, schedule_id: int):
        """Delete a schedule and its run history."""
        from app.database import db_conn
        with db_conn() as conn:
            conn.execute("DELETE FROM schedule_runs WHERE schedule_id = ?", (schedule_id,))
            conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        logger.info("Schedule %d deleted", schedule_id)

    def toggle_schedule(self, schedule_id: int, enabled: bool):
        """Enable or disable a schedule."""
        self.update_schedule(schedule_id, {"enabled": enabled})
        logger.info("Schedule %d %s", schedule_id, "enabled" if enabled else "disabled")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_schedule_now(self, schedule_id: int) -> dict:
        """Immediately run the agent for a given schedule."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return {"success": False, "message": f"Schedule {schedule_id} not found"}

        return self._execute_agent(schedule)

    def _execute_agent(self, schedule: Schedule) -> dict:
        """Execute an agent and record the run."""
        from app.database import db_conn

        agent_name = schedule.agent_name
        config = schedule.config if isinstance(schedule.config, dict) else {}

        start = time.time()
        success = False
        message = ""
        stats = {}

        try:
            # Import and instantiate agent
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from pipeline import get_agent_class
            agent_cls = get_agent_class(agent_name)
            if agent_cls is None:
                message = f"Agent class not found: {agent_name}"
                logger.error(message)
            else:
                agent = agent_cls()
                result = agent.run(**config)
                success = result.success
                message = result.message
                stats = result.stats
        except Exception as e:
            message = f"Agent execution failed: {e}"
            logger.error("Schedule '%s' execution error: %s", schedule.name, e)

        duration = time.time() - start
        now = time.time()

        # Record run in database
        try:
            with db_conn() as conn:
                conn.execute(
                    """INSERT INTO schedule_runs
                       (schedule_id, agent_name, started_at, completed_at,
                        success, result_message, result_stats, duration_seconds)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        schedule.schedule_id,
                        agent_name,
                        start,
                        now,
                        1 if success else 0,
                        message[:500],
                        json.dumps(stats),
                        duration,
                    ),
                )

                # Update next_run_at
                interval = schedule.interval_minutes * 60
                conn.execute(
                    "UPDATE schedules SET last_run_at = ?, next_run_at = ? WHERE id = ?",
                    (now, now + interval, schedule.schedule_id),
                )
        except Exception as e:
            logger.warning("Failed to record schedule run: %s", e)

        result_dict = {
            "success": success,
            "message": message[:200],
            "duration": round(duration, 1),
            "agent": agent_name,
            "schedule": schedule.name,
        }

        if self._callback:
            try:
                self._callback(result_dict)
            except Exception:
                pass

        logger.info("Schedule '%s' (%s) ran in %.1fs: %s",
                     schedule.name, agent_name, duration,
                     "OK" if success else "FAIL")
        return result_dict

    # ------------------------------------------------------------------
    # Background runner
    # ------------------------------------------------------------------

    def start(self):
        """Start the scheduler background thread."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="DEUS-Scheduler")
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self):
        """Stop the scheduler background thread."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Scheduler stopped")

    def set_callback(self, callback: Callable):
        """Set a callback function to be called after each schedule run."""
        self._callback = callback

    def _run_loop(self):
        """Main scheduler loop — checks schedules every 30 seconds."""
        logger.info("Scheduler loop started")
        while self._running:
            try:
                self._check_and_run_due()
            except Exception as e:
                logger.error("Scheduler loop error: %s", e)

            # Wait 30 seconds or until stop signal
            self._stop_event.wait(timeout=30)

    def _check_and_run_due(self):
        """Check all enabled schedules and run any that are due."""
        from app.database import db_conn
        now = time.time()

        with db_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM schedules
                   WHERE enabled = 1 AND next_run_at <= ?
                   ORDER BY next_run_at""",
                (now,),
            ).fetchall()

        for row in rows:
            schedule = self._row_to_schedule(row)
            logger.info("Running due schedule: %s (%s)", schedule.name, schedule.agent_name)
            self._execute_agent(schedule)

    # ------------------------------------------------------------------
    # Run history
    # ------------------------------------------------------------------

    def get_run_history(self, schedule_id: int = None, limit: int = 50) -> list[dict]:
        """Get recent run history, optionally for a specific schedule."""
        from app.database import db_conn
        with db_conn() as conn:
            if schedule_id:
                rows = conn.execute(
                    """SELECT * FROM schedule_runs
                       WHERE schedule_id = ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (schedule_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM schedule_runs
                       ORDER BY started_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_schedule_stats(self, schedule_id: int) -> dict:
        """Get execution stats for a schedule."""
        from app.database import db_conn
        with db_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM schedule_runs WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()["c"]

            successes = conn.execute(
                "SELECT COUNT(*) as c FROM schedule_runs WHERE schedule_id = ? AND success = 1",
                (schedule_id,),
            ).fetchone()["c"]

            avg_duration = conn.execute(
                "SELECT AVG(duration_seconds) as avg FROM schedule_runs WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()["avg"] or 0

            return {
                "total_runs": total,
                "successful": successes,
                "failed": total - successes,
                "success_rate": round(successes / max(total, 1) * 100, 1),
                "avg_duration": round(avg_duration, 1),
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_schedule(self, row) -> Schedule:
        """Convert a database row to a Schedule object."""
        config = {}
        if row["config"]:
            try:
                config = json.loads(row["config"])
            except (json.JSONDecodeError, TypeError):
                config = {}

        return Schedule(
            schedule_id=row["id"],
            name=row["name"],
            agent_name=row["agent_name"],
            interval_minutes=row["interval_minutes"],
            config=config,
            enabled=bool(row["enabled"]),
            last_run_at=row["last_run_at"] or 0,
            next_run_at=row["next_run_at"] or 0,
            created_at=row["created_at"] or 0,
        )

    @property
    def is_running(self) -> bool:
        return self._running


# Singleton
_scheduler: Optional[AgentScheduler] = None


def get_scheduler() -> AgentScheduler:
    """Get or create the singleton AgentScheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AgentScheduler()
    return _scheduler
