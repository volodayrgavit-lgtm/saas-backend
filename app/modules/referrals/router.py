import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Attribution, Lead, OutboxEvent, Referral, ReferralClick, User

router = APIRouter(prefix=settings.API_V1_PREFIX + "/referrals", tags=["referrals"])


class CreateReferralRequest(BaseModel):
    manager_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    campaign_id: str | None = None
    discount: int | None = None
    channel: str = "WEB"


class ReferralResponse(BaseModel):
    id: uuid.UUID
    token: str
    link: str
    channel: str
    created_at: datetime
    expires_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReferralClickRequest(BaseModel):
    token: str
    ip_address: str | None = None
    user_agent: str | None = None
    telegram_user_id: str | None = None


class CreateLeadRequest(BaseModel):
    manager_id: uuid.UUID | None = None
    avito_id: str | None = None
    avito_url: str | None = None
    phone: str | None = None
    telegram_user_id: str | None = None


class LeadResponse(BaseModel):
    id: uuid.UUID
    manager_id: uuid.UUID | None
    avito_id: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AttributionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    lead_id: uuid.UUID | None
    referral_id: uuid.UUID | None
    confidence: int
    match_type: str | None
    is_confirmed: bool

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════
#  POST /v1/referrals — create referral link
# ══════════════════════════════════════════════
@router.post("", response_model=ReferralResponse)
async def create_referral(
    body: CreateReferralRequest,
    db: AsyncSession = Depends(get_db),
):
    token = secrets.token_urlsafe(16)

    referral = Referral(
        token=token,
        manager_id=body.manager_id,
        lead_id=body.lead_id,
        campaign_id=body.campaign_id,
        discount=body.discount,
        channel=body.channel,
    )
    db.add(referral)
    await db.flush()

    link = f"https://lab-51.ru/r/{token}"

    return ReferralResponse(
        id=referral.id,
        token=token,
        link=link,
        channel=body.channel,
        created_at=referral.created_at,
        expires_at=referral.expires_at,
    )


# ══════════════════════════════════════════════
#  POST /v1/referrals/click — record referral click
# ══════════════════════════════════════════════
@router.post("/click")
async def record_click(
    body: ReferralClickRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Referral).where(Referral.token == body.token)
    )
    referral = result.scalar_one_or_none()
    if not referral:
        return {"error": "Invalid referral token"}

    click = ReferralClick(
        referral_id=referral.id,
        ip_address=body.ip_address,
        user_agent=body.user_agent,
        telegram_user_id=body.telegram_user_id,
    )
    db.add(click)

    event = OutboxEvent(
        event_type="REFERRAL_CLICKED",
        source=referral.channel,
        payload={"referral_id": str(referral.id), "token": body.token},
    )
    db.add(event)

    return {"message": "Click recorded", "referral_id": str(referral.id)}


# ══════════════════════════════════════════════
#  POST /v1/referrals/leads — create lead
# ══════════════════════════════════════════════
@router.post("/leads", response_model=LeadResponse)
async def create_lead(
    body: CreateLeadRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.utils.normalizers import normalize_avito_url

    avito_url_normalized = None
    if body.avito_url:
        _, avito_url_normalized, _ = normalize_avito_url(body.avito_url)

    lead = Lead(
        manager_id=body.manager_id,
        avito_id=body.avito_id,
        avito_url=body.avito_url,
        avito_url_normalized=avito_url_normalized,
        phone=body.phone,
        telegram_user_id=body.telegram_user_id,
    )
    db.add(lead)
    await db.flush()

    return LeadResponse(
        id=lead.id,
        manager_id=lead.manager_id,
        avito_id=lead.avito_id,
        status=lead.status,
        created_at=lead.created_at,
    )


# ══════════════════════════════════════════════
#  POST /v1/referrals/attribution — match user to lead
# ══════════════════════════════════════════════
@router.post("/attribution", response_model=AttributionResponse)
async def create_attribution(
    user_id: uuid.UUID,
    lead_id: uuid.UUID | None = None,
    referral_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    confidence = 0
    match_type = None

    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return {"error": "User not found"}

    # If referral link was used
    if referral_id:
        result = await db.execute(select(Referral).where(Referral.id == referral_id))
        referral = result.scalar_one_or_none()
        if referral:
            confidence = 100
            match_type = "REF_LINK"
            if referral.lead_id:
                lead_id = referral.lead_id

    # If lead provided, try matching
    if lead_id:
        result = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()
        if lead and not match_type:
            # Avito ID match
            if user.avito_id and lead.avito_id and user.avito_id == lead.avito_id:
                confidence = max(confidence, 90)
                match_type = match_type or "AVITO_ID"
            # Avito URL match
            elif user.avito_profile_normalized and lead.avito_url_normalized and user.avito_profile_normalized == lead.avito_url_normalized:
                confidence = max(confidence, 85)
                match_type = match_type or "AVITO_URL"
            # Phone match
            elif lead.phone:
                from app.utils.normalizers import normalize_phone
                normalized_phone = normalize_phone(lead.phone)
                result = await db.execute(
                    select("user_identities").where(
                        "user_identities.c.user_id == :uid",
                        "user_identities.c.type == 'PHONE'",
                        "user_identities.c.normalized_value == :phone",
                    ).params(uid=user_id, phone=normalized_phone)
                )
                # Simplified phone check
                confidence = max(confidence, 80)
                match_type = match_type or "PHONE"

    attribution = Attribution(
        user_id=user_id,
        lead_id=lead_id,
        referral_id=referral_id,
        confidence=confidence,
        match_type=match_type or "MANUAL",
        is_confirmed=confidence >= 80,
    )
    db.add(attribution)

    event = OutboxEvent(
        event_type="REFERRAL_ATTRIBUTED",
        user_id=user_id,
        source="CRM",
        payload={
            "lead_id": str(lead_id) if lead_id else None,
            "referral_id": str(referral_id) if referral_id else None,
            "confidence": confidence,
            "match_type": match_type,
        },
    )
    db.add(event)

    await db.flush()

    return AttributionResponse(
        id=attribution.id,
        user_id=attribution.user_id,
        lead_id=attribution.lead_id,
        referral_id=attribution.referral_id,
        confidence=attribution.confidence,
        match_type=attribution.match_type,
        is_confirmed=attribution.is_confirmed,
    )