"""
app/schemas/lead.py — Pydantic v2 Schemas for Lead
===================================================
"""

from typing import Optional
from pydantic import BaseModel, Field


class LeadCreate(BaseModel):
    business_name: Optional[str] = ""
    owner_name: Optional[str] = ""
    business_email: Optional[str] = ""
    phone: Optional[str] = ""
    website: Optional[str] = ""
    address: Optional[str] = ""
    niche: Optional[str] = ""
    category: Optional[str] = ""
    services_offered: Optional[str] = ""
    linkedin_url: Optional[str] = ""
    instagram_handle: Optional[str] = ""
    facebook_url: Optional[str] = ""
    source: Optional[str] = "import"
    status: Optional[str] = "new"
    outreach_ready: Optional[int] = 0
    needs_human: Optional[int] = 0
    needs_human_reason: Optional[str] = ""
    preferred_channel: Optional[str] = "email"
    score: Optional[int] = 0
    notes: Optional[str] = ""
    lead_type: Optional[str] = "cold"
    import_batch_id: Optional[str] = ""
    import_filename: Optional[str] = ""


class LeadUpdate(BaseModel):
    business_name: Optional[str] = None
    owner_name: Optional[str] = None
    business_email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    niche: Optional[str] = None
    category: Optional[str] = None
    services_offered: Optional[str] = None
    linkedin_url: Optional[str] = None
    instagram_handle: Optional[str] = None
    facebook_url: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    outreach_ready: Optional[int] = None
    needs_human: Optional[int] = None
    needs_human_reason: Optional[str] = None
    channel_used: Optional[str] = None
    preferred_channel: Optional[str] = None
    score: Optional[int] = None
    notes: Optional[str] = None
    lead_type: Optional[str] = None
    email_verified: Optional[int] = None
    verification_method: Optional[str] = None
    first_contacted_at: Optional[float] = None
    last_contacted_at: Optional[float] = None
    contact_count: Optional[int] = None


class LeadRead(BaseModel):
    id: int
    business_name: Optional[str] = ""
    owner_name: Optional[str] = ""
    business_email: Optional[str] = ""
    phone: Optional[str] = ""
    website: Optional[str] = ""
    address: Optional[str] = ""
    niche: Optional[str] = ""
    category: Optional[str] = ""
    services_offered: Optional[str] = ""
    linkedin_url: Optional[str] = ""
    instagram_handle: Optional[str] = ""
    facebook_url: Optional[str] = ""
    source: Optional[str] = "unknown"
    status: Optional[str] = "new"
    outreach_ready: Optional[int] = 0
    needs_human: Optional[int] = 0
    needs_human_reason: Optional[str] = ""
    channel_used: Optional[str] = ""
    preferred_channel: Optional[str] = "email"
    score: Optional[int] = 0
    notes: Optional[str] = ""
    first_contacted_at: Optional[float] = None
    last_contacted_at: Optional[float] = None
    contact_count: Optional[int] = 0
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    lead_type: Optional[str] = "cold"
    import_batch_id: Optional[str] = ""
    import_filename: Optional[str] = ""
    email_verified: Optional[int] = 0
    email_verified_at: Optional[float] = None
    verification_method: Optional[str] = ""

    class Config:
        from_attributes = True


class LeadFilter(BaseModel):
    status: Optional[str] = None
    outreach_ready: Optional[bool] = None
    has_email: Optional[bool] = None
    lead_type: Optional[str] = None
    limit: int = 500
    offset: int = 0
