import time
from typing import Optional

from fastapi import Request

from app.config import settings
from app.core.exceptions import RateLimitExceededError
from app.core.redis import redis_client


async def check_rate_limit(
    key: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    """Check and enforce rate limit using Redis sliding window."""
    now = time.time()
    window_start = now - window_seconds

    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zcard(key)
    pipe.zadd(key, {str(now): now})
    pipe.expire(key, window_seconds + 1)
    _, current, _, _ = await pipe.execute()

    if current >= max_requests:
        oldest = await redis_client.zrange(key, 0, 0, withscores=True)
        retry_after = int(window_seconds - (now - oldest[0][1])) + 1 if oldest else window_seconds
        raise RateLimitExceededError(retry_after=max(retry_after, 1))


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit_login(ip: str) -> None:
    await check_rate_limit(
        f"rate:login:{ip}",
        settings.RATE_LIMIT_LOGIN_PER_IP,
        settings.RATE_LIMIT_LOGIN_WINDOW,
    )


async def rate_limit_register(ip: str) -> None:
    await check_rate_limit(
        f"rate:register:{ip}",
        settings.RATE_LIMIT_REGISTER_PER_IP,
        settings.RATE_LIMIT_REGISTER_WINDOW,
    )


async def rate_limit_otp(identity: str) -> None:
    await check_rate_limit(
        f"rate:otp:{identity}",
        settings.RATE_LIMIT_OTP_PER_IDENTITY,
        settings.RATE_LIMIT_OTP_WINDOW,
    )