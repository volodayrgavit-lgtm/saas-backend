from typing import Any


class AppError(Exception):
    """Base application error with error code contract."""

    def __init__(self, code: str, message: str, status_code: int = 400, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


# ── Auth Errors ──
class InvalidCredentialsError(AppError):
    def __init__(self):
        super().__init__("INVALID_CREDENTIALS", "Invalid email/phone or password", 401)


class AccountBlockedError(AppError):
    def __init__(self):
        super().__init__("ACCOUNT_BLOCKED", "Account is blocked", 403)


class AccountDeletedError(AppError):
    def __init__(self):
        super().__init__("ACCOUNT_DELETED", "Account has been deleted", 403)


class EmailNotVerifiedError(AppError):
    def __init__(self):
        super().__init__("EMAIL_NOT_VERIFIED", "Email is not verified", 403)


class PhoneNotVerifiedError(AppError):
    def __init__(self):
        super().__init__("PHONE_NOT_VERIFIED", "Phone is not verified", 403)


class IdentityAlreadyExistsError(AppError):
    def __init__(self, identity_type: str = ""):
        msg = f"Account with this {identity_type} already exists" if identity_type else "Account already exists"
        super().__init__("IDENTITY_ALREADY_EXISTS", msg, 409)


class LinkTokenExpiredError(AppError):
    def __init__(self):
        super().__init__("LINK_TOKEN_EXPIRED", "Link token has expired", 400)


class LinkTokenInvalidError(AppError):
    def __init__(self):
        super().__init__("LINK_TOKEN_INVALID", "Link token is invalid", 400)


class OnboardingRequiredError(AppError):
    def __init__(self):
        super().__init__("ONBOARDING_REQUIRED", "Onboarding not completed", 403)


class TrialAlreadyUsedError(AppError):
    def __init__(self):
        super().__init__("TRIAL_ALREADY_USED", "Trial has already been used", 409)


class EntitlementLimitReachedError(AppError):
    def __init__(self, entitlement: str = ""):
        msg = f"Limit reached: {entitlement}" if entitlement else "Entitlement limit reached"
        super().__init__("ENTITLEMENT_LIMIT_REACHED", msg, 403)


class TokenExpiredError(AppError):
    def __init__(self):
        super().__init__("TOKEN_EXPIRED", "Token has expired", 401)


class TokenInvalidError(AppError):
    def __init__(self):
        super().__init__("TOKEN_INVALID", "Token is invalid", 401)


class RateLimitExceededError(AppError):
    def __init__(self, retry_after: int = 60):
        super().__init__(
            "RATE_LIMIT_EXCEEDED",
            f"Too many requests. Retry after {retry_after} seconds",
            429,
            {"retry_after": retry_after},
        )


class NotFoundError(AppError):
    def __init__(self, resource: str = "Resource"):
        super().__init__("NOT_FOUND", f"{resource} not found", 404)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__("FORBIDDEN", message, 403)