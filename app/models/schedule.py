from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from app.db import Base


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    agent_name = Column(String, nullable=False)
    interval_minutes = Column(Integer, nullable=False, default=60)
    config = Column(Text, default="{}")
    enabled = Column(Integer, default=1)
    last_run_at = Column(Float, default=0)
    next_run_at = Column(Float, default=0)
    created_at = Column(Float, default=None)


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    agent_name = Column(String, nullable=False)
    started_at = Column(Float, default=None)
    completed_at = Column(Float, nullable=True)
    success = Column(Integer, default=0)
    result_message = Column(Text, default="")
    result_stats = Column(Text, default="")
    duration_seconds = Column(Float, default=0)
