from typing import Optional, List
from sqlalchemy import select, func
from app.repositories.base import BaseRepository
from app.models.campaign import Campaign, CampaignStep, CampaignEnrollment


class CampaignRepository(BaseRepository[Campaign]):
    def __init__(self, session):
        super().__init__(Campaign, session)

    def get_active(self) -> List[Campaign]:
        stmt = select(Campaign).where(Campaign.status == "active")
        return list(self.session.execute(stmt).scalars().all())

    def get_by_name(self, name: str) -> Optional[Campaign]:
        stmt = select(Campaign).where(Campaign.name == name)
        return self.session.execute(stmt).scalar_one_or_none()


class CampaignStepRepository(BaseRepository[CampaignStep]):
    def __init__(self, session):
        super().__init__(CampaignStep, session)

    def list_by_campaign(self, campaign_id: int) -> List[CampaignStep]:
        stmt = (
            select(CampaignStep)
            .where(CampaignStep.campaign_id == campaign_id)
            .order_by(CampaignStep.step_order)
        )
        return list(self.session.execute(stmt).scalars().all())


class CampaignEnrollmentRepository(BaseRepository[CampaignEnrollment]):
    def __init__(self, session):
        super().__init__(CampaignEnrollment, session)

    def list_by_campaign(self, campaign_id: int) -> List[CampaignEnrollment]:
        stmt = select(CampaignEnrollment).where(CampaignEnrollment.campaign_id == campaign_id)
        return list(self.session.execute(stmt).scalars().all())

    def list_by_lead(self, lead_id: int) -> List[CampaignEnrollment]:
        stmt = select(CampaignEnrollment).where(CampaignEnrollment.lead_id == lead_id)
        return list(self.session.execute(stmt).scalars().all())

    def get_enrollment(self, campaign_id: int, lead_id: int) -> Optional[CampaignEnrollment]:
        stmt = select(CampaignEnrollment).where(
            CampaignEnrollment.campaign_id == campaign_id,
            CampaignEnrollment.lead_id == lead_id,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def count_by_status(self, status: str) -> int:
        stmt = select(func.count()).select_from(CampaignEnrollment).where(
            CampaignEnrollment.status == status
        )
        result = self.session.execute(stmt).scalar()
        return result or 0

    def mark_completed(self, enrollment_id: int) -> None:
        enrollment = self.get(enrollment_id)
        if enrollment:
            enrollment.status = "completed"
            self.session.flush()
