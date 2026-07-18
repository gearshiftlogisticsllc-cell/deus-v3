from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from app.db import Base


class Analytics(Base):
    __tablename__ = "analytics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric = Column(String, nullable=False)
    value = Column(Float, default=0)
    date = Column(String, nullable=True)
    extra = Column(Text, default="")


class AnalyticsDelivery(Base):
    __tablename__ = "analytics_delivery"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email_log_id = Column(Integer, ForeignKey("email_log.id"), nullable=True)
    inbox_status = Column(String, default="unknown")
    spam_reason = Column(Text, default="")
    bounce_type = Column(String, default="")
    domain = Column(String, default="")
    checked_at = Column(Float, default=None)


class AnalyticsDaily(Base):
    __tablename__ = "analytics_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String, nullable=False)
    metric = Column(String, nullable=False)
    value = Column(Float, default=0)
    dimension = Column(String, default="")
