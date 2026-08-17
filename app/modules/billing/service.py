"""
Billing service - core business logic for quotes, orders, payments
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.models import (
    Quote, QuoteItem, Order, PaymentAttempt, FiscalDocument,
    BillingOutbox, BillingSyncTransaction, Plan, Price,
    OrderStatus, PaymentStatus, FiscalStatus, OutboxStatus,
    SyncTransactionStatus, MutationClass
)
from app.modules.billing.schemas import (
    QuoteCreateRequest, QuoteResponse, QuoteItemResponse,
    OrderCreateRequest, OrderResponse, OrderPaymentResponse,
    RobokassaCallbackData, FiscalReceiptCreate
)
from app.modules.billing.robokassa_adapter import get_robokassa_adapter

logger = logging.getLogger(__name__)


class BillingService:
    """Core billing service for quotes, orders, and payments"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.robokassa = get_robokassa_adapter()
    
    # ──────────────────────────────────────────────
    # Quote Operations
    # ──────────────────────────────────────────────
    
    async def create_quote(
        self,
        request: QuoteCreateRequest,
        user_id: uuid.UUID
    ) -> Quote:
        """Create a new quote with items"""
        
        quote_id = uuid.uuid4()
        expires_at = datetime.utcnow() + timedelta(minutes=request.valid_until_minutes)
        
        quote = Quote(
            id=quote_id,
            user_id=user_id,
            currency=request.currency,
            status="PENDING",
            expires_at=expires_at,
            description=request.description,
        )
        
        self.db.add(quote)
        
        # Add items
        total = 0
        for item_req in request.items:
            # Get plan and price
            plan = await self._get_plan(item_req.plan_id)
            if not plan:
                raise ValueError(f"Plan {item_req.plan_id} not found")
            
            # Get active price for the plan
            price = await self._get_active_price(plan.id, request.currency)
            if not price:
                raise ValueError(f"No active price found for plan {plan.id} in {request.currency}")
            
            unit_price = price.amount  # in kopecks
            item_total = unit_price * item_req.quantity
            total += item_total
            
            quote_item = QuoteItem(
                quote_id=quote_id,
                plan_id=item_req.plan_id,
                quantity=item_req.quantity,
                unit_price=unit_price,
                total_price=item_total,
                currency=request.currency,
            )
            self.db.add(quote_item)
        
        # Set totals
        quote.subtotal = total
        quote.discount = 0  # Can implement discounts later
        quote.total = total
        
        await self.db.commit()
        await self.db.refresh(quote)
        
        logger.info(f"Created quote {quote_id} for user {user_id}, total={total}")
        return quote
    
    async def get_quote(self, quote_id: uuid.UUID) -> Optional[Quote]:
        """Get quote by ID"""
        result = await self.db.execute(
            select(Quote)
            .options(selectinload(Quote.items))
            .where(Quote.id == quote_id)
        )
        return result.scalar_one_or_none()
    
    async def _get_plan(self, plan_id: uuid.UUID) -> Optional[Plan]:
        """Get plan by ID"""
        result = await self.db.execute(select(Plan).where(Plan.id == plan_id))
        return result.scalar_one_or_none()
    
    async def _get_active_price(self, plan_id: uuid.UUID, currency: str) -> Optional[Price]:
        """Get active price for plan and currency"""
        result = await self.db.execute(
            select(Price)
            .where(
                Price.plan_id == plan_id,
                Price.currency == currency,
                Price.active == True
            )
            .order_by(Price.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
    
    # ──────────────────────────────────────────────
    # Order Operations
    # ──────────────────────────────────────────────
    
    async def create_order_from_quote(
        self,
        request: OrderCreateRequest,
        customer_email: Optional[str] = None,
        customer_phone: Optional[str] = None,
        description: Optional[str] = None
    ) -> Order:
        """Create an order from a quote"""
        
        # Get quote
        quote = await self.get_quote(request.quote_id)
        if not quote:
            raise ValueError(f"Quote {request.quote_id} not found")
        
        if quote.status != "PENDING":
            raise ValueError(f"Quote {request.quote_id} is not in PENDING status")
        
        if quote.expires_at < datetime.utcnow():
            raise ValueError(f"Quote {request.quote_id} has expired")
        
        # Generate unique InvId for Robokassa
        inv_id = f"ORDER_{uuid.uuid4().hex[:12].upper()}"
        
        # Create order
        order = Order(
            user_id=request.user_id,
            quote_id=request.quote_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            fiscal_status=FiscalStatus.NOT_REQUIRED if quote.total == 0 else FiscalStatus.PENDING,
            inv_id=inv_id,
            currency=quote.currency,
            amount=quote.total,
            source=request.source,
            idempotency_key=request.idempotency_key,
        )
        
        self.db.add(order)
        
        # Update quote status
        quote.status = "CONVERTED"
        
        # Create fiscal document if needed
        if quote.total > 0:
            fiscal_doc = FiscalDocument(
                order_id=order.id,
                user_email=customer_email or quote.user.email if hasattr(quote, 'user') else None,
                user_phone=customer_phone,
                status=FiscalStatus.PENDING,
            )
            self.db.add(fiscal_doc)
        
        await self.db.commit()
        await self.db.refresh(order)
        
        # Emit event
        await self._emit_event(
            event_type="order.created",
            aggregate_type="order",
            aggregate_id=order.id,
            user_id=order.user_id,
            order_id=order.id,
            payload={"order_id": str(order.id), "amount": order.amount, "currency": order.currency},
            priority=75,
        )
        
        logger.info(f"Created order {order.id} from quote {request.quote_id}, amount={order.amount}")
        return order
    
    async def get_order(self, order_id: uuid.UUID) -> Optional[Order]:
        """Get order by ID"""
        result = await self.db.execute(
            select(Order)
            .options(selectinload(Order.payment_attempts))
            .where(Order.id == order_id)
        )
        return result.scalar_one_or_none()
    
    async def initiate_payment(
        self,
        order_id: uuid.UUID,
        return_url: Optional[str] = None,
        fail_url: Optional[str] = None
    ) -> OrderPaymentResponse:
        """Initiate payment for an order, generate Robokassa payment URL"""
        
        order = await self.get_order(order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found")
        
        if order.status not in [OrderStatus.PENDING, OrderStatus.INVOICE_CREATED]:
            raise ValueError(f"Order {order_id} is not in payable status: {order.status}")
        
        # Generate payment URL
        amount_rub = order.amount / 100.0  # Convert kopecks to rubles
        
        # Custom parameters for callback
        shp_params = {
            "Shp_order_id": str(order.id),
            "Shp_user_id": str(order.user_id),
        }
        
        # Get user email/phone for fiscal receipt
        user_email = None
        user_phone = None
        # Can fetch from user model if needed
        
        payment_url = self.robokassa.generate_payment_url(
            amount=amount_rub,
            inv_id=order.inv_id,
            description=f"Order {order.id}",
            currency=order.currency,
            email=user_email,
            phone=user_phone,
            shp_params=shp_params,
            hold=self.robokassa.hold_mode,
        )
        
        # Update order
        order.status = OrderStatus.INVOICE_CREATED
        order.payment_url = payment_url
        
        await self.db.commit()
        
        response = OrderPaymentResponse(
            order_id=order.id,
            payment_url=payment_url,
            inv_id=order.inv_id,
            amount=order.amount,
            currency=order.currency,
            expires_at=order.created_at + timedelta(hours=24),  # Payment link validity
        )
        
        logger.info(f"Generated payment URL for order {order_id}, inv_id={order.inv_id}")
        return response
    
    # ──────────────────────────────────────────────
    # Callback Handling (ResultURL)
    # ──────────────────────────────────────────────
    
    async def process_callback(
        self,
        callback_data: RobokassaCallbackData
    ) -> bool:
        """
        Process Robokassa ResultURL callback
        
        Returns True if callback was processed successfully
        """
        
        # Validate signature
        if not callback_data.signature_valid:
            logger.warning(f"Invalid signature for InvId={callback_data.inv_id}")
            return False
        
        # Find order by inv_id
        result = await self.db.execute(
            select(Order).where(Order.inv_id == callback_data.inv_id)
        )
        order = result.scalar_one_or_none()
        
        if not order:
            logger.warning(f"Order not found for InvId={callback_data.inv_id}")
            return False
        
        # Verify amount
        callback_amount_kopecks = int(float(callback_data.out_sum) * 100)
        if callback_amount_kopecks != order.amount:
            logger.warning(
                f"Amount mismatch for order {order.id}: "
                f"expected {order.amount}, got {callback_amount_kopecks}"
            )
            return False
        
        # Check if already processed
        existing_attempt = await self.db.execute(
            select(PaymentAttempt).where(
                PaymentAttempt.order_id == order.id,
                PaymentAttempt.inv_id == callback_data.inv_id,
                PaymentAttempt.status == PaymentStatus.SUCCESS
            )
        )
        if existing_attempt.scalar_one_or_none():
            logger.info(f"Duplicate successful callback for order {order.id}")
            return True  # Idempotent
        
        # Create payment attempt
        attempt = PaymentAttempt(
            order_id=order.id,
            inv_id=callback_data.inv_id,
            out_sum=callback_amount_kopecks,
            currency=callback_data.currency,
            payment_method=callback_data.payment_method,
            robokassa_signature=callback_data.signature,
            status=PaymentStatus.SUCCESS,
            raw_payload=callback_data.shp_params,
            processed=True,
            processed_at=datetime.utcnow(),
        )
        self.db.add(attempt)
        
        # Update order status
        order.status = OrderStatus.PAID
        order.payment_status = PaymentStatus.SUCCESS
        order.robokassa_invoice_id = callback_data.inv_id
        
        # Update fiscal document status
        if order.fiscal_documents:
            for doc in order.fiscal_documents:
                doc.status = FiscalStatus.PENDING
                doc.next_attempt_at = datetime.utcnow()
        
        await self.db.commit()
        
        # Emit payment received event
        await self._emit_event(
            event_type="payment.received",
            aggregate_type="order",
            aggregate_id=order.id,
            user_id=order.user_id,
            order_id=order.id,
            payload={
                "order_id": str(order.id),
                "amount": order.amount,
                "currency": order.currency,
                "payment_method": callback_data.payment_method,
            },
            priority=100,  # Critical priority
        )
        
        logger.info(f"Processed successful payment for order {order.id}, amount={order.amount}")
        return True
    
    # ──────────────────────────────────────────────
    # Event Publishing (Outbox)
    # ──────────────────────────────────────────────
    
    async def _emit_event(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
        order_id: Optional[uuid.UUID] = None,
        payload: Dict[str, Any] = None,
        priority: int = 50,
        correlation_id: Optional[uuid.UUID] = None
    ):
        """Add event to billing_outbox for async processing"""
        
        event = BillingOutbox(
            event_id=uuid.uuid4(),
            correlation_id=correlation_id,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            user_id=user_id,
            order_id=order_id,
            priority=priority,
            mutation_class=MutationClass.LOCAL_TRANSACTION,
            payload=payload or {},
            status=OutboxStatus.PENDING,
        )
        self.db.add(event)
        
        logger.debug(f"Added event {event.event_id} to outbox: {event_type}")
    
    # ──────────────────────────────────────────────
    # Fiscal Receipt Operations
    # ──────────────────────────────────────────────
    
    async def create_fiscal_receipt(
        self,
        request: FiscalReceiptCreate
    ) -> FiscalDocument:
        """Create fiscal receipt for order"""
        
        order = await self.get_order(request.order_id)
        if not order:
            raise ValueError(f"Order {request.order_id} not found")
        
        fiscal_doc = FiscalDocument(
            order_id=request.order_id,
            user_email=request.user_email,
            user_phone=request.user_phone,
            status=FiscalStatus.PENDING,
            next_attempt_at=datetime.utcnow(),
        )
        self.db.add(fiscal_doc)
        
        await self.db.commit()
        await self.db.refresh(fiscal_doc)
        
        logger.info(f"Created fiscal receipt {fiscal_doc.id} for order {request.order_id}")
        return fiscal_doc
