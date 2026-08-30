"""
Razorpay gateway - the most widely used payments API in India.
Docs: https://razorpay.com/docs/api/orders/
"""
from __future__ import annotations

import hashlib
import hmac
import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.razorpay.com/v1"


class RazorpayGateway(BaseGateway):
    key = "razorpay"
    display_name = "Razorpay (India)"
    supported_currencies = ("INR",)
    homepage = "https://dashboard.razorpay.com/app/keys"
    credential_fields = (
        GatewayField("key_id", "Key ID", secret=True),
        GatewayField("key_secret", "Key Secret", secret=True),
        GatewayField("webhook_secret", "Webhook Secret (optional)", secret=True, required=False),
    )

    def _auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(self.credentials.get("key_id", ""), self.credentials.get("key_secret", ""))

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        payload = {
            "amount": int(round(amount * 100)),  # paise
            "currency": currency.upper(),
            "receipt": f"davix-{order_id}",
            "notes": {"order_id": str(order_id), "description": description[:250]},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/orders", auth=self._auth(),
                data=json.dumps(payload), headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Razorpay error: {data}")

        # Razorpay Orders don't have a hosted checkout link by default - a
        # short payment link is created instead so buyers can pay in-browser.
        pl_payload = {
            "amount": payload["amount"],
            "currency": currency.upper(),
            "description": description[:250],
            "reference_id": f"davix-{order_id}",
            "notes": {"order_id": str(order_id)},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/payment_links", auth=self._auth(),
                data=json.dumps(pl_payload), headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                link_data = await resp.json()

        return ChargeResult(
            external_id=data["id"],
            checkout_url=link_data.get("short_url"),
            instructions="Open the link to pay via UPI, card, netbanking or wallet.",
            raw={"order": data, "payment_link": link_data},
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/orders/{external_id}/payments", auth=self._auth(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        items = data.get("items", [])
        if any(p.get("status") == "captured" for p in items):
            return PaymentStatus.PAID
        if any(p.get("status") == "authorized" for p in items):
            return PaymentStatus.PENDING
        if any(p.get("status") == "refunded" for p in items):
            return PaymentStatus.REFUNDED
        return PaymentStatus.PENDING

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        secret = self.credentials.get("webhook_secret")
        if not secret:
            return True
        received = headers.get("x-razorpay-signature") or headers.get("X-Razorpay-Signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, received)

    def parse_webhook(self, headers: dict, body: bytes) -> tuple[str, PaymentStatus] | None:
        try:
            event = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        payload = event.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payload.get("order_id")
        if not order_id:
            return None
        status = PaymentStatus.PAID if payload.get("status") == "captured" else PaymentStatus.PENDING
        return order_id, status
