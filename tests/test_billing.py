"""Tests for billing module with Robokassa integration."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import User, Product, Plan, Price, Quote, QuoteItem, Order, PaymentAttempt, FiscalDocument, BillingOutbox
from app.modules.billing.service import BillingService
from app.modules.billing.robokassa_adapter import RobokassaAdapter, RobokassaConfig
from app.modules.billing.schemas import QuoteCreateRequest as QuoteRequest, QuoteItemCreate as QuoteItemRequest, OrderCreateRequest as CreateOrderRequest


class TestRobokassaConfig:
    """Tests for Robokassa configuration."""

    def test_config_creation(self):
        """Test config creation with default values."""
        config = RobokassaConfig(
            login="test_login",
            password1="test_password1",
            password2="test_password2",
            is_test=True
        )
        assert config.login == "test_login"
        assert config.password1 == "test_password1"
        assert config.password2 == "test_password2"
        assert config.is_test is True
        assert config.base_url == "https://auth.robokassa.ru/Merchant/"

    def test_config_production_mode(self):
        """Test config in production mode."""
        config = RobokassaConfig(
            login="prod_login",
            password1="prod_password1",
            password2="prod_password2",
            is_test=False
        )
        assert config.is_test is False
        assert config.base_url == "https://merchant.roboxchange.com/"


class TestRobokassaAdapter:
    """Tests for Robokassa adapter."""

    @pytest.fixture
    def adapter(self):
        """Create adapter with test config."""
        config = RobokassaConfig(
            login="test_login",
            password1="test_password1",
            password2="test_password2",
            is_test=True
        )
        return RobokassaAdapter(config=config)

    def test_generate_signature_success(self, adapter):
        """Test signature generation."""
        # Test for InitPaymentURL signature
        sig = adapter.generate_signature(
            amount=100.00,
            inv_id="12345",
            currency="RUB",
            description="Test payment"
        )
        assert sig is not None
        assert len(sig) > 0

    def test_generate_payment_url(self, adapter):
        """Test payment URL generation."""
        order_id = "12345"
        amount = 100.00
        description = "Test order"
        user_email = "user@example.com"
        
        url = adapter.generate_payment_url(
            amount=amount,
            inv_id=order_id,
            description=description,
            email=user_email
        )
        
        assert url is not None
        assert "http" in url
        assert "MerchantLogin=test_login" in url
        assert f"InvId={order_id}" in url
        assert "OutSum=100.0" in url or "OutSum=100.00" in url

    def test_validate_result_signature(self, adapter):
        """Test ResultURL signature validation."""
        # Generate a valid signature first
        out_sum = "100.0"
        inv_id = "12345"
        status = "Success"
        
        # Create signature manually (Robokassa format: OutSum:InvId:Status:password2)
        sig_string = f"{out_sum}:{inv_id}:{status}:test_password2"
        import hashlib
        expected_sig = hashlib.md5(sig_string.encode()).hexdigest().upper()
        
        form_data = {
            "OutSum": out_sum,
            "InvId": inv_id,
            "SignatureValue": expected_sig,
            "Status": status
        }
        
        is_valid = adapter.validate_result_signature(form_data)
        assert is_valid is True

    def test_validate_result_signature_invalid(self, adapter):
        """Test invalid signature detection."""
        form_data = {
            "OutSum": "100.00",
            "InvId": "12345",
            "SignatureValue": "INVALID_SIGNATURE",
            "Status": "Success"
        }
        
        is_valid = adapter.validate_result_signature(form_data)
        assert is_valid is False


class TestBillingService:
    """Tests for BillingService."""

    @pytest_asyncio.fixture
    async def setup_data(self, client, test_engine):
        """Setup test data."""
        from app.main import app
        from app.database import get_db
        from sqlalchemy.ext.asyncio import async_sessionmaker
        
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        
        async with session_factory() as session:
            # Create user
            user = User(
                email="billing_test@example.com",
                phone="+79991234567",
                is_active=True
            )
            session.add(user)
            
            # Create product
            product = Product(
                name="Test Product",
                code="TEST_PROD",
                is_active=True
            )
            session.add(product)
            await session.flush()
            
            # Create plan
            plan = Plan(
                product_id=product.id,
                name="Test Plan",
                code="TEST_PLAN",
                billing_period="month",
                is_active=True
            )
            session.add(plan)
            await session.flush()
            
            # Create price
            price = Price(
                plan_id=plan.id,
                currency="RUB",
                amount=10000,  # 100.00 RUB in kopecks
                billing_period="month"
            )
            session.add(price)
            
            await session.commit()
            
            yield {
                "user": user,
                "product": product,
                "plan": plan,
                "price": price
            }

    @pytest.mark.asyncio
    async def test_create_quote(self, client, setup_data):
        """Test quote creation."""
        data = setup_data
        user = data["user"]
        plan = data["plan"]
        price = data["price"]
        
        quote_request = QuoteRequest(
            items=[
                QuoteItemRequest(
                    plan_id=plan.id,
                    quantity=1
                )
            ]
        )
        
        # Mock current user dependency
        from app.dependencies import get_current_user
        from app.main import app
        
        mock_user = MagicMock()
        mock_user.id = user.id
        mock_user.email = user.email
        
        with patch.object(app, 'dependency_overrides'):
            app.dependency_overrides[get_current_user] = lambda: mock_user
            
            response = await client.post(
                "/api/v1/billing/quote",
                json={
                    "items": [
                        {
                            "plan_id": str(plan.id),
                            "quantity": 1
                        }
                    ]
                }
            )
            
            assert response.status_code == 200
            result = response.json()
            assert "id" in result
            assert "total_amount" in result
            assert result["currency"] == "RUB"

    @pytest.mark.asyncio
    async def test_create_order_from_quote(self, client, setup_data):
        """Test order creation from quote."""
        data = setup_data
        user = data["user"]
        plan = data["plan"]
        
        # First create a quote
        from app.main import app
        from app.dependencies import get_current_user
        
        mock_user = MagicMock()
        mock_user.id = user.id
        mock_user.email = user.email
        
        app.dependency_overrides[get_current_user] = lambda: mock_user
        
        # Create quote directly via service
        from sqlalchemy.ext.asyncio import async_sessionmaker
        session_factory = async_sessionmaker(client.app.state.engine, class_=AsyncSession, expire_on_commit=False)
        
        async with session_factory() as session:
            service = BillingService(session)
            
            quote = Quote(
                user_id=user.id,
                status="PENDING",
                total_amount=10000,
                currency="RUB"
            )
            session.add(quote)
            await session.flush()
            
            quote_item = QuoteItem(
                quote_id=quote.id,
                plan_id=plan.id,
                quantity=1,
                unit_price=10000,
                total_price=10000,
                currency="RUB"
            )
            session.add(quote_item)
            await session.commit()
            
            # Now create order
            order_request = CreateOrderRequest(
                quote_id=quote.id,
                return_url="https://example.com/return",
                receipt_email=user.email,
                receipt_phone="+79991234567"
            )
            
            # Mock Robokassa adapter
            with patch.object(service, '_robokassa') as mock_robokassa:
                mock_robokassa.generate_payment_url.return_value = "https://test.robokassa.ru/payment"
                
                order = await service.create_order(
                    user=mock_user,
                    request=order_request
                )
                
                assert order is not None
                assert order.quote_id == quote.id
                assert order.status == "PENDING"
                assert order.payment_url is not None


class TestOrderModel:
    """Tests for Order model."""

    @pytest.mark.asyncio
    async def test_order_creation(self, client, test_engine):
        """Test order model creation."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.main import app
        from app.database import get_db
        
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        
        async with session_factory() as session:
            # Create user
            user = User(
                email="order_test@example.com",
                phone="+79991234567",
                is_active=True
            )
            session.add(user)
            await session.commit()
            
            # Create order
            order = Order(
                user_id=user.id,
                status="PENDING",
                total_amount=10000,
                currency="RUB",
                description="Test order"
            )
            session.add(order)
            await session.commit()
            
            # Verify
            result = await session.execute(select(Order).where(Order.id == order.id))
            saved_order = result.scalar_one()
            
            assert saved_order is not None
            assert saved_order.user_id == user.id
            assert saved_order.status == "PENDING"
            assert saved_order.total_amount == 10000


class TestPaymentAttemptModel:
    """Tests for PaymentAttempt model."""

    @pytest.mark.asyncio
    async def test_payment_attempt_creation(self, client, test_engine):
        """Test payment attempt model creation."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.main import app
        
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        
        async with session_factory() as session:
            # Create user and order
            user = User(
                email="payment_test@example.com",
                phone="+79991234567",
                is_active=True
            )
            session.add(user)
            await session.commit()
            
            order = Order(
                user_id=user.id,
                status="PENDING",
                total_amount=10000,
                currency="RUB"
            )
            session.add(order)
            await session.commit()
            
            # Create payment attempt
            attempt = PaymentAttempt(
                order_id=order.id,
                provider="ROBOKASSA",
                provider_transaction_id="RK-12345",
                status="PENDING",
                amount=10000,
                currency="RUB"
            )
            session.add(attempt)
            await session.commit()
            
            # Verify
            result = await session.execute(select(PaymentAttempt).where(PaymentAttempt.id == attempt.id))
            saved_attempt = result.scalar_one()
            
            assert saved_attempt is not None
            assert saved_attempt.order_id == order.id
            assert saved_attempt.provider == "ROBOKASSA"
            assert saved_attempt.status == "PENDING"


class TestFiscalDocumentModel:
    """Tests for FiscalDocument model."""

    @pytest.mark.asyncio
    async def test_fiscal_document_creation(self, client, test_engine):
        """Test fiscal document model creation."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.main import app
        
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        
        async with session_factory() as session:
            # Create user and order
            user = User(
                email="fiscal_test@example.com",
                phone="+79991234567",
                is_active=True
            )
            session.add(user)
            await session.commit()
            
            order = Order(
                user_id=user.id,
                status="PAID",
                total_amount=10000,
                currency="RUB"
            )
            session.add(order)
            await session.commit()
            
            # Create fiscal document
            fiscal_doc = FiscalDocument(
                order_id=order.id,
                fiscal_status="PENDING",
                receipt_data={
                    "operation_type": "income",
                    "tax_system": "osn",
                    "items": [
                        {
                            "name": "Test item",
                            "quantity": 1,
                            "sum": 10000,
                            "tax": "none"
                        }
                    ]
                }
            )
            session.add(fiscal_doc)
            await session.commit()
            
            # Verify
            result = await session.execute(select(FiscalDocument).where(FiscalDocument.id == fiscal_doc.id))
            saved_doc = result.scalar_one()
            
            assert saved_doc is not None
            assert saved_doc.order_id == order.id
            assert saved_doc.fiscal_status == "PENDING"
            assert saved_doc.receipt_data is not None


class TestBillingOutboxModel:
    """Tests for BillingOutbox model."""

    @pytest.mark.asyncio
    async def test_billing_outbox_creation(self, client, test_engine):
        """Test billing outbox model creation."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.main import app
        
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        
        async with session_factory() as session:
            # Create user and order
            user = User(
                email="outbox_test@example.com",
                phone="+79991234567",
                is_active=True
            )
            session.add(user)
            await session.commit()
            
            order = Order(
                user_id=user.id,
                status="PAID",
                total_amount=10000,
                currency="RUB"
            )
            session.add(order)
            await session.commit()
            
            # Create outbox event
            outbox_event = BillingOutbox(
                aggregate_type="Order",
                aggregate_id=str(order.id),
                event_type="order.paid",
                payload={
                    "order_id": str(order.id),
                    "user_id": str(user.id),
                    "amount": 10000
                }
            )
            session.add(outbox_event)
            await session.commit()
            
            # Verify
            result = await session.execute(select(BillingOutbox).where(BillingOutbox.id == outbox_event.id))
            saved_event = result.scalar_one()
            
            assert saved_event is not None
            assert saved_event.aggregate_type == "Order"
            assert saved_event.event_type == "order.paid"
            assert saved_event.processed is False


class TestQuoteModel:
    """Tests for Quote model."""

    @pytest.mark.asyncio
    async def test_quote_creation(self, client, test_engine):
        """Test quote model creation."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.main import app
        
        session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        
        async with session_factory() as session:
            # Create user
            user = User(
                email="quote_test@example.com",
                phone="+79991234567",
                is_active=True
            )
            session.add(user)
            await session.commit()
            
            # Create quote
            quote = Quote(
                user_id=user.id,
                status="PENDING",
                total_amount=10000,
                currency="RUB"
            )
            session.add(quote)
            await session.commit()
            
            # Verify
            result = await session.execute(select(Quote).where(Quote.id == quote.id))
            saved_quote = result.scalar_one()
            
            assert saved_quote is not None
            assert saved_quote.user_id == user.id
            assert saved_quote.status == "PENDING"
            assert saved_quote.total_amount == 10000


class TestAPIEndpoints:
    """Tests for billing API endpoints."""

    @pytest.mark.asyncio
    async def test_health_check(self, client):
        """Test health check endpoint."""
        response = await client.get("/api/v1/billing/health")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_get_quote_not_found(self, client):
        """Test getting non-existent quote."""
        from app.dependencies import get_current_user
        from app.main import app
        
        mock_user = MagicMock()
        mock_user.id = uuid.uuid4()
        
        app.dependency_overrides[get_current_user] = lambda: mock_user
        
        random_id = uuid.uuid4()
        response = await client.get(f"/api/v1/billing/quotes/{random_id}")
        assert response.status_code == 404


class TestSignatureValidation:
    """Tests for signature validation edge cases."""

    @pytest.fixture
    def adapter(self):
        """Create adapter with test config."""
        config = RobokassaConfig(
            login="test_login",
            password1="test_password1",
            password2="test_password2",
            is_test=True
        )
        return RobokassaAdapter(config=config)

    def test_validate_missing_fields(self, adapter):
        """Test validation with missing required fields."""
        # Missing SignatureValue
        form_data = {
            "OutSum": "100.00",
            "InvId": "12345",
            "Status": "Success"
        }
        
        is_valid = adapter.validate_result_signature(form_data)
        assert is_valid is False

    def test_validate_empty_signature(self, adapter):
        """Test validation with empty signature."""
        form_data = {
            "OutSum": "100.00",
            "InvId": "12345",
            "SignatureValue": "",
            "Status": "Success"
        }
        
        is_valid = adapter.validate_result_signature(form_data)
        assert is_valid is False

    def test_validate_case_insensitive(self, adapter):
        """Test that signature validation is case-insensitive where appropriate."""
        out_sum = "100.0"
        inv_id = "12345"
        status = "Success"
        
        sig_string = f"{out_sum}:{inv_id}:{status}:test_password2"
        import hashlib
        expected_sig = hashlib.md5(sig_string.encode()).hexdigest().upper()
        
        # Test with lowercase signature
        form_data_lower = {
            "OutSum": out_sum,
            "InvId": inv_id,
            "SignatureValue": expected_sig.lower(),
            "Status": status
        }
        
        # Robokassa typically uses uppercase, but we should handle both
        is_valid = adapter.validate_result_signature(form_data_lower)
        # Should be valid as we compare uppercase versions
        assert is_valid is True
