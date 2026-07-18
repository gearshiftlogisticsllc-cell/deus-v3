from sqlalchemy import Column, Integer, String, Float, Text
from app.db import Base


class DaemonConfig(Base):
    __tablename__ = "daemon_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, unique=True, nullable=False)
    display_name = Column(String, default="")
    enabled = Column(Integer, default=1)
    lead_type_filter = Column(String, default="")
    max_per_run = Column(Integer, default=0)
    interval_override = Column(Integer, default=0)
    run_at_time = Column(String, default="")
    run_on_days = Column(String, default="")
    config_json = Column(Text, default="{}")


class DaemonLog(Base):
    __tablename__ = "daemon_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cycle_number = Column(Integer, nullable=True, index=True)
    started_at = Column(Float, default=None)
    completed_at = Column(Float, nullable=True)
    replies_found = Column(Integer, default=0)
    leads_marked = Column(Integer, default=0)
    campaign_emails_sent = Column(Integer, default=0)
    followup_emails_sent = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    error_message = Column(Text, default="")
    duration_seconds = Column(Float, default=0)
