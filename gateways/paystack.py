"""
Paystack gateway - the leading payments API across Nigeria, Ghana, South
Africa and Kenya. Docs: https://paystack.com/docs/api/transaction/
"""
from __future__ import annotations

import hashlib
import hmac
import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.paystack.co"
SUPPORTED_CURRENCIES = ("NGN", "GHS", "ZAR", "USD", "KES")


class PaystackGateway(BaseGateway):
    key = "paystack"
    display_name = "Paystack (Africa)"
    supported_currencies = SUPPORTED_CURRENCIES
    homepage = "https://dashboard.paystack.com/#/settings/developers"
    credential_fields = (
        GatewayField("secret_key", "Secret Key (sk_live_... / sk_test_...)", secret=True),
    )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials.get('secret_key', '')}",
            "Content-Type": "application/json",
        }

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        payload = {
            "email": f"buyer{buyer_id}@davixsales.bot",
            "amount": int(round(amount * 100)),  # kobo/cents
            "currency": currency.upper(),
            "reference": f"davix-{order_id}-{idempotency_key[-8:]}",
            "metadata": {"order_id": str(order_id), "description": description[:250]},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/transaction/initialize", headers=self._headers(),
                data=json.dumps(payload), timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201) or not data.get("status"):
                    raise RuntimeError(f"Paystack error: {data}")

        info = data["data"]
        return ChargeResult(
            external_id=info["reference"],
            checkout_url=info.get("authorization_url"),
            instructions="Click the link to pay by card, bank transfer or USSD.",
            raw=info,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/transaction/verify/{external_id}", headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        status = data.get("data", {}).get("status")
        return {
            "success": PaymentStatus.PAID,
            "abandoned": PaymentStatus.PENDING,
            "failed": PaymentStatus.ERROR,
            "reversed": PaymentStatus.REFUNDED,
        }.get(status, PaymentStatus.PENDING)

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        secret = self.credentials.get("secret_key")
        if not secret:
            return True
        received = headers.get("x-paystack-signature") or headers.get("X-Paystack-Signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, received)

    def parse_webhook(self, headers: dict, body: bytes) -> tuple[str, PaymentStatus] | None:
        try:
            event = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        data = event.get("data", {})
        reference = data.get("reference")
        if not reference:
            return None
        status = PaymentStatus.PAID if event.get("event") == "charge.success" else PaymentStatus.PENDING
        return reference, status
