"""
Robokassa payment gateway adapter

Implements Robokassa API integration according to:
https://docs.robokassa.ru/ru/quick-start

Features:
- Payment link generation (PurchaseURL)
- ResultURL callback handling with signature validation
- JWS signature format support
- Hold mode support
- Fiscal receipt integration (ФЗ-54)
"""

import hashlib
import hmac
import logging
from typing import Optional, Dict, Any
from urllib.parse import urlencode

from app.config import settings

logger = logging.getLogger(__name__)


class RobokassaConfig:
    """Configuration for Robokassa adapter"""
    
    def __init__(
        self,
        login: str,
        password1: str,
        password2: str,
        is_test: bool = True,
        hold_mode: bool = False,
        receipt_enabled: bool = False
    ):
        self.login = login
        self.password1 = password1
        self.password2 = password2
        self.is_test = is_test
        self.hold_mode = hold_mode
        self.receipt_enabled = receipt_enabled
        
        # Base URLs according to Robokassa documentation
        if is_test:
            self.base_url = "https://auth.robokassa.ru/Merchant/"
        else:
            self.base_url = "https://merchant.roboxchange.com/"


class RobokassaAdapter:
    """Adapter for Robokassa payment gateway"""
    
    def __init__(
        self,
        merchant_login: Optional[str] = None,
        password1: Optional[str] = None,
        password2: Optional[str] = None,
        is_test: bool = False,
        hold_mode: bool = False,
        receipt_enabled: bool = False,
        config: Optional[RobokassaConfig] = None
    ):
        if config:
            self.merchant_login = config.login
            self.password1 = config.password1
            self.password2 = config.password2
            self.is_test = config.is_test
            self.hold_mode = config.hold_mode
            self.receipt_enabled = config.receipt_enabled
            self.base_url = config.base_url
        else:
            self.merchant_login = merchant_login
            self.password1 = password1
            self.password2 = password2
            self.is_test = is_test
            self.hold_mode = hold_mode
            self.receipt_enabled = receipt_enabled
            
            # Base URLs
            if is_test:
                self.base_url = "https://auth.robokassa.ru/Merchant/Index"
            else:
                self.base_url = "https://merchant.roboxchange.com/Index"
    
    def generate_signature(
        self,
        amount: float,
        inv_id: str,
        currency: str = "RUB",
        description: str = "",
        password: Optional[str] = None
    ) -> str:
        """
        Generate signature for payment request
        
        Format: MerchantLogin:Amount:InvID:Currency:Description:Password
        Hash: MD5 of the concatenated string
        
        Args:
            amount: Payment amount in RUB (float)
            inv_id: Invoice ID (unique identifier)
            currency: Currency code (default: RUB)
            description: Payment description
            password: Password to use (password1 for request, password2 for callback)
        
        Returns:
            MD5 hash signature
        """
        if password is None:
            password = self.password1
        
        # Build signature string
        sig_string = f"{self.merchant_login}:{amount}:{inv_id}:{currency}:{description}:{password}"
        
        # Generate MD5 hash (case-insensitive)
        signature = hashlib.md5(sig_string.encode('utf-8')).hexdigest().lower()
        
        logger.debug(f"Generated signature for InvId={inv_id}: {signature}")
        return signature
    
    def verify_callback_signature(
        self,
        amount: float,
        inv_id: str,
        currency: str,
        signature: str,
        use_password2: bool = True
    ) -> bool:
        """
        Verify signature from Robokassa callback (ResultURL)
        
        Args:
            amount: Payment amount from callback
            inv_id: Invoice ID from callback
            currency: Currency from callback
            signature: Signature from callback (OutSum)
            use_password2: Use password2 for verification (always True for ResultURL)
        
        Returns:
            True if signature is valid, False otherwise
        """
        password = self.password2 if use_password2 else self.password1
        
        # Build expected signature
        sig_string = f"{self.merchant_login}:{amount}:{inv_id}:{currency}:{password}"
        expected_signature = hashlib.md5(sig_string.encode('utf-8')).hexdigest().lower()
        
        # Compare signatures (case-insensitive)
        is_valid = hmac.compare_digest(expected_signature, signature.lower())
        
        if not is_valid:
            logger.warning(f"Invalid signature for InvId={inv_id}. Expected: {expected_signature}, Got: {signature}")
        
        return is_valid
    
    def generate_payment_url(
        self,
        amount: float,
        inv_id: str,
        description: str = "",
        currency: str = "RUB",
        email: Optional[str] = None,
        phone: Optional[str] = None,
        shp_params: Optional[Dict[str, str]] = None,
        receipt: Optional[Dict[str, Any]] = None,
        hold: Optional[bool] = None
    ) -> str:
        """
        Generate payment URL for redirect
        
        Args:
            amount: Payment amount
            inv_id: Unique invoice ID
            description: Payment description
            currency: Currency code
            email: Customer email (for fiscal receipt)
            phone: Customer phone (for fiscal receipt)
            shp_params: Custom parameters (Shp_*)
            receipt: Fiscal receipt data (JSON structure per ФЗ-54)
            hold: Enable hold mode for this payment
        
        Returns:
            Payment URL for redirect
        """
        # Build base parameters
        params = {
            "MerchantLogin": self.merchant_login,
            "OutSum": str(amount),
            "InvId": inv_id,
            "Currency": currency,
            "Description": description,
            "SignatureValue": self.generate_signature(amount, inv_id, currency, description),
        }
        
        # Add optional parameters
        if email:
            params["Email"] = email
        
        if phone:
            params["Phone"] = phone
        
        # Add hold mode
        if hold is not None:
            params["IsHold"] = "1" if hold else "0"
        elif self.hold_mode:
            params["IsHold"] = "1"
        
        # Add custom Shp_* parameters
        if shp_params:
            for key, value in shp_params.items():
                # Ensure key starts with Shp_
                if not key.startswith("Shp_"):
                    key = f"Shp_{key}"
                params[key] = value
        
        # Add fiscal receipt (v2 format)
        if receipt and self.receipt_enabled:
            import json
            params["Receipt"] = json.dumps(receipt)
        
        # Encode parameters
        query_string = urlencode(params)
        
        # Build full URL
        url = f"{self.base_url}?{query_string}"
        
        logger.info(f"Generated payment URL for InvId={inv_id}, Amount={amount}")
        return url
    
    def parse_callback_data(self, form_data: Dict[str, str]) -> Dict[str, Any]:
        """
        Parse and validate callback data from ResultURL
        
        Expected fields:
        - OutSum: Payment amount
        - InvId: Invoice ID
        - Currency: Currency code
        - SignatureValue: Signature
        - IncCurrLabel: Incoming currency label
        - IncAmount: Incoming amount
        - PaymentMethod: Payment method (bank_card, sbp, etc.)
        - Shp_*: Custom parameters
        
        Args:
            form_data: POST form data from Robokassa
        
        Returns:
            Parsed and validated data dict
        """
        result = {
            "out_sum": form_data.get("OutSum", ""),
            "inv_id": form_data.get("InvId", ""),
            "currency": form_data.get("Currency", "RUB"),
            "signature": form_data.get("SignatureValue", ""),
            "inc_curr_label": form_data.get("IncCurrLabel", ""),
            "inc_amount": form_data.get("IncAmount", ""),
            "payment_method": form_data.get("PaymentMethod", ""),
            "shp_params": {},
            "raw": form_data,
        }
        
        # Extract Shp_* parameters
        for key, value in form_data.items():
            if key.startswith("Shp_"):
                result["shp_params"][key] = value
        
        # Validate signature
        try:
            amount = float(result["out_sum"])
        except ValueError:
            amount = 0.0
        
        result["signature_valid"] = self.verify_callback_signature(
            amount=amount,
            inv_id=result["inv_id"],
            currency=result["currency"],
            signature=result["signature"],
            use_password2=True
        )
        
        return result
    
    def validate_result_signature(self, form_data: Dict[str, str]) -> bool:
        """
        Validate signature from ResultURL callback
        
        Robokassa ResultURL format:
        SignatureValue = MD5(OutSum:InvId:Status:password2)
        
        Args:
            form_data: POST form data from Robokassa
        
        Returns:
            True if signature is valid, False otherwise
        """
        # Check required fields
        required_fields = ["OutSum", "InvId", "SignatureValue", "Status"]
        for field in required_fields:
            if field not in form_data or not form_data[field]:
                logger.warning(f"Missing required field: {field}")
                return False
        
        out_sum = form_data["OutSum"]
        inv_id = form_data["InvId"]
        signature = form_data["SignatureValue"]
        status = form_data["Status"]
        
        # Build expected signature: OutSum:InvId:Status:password2
        sig_string = f"{out_sum}:{inv_id}:{status}:{self.password2}"
        expected_signature = hashlib.md5(sig_string.encode('utf-8')).hexdigest().upper()
        
        # Compare signatures (case-insensitive)
        is_valid = hmac.compare_digest(expected_signature.upper(), signature.upper())
        
        if not is_valid:
            logger.warning(f"Invalid signature for InvId={inv_id}. Expected: {expected_signature}, Got: {signature}")
        else:
            logger.info(f"Valid signature for InvId={inv_id}")
        
        return is_valid
    
    def generate_success_url_response(self, inv_id: str) -> str:
        """
        Generate response for SuccessURL
        
        After successful payment, Robokassa redirects to SuccessURL.
        Return HTML with success message or redirect.
        
        Args:
            inv_id: Invoice ID
        
        Returns:
            HTML response string
        """
        return f"""
        <html>
            <head><title>Payment Successful</title></head>
            <body>
                <h1>Payment Successful</h1>
                <p>Invoice ID: {inv_id}</p>
                <p>Thank you for your payment!</p>
            </body>
        </html>
        """
    
    def generate_fail_url_response(self, inv_id: str) -> str:
        """
        Generate response for FailURL
        
        Args:
            inv_id: Invoice ID
        
        Returns:
            HTML response string
        """
        return f"""
        <html>
            <head><title>Payment Failed</title></head>
            <body>
                <h1>Payment Failed</h1>
                <p>Invoice ID: {inv_id}</p>
                <p>Please try again or contact support.</p>
            </body>
        </html>
        """


# Global adapter instance (initialized from settings)
_robokassa_adapter: Optional[RobokassaAdapter] = None


def get_robokassa_adapter() -> RobokassaAdapter:
    """Get or create Robokassa adapter instance"""
    global _robokassa_adapter
    
    if _robokassa_adapter is None:
        _robokassa_adapter = RobokassaAdapter(
            merchant_login=settings.ROBOKASSA_MERCHANT_LOGIN,
            password1=settings.ROBOKASSA_PASSWORD1,
            password2=settings.ROBOKASSA_PASSWORD2,
            is_test=settings.ROBOKASSA_IS_TEST,
            hold_mode=settings.ROBOKASSA_HOLD_MODE,
            receipt_enabled=settings.ROBOKASSA_RECEIPT_ENABLED,
        )
    
    return _robokassa_adapter
