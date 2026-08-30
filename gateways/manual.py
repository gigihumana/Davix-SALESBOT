"""
Manual gateway - lets a shop accept literally ANY payment method (bank
transfer, another local gateway, cash app, a friend's PSP, etc.) that
doesn't have a dedicated integration yet. The admin writes instructions,
the buyer pays outside the bot, and a staff member confirms the order
with /order confirm. This is what makes the bot "support every gateway
in the world" honestly: coded integrations for the major ones, and an
always-available manual fallback for everything else.
"""
from __future__ import annotations

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

ALL_CURRENCIES = (
    "USD", "EUR", "GBP", "BRL", "MXN", "ARS", "CAD", "AUD", "JPY", "CNY",
    "INR", "CHF", "ZAR", "NGN", "PHP", "IDR", "TRY", "PLN", "SEK", "NOK",
)


class ManualGateway(BaseGateway):
    key = "manual"
    display_name = "Manual / Custom Gateway"
    supported_currencies = ALL_CURRENCIES
    homepage = ""
    credential_fields = (
        GatewayField(
            "instructions", "Payment instructions shown to buyers",
            secret=False, required=True,
            placeholder="Send payment to @yourhandle then wait for staff confirmation.",
        ),
    )

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        return ChargeResult(
            external_id=f"manual-{order_id}",
            instructions=self.credentials.get(
                "instructions", "Contact staff to complete your payment."
            ),
            raw={},
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        # Manual orders never auto-confirm - staff must run /order confirm.
        return PaymentStatus.PENDING
