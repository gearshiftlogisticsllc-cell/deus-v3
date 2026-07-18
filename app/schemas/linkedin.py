"""
app/schemas/linkedin.py — Pydantic v2 Schemas for LinkedInQueue
===============================================================
"""

from typing import Optional
from pydantic import BaseModel, Field


class LinkedInQueueCreate(BaseModel):
    lead_id: int
    lead_name: Optional[str] = ""
    lead_email: Optional[str] = ""
    linkedin_url: Optional[str] = ""
    profile_title: Optional[str] = ""
    company: Optional[str] = ""
    industry: Optional[str] = ""
    location: Optional[str] = ""
    niche: Optional[str] = ""
    message_template: Optional[str] = ""
    message_personalized: Optional[str] = ""
    status: Optional[str] = "pending"
    notes: Optional[str] = ""
    source: Optional[str] = ""


class LinkedInQueueRead(BaseModel):
    id: int
    lead_id: int
    lead_name: Optional[str] = ""
    lead_email: Optional[str] = ""
    linkedin_url: Optional[str] = ""
    profile_title: Optional[str] = ""
    company: Optional[str] = ""
    industry: Optional[str] = ""
    location: Optional[str] = ""
    niche: Optional[str] = ""
    message_template: Optional[str] = ""
    message_personalized: Optional[str] = ""
    status: Optional[str] = "pending"
    connection_sent_at: Optional[float] = None
    message_sent_at: Optional[float] = None
    replied_at: Optional[float] = None
    notes: Optional[str] = ""
    source: Optional[str] = ""
    created_at: Optional[float] = None
    updated_at: Optional[float] = None

    class Config:
        from_attributes = True


class LinkedInQueueUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    message_personalized: Optional[str] = None
    connection_sent_at: Optional[float] = None
    message_sent_at: Optional[float] = None
    replied_at: Optional[float] = None


class LinkedInQueueFilter(BaseModel):
    status: Optional[str] = None
    limit: int = 500
