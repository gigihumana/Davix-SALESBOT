"""
Payment gateway plugin interface.

Every gateway (Mercado Pago, Stripe, PayPal, PushinPay, NOWPayments, ...)
implements this same small contract. Adding support for a new gateway
in the future only requires writing one new class and registering it in
`gateways/__init__.py` - nothing else in the bot needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    ERROR = "error"


@dataclass
class ChargeResult:
    """What a gateway returns after creating a charge/payment intent."""
    external_id: str
    checkout_url: str | None = None          # link the buyer opens/pays at
    qr_code_text: str | None = None           # e.g. Pix "copia e cola" payload
    qr_code_base64: str | None = None         # ready-to-render QR image
    instructions: str | None = None           # free text shown to the buyer
    raw: dict = field(default_factory=dict)


@dataclass
class GatewayField:
    """Describes one credential the admin must type in the /gateway setup modal."""
    key: str
    label: str
    secret: bool = True
    required: bool = True
    placeholder: str = ""


class BaseGateway(ABC):
    key: str = "base"
    display_name: str = "Base Gateway"
    supported_currencies: tuple[str, ...] = ()
    credential_fields: tuple[GatewayField, ...] = ()
    homepage: str = ""

    def __init__(self, credentials: dict, extra: dict | None = None):
        self.credentials = credentials
        self.extra = extra or {}

    def supports_currency(self, currency: str) -> bool:
        return currency.upper() in self.supported_currencies

    @abstractmethod
    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        """Create a payment/checkout on the gateway's side and return how to pay."""
        raise NotImplementedError

    @abstractmethod
    async def check_status(self, external_id: str) -> PaymentStatus:
        """Actively poll the gateway for the current status of a charge."""
        raise NotImplementedError

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        """Return True if an inbound webhook request is authentic. Optional to override."""
        return False

    def parse_webhook(self, headers: dict, body: bytes) -> tuple[str, PaymentStatus] | None:
        """Extract (external_id, status) from a verified webhook payload."""
        return None
