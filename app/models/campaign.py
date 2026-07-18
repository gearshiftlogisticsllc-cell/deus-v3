from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from app.db import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    status = Column(String, default="active")
    created_at = Column(Float, default=None)
    updated_at = Column(Float, default=None)


class CampaignStep(Base):
    __tablename__ = "campaign_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    step_order = Column(Integer, nullable=False)
    day_offset = Column(Integer, default=0)
    subject_template = Column(Text, default="")
    body_template = Column(Text, default="")
    channel = Column(String, default="email")
    is_ai_generated = Column(Integer, default=1)


class CampaignEnrollment(Base):
    __tablename__ = "campaign_enrollments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    current_step = Column(Integer, default=0)
    status = Column(String, default="active", index=True)
    enrolled_at = Column(Float, default=None)
    last_sent_at = Column(Float, nullable=True)
    completed_at = Column(Float, nullable=True)
