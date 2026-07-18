from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from app.db import Base


class LinkedInQueue(Base):
    __tablename__ = "linkedin_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    lead_name = Column(String, default="")
    lead_email = Column(String, default="")
    linkedin_url = Column(String, default="")
    profile_title = Column(String, default="")
    company = Column(String, default="")
    industry = Column(String, default="")
    location = Column(String, default="")
    niche = Column(String, default="")
    message_template = Column(Text, default="")
    message_personalized = Column(Text, default="")
    status = Column(String, default="pending", index=True)
    connection_sent_at = Column(Float, nullable=True)
    message_sent_at = Column(Float, nullable=True)
    replied_at = Column(Float, nullable=True)
    notes = Column(Text, default="")
    source = Column(String, default="scout")
    created_at = Column(Float, default=None)
    updated_at = Column(Float, default=None)
