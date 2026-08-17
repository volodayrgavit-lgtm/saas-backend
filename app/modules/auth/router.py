import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppError
from app.core.rate_limit import get_client_ip, rate_limit_login, rate_limit_register, rate_limit_otp
from app.database import get_db
from app.dependencies import get_current_user, get_client_info
from app.models import User
from app.modules.auth import schemas
from app.modules.auth.service import (
    register_user,
    login_user,
    refresh_tokens,
    logout_user,
    send_otp,
    verify_otp_and_mark,
    reset_password,
    create_telegram_link_token,
    confirm_telegram_link,
    find_user_by_telegram,
    link_telegram_to_user,
    list_sessions,
    revoke_session,
)

router = APIRouter(prefix=settings.API_V1_PREFIX + "/auth", tags=["auth"])


# ── Error handler helper ──
def error_response(exc: AppError) -> dict:
    return {"error": {"code": exc.code, "message": exc.message, **exc.details}}


# ══════════════════════════════════════════════
#  POST /v1/auth/register
# ══════════════════════════════════════════════
@router.post("/register", response_model=schemas.RegisterResponse)
async def register(
    body: schemas.RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = get_client_ip(request)
    await rate_limit_register(ip)

    user, access_token, refresh_token = await register_user(
        db=db,
        email=body.email,
        phone=body.phone,
        password=body.password,
        registration_source=body.registration_source,
        acquisition_source=body.acquisition_source,
        referral_token=body.referral_token,
        ip_address=ip,
        user_agent=request.headers.get("User-Agent"),
    )

    return schemas.RegisterResponse(
        user_id=user.id,
        access_token=access_token,
        refresh_token=refresh_token,
        onboarding_required=not user.onboarding_completed,
    )


# ══════════════════════════════════════════════
#  POST /v1/auth/login
# ══════════════════════════════════════════════
@router.post("/login", response_model=schemas.LoginResponse)
async def login(
    body: schemas.LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = get_client_ip(request)
    await rate_limit_login(ip)

    device_type = request.headers.get("X-LAB51-Client", "web")
    client_version = request.headers.get("X-LAB51-Version")

    user, access_token, refresh_token = await login_user(
        db=db,
        identity_value=body.identity,
        password=body.password,
        ip_address=ip,
        user_agent=request.headers.get("User-Agent"),
        device_type=device_type,
        client_version=client_version,
    )

    return schemas.LoginResponse(
        user_id=user.id,
        access_token=access_token,
        refresh_token=refresh_token,
        onboarding_completed=user.onboarding_completed,
    )


# ══════════════════════════════════════════════
#  POST /v1/auth/refresh
# ══════════════════════════════════════════════
@router.post("/refresh", response_model=schemas.RefreshResponse)
async def refresh(
    body: schemas.RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    user, access_token, new_refresh_token = await refresh_tokens(db, body.refresh_token)
    return schemas.RefreshResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )


# ══════════════════════════════════════════════
#  POST /v1/auth/logout
# ══════════════════════════════════════════════
@router.post("/logout")
async def logout(
    body: schemas.LogoutRequest,
    db: AsyncSession = Depends(get_db),
):
    await logout_user(db, body.refresh_token)
    return {"message": "Logged out"}


# ══════════════════════════════════════════════
#  POST /v1/auth/verify-email
# ══════════════════════════════════════════════
@router.post("/verify-email")
async def verify_email(
    body: schemas.VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    await verify_otp_and_mark(db, "EMAIL", body.email, body.otp)
    return {"message": "Email verified"}


# ══════════════════════════════════════════════
#  POST /v1/auth/verify-phone
# ══════════════════════════════════════════════
@router.post("/verify-phone")
async def verify_phone(
    body: schemas.VerifyPhoneRequest,
    db: AsyncSession = Depends(get_db),
):
    await verify_otp_and_mark(db, "PHONE", body.phone, body.otp)
    return {"message": "Phone verified"}


# ══════════════════════════════════════════════
#  POST /v1/auth/send-otp
# ══════════════════════════════════════════════
@router.post("/send-otp", response_model=schemas.OtpResponse)
async def send_otp_endpoint(
    body: schemas.SendOtpRequest,
    request: Request,
):
    identity = body.email or body.phone
    assert identity is not None  # validated by schema
    await rate_limit_otp(identity)

    otp = await send_otp(email=body.email, phone=body.phone)

    # In development, return OTP in response (remove in production!)
    return schemas.OtpResponse(
        message=f"OTP sent. [DEV] OTP: {otp}",
        expires_in=settings.OTP_TTL_SECONDS,
    )


# ══════════════════════════════════════════════
#  POST /v1/auth/password/forgot
# ══════════════════════════════════════════════
@router.post("/password/forgot", response_model=schemas.OtpResponse)
async def forgot_password(
    body: schemas.ForgotPasswordRequest,
    request: Request,
):
    identity = body.email or body.phone
    assert identity is not None  # validated by schema
    await rate_limit_otp(identity)

    otp = await send_otp(email=body.email, phone=body.phone)

    return schemas.OtpResponse(
        message=f"Password reset OTP sent. [DEV] OTP: {otp}",
        expires_in=settings.OTP_TTL_SECONDS,
    )


# ══════════════════════════════════════════════
#  POST /v1/auth/password/reset
# ══════════════════════════════════════════════
@router.post("/password/reset")
async def password_reset(
    body: schemas.ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    identity_type = "EMAIL" if body.email else "PHONE"
    identity_value = body.email or body.phone
    assert identity_value is not None  # validated by schema

    await reset_password(db, identity_type, identity_value, body.otp, body.new_password)
    return {"message": "Password reset successfully"}


# ══════════════════════════════════════════════
#  POST /v1/auth/link/telegram/create
# ══════════════════════════════════════════════
@router.post("/link/telegram/create", response_model=schemas.TelegramLinkCreateResponse)
async def create_telegram_link(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    token, deep_link = await create_telegram_link_token(db, user.id)
    return schemas.TelegramLinkCreateResponse(
        link_token=token,
        deep_link=deep_link,
        expires_in=settings.LINK_TOKEN_TTL_SECONDS,
    )


# ══════════════════════════════════════════════
#  POST /v1/auth/link/telegram/confirm
# ══════════════════════════════════════════════
@router.post("/link/telegram/confirm", response_model=schemas.TelegramLinkConfirmResponse)
async def confirm_telegram_link_endpoint(
    body: schemas.TelegramLinkConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    user_id = await confirm_telegram_link(
        db=db,
        link_token=body.link_token,
        telegram_user_id=body.telegram_user_id,
        telegram_username=body.telegram_username,
        telegram_first_name=body.telegram_first_name,
        telegram_last_name=body.telegram_last_name,
    )
    return schemas.TelegramLinkConfirmResponse(user_id=user_id)


# ══════════════════════════════════════════════
#  POST /v1/auth/telegram/find — for Telegram bot
# ══════════════════════════════════════════════
@router.post("/telegram/find")
async def telegram_find_user(
    telegram_user_id: str,
    phone: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Find existing user by Telegram ID or phone. Used by Telegram bot on /start."""
    user = await find_user_by_telegram(db, telegram_user_id, phone)
    if user:
        return {
            "found": True,
            "user_id": str(user.id),
            "scenario": "A" if any(i.type == "TELEGRAM" for i in user.identities) else "B",
        }
    return {"found": False, "scenario": "C"}


# ══════════════════════════════════════════════
#  POST /v1/auth/telegram/link — direct link for bot
# ══════════════════════════════════════════════
@router.post("/telegram/link")
async def telegram_direct_link(
    user_id: uuid.UUID,
    telegram_user_id: str,
    telegram_username: str | None = None,
    telegram_first_name: str | None = None,
    telegram_last_name: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Directly link Telegram identity to user. Used by Telegram bot after confirmation."""
    await link_telegram_to_user(
        db=db,
        user_id=user_id,
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        telegram_first_name=telegram_first_name,
        telegram_last_name=telegram_last_name,
    )
    return {"message": "Telegram linked"}


# ══════════════════════════════════════════════
#  GET /v1/auth/sessions
# ══════════════════════════════════════════════
@router.get("/sessions", response_model=schemas.SessionListResponse)
async def get_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions = await list_sessions(db, user.id)
    return schemas.SessionListResponse(
        sessions=[
            schemas.SessionResponse(
                id=s.id,
                device_type=s.device_type,
                client_version=s.client_version,
                ip_address=s.ip_address,
                created_at=s.created_at,
                expires_at=s.expires_at,
                is_current=getattr(s, "is_current", False),
            )
            for s in sessions
        ]
    )


# ══════════════════════════════════════════════
#  DELETE /v1/auth/sessions/:id
# ══════════════════════════════════════════════
@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await revoke_session(db, user.id, session_id)
    return {"message": "Session revoked"}