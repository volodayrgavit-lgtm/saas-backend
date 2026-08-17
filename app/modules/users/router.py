import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import log_audit
from app.core.exceptions import (
    NotFoundError,
    TrialAlreadyUsedError,
    OnboardingRequiredError,
)
from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    BonusLedger,
    Category,
    Entitlement,
    OutboxEvent,
    Plan,
    Product,
    Subscription,
    Trial,
    User,
    UserDevice,
    UserIdentity,
)
from app.utils.normalizers import normalize_avito_url

router = APIRouter(prefix=settings.API_V1_PREFIX + "/me", tags=["me"])


# ── Schemas ──
class UserProfileResponse(BaseModel):
    id: uuid.UUID
    status: str
    display_name: Optional[str]
    avito_profile_url: Optional[str]
    avito_id: Optional[str]
    primary_category_id: Optional[uuid.UUID]
    onboarding_completed: bool
    registration_source: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    avito_profile_url: Optional[str] = None
    primary_category_id: Optional[uuid.UUID] = None


class IdentityResponse(BaseModel):
    id: uuid.UUID
    type: str
    normalized_value: str
    verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class BonusResponse(BaseModel):
    total: int
    entries: list[dict]


class TrialResponse(BaseModel):
    id: uuid.UUID
    status: str
    started_at: datetime
    expires_at: datetime
    source: Optional[str]

    model_config = {"from_attributes": True}


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    plan_id: uuid.UUID
    status: str
    started_at: datetime
    expires_at: Optional[datetime]
    auto_renew: bool

    model_config = {"from_attributes": True}


class EntitlementsResponse(BaseModel):
    entitlements: dict


class DeviceResponse(BaseModel):
    id: uuid.UUID
    device_type: Optional[str]
    browser: Optional[str]
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════
#  GET /v1/me
# ══════════════════════════════════════════════
@router.get("", response_model=UserProfileResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user


# ══════════════════════════════════════════════
#  PATCH /v1/me
# ══════════════════════════════════════════════
@router.patch("", response_model=UserProfileResponse)
async def update_me(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.display_name is not None:
        user.display_name = body.display_name

    if body.avito_profile_url is not None:
        original, normalized, avito_id = normalize_avito_url(body.avito_profile_url)
        user.avito_profile_url = original
        user.avito_profile_normalized = normalized
        user.avito_id = avito_id

    if body.primary_category_id is not None:
        user.primary_category_id = body.primary_category_id

    # Check if onboarding is now complete
    if user.display_name and user.avito_profile_url and user.primary_category_id:
        if not user.onboarding_completed:
            user.onboarding_completed = True
            user.onboarding_completed_at = datetime.now(timezone.utc)

            # Grant +300 bonus (idempotent via unique constraint)
            try:
                bonus = BonusLedger(
                    user_id=user.id,
                    type="ONBOARDING_COMPLETED",
                    amount=300,
                    reason="Onboarding completed bonus",
                )
                db.add(bonus)
                await db.flush()

                event = OutboxEvent(
                    event_type="BONUS_GRANTED",
                    user_id=user.id,
                    source=user.registration_source,
                    payload={"amount": 300, "type": "ONBOARDING_COMPLETED"},
                )
                db.add(event)
            except Exception:
                await db.rollback()
                # Bonus already granted — ignore

            # Start trial
            existing_trial = await db.execute(
                select(Trial).where(Trial.user_id == user.id)
            )
            if not existing_trial.scalar_one_or_none():
                trial = Trial(
                    user_id=user.id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                    source=user.registration_source,
                )
                db.add(trial)

                event = OutboxEvent(
                    event_type="TRIAL_STARTED",
                    user_id=user.id,
                    source=user.registration_source,
                    payload={"duration_days": 7},
                )
                db.add(event)

            event = OutboxEvent(
                event_type="ONBOARDING_COMPLETED",
                user_id=user.id,
                source=user.registration_source,
            )
            db.add(event)

            await log_audit(db, "ONBOARDING_COMPLETED", user.id)

    await db.flush()
    return user


# ══════════════════════════════════════════════
#  GET /v1/me/identities
# ══════════════════════════════════════════════
@router.get("/identities", response_model=list[IdentityResponse])
async def get_identities(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserIdentity).where(UserIdentity.user_id == user.id)
    )
    return result.scalars().all()


# ══════════════════════════════════════════════
#  GET /v1/me/bonus
# ══════════════════════════════════════════════
@router.get("/bonus", response_model=BonusResponse)
async def get_bonus(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BonusLedger).where(BonusLedger.user_id == user.id).order_by(BonusLedger.created_at.desc())
    )
    entries = result.scalars().all()
    total = sum(e.amount for e in entries)

    return BonusResponse(
        total=total,
        entries=[
            {
                "id": str(e.id),
                "type": e.type,
                "amount": e.amount,
                "reason": e.reason,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
    )


# ══════════════════════════════════════════════
#  GET /v1/me/subscriptions
# ══════════════════════════════════════════════
@router.get("/subscriptions", response_model=list[SubscriptionResponse])
async def get_subscriptions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    return result.scalars().all()


# ══════════════════════════════════════════════
#  GET /v1/me/trials
# ══════════════════════════════════════════════
@router.get("/trials", response_model=list[TrialResponse])
async def get_trials(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Trial).where(Trial.user_id == user.id)
    )
    return result.scalars().all()


# ══════════════════════════════════════════════
#  GET /v1/me/entitlements
# ══════════════════════════════════════════════
@router.get("/entitlements", response_model=EntitlementsResponse)
async def get_entitlements(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Find active subscription
    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.status == "ACTIVE",
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        # Check trial
        result = await db.execute(
            select(Trial).where(
                Trial.user_id == user.id,
                Trial.status == "ACTIVE",
                Trial.expires_at > datetime.now(timezone.utc),
            )
        )
        trial = result.scalar_one_or_none()
        if trial:
            # Return trial entitlements (default)
            return EntitlementsResponse(
                entitlements={
                    "max_searches": 3,
                    "regions": "BASIC",
                    "telegram_bot": True,
                    "extension": True,
                    "voice": False,
                    "devices": 1,
                    "favorites": 50,
                    "boost_daily": 0,
                }
            )
        return EntitlementsResponse(entitlements={})

    # Get plan entitlements
    result = await db.execute(
        select(Entitlement).where(Entitlement.plan_id == subscription.plan_id)
    )
    entitlements = result.scalars().all()

    import json
    return EntitlementsResponse(
        entitlements={e.key: json.loads(e.value) if e.value else e.value for e in entitlements}
    )


# ══════════════════════════════════════════════
#  GET /v1/me/devices
# ══════════════════════════════════════════════
@router.get("/devices", response_model=list[DeviceResponse])
async def get_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserDevice).where(
            UserDevice.user_id == user.id,
            UserDevice.revoked_at.is_(None),
        )
    )
    return result.scalars().all()