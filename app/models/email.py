from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text
from app.db import Base


class EmailLog(Base):
    __tablename__ = "email_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_email = Column(String, nullable=False)
    lead_name = Column(String, default="")
    subject = Column(String, default="")
    status = Column(String, default="sent")
    channel = Column(String, default="email")
    agent = Column(String, default="OutreachAgent")
    message_id = Column(String, default="")
    opened_at = Column(Float, nullable=True)
    replied_at = Column(Float, nullable=True)
    bounced_at = Column(Float, nullable=True)
    bounce_reason = Column(Text, default="")
    complained_at = Column(Float, nullable=True)
    sent_at = Column(Float, default=None)
    smtp_profile = Column(String, default="")


class EmailEvent(Base):
    __tablename__ = "email_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email_log_id = Column(Integer, ForeignKey("email_log.id"), nullable=True)
    event_type = Column(String, nullable=False)
    event_data = Column(Text, default="")
    timestamp = Column(Float, default=None)
