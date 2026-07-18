from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from app.db import Base


class CampaignCalendar(Base):
    __tablename__ = "campaign_calendar"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    scheduled_date = Column(String, nullable=False)
    lead_source = Column(String, default="all")
    template_html = Column(Text, default="")
    template_text = Column(Text, default="")
    subject_template = Column(Text, default="")
    interval_days = Column(Integer, default=1)
    active = Column(Integer, default=1)
    created_at = Column(Float, default=None)
