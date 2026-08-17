import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, model_validator


# ── Register ──
class RegisterRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str = Field(min_length=8, max_length=128)
    registration_source: str = Field(default="WEB")  # WEB / TELEGRAM / EXTENSION
    acquisition_source: Optional[str] = None
    referral_token: Optional[str] = None

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.email and not self.phone:
            raise ValueError("Either email or phone must be provided")
        return self


class RegisterResponse(BaseModel):
    user_id: uuid.UUID
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    onboarding_required: bool = True


# ── Login ──
class LoginRequest(BaseModel):
    identity: str = Field(description="Email or phone number")
    password: str


class LoginResponse(BaseModel):
    user_id: uuid.UUID
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    onboarding_completed: bool = False


# ── Refresh ──
class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ── Logout ──
class LogoutRequest(BaseModel):
    refresh_token: str


# ── Verification ──
class VerifyEmailRequest(BaseModel):
    email: EmailStr
    otp: str


class VerifyPhoneRequest(BaseModel):
    phone: str
    otp: str


class SendOtpRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.email and not self.phone:
            raise ValueError("Either email or phone must be provided")
        return self


class OtpResponse(BaseModel):
    message: str = "OTP sent"
    expires_in: int


# ── Password Reset ──
class ForgotPasswordRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.email and not self.phone:
            raise ValueError("Either email or phone must be provided")
        return self


class ResetPasswordRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    otp: str
    new_password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.email and not self.phone:
            raise ValueError("Either email or phone must be provided")
        return self


# ── Telegram Link ──
class TelegramLinkCreateRequest(BaseModel):
    pass  # user is determined from auth token


class TelegramLinkCreateResponse(BaseModel):
    link_token: str
    deep_link: str
    expires_in: int


class TelegramLinkConfirmRequest(BaseModel):
    link_token: str
    telegram_user_id: str
    telegram_username: Optional[str] = None
    telegram_first_name: Optional[str] = None
    telegram_last_name: Optional[str] = None


class TelegramLinkConfirmResponse(BaseModel):
    user_id: uuid.UUID
    message: str = "Telegram linked successfully"


# ── Sessions ──
class SessionResponse(BaseModel):
    id: uuid.UUID
    device_type: Optional[str]
    client_version: Optional[str]
    ip_address: Optional[str]
    created_at: datetime
    expires_at: datetime
    is_current: bool = False

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]


# ── Error ──
class ErrorResponse(BaseModel):
    error: dict