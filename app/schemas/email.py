"""
app/schemas/email.py — Pydantic v2 Schemas for EmailLog & EmailEvent
====================================================================
"""

from typing import Optional
from pydantic import BaseModel, Field


class EmailLogCreate(BaseModel):
    lead_email: str
    lead_name: Optional[str] = ""
    subject: Optional[str] = ""
    status: Optional[str] = "pending"
    channel: Optional[str] = "email"
    agent: Optional[str] = ""
    smtp_profile: Optional[str] = ""


class EmailLogRead(BaseModel):
    id: int
    lead_email: str
    lead_name: Optional[str] = ""
    subject: Optional[str] = ""
    status: Optional[str] = "pending"
    channel: Optional[str] = "email"
    agent: Optional[str] = ""
    message_id: Optional[str] = ""
    opened_at: Optional[float] = None
    replied_at: Optional[float] = None
    bounced_at: Optional[float] = None
    bounce_reason: Optional[str] = ""
    complained_at: Optional[float] = None
    sent_at: Optional[float] = None
    smtp_profile: Optional[str] = ""

    class Config:
        from_attributes = True


class EmailLogFilter(BaseModel):
    status: Optional[str] = None
    channel: Optional[str] = None
    limit: int = 500
