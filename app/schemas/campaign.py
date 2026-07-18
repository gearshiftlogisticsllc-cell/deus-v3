"""
app/schemas/campaign.py — Pydantic v2 Schemas for Campaign, CampaignStep & CampaignEnrollment
=============================================================================================
"""

from typing import Optional
from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    status: Optional[str] = "draft"


class CampaignRead(BaseModel):
    id: int
    name: str
    description: Optional[str] = ""
    status: Optional[str] = "draft"
    created_at: Optional[float] = None
    updated_at: Optional[float] = None

    class Config:
        from_attributes = True


class CampaignStepCreate(BaseModel):
    campaign_id: int
    step_order: int
    day_offset: int = 0
    subject_template: Optional[str] = ""
    body_template: Optional[str] = ""
    channel: Optional[str] = "email"
    is_ai_generated: Optional[bool] = False


class CampaignStepRead(BaseModel):
    id: int
    campaign_id: int
    step_order: int
    day_offset: int = 0
    subject_template: Optional[str] = ""
    body_template: Optional[str] = ""
    channel: Optional[str] = "email"
    is_ai_generated: Optional[bool] = False

    class Config:
        from_attributes = True


class CampaignEnrollmentCreate(BaseModel):
    campaign_id: int
    lead_id: int
    current_step: Optional[int] = 0
    status: Optional[str] = "active"


class CampaignEnrollmentRead(BaseModel):
    id: int
    campaign_id: int
    lead_id: int
    current_step: Optional[int] = 0
    status: Optional[str] = "active"
    enrolled_at: Optional[float] = None
    last_sent_at: Optional[float] = None
    completed_at: Optional[float] = None

    class Config:
        from_attributes = True
