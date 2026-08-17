import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import log_audit
from app.core.exceptions import (
    AccountBlockedError,
    AccountDeletedError,
    IdentityAlreadyExistsError,
    InvalidCredentialsError,
    LinkTokenExpiredError,
    LinkTokenInvalidError,
    NotFoundError,
    TokenExpiredError,
    TokenInvalidError,
)
from app.core.redis import redis_client
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_link_token,
    generate_otp,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import (
    AccountLinkToken,
    BonusLedger,
    OutboxEvent,
    Trial,
    User,
    UserCredential,
    UserDevice,
    UserIdentity,
    UserSession,
)
from app.utils.normalizers import normalize_email, normalize_phone, normalize_telegram_id


# ══════════════════════════════════════════════
#  REGISTRATION
# ══════════════════════════════════════════════
async def register_user(
    db: AsyncSession,
    email: str | None,
    phone: str | None,
    password: str,
    registration_source: str,
    acquisition_source: str | None = None,
    referral_token: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str, str]:
    # Normalize identities
    normalized_email = normalize_email(email) if email else None
    normalized_phone = normalize_phone(phone) if phone else None

    # Check for existing identities
    if normalized_email:
        existing = await db.execute(
            select(UserIdentity).where(
                UserIdentity.type == "EMAIL",
                UserIdentity.normalized_value == normalized_email,
            )
        )
        if existing.scalar_one_or_none():
            raise IdentityAlreadyExistsError("email")

    if normalized_phone:
        existing = await db.execute(
            select(UserIdentity).where(
                UserIdentity.type == "PHONE",
                UserIdentity.normalized_value == normalized_phone,
            )
        )
        if existing.scalar_one_or_none():
            raise IdentityAlreadyExistsError("phone")

    # Create user
    user = User(
        status="ACTIVE",
        registration_source=registration_source,
        acquisition_source=acquisition_source,
    )
    db.add(user)
    await db.flush()

    # Create identities
    if normalized_email:
        identity = UserIdentity(
            user_id=user.id,
            type="EMAIL",
            normalized_value=normalized_email,
            verified=False,
        )
        db.add(identity)

    if normalized_phone:
        identity = UserIdentity(
            user_id=user.id,
            type="PHONE",
            normalized_value=normalized_phone,
            verified=False,
        )
        db.add(identity)

    # Create credentials
    credential = UserCredential(
        user_id=user.id,
        password_hash=hash_password(password),
    )
    db.add(credential)

    # Create session
    session = UserSession(
        user_id=user.id,
        refresh_token_hash="",  # Will be updated after token creation
        device_type=registration_source.lower(),
        ip_address=ip_address,
        user_agent=user_agent,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(session)
    await db.flush()

    # Create tokens
    access_token = create_access_token(user.id, source=registration_source)
    refresh_token = create_refresh_token(user.id, session.id)
    session.refresh_token_hash = hash_token(refresh_token)

    # Create outbox event
    event = OutboxEvent(
        event_type="USER_REGISTERED",
        user_id=user.id,
        source=registration_source,
        payload={
            "email": normalized_email,
            "phone": normalized_phone,
            "registration_source": registration_source,
            "acquisition_source": acquisition_source,
        },
    )
    db.add(event)

    # Audit
    await log_audit(db, "USER_REGISTERED", user.id, ip_address, user_agent)

    return user, access_token, refresh_token


# ══════════════════════════════════════════════
#  LOGIN
# ══════════════════════════════════════════════
async def login_user(
    db: AsyncSession,
    identity_value: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    device_type: str | None = None,
    client_version: str | None = None,
) -> tuple[User, str, str]:
    # Determine if email or phone
    normalized = normalize_email(identity_value)
    identity_type = "EMAIL"
    if "@" not in normalized:
        normalized = normalize_phone(identity_value)
        identity_type = "PHONE"

    # Find identity
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.type == identity_type,
            UserIdentity.normalized_value == normalized,
        )
    )
    identity = result.scalar_one_or_none()
    if not identity:
        raise InvalidCredentialsError()

    # Get user
    result = await db.execute(select(User).where(User.id == identity.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise InvalidCredentialsError()

    # Check status
    if user.status == "BLOCKED":
        raise AccountBlockedError()
    if user.status == "DELETED":
        raise AccountDeletedError()

    # Get credentials
    result = await db.execute(select(UserCredential).where(UserCredential.user_id == user.id))
    credential = result.scalar_one_or_none()
    if not credential:
        raise InvalidCredentialsError()

    # Check lock
    if credential.locked_until and credential.locked_until > datetime.now(timezone.utc):
        raise AccountBlockedError()

    # Verify password
    if not verify_password(password, credential.password_hash):
        credential.failed_login_attempts += 1
        if credential.failed_login_attempts >= 5:
            credential.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        await log_audit(db, "LOGIN_FAILED", user.id, ip_address, user_agent)
        raise InvalidCredentialsError()

    # Reset failed attempts on success
    credential.failed_login_attempts = 0
    credential.locked_until = None

    # Update last seen
    user.last_seen_at = datetime.now(timezone.utc)

    # Update identity last used
    identity.last_used_at = datetime.now(timezone.utc)

    # Create session
    session = UserSession(
        user_id=user.id,
        refresh_token_hash="",
        device_type=device_type,
        client_version=client_version,
        ip_address=ip_address,
        user_agent=user_agent,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(session)
    await db.flush()

    # Create tokens
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id, session.id)
    session.refresh_token_hash = hash_token(refresh_token)

    await log_audit(db, "LOGIN_SUCCESS", user.id, ip_address, user_agent)

    return user, access_token, refresh_token


# ══════════════════════════════════════════════
#  REFRESH TOKEN
# ══════════════════════════════════════════════
async def refresh_tokens(
    db: AsyncSession,
    refresh_token_str: str,
) -> tuple[User, str, str]:
    # Decode token
    try:
        payload = decode_token(refresh_token_str)
    except Exception:
        raise TokenInvalidError()

    if payload.get("type") != "refresh":
        raise TokenInvalidError()

    user_id = uuid.UUID(payload["sub"])
    session_id = uuid.UUID(payload["sid"])

    # Find session
    result = await db.execute(select(UserSession).where(UserSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session or session.revoked_at:
        raise TokenInvalidError()

    # Verify token hash
    if session.refresh_token_hash != hash_token(refresh_token_str):
        # Possible token reuse — revoke session
        session.revoked_at = datetime.now(timezone.utc)
        raise TokenInvalidError()

    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise TokenInvalidError()

    if user.status != "ACTIVE":
        raise AccountBlockedError()

    # Revoke old session
    session.revoked_at = datetime.now(timezone.utc)

    # Create new session (rotation)
    new_session = UserSession(
        user_id=user.id,
        refresh_token_hash="",
        device_type=session.device_type,
        client_version=session.client_version,
        ip_address=session.ip_address,
        user_agent=session.user_agent,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(new_session)
    await db.flush()

    # Create new tokens
    access_token = create_access_token(user.id)
    new_refresh_token = create_refresh_token(user.id, new_session.id)
    new_session.refresh_token_hash = hash_token(new_refresh_token)

    return user, access_token, new_refresh_token


# ══════════════════════════════════════════════
#  LOGOUT
# ══════════════════════════════════════════════
async def logout_user(db: AsyncSession, refresh_token_str: str) -> None:
    try:
        payload = decode_token(refresh_token_str)
    except Exception:
        return  # Already invalid, nothing to do

    session_id = uuid.UUID(payload.get("sid", ""))
    result = await db.execute(select(UserSession).where(UserSession.id == session_id))
    session = result.scalar_one_or_none()
    if session and not session.revoked_at:
        session.revoked_at = datetime.now(timezone.utc)


# ══════════════════════════════════════════════
#  OTP / VERIFICATION
# ══════════════════════════════════════════════
async def send_otp(
    email: str | None = None,
    phone: str | None = None,
) -> str:
    otp = generate_otp()
    key = f"otp:{normalize_email(email) if email else normalize_phone(phone)}"  # type: ignore[arg-type]
    await redis_client.setex(key, settings.OTP_TTL_SECONDS, otp)
    # In production: send via email/SMS provider
    # For development: OTP is stored in Redis and can be retrieved from logs
    return otp


async def verify_otp_and_mark(
    db: AsyncSession,
    identity_type: str,
    identity_value: str,
    otp: str,
) -> UserIdentity:
    normalized = normalize_email(identity_value) if identity_type == "EMAIL" else normalize_phone(identity_value)
    key = f"otp:{normalized}"
    stored_otp = await redis_client.get(key)

    if not stored_otp or stored_otp != otp:
        raise TokenInvalidError()

    # Find identity
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.type == identity_type,
            UserIdentity.normalized_value == normalized,
        )
    )
    identity = result.scalar_one_or_none()
    if not identity:
        raise NotFoundError("Identity")

    # Mark verified
    identity.verified = True
    identity.verified_at = datetime.now(timezone.utc)

    # Delete OTP
    await redis_client.delete(key)

    return identity


# ══════════════════════════════════════════════
#  PASSWORD RESET
# ══════════════════════════════════════════════
async def reset_password(
    db: AsyncSession,
    identity_type: str,
    identity_value: str,
    otp: str,
    new_password: str,
) -> None:
    normalized = normalize_email(identity_value) if identity_type == "EMAIL" else normalize_phone(identity_value)

    # Verify OTP
    key = f"otp:{normalized}"
    stored_otp = await redis_client.get(key)
    if not stored_otp or stored_otp != otp:
        raise TokenInvalidError()

    # Find identity
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.type == identity_type,
            UserIdentity.normalized_value == normalized,
        )
    )
    identity = result.scalar_one_or_none()
    if not identity:
        raise NotFoundError("Identity")

    # Update password
    result = await db.execute(select(UserCredential).where(UserCredential.user_id == identity.user_id))
    credential = result.scalar_one_or_none()
    if not credential:
        raise NotFoundError("Credentials")

    credential.password_hash = hash_password(new_password)
    credential.password_changed_at = datetime.now(timezone.utc)
    credential.failed_login_attempts = 0
    credential.locked_until = None

    # Revoke all sessions
    result = await db.execute(
        select(UserSession).where(
            UserSession.user_id == identity.user_id,
            UserSession.revoked_at.is_(None),
        )
    )
    sessions = result.scalars().all()
    for session in sessions:
        session.revoked_at = datetime.now(timezone.utc)

    # Delete OTP
    await redis_client.delete(key)

    await log_audit(db, "PASSWORD_CHANGED", identity.user_id)


# ══════════════════════════════════════════════
#  TELEGRAM LINKING
# ══════════════════════════════════════════════
async def create_telegram_link_token(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> tuple[str, str]:
    token = generate_link_token()
    token_hash = hash_token(token)

    link = AccountLinkToken(
        user_id=user_id,
        token_hash=token_hash,
        purpose="TELEGRAM_LINK",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.LINK_TOKEN_TTL_SECONDS),
    )
    db.add(link)
    await db.flush()

    deep_link = f"https://t.me/Lab51_avito_bot?start=link_{token}"

    return token, deep_link


async def confirm_telegram_link(
    db: AsyncSession,
    link_token: str,
    telegram_user_id: str,
    telegram_username: str | None = None,
    telegram_first_name: str | None = None,
    telegram_last_name: str | None = None,
) -> uuid.UUID:
    token_hash = hash_token(link_token)
    normalized_tg_id = normalize_telegram_id(telegram_user_id)

    # Find valid link token
    result = await db.execute(
        select(AccountLinkToken).where(
            AccountLinkToken.token_hash == token_hash,
            AccountLinkToken.purpose == "TELEGRAM_LINK",
            AccountLinkToken.used_at.is_(None),
        )
    )
    link = result.scalar_one_or_none()

    if not link:
        raise LinkTokenInvalidError()

    if link.expires_at < datetime.now(timezone.utc):
        raise LinkTokenExpiredError()

    # Check if Telegram ID already linked to another account
    existing = await db.execute(
        select(UserIdentity).where(
            UserIdentity.type == "TELEGRAM",
            UserIdentity.normalized_value == normalized_tg_id,
        )
    )
    if existing.scalar_one_or_none():
        raise IdentityAlreadyExistsError("telegram")

    # Create Telegram identity
    identity = UserIdentity(
        user_id=link.user_id,
        type="TELEGRAM",
        normalized_value=normalized_tg_id,
        verified=True,
        verified_at=datetime.now(timezone.utc),
        metadata_={
            "username": telegram_username,
            "first_name": telegram_first_name,
            "last_name": telegram_last_name,
        },
    )
    db.add(identity)

    # Mark link token as used
    link.used_at = datetime.now(timezone.utc)

    # Outbox event
    event = OutboxEvent(
        event_type="IDENTITY_LINKED",
        user_id=link.user_id,
        source="TELEGRAM",
        payload={"identity_type": "TELEGRAM", "telegram_user_id": normalized_tg_id},
    )
    db.add(event)

    await log_audit(db, "IDENTITY_LINKED", link.user_id, details={"type": "TELEGRAM"})

    return link.user_id


# ══════════════════════════════════════════════
#  TELEGRAM AUTH (find existing by telegram_id or phone)
# ══════════════════════════════════════════════
async def find_user_by_telegram(
    db: AsyncSession,
    telegram_user_id: str,
    phone: str | None = None,
) -> User | None:
    """Find existing user by Telegram ID or phone. Returns None if not found."""
    normalized_tg_id = normalize_telegram_id(telegram_user_id)

    # Scenario A: Telegram already linked
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.type == "TELEGRAM",
            UserIdentity.normalized_value == normalized_tg_id,
        )
    )
    identity = result.scalar_one_or_none()
    if identity:
        result = await db.execute(select(User).where(User.id == identity.user_id))
        return result.scalar_one_or_none()

    # Scenario B: Phone exists
    if phone is not None:
        normalized_phone = normalize_phone(phone)
        result = await db.execute(
            select(UserIdentity).where(
                UserIdentity.type == "PHONE",
                UserIdentity.normalized_value == normalized_phone,
            )
        )
        identity = result.scalar_one_or_none()
        if identity:
            result = await db.execute(select(User).where(User.id == identity.user_id))
            return result.scalar_one_or_none()

    return None


async def link_telegram_to_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    telegram_user_id: str,
    telegram_username: str | None = None,
    telegram_first_name: str | None = None,
    telegram_last_name: str | None = None,
) -> None:
    """Directly link Telegram identity to existing user (for Telegram bot flow)."""
    normalized_tg_id = normalize_telegram_id(telegram_user_id)

    # Check if already linked
    existing = await db.execute(
        select(UserIdentity).where(
            UserIdentity.type == "TELEGRAM",
            UserIdentity.normalized_value == normalized_tg_id,
        )
    )
    if existing.scalar_one_or_none():
        return  # Already linked

    identity = UserIdentity(
        user_id=user_id,
        type="TELEGRAM",
        normalized_value=normalized_tg_id,
        verified=True,
        verified_at=datetime.now(timezone.utc),
        metadata_={
            "username": telegram_username,
            "first_name": telegram_first_name,
            "last_name": telegram_last_name,
        },
    )
    db.add(identity)

    event = OutboxEvent(
        event_type="IDENTITY_LINKED",
        user_id=user_id,
        source="TELEGRAM",
        payload={"identity_type": "TELEGRAM", "telegram_user_id": normalized_tg_id},
    )
    db.add(event)

    await log_audit(db, "IDENTITY_LINKED", user_id, details={"type": "TELEGRAM"})


# ══════════════════════════════════════════════
#  SESSIONS MANAGEMENT
# ══════════════════════════════════════════════
async def list_sessions(
    db: AsyncSession,
    user_id: uuid.UUID,
    current_session_id: uuid.UUID | None = None,
) -> list[UserSession]:
    result = await db.execute(
        select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        ).order_by(UserSession.created_at.desc())
    )
    sessions = result.scalars().all()

    # Mark current session
    for s in sessions:
        if current_session_id and s.id == current_session_id:
            s.is_current = True  # type: ignore

    return sessions


async def revoke_session(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
    result = await db.execute(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise NotFoundError("Session")
    session.revoked_at = datetime.now(timezone.utc)