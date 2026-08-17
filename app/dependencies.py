import uuid
from typing import Optional

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AccountBlockedError, TokenExpiredError, TokenInvalidError
from app.database import get_db
from app.models import User


async def get_current_user_id(
    authorization: Optional[str] = Header(None),
) -> Optional[uuid.UUID]:
    """Extract user_id from Bearer token. Returns None if no token."""
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return uuid.UUID(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError()
    except Exception:
        raise TokenInvalidError()


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    user_id: Optional[uuid.UUID] = Depends(get_current_user_id),
) -> User:
    """Get current user from token. Raises if not authenticated."""
    if not user_id:
        raise TokenInvalidError()

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise TokenInvalidError()

    if user.status == "BLOCKED":
        raise AccountBlockedError()

    return user


def get_client_info(request: Request) -> tuple[str, str]:
    """Extract client IP and user agent from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    ua = request.headers.get("User-Agent", "")
    return ip, ua