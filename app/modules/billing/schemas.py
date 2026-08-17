"""
Billing schemas for API requests/responses
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
import uuid


class OrderStatusEnum(str, Enum):
    PENDING = "PENDING"
    INVOICE_CREATED = "INVOICE_CREATED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"


class PaymentStatusEnum(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class FiscalStatusEnum(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


# ──────────────────────────────────────────────
# Quote Schemas
# ──────────────────────────────────────────────

class QuoteItemCreate(BaseModel):
    """Item in a quote"""
    plan_id: uuid.UUID
    quantity: int = 1
    currency: str = "RUB"


class QuoteCreateRequest(BaseModel):
    """Request to create a quote"""
    items: List[QuoteItemCreate]
    user_id: Optional[uuid.UUID] = None
    currency: str = "RUB"
    description: Optional[str] = None
    valid_until_minutes: int = 30  # Quote validity period


class QuoteItemResponse(BaseModel):
    """Item in a quote response"""
    plan_id: uuid.UUID
    plan_name: Optional[str] = None
    quantity: int
    unit_price: int  # in kopecks
    total_price: int  # in kopecks
    currency: str
    
    class Config:
        from_attributes = True


class QuoteResponse(BaseModel):
    """Quote response"""
    id: uuid.UUID
    user_id: uuid.UUID
    items: List[QuoteItemResponse]
    subtotal: int  # in kopecks
    discount: int  # in kopecks
    total: int  # in kopecks
    currency: str
    status: str
    expires_at: datetime
    
    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# Order Schemas
# ──────────────────────────────────────────────

class OrderCreateRequest(BaseModel):
    """Request to create an order from a quote"""
    quote_id: uuid.UUID
    user_id: uuid.UUID
    source: Optional[str] = "website"  # website, telegram, extension, crm
    idempotency_key: Optional[str] = None
    customer_email: Optional[EmailStr] = None
    customer_phone: Optional[str] = None
    description: Optional[str] = None


class OrderPaymentRequest(BaseModel):
    """Request to initiate payment for an order"""
    order_id: uuid.UUID
    return_url: Optional[str] = None  # SuccessURL override
    fail_url: Optional[str] = None  # FailURL override


class OrderResponse(BaseModel):
    """Order response with payment info"""
    id: uuid.UUID
    user_id: uuid.UUID
    quote_id: Optional[uuid.UUID]
    status: OrderStatusEnum
    payment_status: PaymentStatusEnum
    fiscal_status: FiscalStatusEnum
    inv_id: Optional[str]
    robokassa_invoice_id: Optional[str]
    payment_url: Optional[str]
    currency: str
    amount: int  # in kopecks
    source: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class OrderPaymentResponse(BaseModel):
    """Payment initiation response"""
    order_id: uuid.UUID
    payment_url: str
    inv_id: str
    amount: int
    currency: str
    expires_at: datetime


# ──────────────────────────────────────────────
# Callback Schemas (Robokassa ResultURL)
# ──────────────────────────────────────────────

class RobokassaCallbackData(BaseModel):
    """Parsed Robokassa callback data"""
    out_sum: str
    inv_id: str
    currency: str
    signature: str
    signature_valid: bool
    inc_curr_label: Optional[str] = None
    inc_amount: Optional[str] = None
    payment_method: Optional[str] = None
    shp_params: Dict[str, str] = Field(default_factory=dict)


class CallbackResponse(BaseModel):
    """Response to Robokassa callback"""
    status: str  # "OK" or "ERROR"
    message: Optional[str] = None


# ──────────────────────────────────────────────
# Fiscal Receipt Schemas (ФЗ-54)
# ──────────────────────────────────────────────

class FiscalItem(BaseModel):
    """Item in fiscal receipt"""
    name: str
    price: int  # in kopecks
    quantity: int
    sum: int  # in kopecks
    payment_object_type: str = "commodity"  # commodity, service, etc.
    tax_rate: str = "none"  # none, vat0, vat10, vat20, etc.


class FiscalReceiptCreate(BaseModel):
    """Fiscal receipt creation request"""
    order_id: uuid.UUID
    user_email: Optional[EmailStr] = None
    user_phone: Optional[str] = None
    items: List[FiscalItem]
    total: int
    currency: str = "RUB"
    payment_type: str = "incoming"  # incoming, incoming_return


# ──────────────────────────────────────────────
# Event Schemas (for billing_outbox)
# ──────────────────────────────────────────────

class BillingEvent(BaseModel):
    """Billing event for outbox"""
    event_id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    order_id: Optional[uuid.UUID] = None
    payload: Dict[str, Any]
    priority: int = 50
    correlation_id: Optional[uuid.UUID] = None


class EventType(str, Enum):
    ORDER_CREATED = "order.created"
    ORDER_PAID = "order.paid"
    ORDER_CANCELLED = "order.cancelled"
    PAYMENT_RECEIVED = "payment.received"
    PAYMENT_FAILED = "payment.failed"
    FISCAL_RECEIPT_SENT = "fiscal.receipt_sent"
    SUBSCRIPTION_ACTIVATED = "subscription.activated"
    SUBSCRIPTION_EXPIRED = "subscription.expired"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"
