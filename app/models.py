import uuid
from datetime import datetime
from typing import Optional
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    Numeric,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ──────────────────────────────────────────────
# Billing Enums
# ──────────────────────────────────────────────

class QuoteStatus(str, Enum):
    PENDING = "PENDING"
    EXPIRED = "EXPIRED"
    CONVERTED = "CONVERTED"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    INVOICE_CREATED = "INVOICE_CREATED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class FiscalStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


class SubscriptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    TRIAL = "TRIAL"
    PAST_DUE = "PAST_DUE"


class OutboxStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    RETRY = "RETRY"
    DEAD = "DEAD"


class MutationClass(str, Enum):
    READ_INVALIDATION = "READ_INVALIDATION"
    SIMPLE_MUTATION = "SIMPLE_MUTATION"
    LOCAL_TRANSACTION = "LOCAL_TRANSACTION"
    DISTRIBUTED_UPDATE = "DISTRIBUTED_UPDATE"


class SyncTransactionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"


# ──────────────────────────────────────────────
# 1. users — центральная таблица пользователей
# ──────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)  # ACTIVE / BLOCKED / DELETED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    primary_category_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    avito_profile_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avito_profile_normalized: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avito_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)

    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    registration_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # WEB / TELEGRAM / EXTENSION
    acquisition_source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # ORGANIC / MANAGER_REFERRAL / PROMO / PAID_CAMPAIGN / DIRECT

    # Relationships
    identities: Mapped[list["UserIdentity"]] = relationship(back_populates="user", lazy="selectin")
    credentials: Mapped[Optional["UserCredential"]] = relationship(back_populates="user", lazy="selectin")
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", lazy="selectin")
    devices: Mapped[list["UserDevice"]] = relationship(back_populates="user", lazy="selectin")
    bonus_entries: Mapped[list["BonusLedger"]] = relationship(back_populates="user", lazy="selectin")
    trials: Mapped[list["Trial"]] = relationship(back_populates="user", lazy="selectin")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user", lazy="selectin")
    link_tokens: Mapped[list["AccountLinkToken"]] = relationship(back_populates="user", lazy="selectin")


# ──────────────────────────────────────────────
# 2. user_identities — способы идентификации
# ──────────────────────────────────────────────
class UserIdentity(Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("type", "normalized_value", name="uq_identity_type_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)

    type: Mapped[str] = mapped_column(String(20), index=True)  # EMAIL / PHONE / TELEGRAM / GOOGLE / APPLE
    normalized_value: Mapped[str] = mapped_column(String(255), index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, name="metadata")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="identities")


# ──────────────────────────────────────────────
# 3. user_credentials — пароли
# ──────────────────────────────────────────────
class UserCredential(Base):
    __tablename__ = "user_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="credentials")


# ──────────────────────────────────────────────
# 4. user_sessions — сессии (refresh tokens)
# ──────────────────────────────────────────────
class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(255), index=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    device_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # web / telegram / extension
    client_version: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


# ──────────────────────────────────────────────
# 5. user_devices — управление устройствами
# ──────────────────────────────────────────────
class UserDevice(Base):
    __tablename__ = "user_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(255), index=True)
    device_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    client_version: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    browser: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="devices")


# ──────────────────────────────────────────────
# 6. account_link_tokens — токены привязки
# ──────────────────────────────────────────────
class AccountLinkToken(Base):
    __tablename__ = "account_link_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), index=True)
    purpose: Mapped[str] = mapped_column(String(30))  # TELEGRAM_LINK / DEVICE_LINK
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="link_tokens")


# ──────────────────────────────────────────────
# 7. categories — справочник категорий
# ──────────────────────────────────────────────
class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ──────────────────────────────────────────────
# 8. bonus_ledger — бухгалтерия бонусов
# ──────────────────────────────────────────────
class BonusLedger(Base):
    __tablename__ = "bonus_ledger"
    __table_args__ = (
        UniqueConstraint("user_id", "type", name="uq_bonus_user_type"),  # идемпотентность ONBOARDING_COMPLETED
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(50), index=True)  # ONBOARDING_COMPLETED / MANUAL / REFERRAL
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reference_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="bonus_entries")


# ──────────────────────────────────────────────
# 9. products — продукты LAB51 (удалено - перенесено ниже)
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# 10. plans — тарифные планы (удалено - перенесено ниже)
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# 11. entitlements — права тарифа (удалено - перенесено ниже)
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# 12. trials — пробные периоды
# ──────────────────────────────────────────────
class Trial(Base):
    __tablename__ = "trials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE / EXPIRED / REVOKED
    source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # WEB / TELEGRAM / EXTENSION / ADMIN
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="trials")


# ──────────────────────────────────────────────
# 13. subscriptions — подписки
# ──────────────────────────────────────────────
class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE / EXPIRED / CANCELLED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="subscriptions")


# ──────────────────────────────────────────────
# 14. referrals — рефералы
# ──────────────────────────────────────────────
class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    manager_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    discount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # TELEGRAM / WEB / EXTENSION
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ──────────────────────────────────────────────
# 15. referral_clicks — клики по реферальным ссылкам
# ──────────────────────────────────────────────
class ReferralClick(Base):
    __tablename__ = "referral_clicks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    referral_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("referrals.id", ondelete="CASCADE"), index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ──────────────────────────────────────────────
# 16. leads — лиды CRM
# ──────────────────────────────────────────────
class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manager_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    avito_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    avito_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avito_url_normalized: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    telegram_user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="NEW")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ──────────────────────────────────────────────
# 17. attributions — CRM атрибуция
# ──────────────────────────────────────────────
class Attribution(Base):
    __tablename__ = "attributions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=True)
    referral_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("referrals.id"), nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    match_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # REF_LINK / AVITO_ID / PHONE / TELEGRAM / MANUAL
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ──────────────────────────────────────────────
# 18. outbox_events — транзакционный outbox
# ──────────────────────────────────────────────
class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # WEB / TELEGRAM / EXTENSION
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ──────────────────────────────────────────────
# 19. audit_log — аудит
# ──────────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ──────────────────────────────────────────────
# 20. products — продукты LAB51 (обновлено для billing)
# ──────────────────────────────────────────────
class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    plans: Mapped[list["Plan"]] = relationship(back_populates="product", lazy="selectin")


# ──────────────────────────────────────────────
# 21. plans — тарифные планы
# ──────────────────────────────────────────────
class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="plans")
    entitlements: Mapped[list["Entitlement"]] = relationship(back_populates="plan", lazy="selectin")
    prices: Mapped[list["Price"]] = relationship(back_populates="plan", lazy="selectin")


# ──────────────────────────────────────────────
# 22. entitlements — права тарифа
# ──────────────────────────────────────────────
class Entitlement(Base):
    __tablename__ = "entitlements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))  # max_searches, regions, telegram_bot, extension, voice, devices, favorites, boost_daily
    value: Mapped[str] = mapped_column(Text)  # JSON-serialized value
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    plan: Mapped["Plan"] = relationship(back_populates="entitlements")


# ──────────────────────────────────────────────
# 23. prices — цены для планов
# ──────────────────────────────────────────────
class Price(Base):
    __tablename__ = "prices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")  # RUB, USD, EUR
    amount: Mapped[int] = mapped_column(Integer)  # храним в копейках/центах
    period_months: Mapped[int] = mapped_column(Integer, default=1)  # 1, 3, 6, 12
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    robokassa_shop_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    plan: Mapped["Plan"] = relationship(back_populates="prices")


# ──────────────────────────────────────────────
# 24. quotes — цитаты (snapshot цен перед заказом)
# ──────────────────────────────────────────────
class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[QuoteStatus] = mapped_column(Enum(QuoteStatus.PENDING, QuoteStatus.EXPIRED, QuoteStatus.CONVERTED, name='quote_status'), default=QuoteStatus.PENDING, index=True)
    
    # snapshot данных
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    amount: Mapped[int] = mapped_column(Integer)  # в копейках
    period_months: Mapped[int] = mapped_column(Integer, default=1)
    
    # TTL и метаданные
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="quotes")
    items: Mapped[list["QuoteItem"]] = relationship(back_populates="quote", lazy="selectin")


# ──────────────────────────────────────────────
# 24b. quote_items — элементы квоты
# ──────────────────────────────────────────────
class QuoteItem(Base):
    __tablename__ = "quote_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[int] = mapped_column(Integer)  # в копейках
    total_price: Mapped[int] = mapped_column(Integer)  # в копейках
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    quote: Mapped["Quote"] = relationship(back_populates="items")


# ──────────────────────────────────────────────
# 25. orders — заказы
# ──────────────────────────────────────────────
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    quote_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("quotes.id"), nullable=True)
    
    # статусы
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus.PENDING, OrderStatus.INVOICE_CREATED, OrderStatus.PAID, OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.FAILED, name='order_status'), default=OrderStatus.PENDING, index=True)
    payment_status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus.PENDING, PaymentStatus.SUCCESS, PaymentStatus.FAILED, PaymentStatus.REFUNDED, name='payment_status'), default=PaymentStatus.PENDING, index=True)
    fiscal_status: Mapped[FiscalStatus] = mapped_column(Enum(FiscalStatus.PENDING, FiscalStatus.SUCCESS, FiscalStatus.FAILED, FiscalStatus.NOT_REQUIRED, name='fiscal_status'), default=FiscalStatus.PENDING, index=True)
    
    # Robokassa данные
    inv_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True)  # уникальный InvId для Robokassa
    robokassa_invoice_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    payment_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # сумма
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    amount: Mapped[int] = mapped_column(Integer)  # в копейках
    
    # source
    source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # website, telegram, extension, crm
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    
    # timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", lazy="selectin")
    payment_attempts: Mapped[list["PaymentAttempt"]] = relationship(back_populates="order", lazy="selectin")


# ──────────────────────────────────────────────
# 26. order_items — элементы заказа
# ──────────────────────────────────────────────
class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    
    # snapshot данных на момент заказа
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"))
    plan_name: Mapped[str] = mapped_column(String(255))
    plan_slug: Mapped[str] = mapped_column(String(255))
    
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[int] = mapped_column(Integer)  # в копейках
    total_price: Mapped[int] = mapped_column(Integer)  # в копейках
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    
    # fiscal snapshot
    vat_rate: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # none, 0%, 20%
    fiscal_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="items")


# ──────────────────────────────────────────────
# 27. payment_attempts — попытки оплаты
# ──────────────────────────────────────────────
class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    
    # Robokassa callback данные
    inv_id: Mapped[str] = mapped_column(String(100), index=True)
    out_sum: Mapped[int] = mapped_column(Integer)  # в копейках
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    payment_method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # bank_card, sbp, etc.
    robokassa_signature: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # статус
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus.PENDING, PaymentStatus.SUCCESS, PaymentStatus.FAILED, PaymentStatus.REFUNDED, name='payment_attempt_status'), default=PaymentStatus.PENDING, index=True)
    
    # raw callback
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # processing
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="payment_attempts")


# ──────────────────────────────────────────────
# 28. fiscal_documents — фискальные документы (чеки)
# ──────────────────────────────────────────────
class FiscalDocument(Base):
    __tablename__ = "fiscal_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    
    # данные чека
    fiscal_document_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)  # ID из ОФД
    fiscal_receipt_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # статус
    status: Mapped[FiscalStatus] = mapped_column(Enum(FiscalStatus.PENDING, FiscalStatus.SUCCESS, FiscalStatus.FAILED, FiscalStatus.NOT_REQUIRED, name='fiscal_document_status'), default=FiscalStatus.PENDING, index=True)
    
    # данные для отправки
    user_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    user_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    # попытка отправки
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped["Order"] = relationship(back_populates="fiscal_documents")


# ──────────────────────────────────────────────
# 29. billing_outbox — durable очередь событий billing
# ──────────────────────────────────────────────
class BillingOutbox(Base):
    __tablename__ = "billing_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # event identification
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    correlation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    
    # aggregate info
    aggregate_type: Mapped[str] = mapped_column(String(50))  # order, payment, subscription
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    
    # routing
    priority: Mapped[int] = mapped_column(Integer, default=50)  # 100=critical, 75=high, 50=normal, 25=low, 10=bulk
    mutation_class: Mapped[MutationClass] = mapped_column(Enum(MutationClass.READ_INVALIDATION, MutationClass.SIMPLE_MUTATION, MutationClass.LOCAL_TRANSACTION, MutationClass.DISTRIBUTED_UPDATE, name='mutation_class'), default=MutationClass.LOCAL_TRANSACTION)
    
    # payload
    payload: Mapped[dict] = mapped_column(JSONB)
    
    # status
    status: Mapped[OutboxStatus] = mapped_column(Enum(OutboxStatus.PENDING, OutboxStatus.CLAIMED, OutboxStatus.PROCESSING, OutboxStatus.COMPLETED, OutboxStatus.RETRY, OutboxStatus.DEAD, name='outbox_status'), default=OutboxStatus.PENDING, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    
    # lease
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # result
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ──────────────────────────────────────────────
# 30. billing_sync_transactions — отслеживание синхронизации
# ──────────────────────────────────────────────
class BillingSyncTransaction(Base):
    __tablename__ = "billing_sync_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # correlation
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    correlation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    
    # routing
    mutation_class: Mapped[MutationClass] = mapped_column(Enum(MutationClass.READ_INVALIDATION, MutationClass.SIMPLE_MUTATION, MutationClass.LOCAL_TRANSACTION, MutationClass.DISTRIBUTED_UPDATE, name='sync_mutation_class'))
    priority: Mapped[int] = mapped_column(Integer, default=50)
    
    # status per component
    status: Mapped[SyncTransactionStatus] = mapped_column(Enum(SyncTransactionStatus.PENDING, SyncTransactionStatus.RUNNING, SyncTransactionStatus.PARTIAL, SyncTransactionStatus.COMPLETED, SyncTransactionStatus.FAILED_RETRYABLE, SyncTransactionStatus.FAILED_FINAL, name='sync_transaction_status'), default=SyncTransactionStatus.PENDING, index=True)
    core_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    resource_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    share_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    fiscal_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    
    # retry
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sync_transactions")


# ──────────────────────────────────────────────
# 31. processed_events — inbox для идемпотентности
# ──────────────────────────────────────────────
class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    consumer: Mapped[str] = mapped_column(String(50))  # core_service, resource_service, etc.
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    result: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # SUCCESS, FAILED, SKIPPED


# ──────────────────────────────────────────────
# Обновление relationships в User
# ──────────────────────────────────────────────
User.quotes = relationship("Quote", back_populates="user", lazy="selectin")
User.orders = relationship("Order", back_populates="user", lazy="selectin")
User.sync_transactions = relationship("BillingSyncTransaction", back_populates="user", lazy="selectin")


# ──────────────────────────────────────────────
# Обновление relationships в Order
# ──────────────────────────────────────────────
Order.fiscal_documents = relationship("FiscalDocument", back_populates="order", lazy="selectin")