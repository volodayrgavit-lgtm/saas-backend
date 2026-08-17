"""
Billing API routes

Endpoints:
- POST /v1/billing/quote - Create a quote
- GET /v1/billing/quote/{quote_id} - Get quote
- POST /v1/billing/order - Create order from quote
- GET /v1/billing/order/{order_id} - Get order
- POST /v1/billing/order/{order_id}/pay - Initiate payment
- POST /v1/billing/callback/result - Robokassa ResultURL callback
- GET /v1/billing/callback/success - Robokassa SuccessURL
- GET /v1/billing/callback/fail - Robokassa FailURL
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Form
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.modules.billing.service import BillingService
from app.modules.billing.schemas import (
    QuoteCreateRequest, QuoteResponse,
    OrderCreateRequest, OrderResponse, OrderPaymentResponse,
    RobokassaCallbackData, CallbackResponse,
)
from app.modules.billing.robokassa_adapter import get_robokassa_adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


def get_billing_service(db: AsyncSession = Depends(get_db)) -> BillingService:
    """Dependency injection for BillingService"""
    return BillingService(db)


# ──────────────────────────────────────────────
# Quote Endpoints
# ──────────────────────────────────────────────

@router.post("/quote", response_model=QuoteResponse, status_code=status.HTTP_201_CREATED)
async def create_quote(
    request: QuoteCreateRequest,
    service: BillingService = Depends(get_billing_service),
):
    """
    Create a new quote with items
    
    A quote is a snapshot of prices before creating an order.
    Quotes have a TTL (default 30 minutes).
    """
    # TODO: Get user_id from authenticated session
    # For now, use user_id from request or raise error
    if not request.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id is required"
        )
    
    try:
        quote = await service.create_quote(request=request, user_id=request.user_id)
        return quote
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/quote/{quote_id}", response_model=QuoteResponse)
async def get_quote(
    quote_id: str,
    service: BillingService = Depends(get_billing_service),
):
    """Get quote by ID"""
    import uuid
    try:
        quote_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid quote ID format"
        )
    
    quote = await service.get_quote(quote_uuid)
    if not quote:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quote not found"
        )
    
    return quote


# ──────────────────────────────────────────────
# Order Endpoints
# ──────────────────────────────────────────────

@router.post("/order", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    request: OrderCreateRequest,
    service: BillingService = Depends(get_billing_service),
):
    """
    Create an order from a quote
    
    This converts a quote into an order and prepares it for payment.
    """
    try:
        order = await service.create_order_from_quote(
            request=request,
            customer_email=request.customer_email,
            customer_phone=request.customer_phone,
            description=request.description,
        )
        return order
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/order/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    service: BillingService = Depends(get_billing_service),
):
    """Get order by ID"""
    import uuid
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order ID format"
        )
    
    order = await service.get_order(order_uuid)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    
    return order


@router.post("/order/{order_id}/pay", response_model=OrderPaymentResponse)
async def initiate_payment(
    order_id: str,
    request: dict,  # Flexible body for return_url, fail_url
    service: BillingService = Depends(get_billing_service),
):
    """
    Initiate payment for an order
    
    Generates a Robokassa payment URL for redirect.
    """
    import uuid
    try:
        order_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order ID format"
        )
    
    return_url = request.get("return_url")
    fail_url = request.get("fail_url")
    
    try:
        response = await service.initiate_payment(
            order_id=order_uuid,
            return_url=return_url,
            fail_url=fail_url,
        )
        return response
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# ──────────────────────────────────────────────
# Robokassa Callback Endpoints
# ──────────────────────────────────────────────

@router.post("/callback/result")
async def robokassa_result_url(
    request: Request,
    service: BillingService = Depends(get_billing_service),
):
    """
    Robokassa ResultURL callback
    
    Called by Robokassa to notify about payment status.
    Must return "OK|InvId" on success or "ERROR|message" on failure.
    
    According to Robokassa docs:
    - Method: POST
    - Content-Type: application/x-www-form-urlencoded
    - Response: plain text "OK|InvId" or "ERROR|message"
    """
    form_data = await request.form()
    
    # Parse callback data
    adapter = get_robokassa_adapter()
    callback_data = adapter.parse_callback_data(dict(form_data))
    
    # Wrap in schema
    callback_schema = RobokassaCallbackData(**callback_data)
    
    # Process callback
    try:
        success = await service.process_callback(callback_schema)
        
        if success:
            # Return OK|InvId format required by Robokassa
            return PlainTextResponse(
                content=f"OK|{callback_schema.inv_id}",
                media_type="text/plain"
            )
        else:
            return PlainTextResponse(
                content=f"ERROR|Processing failed",
                media_type="text/plain",
                status_code=status.HTTP_400_BAD_REQUEST
            )
    except Exception as e:
        logger.exception(f"Error processing callback: {e}")
        return PlainTextResponse(
            content=f"ERROR|{str(e)}",
            media_type="text/plain",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.get("/callback/success")
async def robokassa_success_url(
    request: Request,
    InvId: Optional[str] = None,
):
    """
    Robokassa SuccessURL
    
    User is redirected here after successful payment.
    Display success page or redirect to frontend.
    """
    adapter = get_robokassa_adapter()
    html = adapter.generate_success_url_response(InvId or "unknown")
    return HTMLResponse(content=html)


@router.get("/callback/fail")
async def robokassa_fail_url(
    request: Request,
    InvId: Optional[str] = None,
):
    """
    Robokassa FailURL
    
    User is redirected here after failed payment.
    Display failure page or redirect to frontend.
    """
    adapter = get_robokassa_adapter()
    html = adapter.generate_fail_url_response(InvId or "unknown")
    return HTMLResponse(content=html)


# ──────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────

@router.get("/health")
async def billing_health():
    """Billing service health check"""
    return {"status": "healthy", "service": "billing"}
